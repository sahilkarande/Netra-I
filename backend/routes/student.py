from flask import Blueprint, render_template, redirect, url_for, flash, current_app, send_file
from flask_login import login_required, current_user
from datetime import datetime

from backend.database import db
from backend.models import User, Exam, StudentExam
from backend.services.pdf_generator import generate_result_pdf

student_bp = Blueprint('student', __name__)

@student_bp.route('/student/dashboard')
@login_required
def student_dashboard():
    if current_user.role != 'student':
        return redirect(url_for('auth.index'))
    
    # Get active and completed exams
    available_exams = Exam.query.filter_by(is_active=True).all()
    my_exams = StudentExam.query.filter_by(student_id=current_user.id).all()
    
    return render_template('student/dashboard.html', 
                         available_exams=available_exams, 
                         my_exams=my_exams)

@student_bp.route('/student/exam/<int:student_exam_id>/result')
@login_required
def student_exam_result(student_exam_id):
    student_exam = StudentExam.query.get_or_404(student_exam_id)
    if student_exam.student_id != current_user.id and current_user.role not in ('faculty', 'admin'):
        flash('Unauthorized access', 'error')
        return redirect(url_for('student.student_dashboard'))
        
    if not student_exam.completed:
        flash('Exam not yet completed', 'warning')
        return redirect(url_for('student.student_dashboard'))
        
    return render_template('student/exam_result.html', student_exam=student_exam)

@student_bp.route('/student/<int:student_id>/profile')
@login_required
def student_profile(student_id):
    if student_id != current_user.id and current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))
    student = User.query.get_or_404(student_id)
    return render_template('student/profile.html', student=student)

@student_bp.route('/student/exam/<int:student_exam_id>/download-pdf')
@login_required
def download_result_pdf(student_exam_id):
    student_exam = StudentExam.query.get_or_404(student_exam_id)
    if student_exam.student_id != current_user.id and current_user.role not in ('faculty', 'admin'):
        return "Unauthorized", 403
        
    pdf_path = generate_result_pdf(student_exam)
    return send_file(pdf_path, as_attachment=True)

@student_bp.route('/global_leaderboard')
@login_required
def global_leaderboard():
    from flask import request
    # 1. Fetch filters
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    batch_filter = request.args.get('batch')
    
    # 2. Base Query for StudentExams
    # Only include exams where leaderboard is enabled and results are submitted
    query = StudentExam.query.join(Exam).filter(
        StudentExam.status == 'submitted',
        Exam.show_leaderboard == True
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
    
    return render_template('leaderboard.html', 
                         leaderboard=leaderboard_list, 
                         batches=batches,
                         start_date=start_date_str,
                         end_date=end_date_str)
