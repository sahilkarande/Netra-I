"""Authentication and authorization fixes."""

from datetime import datetime, timedelta

import pytest

from backend.database import db
from backend.models import Exam, ExamViolation, StudentExam, User
from tests.conftest import PASSWORD, login


# ── Registration and OTP ────────────────────────────────────────────────────

@pytest.fixture
def sent_otps(monkeypatch):
    sent = []

    def fake_send(email, otp, username, role='student'):
        sent.append({'email': email, 'otp': otp, 'username': username, 'role': role})
        return True

    monkeypatch.setattr('backend.routes.auth.send_otp_email', fake_send)
    return sent


def _register(client, **overrides):
    form = {'username': 'newbie', 'email': 'NewBie@Example.com',
            'password': 'secret123', 'full_name': 'New Bie'}
    form.update(overrides)
    return client.post('/register', data=form)


def test_register_cannot_choose_admin_role(app, sent_otps):
    client = app.test_client()
    resp = _register(client, role='admin')
    assert resp.status_code == 302
    with app.app_context():
        user = User.query.filter_by(username='newbie').one()
        assert user.role == 'student'
        assert user.email == 'newbie@example.com'
        assert user.prn_number is None              # '' would break the unique index
    assert sent_otps[0]['username'] == 'newbie'     # was a TypeError before


def test_otp_verifies_and_expires(app, sent_otps):
    client = app.test_client()
    _register(client)
    otp = sent_otps[0]['otp']

    with app.app_context():
        user = User.query.filter_by(username='newbie').one()
        user.otp_created_at = datetime.utcnow() - timedelta(minutes=11)
        db.session.commit()
    client.post('/verify-otp', data={'otp': otp})
    with app.app_context():
        assert User.query.filter_by(username='newbie').one().is_verified is False

    assert client.post('/resend-otp').get_json()['success'] is True
    client.post('/verify-otp', data={'otp': sent_otps[-1]['otp']})
    with app.app_context():
        assert User.query.filter_by(username='newbie').one().is_verified is True


def test_otp_bruteforce_locks_out(app, sent_otps):
    client = app.test_client()
    _register(client)
    real = sent_otps[0]['otp']
    wrong = '000000' if real != '000000' else '111111'
    for _ in range(5):
        client.post('/verify-otp', data={'otp': wrong})
    client.post('/verify-otp', data={'otp': real})   # correct, but locked out
    with app.app_context():
        assert User.query.filter_by(username='newbie').one().is_verified is False


# ── Login ───────────────────────────────────────────────────────────────────

def test_login_lockout_after_failures(app, seed):
    client = app.test_client()
    for _ in range(5):
        login(client, 'stud1', 'wrong-password')
    resp = login(client, 'stud1', PASSWORD)
    assert resp.status_code == 429
    assert b'Too many failed attempts' in resp.data


def test_secret_key_is_not_the_old_default(app):
    assert app.config['SECRET_KEY'] != 'dev-secret-key-change-in-production'


def test_load_secret_key_persists_random_key(tmp_path, monkeypatch):
    from app import load_secret_key
    monkeypatch.delenv('SECRET_KEY', raising=False)
    first = load_secret_key(str(tmp_path))
    assert len(first) >= 32
    assert load_secret_key(str(tmp_path)) == first


# ── Student management ──────────────────────────────────────────────────────

def test_student_cannot_edit_students(app, seed, as_user):
    client = as_user('stud1')
    client.post(f"/faculty/student/{seed['s2']}/edit",
                data={'full_name': 'Hacked', 'email': 'evil@example.com'})
    with app.app_context():
        assert User.query.get(seed['s2']).email == 'stud2@example.com'


def test_faculty_cannot_edit_admin_account(app, seed, as_user):
    client = as_user('faca')
    client.post(f"/faculty/student/{seed['admin']}/edit",
                data={'full_name': 'x', 'email': 'takeover@example.com'})
    with app.app_context():
        assert User.query.get(seed['admin']).email == 'admin1@example.com'


def test_faculty_edit_rejects_duplicate_email(app, seed, as_user):
    as_user('faca').post(f"/faculty/student/{seed['s2']}/edit",
                         data={'full_name': 'S', 'email': 'stud1@example.com'})
    with app.app_context():
        assert User.query.get(seed['s2']).email == 'stud2@example.com'


def test_faculty_cannot_reset_admin_password(app, seed, as_user):
    resp = as_user('faca').post(f"/faculty/change-student-password/{seed['admin']}")
    assert resp.status_code == 403
    assert login(app.test_client(), 'admin1', '123456789').status_code == 200   # no redirect = failed


def test_faculty_can_reset_student_password(app, seed, as_user):
    resp = as_user('faca').post(f"/faculty/change-student-password/{seed['s1']}")
    assert resp.get_json()['success'] is True
    with app.app_context():
        assert User.query.get(seed['s1']).password_changed is False


