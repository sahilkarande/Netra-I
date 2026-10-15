import time
import json
import cv2
import numpy as np
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app, session
from flask_login import login_required, current_user
from flask_socketio import emit, join_room
from sqlalchemy.exc import IntegrityError

from backend.database import db
from backend.models import Exam, Question, StudentExam, StudentAnswer, ActivityLog, ExamViolation, ExamCalibration
from backend.socket_io import socketio
from backend.services import face_service
from backend.services.proctor_service import (
    get_proctor_instance, detect_faces, decode_base64_image,
    log_proctor_event, _heartbeat_registry, _last_frame_processed,
    FRAME_INTERVAL_SEC, cleanup_proctor_instance
)
from backend.services.exam_service import (
    calculate_student_score, assign_shuffle, ANSWER_GRACE_SEC,
    finalize_overdue_attempts, remaining_seconds,
)

exam_bp = Blueprint('exam', __name__)



def exam_room(exam_id):
    return f'exam_{exam_id}'


def attempt_room(student_exam_id):
    return f'attempt_{student_exam_id}'


def _key_session_name(exam_id):
    return f'exam_key_verified_{exam_id}'


def _needs_access_key(exam):
    """A student must enter the exam's access key before seeing the paper."""
    return (current_user.role == 'student'
            and bool(exam.access_key)
            and not session.get(_key_session_name(exam.id)))


def _exam_closed_reason(exam):
    """Why a student cannot start this exam right now, or None if they can."""
    if not exam.is_active:
        return 'This exam is not currently open.'
    if exam.force_ended:
        return 'This exam has been ended by the instructor.'
    now = datetime.utcnow()
    if exam.start_time and now < exam.start_time:
        return 'This exam has not started yet.'
    if exam.end_time and now > exam.end_time:
        return 'This exam has closed.'
    return None


def _staff_can_access(exam):
    from backend.routes.faculty import _faculty_can_access_exam
    return current_user.role in ('faculty', 'admin') and _faculty_can_access_exam(exam)


def _time_up(student_exam, exam, grace_sec=0):
    if not student_exam.started_at or not exam.duration_minutes:
        return False
    deadline = student_exam.started_at + timedelta(minutes=exam.duration_minutes, seconds=grace_sec)
    return datetime.utcnow() > deadline


@exam_bp.route("/start-exam/<int:exam_id>", methods=["POST", "GET"])
@login_required
def start_exam(exam_id):
    # Starting an attempt starts the clock, so it must be a deliberate POST from
    # the dashboard — a GET link from another site must not be able to do it.
    if request.method != 'POST':
        return redirect(url_for('student.student_dashboard'))

    exam = Exam.query.get_or_404(exam_id)
    if current_user.role != 'student':
        flash('Only students can take exams.', 'error')
        return redirect(url_for('auth.index'))
    if not exam.is_assigned:
        flash('You are not authorized or assigned to take this exam.', 'error')
        return redirect(url_for('student.student_dashboard'))

    # Check if student already has a session
    student_exam = StudentExam.query.filter_by(
        student_id=current_user.id,
        exam_id=exam_id
    ).first()

    if student_exam and not student_exam.completed:
        finalize_overdue_attempts(attempt_id=student_exam.id)
    if student_exam and student_exam.completed:
        flash('You have already completed this exam.', 'info')
        return redirect(url_for('student.student_dashboard'))

    # Resuming an attempt already under way is always allowed; opening a new
    # one only while the exam is open.
    resuming = student_exam is not None and student_exam.status == 'started'
    if not resuming:
        reason = _exam_closed_reason(exam)
        if reason:
            flash(reason, 'error')
            return redirect(url_for('student.student_dashboard'))

    if not student_exam:
        student_exam = StudentExam(
            student_id=current_user.id,
            exam_id=exam_id,
            status='started',
            started_at=datetime.utcnow()
        )
        db.session.add(student_exam)
        try:
            db.session.commit()
        except IntegrityError:
            # A double-clicked Start already created the attempt; use that one
            db.session.rollback()
    elif student_exam.status != 'started':
        # Pre-created at import time: the clock starts now, not at import.
        student_exam.status = 'started'
        student_exam.started_at = datetime.utcnow()
        db.session.commit()

    return redirect(url_for('exam.take_exam', exam_id=exam_id))

