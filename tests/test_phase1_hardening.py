"""Regression tests for the Phase 1 audit (docs/audit/PHASE1_AUDIT.md).

Each test names the audit finding it guards.
"""

import re
from datetime import datetime, timedelta

import pytest

from backend.database import db
from backend.models import Exam, StudentExam, User
from tests.conftest import PASSWORD, login


def _set(app, model, pk, **fields):
    with app.app_context():
        row = db.session.get(model, pk)
        for k, v in fields.items():
            setattr(row, k, v)
        db.session.commit()


# ── P0-1: students must never pass the faculty exam-access check ────────────

FACULTY_EXAM_ACTIONS = [
    ('post', '/faculty/exam/{exam}/generate_key', {}),
    ('post', '/faculty/exam/{exam}/clear_key', {}),
    ('post', '/faculty/exam/{exam}/extend_time', {'minutes': '60'}),
    ('post', '/faculty/exam/{exam}/update_access', {'is_active': 'false'}),
    ('post', '/faculty/exam/{exam}/force_end', {}),
    ('post', '/faculty/exam/{exam}/force_end_student/{attempt}', {}),
    ('post', '/faculty/exam/{exam}/delete', {}),
    ('get', '/faculty/exam/{exam}/analytics', None),
    ('get', '/faculty/exam/{exam}/upload', None),
]


@pytest.mark.parametrize('method,path,data', FACULTY_EXAM_ACTIONS)
def test_p0_1_same_department_student_is_not_staff(app, seed, method, path, data):
    _set(app, User, seed['s1'], department='DBDA')       # same as faculty A
    client = app.test_client()
    login(client, 'stud1')
    url = path.format(exam=seed['exam'], attempt=seed['attempt'])
    resp = client.post(url, data=data) if method == 'post' else client.get(url)
    assert resp.status_code in (302, 403)
    if resp.status_code == 302:
        assert '/faculty/' not in resp.headers['Location']

    with app.app_context():
        exam = db.session.get(Exam, seed['exam'])
        assert exam is not None
        assert exam.access_key is None
        assert exam.duration_minutes == 30
        assert exam.is_active is True
        assert exam.force_ended is not True


def test_p0_1_helpers_reject_students(app, seed):
    from backend.routes.faculty import _faculty_can_access_exam, _get_faculty_exams
    _set(app, User, seed['s1'], department='DBDA')
    with app.test_request_context():
        from flask_login import login_user
        login_user(db.session.get(User, seed['s1']))
        assert _faculty_can_access_exam(db.session.get(Exam, seed['exam'])) is False
        assert _get_faculty_exams() == []


def test_p0_1_every_faculty_route_is_staff_only(app):
    """Static guard: no faculty endpoint may skip staff_required."""
    import backend.routes.faculty as faculty
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith('faculty.'):
            view = getattr(faculty, rule.endpoint.split('.', 1)[1])
            assert getattr(view, '__wrapped__', None) is not None, rule.endpoint


# ── P0-2: the Werkzeug debugger is opt-in and loopback-only ─────────────────

def test_p0_2_default_run_is_not_debug():
    from app import resolve_run_options
    opts = resolve_run_options(['app.py'], {})
    assert opts['debug'] is False
    assert opts['host'] == '0.0.0.0'
    assert opts['use_reloader'] is False


def test_p0_2_debug_binds_loopback_only():
    from app import resolve_run_options
    opts = resolve_run_options(['app.py'], {'FLASK_DEBUG': 'True'})
    assert opts['debug'] is True
    assert opts['host'] == '127.0.0.1'


def test_p0_2_ssl_never_debug():
    from app import resolve_run_options
    opts = resolve_run_options(['app.py', '--ssl'], {'FLASK_DEBUG': 'True'})
    assert opts['debug'] is False and opts['use_ssl'] is True


# ── P0-3: no persistent login on shared machines ────────────────────────────

def test_p0_3_login_sets_no_remember_cookie(app, seed):
    client = app.test_client()
    resp = login(client, 'stud1')
    cookies = resp.headers.getlist('Set-Cookie')
    assert not any(c.startswith('remember_token=') for c in cookies)
    session_cookie = next(c for c in cookies if c.startswith('session='))
    assert 'HttpOnly' in session_cookie
    assert 'SameSite=Lax' in session_cookie
    assert 'Expires=' not in session_cookie          # dies with the browser