# ── SQL console ─────────────────────────────────────────────────────────────

def test_sql_console_is_admin_only(seed, as_user):
    fac = as_user('faca')
    assert fac.get('/admin/sql_console').status_code == 403
    resp = fac.post('/admin/sql_console/run', json={'sql': 'SELECT password_hash FROM users'})
    assert resp.status_code == 403


def test_sql_console_admin_select(seed, as_user):
    resp = as_user('admin1').post('/admin/sql_console/run',
                                  json={'sql': 'SELECT id, username, created_at FROM users ORDER BY id'})
    data = resp.get_json()
    assert data['success'] is True
    assert data['columns'] == ['id', 'username', 'created_at']
    assert data['rowcount'] == 5


# ── Results and review ──────────────────────────────────────────────────────

def test_other_department_faculty_cannot_see_result(seed, as_user):
    resp = as_user('facb').get(f"/student/exam/{seed['attempt']}/download-pdf")
    assert resp.status_code == 403


def test_review_exam_invalidate_and_accept(app, seed, as_user):
    client = as_user('faca')
    url = f"/api/faculty/review-exam/{seed['attempt']}"

    assert client.post(url, json={'action': 'invalidate', 'note': ''}).status_code == 400
    assert client.post(url, json={'action': 'invalidate', 'note': 'Phone in frame'}).get_json()['success']
    with app.app_context():
        se = StudentExam.query.get(seed['attempt'])
        assert se.is_invalidated and se.faculty_review_note == 'Phone in frame'
        se.exam.results_published = True
        db.session.commit()

    # Invalidated attempts stay hidden from the student even once published
    assert as_user('stud1').get(f"/student/exam/{seed['attempt']}/result").status_code == 302

    assert client.post(url, json={'action': 'accept', 'note': ''}).get_json()['success']
    assert as_user('stud1').get(f"/student/exam/{seed['attempt']}/result").status_code == 200


def test_review_exam_scoped_to_exam_access(seed, as_user):
    resp = as_user('facb').post(f"/api/faculty/review-exam/{seed['attempt']}",
                                json={'action': 'invalidate', 'note': 'x'})
    assert resp.status_code == 404
    assert as_user('stud1').post(f"/api/faculty/review-exam/{seed['attempt']}",
                                 json={'action': 'accept'}).status_code == 404


# ── Evidence images ─────────────────────────────────────────────────────────

@pytest.fixture
def evidence(app, seed):
    import os
    from backend.routes.faculty import _EVIDENCE_ROOT, _PROJECT_ROOT
    folder = os.path.join(_EVIDENCE_ROOT, '_pytest')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'frame.jpg')
    with open(path, 'wb') as f:
        f.write(b'\xff\xd8\xff\xe0fakejpeg')
    with app.app_context():
        v = ExamViolation(student_exam_id=seed['attempt'], violation_type='no_face',
                          severity='medium', message='No face',
                          evidence_path=os.path.relpath(path, _PROJECT_ROOT))
        db.session.add(v)
        db.session.commit()
        vid = v.id
    yield {'id': vid, 'static_url': '/static/violations/_pytest/frame.jpg', 'path': path}
    if os.path.exists(path):
        os.remove(path)
    if os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)


def test_static_evidence_blocked_for_students(seed, evidence, as_user, app):
    assert as_user('stud1').get(evidence['static_url']).status_code == 403
    assert app.test_client().get(evidence['static_url']).status_code == 403


def test_proctor_status_and_evidence_route(seed, evidence, as_user):
    client = as_user('faca')
    data = client.get(f"/api/proctor/status/{seed['attempt']}").get_json()
    assert data['success'] and len(data['recent_violations']) == 1
    url = data['recent_violations'][0]['evidence_url']
    assert url and url.startswith('/faculty/violation/')
    img = client.get(url)
    assert img.status_code == 200 and img.data.startswith(b'\xff\xd8')

    other = as_user('facb')
    assert other.get(url).status_code == 404
    assert other.get(f"/api/proctor/status/{seed['attempt']}").status_code == 404


def test_evidence_path_outside_folder_is_refused(app, seed, as_user):
    with app.app_context():
        v = ExamViolation(student_exam_id=seed['attempt'], violation_type='x',
                          severity='low', evidence_path='app.py')
        db.session.add(v)
        db.session.commit()
        vid = v.id
    assert as_user('faca').get(f'/faculty/violation/{vid}/evidence').status_code == 404


def test_delete_evidence(app, seed, evidence, as_user):
    import os
    resp = as_user('faca').delete(f"/api/faculty/violation/{evidence['id']}/evidence")
    assert resp.get_json()['success'] is True
    assert not os.path.exists(evidence['path'])
    with app.app_context():
        assert ExamViolation.query.get(evidence['id']).evidence_path is None
