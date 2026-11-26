"""Phase 2 hardening (docs/audit/PHASE1_AUDIT.md §5). Tests are named test_<id>_…"""

import time

import pytest

from backend.database import db
from backend.models import ExamViolation, StudentExam
from backend.services import attempt_device
from backend.socket_io import socketio


def _attempt(app, student_id, exam_id):
    with app.app_context():
        return StudentExam.query.filter_by(student_id=student_id, exam_id=exam_id).first()


def _violations(app, attempt_id, kind):
    with app.app_context():
        return ExamViolation.query.filter_by(student_exam_id=attempt_id, violation_type=kind).count()


@pytest.fixture
def running(app, seed, as_user):
    """stud2 has started the exam and opened the paper on device A."""
    device_a = as_user('stud2')
    device_a.post(f"/start-exam/{seed['exam']}")
    assert device_a.get(f"/exam/{seed['exam']}/take").status_code == 200
    se = _attempt(app, seed['s2'], seed['exam'])
    return dict(seed, a=device_a, se=se.id)


def _save(client, se_id, qid, answer='A'):
    return client.post('/api/save-answer', json={'student_exam_id': se_id, 'answers': {str(qid): answer}})


# ── P2-11: one attempt, one device ──────────────────────────────────────────

def test_p2_11_first_device_claims_and_refresh_keeps_it(app, running):
    with app.app_context():
        assert StudentExam.query.get(running['se']).device_token
    assert running['a'].get(f"/exam/{running['exam']}/take").status_code == 200
    assert _save(running['a'], running['se'], running['q1']).status_code == 200


def test_p2_11_second_device_refused_while_first_active(app, running, as_user):
    device_b = as_user('stud2')
    resp = device_b.get(f"/exam/{running['exam']}/take")
    assert resp.status_code == 409
    assert b'Open on another device' in resp.data
    assert _violations(app, running['se'], 'second_device') == 1

    # Nothing it sends counts
    r = _save(device_b, running['se'], running['q1'])
    assert r.status_code == 409 and r.get_json()['moved']
    assert device_b.get(f"/api/check-exam-status/{running['se']}").get_json()['moved']
    assert device_b.post(f"/api/update-tabcount/{running['se']}", json={'tab_switch_count': 9}).status_code == 409
    resp = device_b.post(f"/submit_exam/{running['se']}")
    assert resp.status_code == 302
    assert not _attempt(app, running['s2'], running['exam']).completed

    # Device A carries on
    assert _save(running['a'], running['se'], running['q1']).status_code == 200


def test_p2_11_takeover_after_first_device_goes_quiet(app, running, as_user):
    attempt_device._last_seen[running['se']] = time.time() - attempt_device.DEVICE_STALE_SECONDS - 5
    device_b = as_user('stud2')
    assert device_b.get(f"/exam/{running['exam']}/take").status_code == 200
    assert _violations(app, running['se'], 'device_switch') == 1
    assert _attempt(app, running['s2'], running['exam']).device_switches == 1

    # The old device is told it moved and can no longer write or submit
    status = running['a'].get(f"/api/check-exam-status/{running['se']}")
    assert status.status_code == 409 and status.get_json()['moved']
    assert _save(running['a'], running['se'], running['q1']).status_code == 409
    running['a'].post(f"/submit_exam/{running['se']}")
    assert not _attempt(app, running['s2'], running['exam']).completed

    assert _save(device_b, running['se'], running['q1']).status_code == 200
    assert device_b.post(f"/submit_exam/{running['se']}").status_code == 302
    assert _attempt(app, running['s2'], running['exam']).completed


def test_p2_11_takeover_allowed_after_restart(app, running, as_user):
    attempt_device._last_seen.clear()          # what a restart looks like
    device_b = as_user('stud2')
    assert device_b.get(f"/exam/{running['exam']}/take").status_code == 200
    assert _violations(app, running['se'], 'device_switch') == 1