@exam_bp.route('/exam/<int:exam_id>/take')
@login_required
def take_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not exam.is_assigned:
        flash('You are not authorized or assigned to take this exam.', 'error')
        return redirect(url_for('student.student_dashboard'))

    student_exam = StudentExam.query.filter_by(
        student_id=current_user.id,
        exam_id=exam_id
    ).first_or_404()

    if student_exam.completed:
        return redirect(url_for('student.student_dashboard'))

    # Only a started attempt may be shown. Anything else (pre-created at
    # import) must go through start_exam, which checks the exam is open and
    # starts the clock now.
    if student_exam.status != 'started':
        flash('Press Start on your dashboard to begin this exam.', 'info')
        return redirect(url_for('student.student_dashboard'))

    # The paper is only rendered once the access key has been accepted, so the
    # key actually protects the questions rather than just hiding them.
    if _needs_access_key(exam):
        return render_template('student/exam_key.html', exam=exam)

    questions = Question.query.filter_by(exam_id=exam_id).all()

    # ── Per-student shuffle ──────────────────────────────────────────────
    # Decided and stored server-side so every student gets a different paper and
    # a refresh gives the SAME paper back. Honours exam.randomize_questions.
    option_mapping = {}
    if exam.randomize_questions and questions:
        if not student_exam.question_order:
            assign_shuffle(student_exam)

        order = student_exam.get_question_order() or []
        if order:
            by_id = {q.id: q for q in questions}
            ordered = [by_id[qid] for qid in order if qid in by_id]
            # Any question added after this student started goes on the end
            ordered += [q for q in questions if q.id not in set(order)]
            questions = ordered

        option_mapping = student_exam.get_option_mapping() or {}

    existing_answers = {ans.question_id: ans.selected_answer for ans in student_exam.answers}

    # Calculate remaining time in minutes
    elapsed_time = (datetime.utcnow() - student_exam.started_at).total_seconds() / 60.0
    time_remaining = max(0.0, float(exam.duration_minutes) - elapsed_time)

    return render_template('student/take_exam.html',
                           exam=exam,
                           student_exam=student_exam,
                           questions=questions,
                           existing_answers=existing_answers,
                           option_mapping=option_mapping,
                           needs_key=False,
                           proctor_settings=exam.get_proctor_settings(),
                           frame_interval_ms=int(FRAME_INTERVAL_SEC * 1000),
                           time_remaining=time_remaining)

def _apply_answers(student_exam, answers_dict):
    """Write an {question_id: answer} map onto an attempt (not committed).

    Only questions on this exam's paper are accepted. A null value clears a
    previously saved answer — without that, an answer the student removed would
    still be marked.
    """
    valid_ids = {qid for (qid,) in db.session.query(Question.id).filter_by(exam_id=student_exam.exam_id)}
    existing = {a.question_id: a for a in StudentAnswer.query.filter_by(student_exam_id=student_exam.id)}

    for q_id_str, selected_answer in answers_dict.items():
        try:
            q_id = int(q_id_str)
        except (ValueError, TypeError):
            continue
        if q_id not in valid_ids:
            continue
        value = str(selected_answer).strip().upper()[:10] if selected_answer not in (None, '') else None

        answer = existing.get(q_id)
        if answer is None:
            if value is None:
                continue
            answer = StudentAnswer(student_exam_id=student_exam.id, question_id=q_id)
            db.session.add(answer)
            existing[q_id] = answer
        answer.selected_answer = value
        answer.answered_at = datetime.utcnow()


@exam_bp.route('/api/save-answer', methods=['POST'])
@login_required
def save_answer():
    data = request.get_json(silent=True) or {}
    student_exam_id = data.get('student_exam_id')

    if not student_exam_id:
        return jsonify({"error": "Missing student_exam_id"}), 400

    student_exam = StudentExam.query.get(student_exam_id)
    # Only the student sitting the attempt may write its answers
    if not student_exam or student_exam.student_id != current_user.id:
        return jsonify({"error": "Unauthorized or invalid exam session"}), 403

    if student_exam.completed:
        return jsonify({"error": "This exam has already been submitted."}), 409

    exam = student_exam.exam
    if _time_up(student_exam, exam, grace_sec=ANSWER_GRACE_SEC):
        return jsonify({"error": "Time is up for this exam."}), 409

    answers_dict = data.get('answers')

    # Support single question_id submission as well
    if not answers_dict and data.get('question_id'):
        answers_dict = {str(data.get('question_id')): data.get('selected_answer')}

    if not isinstance(answers_dict, dict):
        return jsonify({"error": "No valid answers payload provided"}), 400

    _apply_answers(student_exam, answers_dict)

    try:
        db.session.commit()
    except IntegrityError:
        # Two overlapping autosaves raced to insert the same answer row. The
        # other request won; the client sends the full map again next tick.
        db.session.rollback()
        return jsonify({"success": False, "retry": True}), 409
    return jsonify({"success": True})