def test_p0_3_remember_cookie_hardened(app):
    assert app.config['REMEMBER_COOKIE_DURATION'] <= timedelta(hours=1)
    assert app.config['REMEMBER_COOKIE_SAMESITE'] == 'Lax'
    assert app.config['REMEMBER_COOKIE_HTTPONLY'] is True


def test_p0_3_idle_session_is_signed_out(app, seed):
    client = app.test_client()
    login(client, 'stud1')
    assert client.get('/student/dashboard').status_code == 200
    with client.session_transaction() as sess:
        sess['_last_seen'] = sess['_last_seen'] - 2 * 3600
    resp = client.get('/student/dashboard')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']
    assert client.get('/student/dashboard').status_code == 302


def test_p0_3_logout_clears_session(app, seed):
    client = app.test_client()
    login(client, 'stud1')
    client.get('/logout')
    assert client.get('/student/dashboard').status_code == 302


# ── P0-6 / P1-3: pre-created attempts cannot be opened without starting ─────

@pytest.fixture
def imported_attempt(app, seed):
    with app.app_context():
        se = StudentExam(student_id=seed['s2'], exam_id=seed['exam'],
                         started_at=datetime.utcnow() - timedelta(days=1))
        db.session.add(se)
        db.session.commit()
        return se.id


def test_p0_6_imported_attempt_not_shown_as_in_progress(app, seed, imported_attempt):
    client = app.test_client()
    login(client, 'stud2')
    page = client.get('/student/dashboard').data
    assert b'Exam in Progress' not in page
    assert b'class="tag attempted"' not in page


def test_p0_6_take_redirects_until_started(app, seed, imported_attempt):
    client = app.test_client()
    login(client, 'stud2')
    resp = client.get(f"/exam/{seed['exam']}/take")
    assert resp.status_code == 302

    client.post(f"/start-exam/{seed['exam']}")
    page = client.get(f"/exam/{seed['exam']}/take")
    total = int(re.search(rb'totalSec:\s*(\d+)', page.data).group(1))
    assert total > 29 * 60                              # full 30 minutes, not 0


def test_p1_3_closed_exam_blocks_never_started_attempts(app, seed, imported_attempt):
    client = app.test_client()
    login(client, 'stud2')
    fac = app.test_client()
    login(fac, 'faca')
    fac.post(f"/faculty/exam/{seed['exam']}/force_end")

    client.post(f"/start-exam/{seed['exam']}")
    assert client.get(f"/exam/{seed['exam']}/take").status_code == 302
    with app.app_context():
        assert db.session.get(StudentExam, imported_attempt).status != 'started'


# ── P1-12: no internals in error responses ──────────────────────────────────

def test_p1_12_unhandled_error_is_generic(app, seed, monkeypatch):
    import backend.routes.student as student_routes

    def boom(*a, **k):
        raise RuntimeError('secret internal detail SELECT * FROM users')

    monkeypatch.setattr(student_routes, 'render_template', boom)
    app.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        client = app.test_client()
        login(client, 'stud1')
        resp = client.get('/student/dashboard')
    finally:
        app.config['PROPAGATE_EXCEPTIONS'] = None
    assert resp.status_code == 500
    assert b'secret internal detail' not in resp.data
    assert b'SELECT' not in resp.data


# ── P0-4: no shared default passwords ───────────────────────────────────────

def _import_csv(client, body: bytes):
    import io
    return client.post('/faculty/import_students',
                       data={'file': (io.BytesIO(body), 'students.csv')},
                       content_type='multipart/form-data')


def test_p0_4_import_generates_distinct_random_passwords(app, seed):
    fac = app.test_client()
    login(fac, 'faca')
    _import_csv(fac, b"full_name,email,prn_number\nAsha,asha@example.com,250840125101\n"
                     b"Ravi,ravi@example.com,250840125102\n")

    # Neither the old default nor each other's password works
    for who in ('asha@example.com', 'ravi@example.com'):
        assert login(app.test_client(), who, 'Student@123').status_code == 200

    page = fac.get('/faculty/students').data.decode()
    token = re.search(r'/faculty/credentials/([\w-]+)', page).group(1)
    sheet = fac.get(f'/faculty/credentials/{token}')
    assert sheet.status_code == 200 and sheet.mimetype == 'text/csv'
    rows = [r.split(',') for r in sheet.data.decode().strip().splitlines()[1:]]
    passwords = {r[3]: r[4] for r in rows}
    assert len(set(passwords.values())) == 2
    assert all(len(p) >= 10 for p in passwords.values())
    assert login(app.test_client(), 'asha@example.com', passwords['asha@example.com']).status_code == 302

    # One-time: the second download is refused and the file is gone
    again = fac.get(f'/faculty/credentials/{token}')
    assert again.status_code == 302
    with app.app_context():
        assert db.session.query(User).filter_by(email='asha@example.com').one().created_by == seed['fac_a']