def test_p2_11_polling_keeps_the_claim_alive(app, running, as_user):
    attempt_device._last_seen[running['se']] = time.time() - attempt_device.DEVICE_STALE_SECONDS - 5
    running['a'].get(f"/api/check-exam-status/{running['se']}")      # device A polls
    assert as_user('stud2').get(f"/exam/{running['exam']}/take").status_code == 409


def test_p2_11_socket_ignores_the_other_device(app, running, as_user):
    device_b = as_user('stud2')
    sock = socketio.test_client(app, flask_test_client=device_b)
    sock.emit('heartbeat', {'studentExamId': running['se']})
    assert sock.get_received() == []
    sock.disconnect()

    sock = socketio.test_client(app, flask_test_client=running['a'])
    sock.emit('heartbeat', {'studentExamId': running['se']})
    assert [m['name'] for m in sock.get_received()] == ['heartbeat_ack']
    sock.disconnect()


def test_p2_11_exam_page_handles_a_move(app, running):
    html = running['a'].get(f"/exam/{running['exam']}/take").get_data(as_text=True)
    assert 'id="moved-overlay"' in html
    assert 'function movedAway()' in html
    assert 'body.moved' in html and 'd.moved' in html


# ── P2-4: lockouts survive a restart ────────────────────────────────────────

def _fail_login(client, username, n):
    for _ in range(n):
        client.post('/login', data={'username': username, 'password': 'wrong-password'})


def test_p2_4_password_lockout_is_stored_in_the_database(app, seed):
    from backend.models import LoginThrottle
    client = app.test_client()
    _fail_login(client, 'stud1', 5)
    with app.app_context():
        row = db.session.get(LoginThrottle, 'pw:stud1')
        assert row and row.count == 5
    # Nothing in memory to lose: a fresh client (or a restarted server) is still locked
    resp = app.test_client().post('/login', data={'username': 'stud1', 'password': 'Passw0rd!'})
    assert resp.status_code == 429


def test_p2_4_lockout_expires_and_success_clears(app, seed):
    from datetime import datetime, timedelta
    from backend.models import LoginThrottle
    client = app.test_client()
    _fail_login(client, 'stud1', 5)
    with app.app_context():
        db.session.get(LoginThrottle, 'pw:stud1').window_start = datetime.utcnow() - timedelta(minutes=11)
        db.session.commit()
    assert client.post('/login', data={'username': 'stud1', 'password': 'Passw0rd!'}).status_code == 302
    with app.app_context():
        assert db.session.get(LoginThrottle, 'pw:stud1') is None


def test_p2_4_purge_expired(app, seed):
    from datetime import datetime, timedelta
    from backend.models import LoginThrottle
    from backend.services import throttle
    with app.app_context():
        throttle.record('old')
        throttle.record('new')
        db.session.get(LoginThrottle, 'old').window_start = datetime.utcnow() - timedelta(days=2)
        db.session.commit()
        assert throttle.purge_expired() == 1
        assert db.session.get(LoginThrottle, 'new')


# ── P2-6: sign-out needs a POST with the CSRF token ─────────────────────────

def test_p2_6_get_logout_does_not_sign_out(app, seed, as_user):
    client = as_user('stud1')
    resp = client.get('/logout')
    assert resp.status_code == 302
    assert client.get('/student/dashboard').status_code == 200


def test_p2_6_post_logout_signs_out_and_needs_csrf(app, seed, as_user):
    client = as_user('stud1')
    app.config['CSRF_ENABLED'] = True
    try:
        client.post('/logout')                                    # no token: refused
        assert client.get('/student/dashboard').status_code == 200
    finally:
        app.config['CSRF_ENABLED'] = False
    client.post('/logout')
    assert client.get('/student/dashboard').status_code == 302