@exam_bp.route('/submit_exam/<int:student_exam_id>', methods=['POST'])
@login_required
def submit_exam(student_exam_id):
    student_exam = StudentExam.query.get_or_404(student_exam_id)
    is_owner = student_exam.student_id == current_user.id
    if not is_owner and not _staff_can_access(student_exam.exam):
        return "Unauthorized", 403

    already_done = student_exam.completed

    # The page sends its final answer map with the submit, so answers whose
    # last autosave failed (Wi-Fi drop near the end) are still recorded.
    if is_owner and not already_done:
        raw = request.form.get('answers')
        if raw and not _time_up(student_exam, student_exam.exam, grace_sec=ANSWER_GRACE_SEC):
            try:
                final_answers = json.loads(raw)
            except (TypeError, ValueError):
                final_answers = None
            if isinstance(final_answers, dict):
                _apply_answers(student_exam, final_answers)

    student_exam.completed = True
    student_exam.status = 'submitted'
    if not student_exam.submitted_at:
        student_exam.submitted_at = datetime.utcnow()

    if not already_done:
        calculate_student_score(student_exam_id)
    db.session.commit()

    # Free the per-attempt vision instance — otherwise it lives for the process lifetime
    cleanup_proctor_instance(student_exam_id)

    exam = student_exam.exam
    if not is_owner:
        return redirect(url_for('faculty.view_exam', exam_id=exam.id))
    if not exam.results_published or exam.post_exam_redirect == 'dashboard':
        flash('Exam submitted successfully!', 'success')
        return redirect(url_for('student.student_dashboard'))

    return redirect(url_for('student.student_exam_result', student_exam_id=student_exam_id))