def test_p0_4_credentials_bound_to_session(app, seed):
    fac = app.test_client()
    login(fac, 'faca')
    _import_csv(fac, b"full_name,email\nAsha,asha@example.com\n")
    token = re.search(r'/faculty/credentials/([\w-]+)', fac.get('/faculty/students').data.decode()).group(1)
    other = app.test_client()
    login(other, 'facb')
    assert other.get(f'/faculty/credentials/{token}').status_code == 302


def test_p0_4_file_password_is_respected(app, seed):
    fac = app.test_client()
    login(fac, 'faca')
    _import_csv(fac, b"full_name,email,password\nAsha,asha@example.com,Chosen#123\n")
    assert login(app.test_client(), 'asha@example.com', 'Chosen#123').status_code == 302
    assert b'/faculty/credentials/' not in fac.get('/faculty/students').data


def test_p0_4_reset_returns_random_password_once(app, seed):
    fac = app.test_client()
    login(fac, 'faca')
    first = fac.post(f"/faculty/change-student-password/{seed['s1']}").get_json()
    second = fac.post(f"/faculty/change-student-password/{seed['s1']}").get_json()
    assert first['temporary_password'] != second['temporary_password']
    assert login(app.test_client(), 'stud1', '123456789').status_code == 200
    assert login(app.test_client(), 'stud1', first['temporary_password']).status_code == 200
    assert login(app.test_client(), 'stud1', second['temporary_password']).status_code == 302


# ── P0-7: deletes leave no orphans and never crash ──────────────────────────

def _orphans(app):
    from sqlalchemy import text
    with app.app_context():
        return db.session.execute(text(
            'select count(*) from student_exam se left join users u on u.id = se.student_id '
            'where u.id is null')).scalar()


def test_p0_7_delete_student_with_attempt(app, seed):
    fac = app.test_client()
    login(fac, 'faca')
    resp = fac.post(f"/faculty/delete_student/{seed['s1']}")
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert _orphans(app) == 0
    with app.app_context():
        assert db.session.get(User, seed['s1']) is None


def test_p0_7_bulk_delete_keeps_leaderboard_working(app, seed):
    _set(app, Exam, seed['exam'], results_published=True, show_leaderboard=True)
    fac = app.test_client()
    login(fac, 'faca')
    resp = fac.post('/faculty/delete_students', json={'ids': [seed['s1'], seed['admin']]})
    assert resp.get_json()['deleted'] == 1             # the admin id is refused
    assert _orphans(app) == 0
    admin = app.test_client()
    login(admin, 'admin1')
    assert admin.get('/global_leaderboard').status_code == 200
    with app.app_context():
        assert db.session.get(User, seed['admin']) is not None


def test_p0_7_delete_faculty_with_exams_is_refused(app, seed):
    admin = app.test_client()
    login(admin, 'admin1')
    resp = admin.post(f"/admin/delete-faculty/{seed['fac_a']}")
    assert resp.status_code == 409
    assert 'owns' in resp.get_json()['error']
    assert admin.post(f"/admin/delete-faculty/{seed['fac_b']}").status_code == 200


def test_p0_7_foreign_keys_enforced(app):
    from sqlalchemy import text
    with app.app_context():
        assert db.session.execute(text('PRAGMA foreign_keys')).scalar() == 1


# ── P1-9: faculty only manage students in their scope ───────────────────────

@pytest.fixture
def other_dept_student(app, seed):
    """A student imported by faculty B (DAC) who never sat a DBDA exam."""
    from werkzeug.security import generate_password_hash
    with app.app_context():
        u = User(username='dacstud', email='dac@example.com', role='student',
                 password_hash=generate_password_hash(PASSWORD), is_verified=True,
                 password_changed=True, created_by=seed['fac_b'], full_name='Dac Student')
        db.session.add(u)
        db.session.commit()
        return u.id


