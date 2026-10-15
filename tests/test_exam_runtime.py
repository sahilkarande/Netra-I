"""Taking an exam: start, access key, answers, submit, force-end, sockets."""

from datetime import datetime, timedelta

import pytest

from backend.database import db
from backend.models import Exam, StudentAnswer, StudentExam
from backend.socket_io import socketio


@pytest.fixture
def open_exam(app, seed):
    """Student 2 has not started the seeded exam yet."""
    return seed


def _attempt(app, student_id, exam_id):
    with app.app_context():
        return StudentExam.query.filter_by(student_id=student_id, exam_id=exam_id).first()


def _start(client, exam_id):
    return client.post(f'/start-exam/{exam_id}')


# ── Starting ────────────────────────────────────────────────────────────────

def test_start_exam_requires_post(app, open_exam, as_user):
    client = as_user('stud2')
    resp = client.get(f"/start-exam/{open_exam['exam']}")
    assert resp.status_code == 302
    assert _attempt(app, open_exam['s2'], open_exam['exam']) is None   # clock not started


def test_start_and_take_exam(app, open_exam, as_user):
    client = as_user('stud2')
    assert _start(client, open_exam['exam']).status_code == 302
    page = client.get(f"/exam/{open_exam['exam']}/take")
    assert page.status_code == 200
    assert b'Q1' in page.data


def test_cannot_start_inactive_or_closed_exam(app, open_exam, as_user):
    with app.app_context():
        exam = Exam.query.get(open_exam['exam'])
        exam.end_time = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
    _start(as_user('stud2'), open_exam['exam'])
    assert _attempt(app, open_exam['s2'], open_exam['exam']) is None


def test_imported_attempt_clock_starts_on_start(app, open_exam, as_user):
    with app.app_context():
        se = StudentExam(student_id=open_exam['s2'], exam_id=open_exam['exam'],
                         started_at=datetime.utcnow() - timedelta(days=2))
        db.session.add(se)
        db.session.commit()
    _start(as_user('stud2'), open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])
    assert se.status == 'started'
    assert datetime.utcnow() - se.started_at < timedelta(minutes=1)


# ── Access key ──────────────────────────────────────────────────────────────

def test_access_key_gates_the_paper(app, open_exam, as_user):
    with app.app_context():
        Exam.query.get(open_exam['exam']).access_key = 'ABCD2345'
        db.session.commit()

    client = as_user('stud2')
    _start(client, open_exam['exam'])
    page = client.get(f"/exam/{open_exam['exam']}/take")
    assert b'access key' in page.data.lower()
    assert b'Q1' not in page.data                      # questions not sent before the key

    bad = client.post(f"/exam/{open_exam['exam']}/verify-key", data={'access_key': 'WRONG'})
    assert bad.status_code == 403
    good = client.post(f"/exam/{open_exam['exam']}/verify-key", data={'access_key': 'abcd2345'})
    assert good.get_json()['success'] is True
    assert b'Q1' in client.get(f"/exam/{open_exam['exam']}/take").data


def test_access_key_bruteforce_locks_out(app, open_exam, as_user):
    with app.app_context():
        Exam.query.get(open_exam['exam']).access_key = 'ABCD2345'
        db.session.commit()
    client = as_user('stud2')
    for _ in range(5):
        client.post(f"/exam/{open_exam['exam']}/verify-key", data={'access_key': 'NOPE'})
    resp = client.post(f"/exam/{open_exam['exam']}/verify-key", data={'access_key': 'ABCD2345'})
    assert resp.status_code == 429


# ── Answers ─────────────────────────────────────────────────────────────────

def test_save_answer_rules(app, open_exam, as_user):
    client = as_user('stud2')
    _start(client, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])

    ok = client.post('/api/save-answer', json={
        'student_exam_id': se.id,
        'answers': {str(open_exam['q1']): 'A', '999999': 'B'},
    })
    assert ok.get_json()['success'] is True
    with app.app_context():
        rows = StudentAnswer.query.filter_by(student_exam_id=se.id).all()
        assert [(r.question_id, r.selected_answer) for r in rows] == [(open_exam['q1'], 'A')]

    # Another student, and even the exam's own faculty, cannot write answers
    other = as_user('stud1').post('/api/save-answer', json={
        'student_exam_id': se.id, 'answers': {str(open_exam['q1']): 'B'}})
    assert other.status_code == 403
    fac = as_user('faca').post('/api/save-answer', json={
        'student_exam_id': se.id, 'answers': {str(open_exam['q1']): 'B'}})
    assert fac.status_code == 403


