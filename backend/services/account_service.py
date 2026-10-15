"""
Student account lifecycle: temporary passwords, faculty scoping, deletion.

Why this is a service
─────────────────────
These rules were scattered across routes and each copy drifted: imports and
resets handed out the same well-known password, deletes left orphaned rows
behind, and every faculty could act on every student. Keeping them here means
the import page, the reset button, the delete buttons and the admin pages all
follow the same rules.
"""

import csv
import io
import logging
import os
import secrets
from typing import Iterable, List, Optional, Tuple

from backend.database import db
from backend.models import (
    ActivityLog, Exam, ExamCalibration, ExamViolation, FaceAuditLog,
    FaceEnrollment, FaceLoginAttempt, StudentAnswer, StudentExam, User,
)

log = logging.getLogger(__name__)

# No 0/O/1/l/I: temporary passwords are read off a sheet and typed by hand.
TEMP_PASSWORD_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789'
TEMP_PASSWORD_LENGTH = 10


def generate_temp_password(length: int = TEMP_PASSWORD_LENGTH) -> str:
    """A random first-login password. Never a shared default.

    A shared default plus sequential PRNs let anyone sign in as a student who
    had not logged in yet and register their own face against that account.
    """
    return ''.join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(length))


def credentials_csv(rows: Iterable[dict]) -> bytes:
    """One-time credentials sheet for the faculty to hand out."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['full_name', 'username', 'prn_number', 'email', 'temporary_password'])
    for r in rows:
        writer.writerow([r.get('full_name') or '', r.get('username') or '',
                         r.get('prn_number') or '', r.get('email') or '',
                         r.get('temporary_password') or ''])
    return out.getvalue().encode('utf-8')


# ── Faculty scoping ─────────────────────────────────────────────────────────

def _department_key(user) -> Optional[str]:
    return (getattr(user, 'course', None) or getattr(user, 'department', None)) or None


def faculty_peer_ids(actor) -> List[int]:
    """The actor plus faculty in the same course/department."""
    key = _department_key(actor)
    if not key:
        return [actor.id]
    peers = User.query.filter(
        User.role.in_(('faculty', 'admin')),
        db.or_(User.course == key, User.department == key),
    ).with_entities(User.id).all()
    return sorted({actor.id, *(p[0] for p in peers)})


def scoped_students_query(actor):
    """Students this staff member may see and manage.

    Admin: everyone. Faculty: students created by their department, students
    who sat or were assigned one of the department's exams, and students with
    no recorded owner (created before ownership was tracked, or
    self-registered) so existing installations keep working.
    """
    query = User.query.filter(User.role == 'student')
    if actor.role == 'admin':
        return query

    peers = faculty_peer_ids(actor)
    exam_ids = db.session.query(Exam.id).filter(Exam.creator_id.in_(peers))
    attempted = db.session.query(StudentExam.student_id).filter(StudentExam.exam_id.in_(exam_ids))
    return query.filter(db.or_(
        User.created_by.is_(None),
        User.created_by.in_(peers),
        User.id.in_(attempted),
    ))


def can_manage_student(actor, student) -> bool:
    if not student or student.role != 'student':
        return False
    if actor.role == 'admin':
        return True
    if actor.role != 'faculty':
        return False
    return scoped_students_query(actor).filter(User.id == student.id).first() is not None


# ── Deletion ────────────────────────────────────────────────────────────────

def _remove_evidence_file(path: Optional[str]):
    if not path:
        return
    try:
        full = path if os.path.isabs(path) else os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), path)
        if os.path.isfile(full):
            os.remove(full)
    except OSError:
        log.warning('could not remove evidence file %s', path)


def _delete_attempts(attempt_ids: List[int]):
    if not attempt_ids:
        return
    for v in ExamViolation.query.filter(ExamViolation.student_exam_id.in_(attempt_ids)):
        _remove_evidence_file(v.evidence_path)
    for model in (StudentAnswer, ActivityLog, ExamViolation, ExamCalibration):
        model.query.filter(model.student_exam_id.in_(attempt_ids)).delete(synchronize_session=False)
    StudentExam.query.filter(StudentExam.id.in_(attempt_ids)).delete(synchronize_session=False)


def delete_students(student_ids: Iterable[int], actor) -> Tuple[int, List[int]]:
    """Delete student accounts and everything that belongs to them.

    Returns (deleted_count, refused_ids). Only students the actor may manage
    are touched; anything else is refused rather than silently skipped.
    """
    ids = sorted({int(i) for i in student_ids})
    students = User.query.filter(User.id.in_(ids)).all() if ids else []
    allowed = [s for s in students if can_manage_student(actor, s)]
    refused = sorted(set(ids) - {s.id for s in allowed})
    if not allowed:
        return 0, refused

    allowed_ids = [s.id for s in allowed]
    attempt_ids = [r[0] for r in db.session.query(StudentExam.id)
                   .filter(StudentExam.student_id.in_(allowed_ids))]
    _delete_attempts(attempt_ids)

    FaceEnrollment.query.filter(FaceEnrollment.user_id.in_(allowed_ids)).delete(synchronize_session=False)
    FaceAuditLog.query.filter(FaceAuditLog.subject_user_id.in_(allowed_ids)).delete(synchronize_session=False)
    FaceLoginAttempt.query.filter(FaceLoginAttempt.user_id.in_(allowed_ids)).update(
        {FaceLoginAttempt.user_id: None}, synchronize_session=False)
    User.query.filter(User.id.in_(allowed_ids)).delete(synchronize_session=False)
    db.session.commit()

    log.info('user %s deleted students %s (%d attempts)', actor.id, allowed_ids, len(attempt_ids))
    return len(allowed_ids), refused


def delete_faculty(faculty: User, actor) -> Tuple[bool, str]:
    """Remove a faculty account. Refused while they still own exams."""
    if faculty.role != 'faculty':
        return False, 'User is not faculty.'
    owned = Exam.query.filter_by(creator_id=faculty.id).count()
    if owned:
        return False, (f'This faculty member still owns {owned} exam(s). '
                       'Delete or reassign those exams first.')

    fid = faculty.id
    StudentExam.query.filter_by(reviewed_by=fid).update({StudentExam.reviewed_by: None}, synchronize_session=False)
    FaceEnrollment.query.filter_by(enrolled_by=fid).update({FaceEnrollment.enrolled_by: None}, synchronize_session=False)
    FaceEnrollment.query.filter_by(reset_by=fid).update({FaceEnrollment.reset_by: None}, synchronize_session=False)
    FaceAuditLog.query.filter_by(actor_user_id=fid).update({FaceAuditLog.actor_user_id: None}, synchronize_session=False)
    FaceLoginAttempt.query.filter_by(user_id=fid).update({FaceLoginAttempt.user_id: None}, synchronize_session=False)
    User.query.filter(User.created_by == fid).update({User.created_by: None}, synchronize_session=False)
    User.query.filter_by(id=fid).delete(synchronize_session=False)
    db.session.commit()
    log.info('user %s deleted faculty %s', actor.id, fid)
    return True, 'Faculty deleted successfully!'
