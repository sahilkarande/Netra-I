from flask import Blueprint, render_template, redirect, url_for, flash, current_app, send_file
from flask_login import login_required, current_user
from datetime import datetime

from backend.database import db
from backend.models import User, Exam, StudentExam, StudentAnswer
from backend.services.pdf_generator import generate_result_pdf

student_bp = Blueprint('student', __name__)


def _can_view_attempt(student_exam):
    """The student who sat it, or staff who can access the exam."""
    if student_exam.student_id == current_user.id:
        return True
    if current_user.role in ('faculty', 'admin'):
        from backend.routes.faculty import _faculty_can_access_exam
        return _faculty_can_access_exam(student_exam.exam)
    return False


@student_bp.route('/student/dashboard')
@login_required
def student_dashboard():
    if current_user.role != 'student':
        return redirect(url_for('auth.index'))
    
    # Close any of this student's attempts whose time ran out unattended
    from backend.services.exam_service import finalize_overdue_attempts
    finalize_overdue_attempts(student_id=current_user.id)

    from backend.services.student_dashboard_service import exam_rows, score_trend
    from backend.services import face_service

    my_exams = StudentExam.query.filter_by(student_id=current_user.id).all()
    attempted_ids = {se.exam_id for se in my_exams}

    # Open exams assigned to this student, plus any exam they have an attempt
    # on (so a finished exam stays listed after the instructor closes it)
    exams = [e for e in Exam.query.filter_by(is_active=True).all() if e.is_assigned]
    listed = {e.id for e in exams}
    exams += [se.exam for se in my_exams if se.exam and se.exam_id not in listed]
    rows = exam_rows(exams, my_exams)

    completed_exams = [se for se in my_exams if se.completed or se.status in ('submitted', 'completed')]
    completed_exams.sort(key=lambda se: se.submitted_at or datetime.min, reverse=True)

    # Only attempts whose results the instructor has published may reveal a score
    published_exams = [se for se in completed_exams if se.result_visible_to_student]
    hidden_results_count = sum(1 for se in completed_exams
                               if not se.result_visible_to_student and not se.is_invalidated)

    published_count = len(published_exams)
    avg_score = (sum(se.percentage or 0 for se in published_exams) / published_count) if published_count else 0
    passed_count = sum(1 for se in published_exams if se.passed)
    best_score = max([se.percentage or 0 for se in published_exams], default=0)

    # The leaderboard link is only useful when at least one of this student's
    # exams has both the leaderboard and its results opened up
    leaderboard_available = any(
        se.exam and se.exam.leaderboard_visible_to_students for se in completed_exams
    )

    # Only an attempt the student actually started is "in progress". Rows
    # pre-created at import are assignments, not attempts — resuming one would
    # skip start_exam and open the paper with the clock already run down.
    in_progress = next((r for r in rows if r['state'] == 'in_progress'), None)

    return render_template('student/dashboard.html',
                           rows=rows,
                           counts={s: sum(1 for r in rows if r['state'] == s)
                                   for s in ('open', 'upcoming', 'in_progress')},
                           completed_exams=completed_exams,
                           published_count=published_count,
                           hidden_results_count=hidden_results_count,
                           leaderboard_available=leaderboard_available,
                           total_exams_taken=len(completed_exams),
                           avg_score=avg_score,
                           passed_count=passed_count,
                           best_score=best_score,
                           trend=score_trend(published_exams),
                           face_registered=face_service.enrollment_locked(current_user),
                           in_progress=in_progress)

@student_bp.route('/student/exam/<int:student_exam_id>/result')
@login_required
def student_exam_result(student_exam_id):
    student_exam = StudentExam.query.get_or_404(student_exam_id)
    if not _can_view_attempt(student_exam):
        flash('Unauthorized access', 'error')
        return redirect(url_for('student.student_dashboard'))
        
    if not student_exam.completed:
        flash('Exam not yet completed', 'warning')
        return redirect(url_for('student.student_dashboard'))
        
    exam = student_exam.exam
    if current_user.role == 'student' and not student_exam.result_visible_to_student:
        if student_exam.is_invalidated:
            flash('This attempt was invalidated by your instructor.', 'warning')
        else:
            flash('Your instructor has not published the results for this exam yet.', 'info')
        return redirect(url_for('student.student_dashboard'))

    review = _answer_review(student_exam)
    counts = {k: sum(1 for r in review if r['status'] == k) for k in ('correct', 'incorrect', 'skipped')}

    return render_template('student/result.html',
                           student_exam=student_exam,
                           exam=exam,
                           review=review,
                           counts=counts)


def _answer_review(student_exam):
    """Every question on the paper, in the order this student saw it, with
    their answer. Questions they skipped have no answer row, so they are
    listed from the exam's questions rather than from the answers."""
    from backend.models import Question
    questions = Question.query.filter_by(exam_id=student_exam.exam_id).order_by(Question.id).all()
    order = student_exam.get_question_order() or []
    if order:
        position = {qid: i for i, qid in enumerate(order)}
        questions.sort(key=lambda q: position.get(q.id, len(order)))
    answers = {a.question_id: a for a in
               StudentAnswer.query.filter_by(student_exam_id=student_exam.id)}

    review = []
    for q in questions:
        a = answers.get(q.id)
        chosen = (a.selected_answer or '').strip().upper() if a else ''
        status = 'skipped' if not chosen else ('correct' if a.is_correct else 'incorrect')
        options = [(letter, text) for letter, text in
                   (('A', q.option_a), ('B', q.option_b), ('C', q.option_c), ('D', q.option_d)) if text]
        review.append({'question': q, 'chosen': chosen, 'status': status, 'options': options,
                       'correct': (q.correct_answer or '').strip().upper()})
    return review


