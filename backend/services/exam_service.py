import random
import json
from datetime import datetime
from backend.database import db
from backend.models import StudentExam, Question, StudentAnswer

def calculate_student_score(student_exam_id):
    """Calculate and update the score for a completed student exam"""
    try:
        student_exam = StudentExam.query.get(student_exam_id)
        if not student_exam:
            return None
        
        exam = student_exam.exam
        questions = Question.query.filter_by(exam_id=exam.id).all()
        
        if not questions:
            student_exam.score = 0
            student_exam.total_points = 0
            student_exam.percentage = 0
            student_exam.passed = False
            db.session.commit()
            return {'score': 0, 'total_points': 0}
        
        student_answers = StudentAnswer.query.filter_by(student_exam_id=student_exam_id).all()
        answer_dict = {ans.question_id: ans for ans in student_answers}
        
        earned_points = 0
        total_points = 0
        
        for question in questions:
            points = question.points or 1.0
            total_points += points
            
            student_answer = answer_dict.get(question.id)
            
            if student_answer and student_answer.selected_answer:
                is_correct = (student_answer.selected_answer.upper() == question.correct_answer.upper())
                
                if is_correct:
                    earned_points += points
                    student_answer.is_correct = True
                    student_answer.points_earned = points
                else:
                    student_answer.is_correct = False
                    student_answer.points_earned = 0
            else:
                if not student_answer:
                    student_answer = StudentAnswer(
                        student_exam_id=student_exam_id,
                        question_id=question.id,
                        selected_answer="0",
                        is_correct=False,
                        points_earned=0
                    )
                    db.session.add(student_answer)
        
        percentage = (earned_points / total_points * 100) if total_points > 0 else 0
        passing_score = exam.passing_score or 50.0
        passed = percentage >= passing_score
        
        student_exam.score = round(earned_points, 2)
        student_exam.total_points = round(total_points, 2)
        student_exam.percentage = round(percentage, 2)
        student_exam.passed = passed
        student_exam.status = 'submitted'
        student_exam.completed = True
        
        if student_exam.started_at and student_exam.submitted_at:
            time_taken = (student_exam.submitted_at - student_exam.started_at).total_seconds() / 60
            student_exam.time_taken_minutes = int(time_taken)
        
        db.session.commit()
        
        return {
            'score': earned_points,
            'total_points': total_points,
            'percentage': percentage,
            'passed': passed
        }
        
    except Exception as e:
        print(f"❌ Error calculating score: {e}")
        db.session.rollback()
        return None

def assign_shuffle(student_exam):
    """Create a persistent shuffled question & option order for a new StudentExam."""
    exam = student_exam.exam
    questions = Question.query.filter_by(exam_id=exam.id).all()
    random.shuffle(questions)
    q_order = [q.id for q in questions]
    option_mapping = {}

    for q in questions:
        options = ['A', 'B', 'C', 'D']
        random.shuffle(options)
        option_mapping[str(q.id)] = options

    student_exam.question_order = json.dumps(q_order)
    student_exam.option_mapping = json.dumps(option_mapping)
    db.session.commit()


# ── Deadlines ───────────────────────────────────────────────────────────────

# Answers arriving this long after the clock ran out are still accepted, so a
# final autosave racing the auto-submit is not lost. Overdue attempts are
# closed once this grace has passed too.
ANSWER_GRACE_SEC = 120


def attempt_deadline(student_exam):
    """When this attempt's time runs out (None if it has no clock)."""
    from datetime import timedelta
    exam = student_exam.exam
    if not student_exam.started_at or not exam or not exam.duration_minutes:
        return None
    return student_exam.started_at + timedelta(minutes=exam.duration_minutes)


def remaining_seconds(student_exam, now=None):
    deadline = attempt_deadline(student_exam)
    if deadline is None:
        return None
    now = now or datetime.utcnow()
    return max(0, int((deadline - now).total_seconds()))


def finalize_overdue_attempts(exam_ids=None, student_id=None, attempt_id=None, now=None):
    """Score and close attempts whose time (plus grace) has run out.

    A student who closes the browser, loses power or never presses Submit must
    still get a result from the answers they saved. The attempt is marked
    submitted at its deadline, not at the moment the sweep happened to run.
    Returns how many attempts were closed.
    """
    from datetime import timedelta
    from backend.models import Exam

    now = now or datetime.utcnow()
    query = StudentExam.query.join(Exam, StudentExam.exam_id == Exam.id).filter(
        StudentExam.status == 'started',
        db.or_(StudentExam.completed == False, StudentExam.completed.is_(None)),  # noqa: E712
    )
    if exam_ids is not None:
        if not exam_ids:
            return 0
        query = query.filter(StudentExam.exam_id.in_(list(exam_ids)))
    if student_id is not None:
        query = query.filter(StudentExam.student_id == student_id)
    if attempt_id is not None:
        query = query.filter(StudentExam.id == attempt_id)

    closed = 0
    for se in query.all():
        deadline = attempt_deadline(se)
        if deadline is None or now <= deadline + timedelta(seconds=ANSWER_GRACE_SEC):
            continue
        se.completed = True
        se.status = 'submitted'
        se.submitted_at = se.submitted_at or deadline
        db.session.commit()
        calculate_student_score(se.id)
        try:
            from backend.services.proctor_service import cleanup_proctor_instance
            cleanup_proctor_instance(se.id)
        except Exception:
            pass
        closed += 1
    return closed


def start_attempt_sweeper(app, socketio, interval_sec=60):
    """Background task: close overdue attempts every `interval_sec` seconds.

    Lazy finalisation on page views covers most cases; this catches attempts
    nobody looks at (a student who never comes back, a faculty who never opens
    the exam page) so results and reports are complete.
    """
    if getattr(app, '_attempt_sweeper_started', False):
        return
    app._attempt_sweeper_started = True

    def loop():
        import logging
        log = logging.getLogger('netra.sweeper')
        while True:
            socketio.sleep(interval_sec)
            try:
                with app.app_context():
                    closed = finalize_overdue_attempts()
                    if closed:
                        log.info('closed %d overdue attempt(s)', closed)
            except Exception:
                log.exception('attempt sweeper failed')

    socketio.start_background_task(loop)