@pytest.mark.parametrize('method,path', [
    ('get', '/faculty/student/{sid}/profile'),
    ('get', '/faculty/student/{sid}/edit'),
    ('post', '/faculty/change-student-password/{sid}'),
    ('post', '/faculty/student/{sid}/reset-face'),
    ('get', '/faculty/student/{sid}/face-history'),
    ('post', '/faculty/delete_student/{sid}'),
])
def test_p1_9_other_department_student_is_out_of_scope(app, seed, other_dept_student, method, path):
    fac = app.test_client()
    login(fac, 'faca')
    url = path.format(sid=other_dept_student)
    resp = fac.post(url, json={'reason': 'x'}) if method == 'post' else fac.get(url)
    assert resp.status_code == 404
    with app.app_context():
        assert db.session.get(User, other_dept_student) is not None


def test_p1_9_lists_and_exports_are_scoped(app, seed, other_dept_student):
    fac = app.test_client()
    login(fac, 'faca')
    assert b'dac@example.com' not in fac.get('/faculty/students').data
    assert b'dac@example.com' not in fac.get('/faculty/export_students').data
    assert b'stud1@example.com' in fac.get('/faculty/export_students').data   # legacy, unowned

    owner = app.test_client()
    login(owner, 'facb')
    assert b'dac@example.com' in owner.get('/faculty/students').data
    admin = app.test_client()
    login(admin, 'admin1')
    assert admin.get(f'/faculty/student/{other_dept_student}/profile').status_code == 200


# ── P1-10: one attempt per student per exam, one answer per question ───────

def test_p1_10_unique_attempt_enforced(app, seed):
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        db.session.add(StudentExam(student_id=seed['s1'], exam_id=seed['exam']))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_p1_10_unique_answer_enforced(app, seed):
    from sqlalchemy.exc import IntegrityError
    from backend.models import StudentAnswer
    with app.app_context():
        db.session.add_all([
            StudentAnswer(student_exam_id=seed['attempt'], question_id=seed['q1'], selected_answer='A'),
            StudentAnswer(student_exam_id=seed['attempt'], question_id=seed['q1'], selected_answer='B'),
        ])
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_p1_10_indexes_added_to_existing_database(tmp_path):
    """An old database without the constraints gains them at start-up."""
    import sqlite3
    from flask import Flask
    from backend.database import db as _db
    from app import ensure_schema_indexes

    path = tmp_path / 'old.db'
    con = sqlite3.connect(path)
    con.executescript(
        'CREATE TABLE student_exam (id INTEGER PRIMARY KEY, student_id INTEGER, exam_id INTEGER);'
        'CREATE TABLE student_answer (id INTEGER PRIMARY KEY, student_exam_id INTEGER, question_id INTEGER);')
    con.close()

    mini = Flask('mini')
    mini.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{path}'
    _db.init_app(mini)
    with mini.app_context():
        ensure_schema_indexes()
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute("select name from sqlite_master where type='index'")}
    assert {'uq_student_exam_student_exam', 'uq_student_answer_attempt_question'} <= names


# ── P1-2: attempts whose time ran out are closed and scored ─────────────────

@pytest.fixture
def started_attempt(app, seed):
    """Student 2 started the 30-minute exam and saved one correct answer."""
    client = app.test_client()
    login(client, 'stud2')
    client.post(f"/start-exam/{seed['exam']}")
    with app.app_context():
        se = StudentExam.query.filter_by(student_id=seed['s2'], exam_id=seed['exam']).one()
        sid = se.id
    client.post('/api/save-answer', json={'student_exam_id': sid,
                                          'answers': {str(seed['q1']): 'A'}})
    return client, sid


def _age(app, sid, minutes):
    _set(app, StudentExam, sid, started_at=datetime.utcnow() - timedelta(minutes=minutes))


@pytest.mark.parametrize('trigger', ['dashboard', 'faculty_view', 'status', 'sweep'])
def test_p1_2_overdue_attempt_is_finalised(app, seed, started_attempt, trigger):
    client, sid = started_attempt
    _age(app, sid, 45)                                   # 30 min exam + 2 min grace passed
    if trigger == 'dashboard':
        client.get('/student/dashboard')
    elif trigger == 'faculty_view':
        fac = app.test_client()
        login(fac, 'faca')
        fac.get(f"/faculty/exam/{seed['exam']}")
    elif trigger == 'status':
        assert client.get(f'/api/check-exam-status/{sid}').get_json()['force_ended'] is True
    else:
        from backend.services.exam_service import finalize_overdue_attempts
        with app.app_context():
            assert finalize_overdue_attempts() == 1

    with app.app_context():
        se = db.session.get(StudentExam, sid)
        assert se.completed and se.status == 'submitted'
        assert se.score == 1 and se.total_points == 2      # the saved answer counted
        deadline = se.started_at + timedelta(minutes=30)
        assert abs((se.submitted_at - deadline).total_seconds()) < 1