def test_p2_6_menu_signs_out_with_a_form(app, seed, as_user):
    html = as_user('stud1').get('/student/dashboard').get_data(as_text=True)
    assert 'method="post" action="/logout"' in html
    assert 'href="/logout"' not in html


# ── P2-8: hint and face-availability lookups are rate-limited ───────────────

def test_p2_8_hint_limited_per_account(app, seed):
    with app.app_context():
        from backend.models import User
        User.query.filter_by(username='stud1').first().password_hint = 'my dog'
        db.session.commit()
    client = app.test_client()
    for _ in range(3):
        assert client.post('/password-hint', json={'username': 'stud1'}).get_json()['hint'] == 'my dog'
    resp = client.post('/password-hint', json={'username': 'stud1'})
    assert resp.status_code == 429 and 'hint' not in resp.get_json()


def test_p2_8_hint_limited_per_machine(app, seed):
    client = app.test_client()
    for i in range(20):
        client.post('/password-hint', json={'username': f'25084012{i:04d}'})
    assert client.post('/password-hint', json={'username': 'stud2'}).status_code == 429


def test_p2_8_face_available_limited_per_machine(app, seed):
    client = app.test_client()
    for i in range(60):
        assert client.post('/face/available', json={'username': f'p{i}'}).status_code == 200
    assert client.post('/face/available', json={'username': 'stud1'}).status_code == 429


def test_p2_8_forwarded_for_header_is_not_trusted(app, seed):
    client = app.test_client()
    for i in range(20):
        client.post('/password-hint', json={'username': f'x{i}'},
                    headers={'X-Forwarded-For': f'10.0.0.{i}'})       # pretending to be 20 machines
    assert client.post('/password-hint', json={'username': 'stud2'},
                       headers={'X-Forwarded-For': '10.9.9.9'}).status_code == 429


# ── P2-13: roll numbers are not sign-in names ───────────────────────────────

def test_p2_13_roll_number_does_not_sign_in(app, seed):
    with app.app_context():
        from backend.models import User
        User.query.filter_by(username='stud1').first().roll_id = '12'
        db.session.commit()
    client = app.test_client()
    resp = client.post('/login', data={'username': '12', 'password': 'Passw0rd!'})
    assert resp.status_code == 200          # form re-shown, not signed in
    assert client.post('/login', data={'username': '250840125001', 'password': 'Passw0rd!'}).status_code == 302


# ── P2-5: logging ───────────────────────────────────────────────────────────

def test_p2_5_every_response_carries_a_request_id(app, seed):
    resp = app.test_client().get('/login')
    rid = resp.headers.get('X-Request-ID')
    assert rid and len(rid) == 8


def test_p2_5_log_file_written_with_request_reference(app, seed):
    import logging
    from backend import logging_setup
    with app.test_request_context('/x'):
        from flask import g
        g.request_id = 'feedbeef'
        logging.getLogger('netra.test').warning('hello from the test')
    for h in logging.getLogger().handlers:
        h.flush()
    text = open(logging_setup.log_file_path(app), encoding='utf-8').read()
    assert '[feedbeef] netra.test: hello from the test' in text


def test_p2_5_unhandled_error_shows_reference(app, seed, monkeypatch):
    def _boom():
        raise RuntimeError('boom')
    monkeypatch.setitem(app.view_functions, 'auth.login', _boom)
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    resp = app.test_client().get('/login')
    assert resp.status_code == 500
    ref = resp.headers.get('X-Request-ID')
    assert ref and ref.encode() in resp.data
    assert b'boom' not in resp.data           # no internals shown


def test_p2_5_no_print_calls_left_in_backend():
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parent.parent / 'backend'
    offenders = []
    for path in root.rglob('*.py'):
        if path.name == 'email_utils.py':
            continue        # prints the OTP to the console on purpose when SMTP is off
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if re.match(r'\s*print\(', line):
                offenders.append(f'{path.name}:{n}')
    assert offenders == []


# ── P2-10: evidence retention and restorable exam deletion ──────────────────