@student_bp.route('/student/<int:student_id>/profile')
@login_required
def student_profile(student_id):
    # Staff use the faculty profile, which is scoped to the students and exams
    # they manage; this route would otherwise show any student's whole record
    if current_user.role in ('faculty', 'admin'):
        return redirect(url_for('faculty.faculty_student_profile', student_id=student_id))
    if student_id != current_user.id:
        return redirect(url_for('auth.index'))

    from backend.services import face_service
    student = current_user
    attempts = StudentExam.query.filter_by(student_id=student.id).all()
    finished = [se for se in attempts if se.completed or se.status in ('submitted', 'completed')]
    finished.sort(key=lambda se: se.submitted_at or datetime.min, reverse=True)
    return render_template('student/student_profile.html', student=student,
                           student_exams=finished,
                           face_registered=face_service.enrollment_locked(student))


@student_bp.route('/student/exam/<int:student_exam_id>/download-pdf')
@login_required
def download_result_pdf(student_exam_id):
    student_exam = StudentExam.query.get_or_404(student_exam_id)
    if not _can_view_attempt(student_exam):
        return "Unauthorized", 403

    # A hidden result must not be downloadable either
    if current_user.role == 'student' and not student_exam.result_visible_to_student:
        flash('Your instructor has not published the results for this exam yet.', 'info')
        return redirect(url_for('student.student_dashboard'))

    exam = student_exam.exam
    student = student_exam.student
    answers = StudentAnswer.query.filter_by(student_exam_id=student_exam.id).all()
    answers_with_questions = [{'answer': a, 'question': a.question} for a in answers]

    pdf_buffer = generate_result_pdf(student_exam, exam, student, answers_with_questions)

    safe_title = ''.join(c if c.isalnum() or c in '-_ ' else '_' for c in (exam.title or 'exam')).strip() or 'exam'
    filename = f"{safe_title.replace(' ', '_')}_{student.prn_number or student.username}_result.pdf"

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )

@student_bp.route('/global_leaderboard')
@login_required
def global_leaderboard():
    from flask import request
    # 1. Fetch filters
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    batch_filter = request.args.get('batch')
    
    is_staff = current_user.role in ('faculty', 'admin')

    # 2. Base Query for StudentExams
    # Students only see exams the instructor opened up: leaderboard ON *and*
    # results published (the board shows marks, so it is a result view too).
    # Staff always see the full picture.
    query = StudentExam.query.join(Exam).filter(
        StudentExam.status == 'submitted',
        StudentExam.is_invalidated == False
    )
    if not is_staff:
        query = query.filter(
            Exam.show_leaderboard == True,
            Exam.results_published == True
        )
    
    # Apply date filters if provided
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(StudentExam.submitted_at >= start_date)
        except ValueError: pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            query = query.filter(StudentExam.submitted_at <= end_date)
        except ValueError: pass
        
    all_submitted = query.all()
    
    # 3. Aggregate data by student
    student_data = {}
    for se in all_submitted:
        if se.student is None:          # account removed; never crash the board
            continue
        # Check batch filter at student level
        if batch_filter and se.student.batch != batch_filter:
            continue
            
        s_id = se.student_id
        if s_id not in student_data:
            student_data[s_id] = {
                'student_id': s_id,
                'name': se.student.full_name or se.student.username,
                'prn': se.student.prn_number,
                'exams_attempted': 0,
                'total_questions': 0,
                'marks_obtained': 0,
                'possible_marks': 0,
                'percentage': 0,
                'batch': se.student.batch
            }
        
        student_data[s_id]['exams_attempted'] += 1
        student_data[s_id]['marks_obtained'] += (se.score or 0)
        student_data[s_id]['possible_marks'] += (se.total_points or 0)
        # Assuming total_questions in template is count of answers
        student_data[s_id]['total_questions'] += len(se.answers)

    # 4. Finalize calculations
    leaderboard_list = []
    for s_id, data in student_data.items():
        data['percentage'] = round((data['marks_obtained'] / data['possible_marks'] * 100), 1) if data['possible_marks'] > 0 else 0
        leaderboard_list.append(data)
        
    # Sort by marks (desc)
    leaderboard_list.sort(key=lambda x: x['marks_obtained'], reverse=True)
    
    # Calculate behind_marks
    if leaderboard_list:
        max_marks = leaderboard_list[0]['marks_obtained']
        for item in leaderboard_list:
            item['behind_marks'] = round(max_marks - item['marks_obtained'], 1)
    
    # 5. Get all unique batches for filter
    batches = db.session.query(User.batch).filter(User.role == 'student', User.batch != None).distinct().all()
    batches = [b[0] for b in batches]
    
    # Nothing at all to show a student => the board is closed rather than empty
    locked = (not is_staff) and not leaderboard_list

    return render_template('leaderboard.html', 
                         leaderboard=leaderboard_list, 
                         batches=batches,
                         locked=locked,
                         is_staff=is_staff,
                         current_student_id=current_user.id if current_user.role == 'student' else None,
                         start_date=start_date_str,
                         end_date=end_date_str)