def test_p1_2_attempt_within_grace_is_left_alone(app, seed, started_attempt):
    client, sid = started_attempt
    _age(app, sid, 31)                                   # past the clock, inside grace
    client.get('/student/dashboard')
    with app.app_context():
        assert db.session.get(StudentExam, sid).status == 'started'


def test_p1_2_overdue_attempt_cannot_be_resumed(app, seed, started_attempt):
    client, sid = started_attempt
    _age(app, sid, 45)
    client.post(f"/start-exam/{seed['exam']}")
    assert client.get(f"/exam/{seed['exam']}/take").status_code == 302
    with app.app_context():
        assert db.session.get(StudentExam, sid).completed


# ── P1-4: the countdown follows the server ──────────────────────────────────

def test_p1_4_status_reports_remaining_time_and_extension(app, seed, started_attempt):
    client, sid = started_attempt
    before = client.get(f'/api/check-exam-status/{sid}').get_json()['remaining_seconds']
    assert 29 * 60 < before <= 30 * 60

    fac = app.test_client()
    login(fac, 'faca')
    fac.post(f"/faculty/exam/{seed['exam']}/extend_time", data={'minutes': '10'})
    after = client.get(f'/api/check-exam-status/{sid}').get_json()['remaining_seconds']
    assert after - before >= 10 * 60 - 5


def test_p1_4_extension_is_pushed_to_open_pages(app, seed, started_attempt):
    from backend.socket_io import socketio
    client, sid = started_attempt
    sock = socketio.test_client(app, flask_test_client=client)
    sock.emit('join_exam', {'exam_id': seed['exam'], 'student_exam_id': sid})
    sock.get_received()
    fac = app.test_client()
    login(fac, 'faca')
    fac.post(f"/faculty/exam/{seed['exam']}/extend_time", data={'minutes': '5'})
    events = [m for m in sock.get_received() if m['name'] == 'time_extended']
    assert events and events[0]['args'][0]['minutes'] == 5
    sock.disconnect()


# ── P1-5: answers survive a failed autosave ─────────────────────────────────

def _answers(app, sid):
    from backend.models import StudentAnswer
    with app.app_context():
        return {a.question_id: a.selected_answer
                for a in StudentAnswer.query.filter_by(student_exam_id=sid)}


def test_p1_5_submit_carries_final_answers(app, seed, started_attempt):
    import json
    client, sid = started_attempt
    final = {str(seed['q1']): 'A', str(seed['q2']): 'b', '999999': 'C'}
    client.post(f'/submit_exam/{sid}', data={'answers': json.dumps(final)})
    saved = _answers(app, sid)
    assert saved[seed['q2']] == 'B'                       # stored, normalised
    assert 999999 not in saved                            # foreign question ignored
    with app.app_context():
        assert db.session.get(StudentExam, sid).score == 2


def test_p1_5_submit_payload_ignored_after_time_up(app, seed, started_attempt):
    import json
    client, sid = started_attempt
    _age(app, sid, 40)
    client.post(f'/submit_exam/{sid}', data={'answers': json.dumps({str(seed['q2']): 'B'})})
    assert seed['q2'] not in _answers(app, sid) or _answers(app, sid)[seed['q2']] in (None, '0')


def test_p1_5_cleared_answer_is_not_marked(app, seed, started_attempt):
    client, sid = started_attempt
    client.post('/api/save-answer', json={'student_exam_id': sid,
                                          'answers': {str(seed['q1']): None}})
    assert _answers(app, sid)[seed['q1']] is None
    client.post(f'/submit_exam/{sid}')
    with app.app_context():
        assert db.session.get(StudentExam, sid).score == 0


def test_p1_5_exam_page_journals_answers_locally(app, seed, started_attempt):
    client, _sid = started_attempt
    html = client.get(f"/exam/{seed['exam']}/take").data.decode()
    assert 'localStorage.setItem(JOURNAL_KEY' in html
    assert "ans.name = 'answers'" in html                  # submit carries answers
    assert 'remaining_seconds' in html                     # countdown resync
