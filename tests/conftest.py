"""Shared fixtures: an app on a throwaway SQLite file and seeded users."""

import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

# Must be set before app.py is imported — create_app() reads them.
_DB_DIR = tempfile.mkdtemp(prefix='netra_test_')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_DB_DIR, 'test.db')
os.environ['SECRET_KEY'] = 'test-secret-key'

from app import create_app  # noqa: E402
from backend.database import db  # noqa: E402
from backend.models import User, Exam, Question, StudentExam  # noqa: E402
from backend.services import face_service  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

PASSWORD = 'Passw0rd!'


@pytest.fixture(scope='session')
def app():
    application = create_app()
    # CSRF is exercised by its own tests (test_phase1_hardening.py); the rest
    # of the suite posts directly.
    application.config.update(TESTING=True, CSRF_ENABLED=False)
    # Anything the app writes under instance/ (e.g. credential sheets) goes
    # to a throwaway folder, never the real installation's.
    application.instance_path = os.path.join(_DB_DIR, 'instance')
    return application


@pytest.fixture(autouse=True)
def clean_db(app):
    with app.app_context():
        db.drop_all()
        db.create_all()
    face_service._failed_attempts.clear()
    yield
    with app.app_context():
        db.session.remove()


def _user(username, role, **extra):
    fields = dict(
        username=username,
        email=f'{username}@example.com',
        password_hash=generate_password_hash(PASSWORD),
        role=role,
        full_name=username.title(),
        is_verified=True,
        password_changed=True,
    )
    fields.update(extra)
    u = User(**fields)
    db.session.add(u)
    return u


@pytest.fixture
def seed(app):
    """Two faculty in different departments, an admin, two students, one exam
    owned by faculty A with two questions and a submitted attempt by student 1."""
    with app.app_context():
        admin = _user('admin1', 'admin')
        fac_a = _user('faca', 'faculty', department='DBDA')
        fac_b = _user('facb', 'faculty', department='DAC')
        s1 = _user('stud1', 'student', prn_number='250840125001')
        s2 = _user('stud2', 'student', prn_number='250840125002')
        db.session.commit()

        exam = Exam(title='SQL Basics', duration_minutes=30, creator_id=fac_a.id,
                    is_active=True, randomize_questions=False)
        db.session.add(exam)
        db.session.commit()

        q1 = Question(exam_id=exam.id, question_text='Q1', option_a='a', option_b='b',
                      option_c='c', option_d='d', correct_answer='A', points=1)
        q2 = Question(exam_id=exam.id, question_text='Q2', option_a='a', option_b='b',
                      option_c='c', option_d='d', correct_answer='B', points=1)
        db.session.add_all([q1, q2])
        db.session.commit()

        attempt = StudentExam(student_id=s1.id, exam_id=exam.id, status='submitted',
                              completed=True, score=1, total_points=2, percentage=50,
                              passed=True)
        db.session.add(attempt)
        db.session.commit()

        return {
            'admin': admin.id, 'fac_a': fac_a.id, 'fac_b': fac_b.id,
            's1': s1.id, 's2': s2.id, 'exam': exam.id,
            'q1': q1.id, 'q2': q2.id, 'attempt': attempt.id,
        }


def login(client, username, password=PASSWORD):
    return client.post('/login', data={'username': username, 'password': password})


@pytest.fixture
def as_user(app):
    """as_user('stud1') -> a logged-in test client."""
    def make(username):
        client = app.test_client()
        resp = login(client, username)
        assert resp.status_code == 302, f'login failed for {username}'
        return client
    return make
