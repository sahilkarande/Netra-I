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
