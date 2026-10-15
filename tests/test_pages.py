"""Pages that used to crash with a 500 or render empty."""

from backend.database import db
from backend.models import Exam, ExamViolation, StudentExam


def _publish(app, exam_id, leaderboard=True):
    with app.app_context():
        exam = Exam.query.get(exam_id)
        exam.results_published = True
        exam.show_leaderboard = leaderboard
        db.session.commit()


def test_student_dashboard_renders(seed, as_user):
    assert as_user('stud1').get('/student/dashboard').status_code == 200


def test_result_hidden_until_published(app, seed, as_user):
    client = as_user('stud1')
    url = f"/student/exam/{seed['attempt']}/result"
    assert client.get(url).status_code == 302          # bounced to dashboard
    assert client.get(f"/student/exam/{seed['attempt']}/download-pdf").status_code == 302

    _publish(app, seed['exam'])
    assert client.get(url).status_code == 200
    pdf = client.get(f"/student/exam/{seed['attempt']}/download-pdf")
    assert pdf.status_code == 200
    assert pdf.mimetype == 'application/pdf'


def test_leaderboard_needs_results_published(app, seed, as_user):
    with app.app_context():
        exam = Exam.query.get(seed['exam'])
        exam.show_leaderboard = True
        exam.results_published = False
        assert exam.leaderboard_visible_to_students is False
        exam.results_published = True
        assert exam.leaderboard_visible_to_students is True


def test_student_own_profile(seed, as_user):
    resp = as_user('stud1').get(f"/student/{seed['s1']}/profile")
    assert resp.status_code == 200
    # The faculty-only reset button is not shown to the student
    assert b'openPasswordModal()"' not in resp.data


def test_student_profile_hides_unpublished_scores(app, seed, as_user):
    resp = as_user('stud1').get(f"/student/{seed['s1']}/profile")
    assert b'50.0%' not in resp.data
    _publish(app, seed['exam'])
    resp = as_user('stud1').get(f"/student/{seed['s1']}/profile")
    assert b'50.0%' in resp.data


def test_student_cannot_view_other_profile(seed, as_user):
    resp = as_user('stud1').get(f"/student/{seed['s2']}/profile")
    assert resp.status_code == 302


def test_download_template(seed, as_user):
    resp = as_user('faca').get('/faculty/download_template')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/csv'
    assert b'prn_number' in resp.data


def test_admin_pages_render_with_data(seed, as_user):
    client = as_user('admin1')
    students = client.get('/admin/students')
    assert students.status_code == 200
    assert b'Stud1' in students.data                    # previously always empty
    exams = client.get('/admin/all-exams')
    assert exams.status_code == 200
    assert b'SQL Basics' in exams.data
    assert client.get('/admin/sql_console').status_code == 200
    assert client.get('/admin/dashboard').status_code == 200


def test_admin_students_filter(seed, as_user):
    resp = as_user('admin1').get('/admin/students?q=stud2')
    assert b'Stud2' in resp.data and b'Stud1' not in resp.data


def test_faculty_pages_render(seed, as_user):
    client = as_user('faca')
    for url in ('/faculty/dashboard', f"/faculty/exam/{seed['exam']}",
                f"/faculty/exam/{seed['exam']}/analytics", '/faculty/students',
                '/faculty/student_report', '/faculty/exam/create',
                f"/faculty/student/{seed['s1']}/profile"):
        assert client.get(url).status_code == 200, url


def test_analytics_escapes_student_name(app, seed, as_user):
    with app.app_context():
        from backend.models import User
        s = User.query.get(seed['s1'])
        s.full_name = "x');alert(1);('"
        se = StudentExam.query.get(seed['attempt'])
        se.total_violations = 3
        db.session.commit()
    html = as_user('faca').get(f"/faculty/exam/{seed['exam']}/analytics").data.decode()
    assert "'x');alert(1);('" not in html
    assert 'viewViolations(' in html


def test_proctor_activity_log_is_written(app, seed):
    from backend.services.proctor_service import log_proctor_event
    from backend.models import ActivityLog
    with app.app_context():
        log_proctor_event(seed['attempt'], 'no_face', 'medium', 'No face')
        assert ExamViolation.query.filter_by(student_exam_id=seed['attempt']).count() == 1
        assert ActivityLog.query.filter_by(student_exam_id=seed['attempt']).count() == 1