@pytest.fixture
def evidence(app, seed, tmp_path, monkeypatch):
    """A flagged moment with an evidence image on stud1's submitted attempt."""
    from backend.services import retention
    root = tmp_path / 'violations'
    (root / 'attempt').mkdir(parents=True)
    monkeypatch.setattr(retention, 'EVIDENCE_ROOT', str(root))
    image = root / 'attempt' / 'flag.jpg'
    image.write_bytes(b'\xff\xd8 fake jpeg')
    with app.app_context():
        v = ExamViolation(student_exam_id=seed['attempt'], violation_type='phone', severity='high',
                          message='Phone in view', evidence_path=str(image))
        db.session.add(v)
        db.session.commit()
        vid = v.id
    return dict(seed, image=image, violation=vid)


def test_p2_10_publishing_keeps_the_evidence(app, evidence, as_user):
    resp = as_user('faca').post(f"/api/faculty/publish-results/{evidence['exam']}")
    assert resp.get_json()['success']
    assert '30 days' in resp.get_json()['message']
    assert evidence['image'].exists()
    with app.app_context():
        assert db.session.get(ExamViolation, evidence['violation']).evidence_path


def test_p2_10_sweep_deletes_evidence_after_retention(app, evidence):
    from datetime import datetime, timedelta
    from backend.models import Exam
    from backend.services import retention
    with app.app_context():
        exam = db.session.get(Exam, evidence['exam'])
        exam.results_published, exam.results_published_at = True, datetime.utcnow() - timedelta(days=29)
        db.session.commit()
        assert retention.purge_expired()['evidence'] == 0
        assert evidence['image'].exists()

        exam.results_published_at = datetime.utcnow() - timedelta(days=31)
        db.session.commit()
        assert retention.purge_expired()['evidence'] == 1
        assert not evidence['image'].exists()
        assert not evidence['image'].parent.exists()          # empty folder tidied
        assert db.session.get(ExamViolation, evidence['violation']).evidence_path is None


def test_p2_10_sweep_never_deletes_outside_the_evidence_folder(app, evidence, tmp_path):
    from datetime import datetime, timedelta
    from backend.models import Exam
    from backend.services import retention
    outside = tmp_path / 'precious.txt'
    outside.write_text('keep me')
    with app.app_context():
        db.session.get(ExamViolation, evidence['violation']).evidence_path = str(outside)
        exam = db.session.get(Exam, evidence['exam'])
        exam.results_published, exam.results_published_at = True, datetime.utcnow() - timedelta(days=40)
        db.session.commit()
        retention.purge_expired()
    assert outside.exists()


def test_p2_10_delete_archives_and_admin_restores(app, evidence, as_user):
    from backend.models import Exam, Question, StudentAnswer
    with app.app_context():
        db.session.add(StudentAnswer(student_exam_id=evidence['attempt'], question_id=evidence['q2'],
                                     selected_answer='B'))
        se = db.session.get(StudentExam, evidence['attempt'])
        se.question_order = f"[{evidence['q2']}, {evidence['q1']}]"
        db.session.commit()

    resp = as_user('faca').post(f"/faculty/exam/{evidence['exam']}/delete", follow_redirects=True)
    assert b'An administrator can restore it' in resp.data
    with app.app_context():
        assert db.session.get(Exam, evidence['exam']) is None

    admin = as_user('admin1')
    page = admin.get('/admin/all-exams').get_data(as_text=True)
    assert 'Recently deleted' in page and 'SQL Basics' in page
    import re
    name = re.search(r'/admin/deleted-exams/(exam-[^/]+\.json)/restore', page).group(1)

    assert as_user('faca').post(f'/admin/deleted-exams/{name}/restore').status_code == 403
    resp = admin.post(f'/admin/deleted-exams/{name}/restore', follow_redirects=True)
    assert b'restored with its results' in resp.data

    with app.app_context():
        exam = Exam.query.filter_by(title='SQL Basics').one()
        qids = {q.question_text: q.id for q in Question.query.filter_by(exam_id=exam.id)}
        att = StudentExam.query.filter_by(exam_id=exam.id).one()
        assert (att.student_id, att.score, att.percentage) == (evidence['s1'], 1, 50)
        assert att.get_question_order() == [qids['Q2'], qids['Q1']]           # remapped ids
        ans = StudentAnswer.query.filter_by(student_exam_id=att.id).one()
        assert (ans.question_id, ans.selected_answer) == (qids['Q2'], 'B')
        v = ExamViolation.query.filter_by(student_exam_id=att.id).one()
        assert v.evidence_path == str(evidence['image'])
    assert 'Recently deleted' not in admin.get('/admin/all-exams').get_data(as_text=True)