@exam_bp.route('/api/update-tabcount/<int:student_exam_id>', methods=['POST'])
@login_required
def api_update_tabcount(student_exam_id):
    """Update the number of tab switches."""
    data = request.get_json(silent=True) or {}
    try:
        new_count = max(0, int(data.get('tab_switch_count', 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid count"}), 400

    student_exam = StudentExam.query.get_or_404(student_exam_id)
    if student_exam.student_id != current_user.id:
        return jsonify({"error": "Unauthorized"}), 403

    # The count only ever goes up — a student must not be able to reset it
    student_exam.tab_switch_count = max(student_exam.tab_switch_count or 0, new_count)
    db.session.commit()

    return jsonify({"status": "updated", "tab_switch_count": student_exam.tab_switch_count})

@exam_bp.route('/api/log-activity/<int:student_exam_id>', methods=['POST'])
@login_required
def api_log_activity(student_exam_id):
    """Log tab switches, screenshot attempts, or other suspicious activities."""
    data = request.get_json(silent=True) or {}
    activity_type = str(data.get('activity_type', 'unknown'))[:50]
    description = str(data.get('description', ''))[:2000]
    severity = str(data.get('severity', 'low'))
    if severity not in ('low', 'medium', 'high'):
        severity = 'low'

    student_exam = StudentExam.query.get_or_404(student_exam_id)
    if student_exam.student_id != current_user.id:
        return jsonify({"error": "Unauthorized"}), 403

    log = ActivityLog(
        student_exam_id=student_exam.id,
        activity_type=activity_type,
        description=description,
        severity=severity
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({"status": "logged"})

@exam_bp.route("/api/check-exam-status/<int:student_exam_id>")
@login_required
def api_check_exam_status(student_exam_id):
    """Check if exam was force-ended by faculty"""
    student_exam = StudentExam.query.get(student_exam_id)

    if not student_exam:
        return jsonify({"error": "Not found"}), 404

    if student_exam.student_id != current_user.id:
        return jsonify({"error": "Unauthorized"}), 403

    exam = student_exam.exam
    finalize_overdue_attempts(attempt_id=student_exam.id)
    # Ended for everyone, or this one attempt was closed by the instructor
    # (or by the overdue sweep)
    force_ended = bool(exam.force_ended) or bool(student_exam.completed)

    if student_exam.started_at and exam.duration_minutes:
        end_time = student_exam.started_at + timedelta(minutes=exam.duration_minutes)
        updated_end_time = end_time.isoformat()
    else:
        updated_end_time = exam.end_time.isoformat() if exam.end_time else None

    return jsonify({
        "force_ended": force_ended,
        "updated_end_time": updated_end_time,
        # The client resyncs its countdown to this, so time extensions and
        # clock drift are corrected without a page reload
        "remaining_seconds": remaining_seconds(student_exam),
        "exam_status": exam.status or 'active'
    })


@exam_bp.route('/exam/<int:exam_id>/verify-key', methods=['POST'])
@login_required
def verify_exam_key(exam_id):
    """Student submits the access key for an exam. Stores approval in session."""
    if current_user.role != 'student':
        return jsonify({'success': False, 'error': 'Students only'}), 403

    exam = Exam.query.get_or_404(exam_id)

    # If no key is set on this exam, access is open
    if not exam.access_key:
        session[_key_session_name(exam_id)] = True
        return jsonify({'success': True})

    lock_key = f'examkey:{current_user.id}:{exam_id}'
    locked, minutes = face_service.is_locked_out(lock_key)
    if locked:
        return jsonify({'success': False,
                        'error': f'Too many wrong keys. Try again in {minutes} minute(s).'}), 429

    if request.is_json:
        submitted_key = (request.get_json(silent=True) or {}).get('access_key', '')
    else:
        submitted_key = request.form.get('access_key', '')
    submitted_key = (submitted_key or '').strip().upper()

    if submitted_key == exam.access_key.strip().upper():
        face_service.clear_failures(lock_key)
        session[_key_session_name(exam_id)] = True
        return jsonify({'success': True})

    face_service.record_failure(lock_key)
    return jsonify({'success': False, 'error': 'Invalid access key'}), 403


# ═══════════════════════════════════════════════════════════════════════
# Socket.IO
# Every handler acts on a StudentExam id supplied by the client, so each one
# must first confirm the connected user owns that attempt — otherwise anyone
# could pile violations onto another student or recalibrate their session.
# ═══════════════════════════════════════════════════════════════════════

def _owned_active_attempt(student_exam_id):
    if not current_user.is_authenticated:
        return None
    try:
        student_exam_id = int(student_exam_id)
    except (TypeError, ValueError):
        return None
    attempt = StudentExam.query.get(student_exam_id)
    if not attempt or attempt.student_id != current_user.id or attempt.completed:
        return None
    return attempt


@socketio.on('join_exam')
def handle_join_exam(data):
    data = data or {}
    attempt = _owned_active_attempt(data.get('student_exam_id') or data.get('studentExamId'))
    if not attempt:
        return
    join_room(exam_room(attempt.exam_id))
    join_room(attempt_room(attempt.id))


@socketio.on('calibrationBinary')
def handle_calibration_binary(data):
    try:
        data = data or {}
        frame_buffer = data.get('frame')
        student_exam = _owned_active_attempt(data.get('studentExamId'))
        if not frame_buffer or not student_exam:
            return

        nparr = np.frombuffer(frame_buffer, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None: return

        faces = detect_faces(frame)

        if len(faces) == 1:
            exam = Exam.query.get(student_exam.exam_id)
            proctor_state, vision = get_proctor_instance(student_exam.id, exam)
            vision.calibrate([frame] * 5)
            student_exam.calibration_completed = True
            db.session.commit()
            emit('calibration_result', {'success': True, 'message': 'Calibrated!'})
        else:
            emit('calibration_result', {'success': False, 'message': 'Face error'})
    except Exception as e:
        print(f"Calibration error: {e}")

@socketio.on('frameBinary')
def handle_frame_binary(data):
    try:
        data = data or {}
        frame_buffer = data.get('frame')
        student_exam = _owned_active_attempt(data.get('studentExamId'))
        if not frame_buffer or not student_exam: return
        student_exam_id = student_exam.id

        # Throttle FIRST — decoding a frame we are about to drop is pure waste.
        current_time = time.time()
        if current_time - _last_frame_processed.get(student_exam_id, 0) < FRAME_INTERVAL_SEC:
            return
        _last_frame_processed[student_exam_id] = current_time

        nparr = np.frombuffer(frame_buffer, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None: return

        exam = Exam.query.get(student_exam.exam_id)
        proctor_state, vision = get_proctor_instance(student_exam_id, exam)
        status, details = vision.check_frame(frame)

        if status in ("WARNING", "TERMINATE"):
            student_exam.total_violations = (student_exam.total_violations or 0) + 1
            if status == "TERMINATE":
                student_exam.proctoring_status = 'terminated'
            log_proctor_event(
                student_exam_id,
                details.get('violation_type', 'unknown'),
                'medium',
                details.get('message'),
                evidence_path=details.get('evidence_path'),
            )
            db.session.commit()
            emit('proctor_result', {
                'success': False,
                'message': details.get('message'),
                'violation': details.get('violation_type'),
                'count': student_exam.total_violations,
                'total_violations': student_exam.total_violations,
                'terminated': status == "TERMINATE",
            })
        else:
            emit('proctor_result', {'success': True})

    except Exception as e:
        print(f"Frame error: {e}")

@socketio.on('heartbeat')
def handle_heartbeat(data):
    attempt = _owned_active_attempt((data or {}).get('studentExamId'))
    if attempt:
        _heartbeat_registry[attempt.id] = time.time()
        emit('heartbeat_ack', {'timestamp': time.time()})


@socketio.on('audioLevel')
def handle_audio_level(data):
    """Microphone loudness from the exam page. Only a number is sent, never audio.

    Voice monitoring is not adjudicated server-side yet; accepting the event
    keeps it from raising unhandled-event noise.
    """
    return None
