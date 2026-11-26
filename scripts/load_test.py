#!/usr/bin/env python3
"""Load test: how many students can one Netra-I server proctor at once? (audit P2-14)

Simulates students sitting a camera-proctored exam against a running server:
each one signs in, starts the exam, opens the paper, then for the whole run
sends a webcam frame over Socket.IO every few seconds, autosaves answers every
20 s and polls the exam status every 15 s, exactly like the exam page does.

Standard library only (Socket.IO is spoken over HTTP long-polling), so it runs
from the project's venv with nothing extra installed.

    # 1. Create the test students and exam in the server's database
    #    (same DATABASE_URL as the server; run from the project folder)
    venv/bin/python scripts/load_test.py seed --students 300

    # 2. Start the server as for an exam, then from another terminal or machine
    venv/bin/python scripts/load_test.py run --url http://127.0.0.1:5000 \
        --students 100 --minutes 3 --frame path/to/webcam_snapshot.jpg

    # 3. Remove the test students, exam and their evidence images
    venv/bin/python scripts/load_test.py cleanup

Use a real webcam snapshot with one face for --frame: the proctoring engine
does more work when it finds a face (head pose) than on an empty frame.
Step the student count up (50, 100, 150 ...) until the report says FAIL;
the last passing count, minus a safety margin, is the hall's capacity.
Run it against a test database, never the live one.
"""
import argparse
import base64
import http.cookiejar
import json
import os
import random
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
USER_PREFIX = 'loadtest'
PASSWORD = 'loadtest-pass-1'
EXAM_TITLE = 'Load test (safe to delete)'
QUESTIONS = 20

# Pass/fail thresholds for the report
HTTP_P95_LIMIT = 1.0         # seconds
FRAME_P95_LIMIT = 4.0        # a frame must be judged before the next one is due
ERROR_RATE_LIMIT = 0.01


# ── Seeding (runs inside the app, against DATABASE_URL) ──────────────────────

def _app():
    sys.path.insert(0, ROOT)
    os.environ.setdefault('LOG_TO_FILE', 'false')
    from app import create_app
    return create_app()


def seed(args):
    app = _app()
    from werkzeug.security import generate_password_hash
    from backend.database import db
    from backend.models import Exam, Question, User

    with app.app_context():
        pw = generate_password_hash(PASSWORD)
        existing = {u.username for u in User.query.filter(User.username.like(f'{USER_PREFIX}%'))}
        made = 0
        for i in range(1, args.students + 1):
            name = f'{USER_PREFIX}{i:04d}'
            if name in existing:
                continue
            db.session.add(User(username=name, email=f'{name}@loadtest.invalid', password_hash=pw,
                                role='student', full_name=f'Load Test {i:04d}', is_verified=True,
                                password_changed=True, batch='Load test'))
            made += 1
        owner = User.query.filter_by(role='admin').first() or User.query.filter_by(role='faculty').first()
        if owner is None:
            sys.exit('Create an admin or faculty account first; the test exam needs an owner.')
        exam = Exam.query.filter_by(title=EXAM_TITLE).first()
        if exam is None:
            exam = Exam(title=EXAM_TITLE, duration_minutes=180, passing_score=40, creator_id=owner.id,
                        is_active=True, allow_all_students=True, randomize_questions=True)
            db.session.add(exam)
            db.session.flush()
            for n in range(1, QUESTIONS + 1):
                db.session.add(Question(exam_id=exam.id, question_text=f'Load test question {n}',
                                        option_a='A', option_b='B', option_c='C', option_d='D',
                                        correct_answer=random.choice('ABCD'), points=1, order_number=n))
        db.session.commit()
        print(f'Seeded {made} new student(s) ({args.students} wanted) and exam #{exam.id} "{EXAM_TITLE}".')


