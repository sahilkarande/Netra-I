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