def test_p2_10_restore_rejects_bad_names(app, seed, as_user):
    admin = as_user('admin1')
    for name in ('..%2F..%2Fapp.py', 'exam-1-20260101T000000.json', 'x.json'):
        resp = admin.post(f'/admin/deleted-exams/{name}/restore', follow_redirects=True)
        assert resp.status_code in (200, 404)
        assert b'restored with its results' not in resp.data


def test_p2_10_archives_expire_with_their_evidence(app, evidence, as_user):
    from datetime import datetime, timedelta
    from backend.services import retention
    as_user('faca').post(f"/faculty/exam/{evidence['exam']}/delete")
    with app.app_context():
        assert len(retention.list_archives()) == 1
        assert retention.purge_expired()['archives'] == 0
        result = retention.purge_expired(now=datetime.utcnow() + timedelta(days=31))
        assert result == {'evidence': 1, 'archives': 1}
        assert retention.list_archives() == []
    assert not evidence['image'].exists()


def test_p2_10_faculty_copy_no_longer_says_publishing_deletes(app, seed, as_user):
    client = as_user('faca')
    for url in (f"/faculty/exam/{seed['exam']}", f"/faculty/exam/{seed['exam']}/analytics"):
        html = client.get(url).get_data(as_text=True)
        assert 'delete evidence' not in html.lower()
        assert 'Publishing deletes' not in html


# ── P2-14: the listening socket is safe for an exam hall ────────────────────

def test_p2_14_listener_refuses_a_second_server_on_the_same_port():
    import socket as pysocket
    import eventlet
    import app as app_module
    original = eventlet.listen
    try:
        assert app_module.configure_listener({'LISTEN_BACKLOG': '512'}) == 512
        first = eventlet.listen(('127.0.0.1', 0))
        port = first.getsockname()[1]
        if hasattr(pysocket, 'SO_REUSEPORT'):
            assert first.getsockopt(pysocket.SOL_SOCKET, pysocket.SO_REUSEPORT) == 0
        with pytest.raises(OSError):
            eventlet.listen(('127.0.0.1', port))      # a second copy fails loudly
        first.close()
        app_module.configure_listener({})              # idempotent: wraps the original once
        assert eventlet.listen.__wrapped__ is original
    finally:
        eventlet.listen = original


def test_p2_14_load_test_script_is_importable_and_documented():
    import importlib.util, pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'load_test.py'
    spec = importlib.util.spec_from_file_location('load_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._pct([1, 2, 3, 4], 50) == 2 and mod._pct([], 95) is None
    assert 'seed' in mod.__doc__ and 'cleanup' in mod.__doc__


def test_p2_14_openvino_telemetry_is_disabled():
    """OpenVINO's telemetry forks a child that steals the server's connections
    for a few seconds after start-up (and reports usage to Google)."""
    import sys
    import backend  # noqa: F401  (installs the block)
    assert sys.modules.get('openvino_telemetry') is None
    from openvino.tools.ovc import telemetry_utils
    assert telemetry_utils.tm.__name__.endswith('telemetry_stub')