def cleanup(args):
    app = _app()
    import shutil
    from backend.database import db
    from backend.models import Exam, StudentExam, User

    with app.app_context():
        exam = Exam.query.filter_by(title=EXAM_TITLE).first()
        if exam:
            db.session.delete(exam)
        users = User.query.filter(User.username.like(f'{USER_PREFIX}%'),
                                  User.email.like('%@loadtest.invalid')).all()
        for u in users:
            StudentExam.query.filter_by(student_id=u.id).delete()
            db.session.delete(u)
        db.session.commit()
        print(f'Removed the test exam and {len(users)} test student(s).')
    folder = os.path.join(ROOT, 'frontend', 'static', 'violations')
    removed = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.startswith('Load_test_safe_to_delete'):
                shutil.rmtree(os.path.join(folder, name), ignore_errors=True)
                removed += 1
    print(f'Removed {removed} evidence folder(s).')


# ── A simulated student ──────────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.times = {}
        self.errors = {}
        self.frames_sent = 0
        self.frames_answered = 0
        self.warnings = 0

    def add(self, kind, seconds):
        with self.lock:
            self.times.setdefault(kind, []).append(seconds)

    def error(self, kind, detail):
        with self.lock:
            self.errors.setdefault(kind, []).append(detail)


class Student:
    def __init__(self, base, index, frame, stats, frame_interval, stop, transport='websocket'):
        self.base = base.rstrip('/')
        self.username = f'{USER_PREFIX}{index:04d}'
        self.frame = frame
        self.stats = stats
        self.frame_interval = frame_interval
        self.stop = stop
        self.jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None
        self.se_id = None
        self.sid = None
        self.transport = transport
        self.ws = None
        self.ws_lock = threading.Lock()
        self.pending = []           # send times of frames awaiting a proctor_result
        self.pending_lock = threading.Lock()

    # HTTP --------------------------------------------------------------
    def request(self, method, path, data=None, json_body=None, kind=None, timeout=30):
        headers = {}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers['Content-Type'] = 'application/json'
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if method != 'GET' and self.csrf:
            headers['X-CSRFToken'] = self.csrf
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        started = time.perf_counter()
        try:
            with self.http.open(req, timeout=timeout) as resp:
                text = resp.read().decode('utf-8', 'replace')
                status = resp.status
        except urllib.error.HTTPError as exc:
            text, status = exc.read().decode('utf-8', 'replace'), exc.code
        except Exception as exc:                      # timeout, refused, reset
            if kind:
                self.stats.error(kind, type(exc).__name__)
            return None, None
        if kind:
            self.stats.add(kind, time.perf_counter() - started)
            if status >= 400:
                self.stats.error(kind, f'HTTP {status}')
        return status, text

    def _token_from(self, html):
        m = re.search(r'name="csrf-token" content="([^"]+)"', html or '')
        if m:
            self.csrf = m.group(1)

    def sign_in_and_open(self, exam_id):
        _, html = self.request('GET', '/login')
        self._token_from(html)
        status, html = self.request('POST', '/login', data={
            'username': self.username, 'password': PASSWORD, 'csrf_token': self.csrf or ''}, kind='login')
        if status is None or 'Invalid username' in (html or ''):
            raise RuntimeError('login failed')
        self._token_from(html)
        self.request('POST', f'/start-exam/{exam_id}', data={'csrf_token': self.csrf or ''}, kind='start')
        status, html = self.request('GET', f'/exam/{exam_id}/take', kind='open paper')
        if status != 200:
            raise RuntimeError(f'could not open the paper (HTTP {status})')
        self._token_from(html)
        m = re.search(r'seId\s*:\s*(\d+)', html) or re.search(r'student_exam_id["\']?\s*[:=]\s*(\d+)', html)
        if not m:
            raise RuntimeError('attempt id not found on the exam page')
        self.se_id = int(m.group(1))
        self.question_ids = [int(q) for q in re.findall(r'data-qid="(\d+)"', html)] or list(range(1, 21))

    # Socket.IO: WebSocket (what browsers use) or long-polling ----------
    def _eio(self, method, body=None, timeout=70):
        query = {'EIO': '4', 'transport': 'polling', 't': str(time.time())}
        if self.sid:
            query['sid'] = self.sid
        req = urllib.request.Request(f'{self.base}/socket.io/?{urllib.parse.urlencode(query)}',
                                     data=body.encode() if body is not None else None,
                                     headers={'Content-Type': 'text/plain;charset=UTF-8'},
                                     method=method)
        with self.http.open(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', 'replace')

    def connect_socket(self):
        if self.transport == 'websocket':
            import simple_websocket            # ships with python-engineio, which the server needs
            url = self.base.replace('http', 'ws', 1) + '/socket.io/?EIO=4&transport=websocket'
            cookie = '; '.join(f'{c.name}={c.value}' for c in self.jar)
            self.ws = simple_websocket.Client.connect(url, headers={'Cookie': cookie})
            opened = self.ws.receive(timeout=30)
            if not opened or not opened.startswith('0'):
                raise RuntimeError('no Engine.IO handshake')
            self._ws_send('40')
            reply = self.ws.receive(timeout=30)
            if not reply or not reply.startswith('40'):
                raise RuntimeError('Socket.IO connect refused')
        else:
            opened = self._eio('GET')
            self.sid = json.loads(opened[1:].split('\x1e')[0])['sid']
            self._eio('POST', '40')
        threading.Thread(target=self._reader, daemon=True).start()
        self.emit('join_exam', {'student_exam_id': self.se_id})

    def _ws_send(self, data):
        with self.ws_lock:
            self.ws.send(data)

    def emit(self, event, payload, binary=None):
        if binary is None:
            text = '42' + json.dumps([event, payload])
        else:
            payload = dict(payload, frame={'_placeholder': True, 'num': 0})
            text = '451-' + json.dumps([event, payload])
        if self.ws is not None:
            with self.ws_lock:                  # the attachment must follow its header
                self.ws.send(text)
                if binary is not None:
                    self.ws.send(binary)
        elif binary is None:
            self._eio('POST', text)
        else:
            self._eio('POST', text + '\x1e' + 'b' + base64.b64encode(binary).decode())

    def _packets(self):
        """Yield Engine.IO packets from whichever transport is in use."""
        while not self.stop.is_set():
            if self.ws is not None:
                data = self.ws.receive(timeout=70)
                if data is None:
                    raise ConnectionError('websocket closed')
                yield data
            else:
                yield from self._eio('GET').split('\x1e')

    def _pong(self):
        if self.ws is not None:
            self._ws_send('3')
        else:
            self._eio('POST', '3')

    def _reader(self):
        try:
            for packet in self._packets():
                if not isinstance(packet, str):
                    continue
                if packet == '2':
                    try:
                        self._pong()
                    except Exception:
                        pass
                elif packet.startswith('42'):
                    try:
                        name, body = json.loads(packet[2:])[:2]
                    except ValueError:
                        continue
                    if name == 'proctor_result':
                        with self.pending_lock:
                            sent = self.pending.pop(0) if self.pending else None
                        if sent is not None:
                            self.stats.add('frame judged', time.perf_counter() - sent)
                            with self.stats.lock:
                                self.stats.frames_answered += 1
                                if not body.get('success'):
                                    self.stats.warnings += 1
                elif packet.startswith('41') or packet == '1':
                    self.stats.error('socket', 'disconnected by server')
                    return
        except Exception as exc:
            if not self.stop.is_set():
                self.stats.error('socket', type(exc).__name__)

    # The exam ---------------------------------------------------------
    def sit(self, until, camera):
        now = time.monotonic()
        next_frame = now + random.uniform(0, self.frame_interval)
        next_save = now + random.uniform(0, 20)
        next_poll = now + random.uniform(0, 15)
        answers = {}
        while not self.stop.is_set() and time.monotonic() < until:
            now = time.monotonic()
            if camera and now >= next_frame:
                with self.pending_lock:
                    # Frames the server never answered are counted, not waited for
                    self.pending = [t for t in self.pending if time.perf_counter() - t < 30]
                    self.pending.append(time.perf_counter())
                try:
                    self.emit('frameBinary', {'studentExamId': self.se_id}, binary=self.frame)
                    with self.stats.lock:
                        self.stats.frames_sent += 1
                except Exception as exc:
                    self.stats.error('socket', type(exc).__name__)
                next_frame = now + self.frame_interval
            if now >= next_save:
                answers[str(random.choice(self.question_ids))] = random.choice('ABCD')
                self.request('POST', '/api/save-answer',
                             json_body={'student_exam_id': self.se_id, 'answers': answers}, kind='autosave')
                next_save = now + 20
            if now >= next_poll:
                self.request('GET', f'/api/check-exam-status/{self.se_id}', kind='status poll')
                next_poll = now + 15
            time.sleep(0.05)


# ── The run ──────────────────────────────────────────────────────────────────

def _pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = min(len(values) - 1, max(0, int(round(p / 100 * len(values) + 0.5)) - 1))
    return values[k]


def _find_exam_id(base):
    probe = Student(base, 1, b'', Stats(), 4, threading.Event())
    _, html = probe.request('GET', '/login')
    probe._token_from(html)
    probe.request('POST', '/login', data={'username': probe.username, 'password': PASSWORD,
                                          'csrf_token': probe.csrf or ''})
    _, html = probe.request('GET', '/student/dashboard')
    html = html or ''
    titles = [m.start() for m in re.finditer(re.escape(EXAM_TITLE), html)]
    best = None
    for m in re.finditer(r'/(?:start-exam/(\d+)|exam/(\d+)/take)', html):
        for t in titles:
            gap = abs(m.start() - t)
            if best is None or gap < best[0]:
                best = (gap, int(m.group(1) or m.group(2)))
    return best[1] if best else None


def run(args):
    frame = open(args.frame, 'rb').read() if args.frame else _blank_jpeg()
    exam_id = args.exam_id or _find_exam_id(args.url)
    if not exam_id:
        sys.exit('Could not find the load-test exam. Run "seed" first, or pass --exam-id.')

    stats = Stats()
    stop = threading.Event()
    students = [Student(args.url, i, frame, stats, args.frame_interval, stop, args.transport)
                for i in range(args.offset + 1, args.offset + args.students + 1)]
    ready = []
    ready_lock = threading.Lock()
    failed = []

    def join(st):
        try:
            st.sign_in_and_open(exam_id)
            if not args.no_camera:
                st.connect_socket()
            with ready_lock:
                ready.append(st)
        except Exception as exc:
            failed.append(f'{st.username}: {exc}')

    print(f'Starting {len(students)} student(s) over {args.ramp}s against {args.url} (exam #{exam_id}) ...')
    threads = []
    for st in students:
        t = threading.Thread(target=join, args=(st,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(args.ramp / max(1, len(students)))
    for t in threads:
        t.join(timeout=120)
    print(f'{len(ready)} sitting the exam, {len(failed)} could not start.')
    for line in failed[:5]:
        print('   ', line)

    until = time.monotonic() + args.minutes * 60
    workers = [threading.Thread(target=st.sit, args=(until, not args.no_camera), daemon=True) for st in ready]
    for w in workers:
        w.start()
    started = time.monotonic()
    while time.monotonic() < until:
        time.sleep(min(15, max(0.1, until - time.monotonic())))
        frames = stats.times.get('frame judged', [])
        print(f'  {int(time.monotonic() - started):4d}s  frames sent {stats.frames_sent:6d}  '
              f'judged {stats.frames_answered:6d}  p95 {(_pct(frames[-500:], 95) or 0):5.2f}s  '
              f'errors {sum(len(v) for v in stats.errors.values())}')
    stop.set()
    for w in workers:
        w.join(timeout=5)
    for st in ready:
        if st.ws is not None:
            try:
                st.ws.close()
            except Exception:
                pass

    report = _report(args, stats, len(ready), len(failed))
    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(report, fh, indent=2)
        print(f'Report written to {args.json}')
    sys.exit(0 if report['verdict'] == 'PASS' else 1)


def _report(args, stats, sitting, failed):
    print('\n' + '=' * 72)
    print(f'{sitting} students for {args.minutes} min, frame every {args.frame_interval}s'
          f' over {args.transport}{" (camera off)" if args.no_camera else ""}')
    print('=' * 72)
    print(f'{"":16}{"count":>8}{"p50":>9}{"p95":>9}{"p99":>9}{"max":>9}{"errors":>9}')
    rows = {}
    for kind in ('login', 'start', 'open paper', 'autosave', 'status poll', 'frame judged'):
        values = stats.times.get(kind, [])
        errs = len(stats.errors.get(kind, []))
        rows[kind] = {'count': len(values), 'errors': errs,
                      'p50': _pct(values, 50), 'p95': _pct(values, 95),
                      'p99': _pct(values, 99), 'max': max(values) if values else None}
        if not values and not errs:
            continue
        r = rows[kind]
        fmt = lambda v: f'{v:8.2f}s' if v is not None else '       —'
        print(f'{kind:16}{r["count"]:8d} {fmt(r["p50"])}{fmt(r["p95"])}{fmt(r["p99"])}{fmt(r["max"])}{errs:9d}')
    socket_errors = len(stats.errors.get('socket', []))
    unanswered = max(0, stats.frames_sent - stats.frames_answered)
    print(f'\nFrames sent {stats.frames_sent}, judged {stats.frames_answered}, '
          f'unanswered {unanswered}, flagged {stats.warnings}. Socket errors {socket_errors}.')

    problems = []
    for kind in ('autosave', 'status poll'):
        p95 = rows[kind]['p95']
        if p95 is not None and p95 > HTTP_P95_LIMIT:
            problems.append(f'{kind} p95 {p95:.2f}s is over {HTTP_P95_LIMIT}s')
    fp95 = rows['frame judged']['p95']
    if not args.no_camera and fp95 is not None and fp95 > FRAME_P95_LIMIT:
        problems.append(f'frames take {fp95:.2f}s (p95) to judge, over {FRAME_P95_LIMIT}s: the server is falling behind')
    if not args.no_camera and stats.frames_sent and unanswered / stats.frames_sent > 0.05:
        problems.append(f'{unanswered} of {stats.frames_sent} frames never got an answer')
    requests_total = sum(r['count'] + r['errors'] for r in rows.values()) or 1
    error_total = sum(r['errors'] for r in rows.values()) + socket_errors
    if error_total / requests_total > ERROR_RATE_LIMIT:
        problems.append(f'{error_total} errors ({100 * error_total / requests_total:.1f}%)')
    if failed:
        problems.append(f'{failed} student(s) could not start')

    verdict = 'FAIL' if problems else 'PASS'
    print(f'\n{verdict}' + (': ' + '; '.join(problems) if problems else
                            f': {sitting} students were handled comfortably.'))
    return {'students': sitting, 'minutes': args.minutes, 'frame_interval': args.frame_interval,
            'transport': args.transport,
            'camera': not args.no_camera, 'rows': rows, 'frames_sent': stats.frames_sent,
            'frames_answered': stats.frames_answered, 'socket_errors': socket_errors,
            'errors': {k: v[:20] for k, v in stats.errors.items()},
            'problems': problems, 'verdict': verdict}


def _blank_jpeg():
    """A plain 320x240 frame for when no snapshot is given (no face: a light load)."""
    import cv2
    import numpy as np
    img = np.full((240, 320, 3), 128, np.uint8)
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return buf.tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('seed', help='create test students and the test exam')
    p.add_argument('--students', type=int, default=300)
    sub.add_parser('cleanup', help='remove test students, exam and evidence')
    p = sub.add_parser('run', help='drive a running server')
    p.add_argument('--url', default='http://127.0.0.1:5000')
    p.add_argument('--students', type=int, default=50)
    p.add_argument('--offset', type=int, default=0, help='start at loadtest<offset+1> (to run from several machines)')
    p.add_argument('--minutes', type=float, default=3)
    p.add_argument('--ramp', type=float, default=30, help='seconds over which students sign in')
    p.add_argument('--frame', help='JPEG webcam snapshot to send (recommended)')
    p.add_argument('--frame-interval', type=float,
                   default=float(os.getenv('NETRA_FRAME_INTERVAL_SEC', '4.0')) + 0.25)
    p.add_argument('--no-camera', action='store_true', help='answers and polling only')
    p.add_argument('--transport', choices=('websocket', 'polling'), default='websocket',
                   help='websocket is what browsers use; polling is the fallback behind strict proxies')
    p.add_argument('--exam-id', type=int)
    p.add_argument('--json', help='also write the report to this file')
    args = parser.parse_args()
    {'seed': seed, 'cleanup': cleanup, 'run': run}[args.cmd](args)


if __name__ == '__main__':
    main()
