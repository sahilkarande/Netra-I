"""Exam windows: typed in the hall's local time, stored and compared in UTC."""

from datetime import datetime, timedelta, timezone

import pytest

from app import convert_exam_times_to_utc
from backend.database import db
from backend.models import Exam, StudentExam
from backend.services import time_service
from backend.services.exam_builder import validate

IST = timedelta(hours=5, minutes=30)


def _local_now():
    return datetime.utcnow() + IST


def _fmt(dt):
    return dt.strftime('%Y-%m-%dT%H:%M')


def test_conversions_round_trip(app):
    with app.app_context():
        assert app.config['APP_TIMEZONE'] == 'Asia/Kolkata'
        local = datetime(2026, 11, 20, 10, 0)
        utc = time_service.local_to_utc(local)
        assert utc == datetime(2026, 11, 20, 4, 30)
        assert time_service.utc_to_local(utc) == local
        assert time_service.localtime(utc, '%H:%M') == '10:00'
        assert time_service.local_to_utc(None) is None and time_service.localtime(None) == ''


def test_fixed_offset_fallback_without_tz_database(monkeypatch):
    import zoneinfo
    time_service._cache.clear()
    monkeypatch.setattr(zoneinfo, 'ZoneInfo', lambda name: (_ for _ in ()).throw(KeyError(name)))
    try:
        zone = time_service.resolve_zone('Asia/Kolkata')
        assert datetime(2026, 1, 1, tzinfo=zone).utcoffset() == IST
        assert time_service.resolve_zone('Mars/Olympus') is timezone.utc
    finally:
        time_service._cache.clear()


def test_form_times_are_stored_as_utc(app):
    with app.test_request_context():
        cleaned, errors = _validate({'start_time': '2026-11-20T10:00', 'end_time': '2026-11-20T12:00'})
        assert not errors
        assert cleaned['start_time'] == datetime(2026, 11, 20, 4, 30)
        assert cleaned['end_time'] == datetime(2026, 11, 20, 6, 30)


def _validate(times):
    form = {'title': 'TZ', 'duration_minutes': '30', 'passing_score': '40', **times}
    errors, cleaned = validate(form)
    return cleaned, errors


def _open_window(app, seed, start_local, end_local):
    with app.test_request_context():
        cleaned, errors = _validate({'start_time': _fmt(start_local), 'end_time': _fmt(end_local)})
        assert not errors
        exam = Exam.query.get(seed['exam'])
        exam.start_time, exam.end_time = cleaned['start_time'], cleaned['end_time']
        db.session.commit()


def _started(app, seed):
    with app.app_context():
        return StudentExam.query.filter_by(student_id=seed['s2'], exam_id=seed['exam']).first() is not None


def test_exam_set_for_the_next_local_hour_is_not_open_yet(app, seed, as_user):
    now = _local_now()
    _open_window(app, seed, now + timedelta(hours=1), now + timedelta(hours=3))
    client = as_user('stud2')
    client.post(f"/start-exam/{seed['exam']}")
    assert not _started(app, seed)
    html = client.get('/student/dashboard').data.decode()
    assert 'Opens ' + (now + timedelta(hours=1)).strftime('%d %b, %H:%M') in html   # shown in local time


def test_exam_that_opened_a_local_hour_ago_can_start(app, seed, as_user):
    now = _local_now()
    _open_window(app, seed, now - timedelta(hours=1), now + timedelta(hours=2))
    as_user('stud2').post(f"/start-exam/{seed['exam']}")
    assert _started(app, seed)


def test_exam_that_closed_a_local_hour_ago_cannot_start(app, seed, as_user):
    now = _local_now()
    _open_window(app, seed, now - timedelta(hours=3), now - timedelta(hours=1))
    as_user('stud2').post(f"/start-exam/{seed['exam']}")
    assert not _started(app, seed)


def test_old_local_time_rows_are_converted_once(app, seed):
    with app.app_context():
        exam = Exam.query.get(seed['exam'])
        exam.start_time = datetime(2026, 11, 20, 10, 0)       # saved by the old code: IST
        exam.times_utc = False
        db.session.commit()

        convert_exam_times_to_utc()
        convert_exam_times_to_utc()                            # second run changes nothing
        exam = Exam.query.get(seed['exam'])
        assert exam.start_time == datetime(2026, 11, 20, 4, 30)
        assert exam.times_utc is True


def test_new_exams_are_marked_utc(app, seed):
    with app.app_context():
        assert Exam.query.get(seed['exam']).times_utc is True
