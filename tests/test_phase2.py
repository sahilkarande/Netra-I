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