def test_no_answers_after_submit_or_time_up(app, open_exam, as_user):
    client = as_user('stud2')
    _start(client, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])

    with app.app_context():
        row = StudentExam.query.get(se.id)
        row.started_at = datetime.utcnow() - timedelta(minutes=40)   # 30-minute exam
        db.session.commit()
    late = client.post('/api/save-answer', json={
        'student_exam_id': se.id, 'answers': {str(open_exam['q1']): 'A'}})
    assert late.status_code == 409

    with app.app_context():
        row = StudentExam.query.get(se.id)
        row.started_at = datetime.utcnow()
        db.session.commit()
    client.post(f'/submit_exam/{se.id}')
    after = client.post('/api/save-answer', json={
        'student_exam_id': se.id, 'answers': {str(open_exam['q1']): 'A'}})
    assert after.status_code == 409


def test_submit_scores_and_is_post_only(app, open_exam, as_user):
    client = as_user('stud2')
    _start(client, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])
    client.post('/api/save-answer', json={
        'student_exam_id': se.id,
        'answers': {str(open_exam['q1']): 'A', str(open_exam['q2']): 'C'}})

    assert client.get(f'/submit_exam/{se.id}').status_code == 405
    assert client.post(f'/submit_exam/{se.id}').status_code == 302
    se = _attempt(app, open_exam['s2'], open_exam['exam'])
    assert se.completed and se.score == 1 and se.total_points == 2


def test_other_faculty_cannot_submit_attempt(app, open_exam, as_user):
    client = as_user('stud2')
    _start(client, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])
    assert as_user('facb').post(f'/submit_exam/{se.id}').status_code == 403
    assert as_user('stud1').post(f'/submit_exam/{se.id}').status_code == 403


def test_tab_count_cannot_go_down(app, open_exam, as_user):
    client = as_user('stud2')
    _start(client, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])
    url = f'/api/update-tabcount/{se.id}'
    client.post(url, json={'tab_switch_count': 3})
    resp = client.post(url, json={'tab_switch_count': 0})
    assert resp.get_json()['tab_switch_count'] == 3


# ── Force-end ───────────────────────────────────────────────────────────────

def test_force_end_exam(app, open_exam, as_user):
    client = as_user('stud2')
    _start(client, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])

    as_user('faca').post(f"/faculty/exam/{open_exam['exam']}/force_end")

    status = client.get(f'/api/check-exam-status/{se.id}').get_json()
    assert status['force_ended'] is True
    assert _attempt(app, open_exam['s2'], open_exam['exam']).completed

    # Reactivating clears it
    as_user('faca').post(f"/faculty/exam/{open_exam['exam']}/update_access",
                         data={'is_active': 'true'})
    with app.app_context():
        assert Exam.query.get(open_exam['exam']).force_ended is False


def test_force_end_student_must_match_exam(app, open_exam, as_user):
    with app.app_context():
        other = Exam(title='Other', duration_minutes=10, creator_id=open_exam['fac_b'])
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    client = as_user('stud2')
    _start(client, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])

    # facb owns "Other" but not the attempt's exam
    resp = as_user('facb').post(f'/faculty/exam/{other_id}/force_end_student/{se.id}')
    assert resp.status_code == 403
    assert not _attempt(app, open_exam['s2'], open_exam['exam']).completed

    as_user('faca').post(f"/faculty/exam/{open_exam['exam']}/force_end_student/{se.id}")
    assert _attempt(app, open_exam['s2'], open_exam['exam']).completed
    assert client.get(f'/api/check-exam-status/{se.id}').get_json()['force_ended'] is True


# ── Socket.IO ───────────────────────────────────────────────────────────────

def test_socket_rejects_frames_for_someone_elses_attempt(app, open_exam, as_user):
    victim = as_user('stud2')
    _start(victim, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])

    attacker = as_user('stud1')
    sock = socketio.test_client(app, flask_test_client=attacker)
    sock.emit('calibrationBinary', {'studentExamId': se.id, 'frame': b'\xff\xd8junk'})
    sock.emit('frameBinary', {'studentExamId': se.id, 'frame': b'\xff\xd8junk'})
    sock.emit('heartbeat', {'studentExamId': se.id})
    assert sock.get_received() == []
    sock.disconnect()


def test_socket_force_end_events_reach_the_student(app, open_exam, as_user):
    client = as_user('stud2')
    _start(client, open_exam['exam'])
    se = _attempt(app, open_exam['s2'], open_exam['exam'])

    sock = socketio.test_client(app, flask_test_client=client)
    sock.emit('join_exam', {'exam_id': open_exam['exam'], 'student_exam_id': se.id})
    sock.emit('heartbeat', {'studentExamId': se.id})
    assert [m['name'] for m in sock.get_received()] == ['heartbeat_ack']

    as_user('faca').post(f"/faculty/exam/{open_exam['exam']}/force_end_student/{se.id}")
    names = [m['name'] for m in sock.get_received()]
    assert 'student_force_ended' in names
    sock.disconnect()


def test_socket_unauthenticated_join_is_ignored(app, open_exam):
    sock = socketio.test_client(app)
    sock.emit('join_exam', {'exam_id': open_exam['exam'], 'student_exam_id': open_exam['attempt']})
    sock.emit('heartbeat', {'studentExamId': open_exam['attempt']})
    assert sock.get_received() == []
