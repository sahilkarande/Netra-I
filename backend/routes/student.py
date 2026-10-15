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
    
    # Get active and completed exams
    available_exams = Exam.query.filter_by(is_active=True).all()
    my_exams = StudentExam.query.filter_by(student_id=current_user.id).all()
    
    completed_exams = [se for se in my_exams if se.completed or se.status in ('submitted', 'completed')]
    attempted_exam_ids = {se.exam_id for se in my_exams if se.completed or se.status in ('submitted', 'completed') or se.started_at is not None}
    
    for exam in available_exams:
        exam.attempted_by_current_user = (exam.id in attempted_exam_ids)

    # Only attempts whose results the instructor has published may reveal a score
    published_exams = [se for se in completed_exams if se.result_visible_to_student]
    hidden_results_count = len(completed_exams) - len(published_exams)

    total_exams_taken = len(completed_exams)
    published_count = len(published_exams)
    avg_score = (sum(se.percentage or 0 for se in published_exams) / published_count) if published_count else 0
    passed_count = sum(1 for se in published_exams if se.passed)
    best_score = max([se.percentage or 0 for se in published_exams], default=0)

    # The leaderboard link is only useful when at least one of this student's
    # exams has both the leaderboard and its results opened up
    leaderboard_available = any(
        se.exam and se.exam.leaderboard_visible_to_students for se in completed_exams
    )

    in_progress = StudentExam.query.filter_by(student_id=current_user.id, completed=False).first()
    
    return render_template('student/dashboard.html', 
                         available_exams=available_exams, 
                         my_exams=my_exams,
                         completed_exams=completed_exams,
                         published_exams=published_exams,
                         visible_completed=published_exams,
                         published_count=published_count,
                         hidden_results_count=hidden_results_count,
                         leaderboard_available=leaderboard_available,
                         total_exams_taken=total_exams_taken,
                         avg_score=avg_score,
                         passed_count=passed_count,
                         best_score=best_score,
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

    answers = StudentAnswer.query.filter_by(student_exam_id=student_exam.id).all()
    answers_with_questions = [{'answer': a, 'question': a.question} for a in answers]

    return render_template('student/result.html', 
                         student_exam=student_exam,
                         exam=exam,
                         answers_with_questions=answers_with_questions)

@student_bp.route('/student/<int:student_id>/profile')
@login_required
def student_profile(student_id):
    if student_id != current_user.id and current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))
    student = User.query.get_or_404(student_id)
    student_exams = StudentExam.query.filter_by(student_id=student_id).all()
    if current_user.role == 'student':
        # A student's own profile must not leak scores that are not published yet
        student_exams = [se for se in student_exams if se.result_visible_to_student]
    return render_template('student/student_profile.html', student=student,
                           student_exams=student_exams)

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
