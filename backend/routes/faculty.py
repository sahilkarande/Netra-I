import os
import csv
import io
import json
import pandas as pd
import shutil
import traceback
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, current_app
from flask_login import login_required, current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash

from backend.database import db
from backend.models import User, Exam, Question, StudentExam, StudentAnswer, ExamViolation
from backend.services.pdf_generator import generate_batch_report_pdf
from backend.services.proctor_service import PROCTOR_INSTANCES, _heartbeat_registry, _last_frame_processed
from backend.services.exam_service import assign_shuffle

faculty_bp = Blueprint('faculty', __name__)

def _faculty_can_access_student(student_id):
    """Check if faculty created an exam that this student has taken"""
    return db.session.query(StudentExam).join(Exam).filter(
        Exam.creator_id == current_user.id,
        StudentExam.student_id == student_id
    ).first() is not None

@faculty_bp.route('/faculty/dashboard')
@login_required
def faculty_dashboard():
    if current_user.role != 'faculty' and current_user.role != 'admin':
        return redirect(url_for('auth.index'))
    
    if current_user.role == 'admin':
        exams = Exam.query.order_by(Exam.created_at.desc()).all()
    else:
        exams = Exam.query.filter_by(creator_id=current_user.id).order_by(Exam.created_at.desc()).all()
    
    exam_ids = [e.id for e in exams]
    
    # Calculate statistics for the dashboard
    total_attempts = StudentExam.query.filter(StudentExam.exam_id.in_(exam_ids)).count() if exam_ids else 0
    unique_students = db.session.query(func.count(func.distinct(StudentExam.student_id))).filter(StudentExam.exam_id.in_(exam_ids)).scalar() or 0
    
    submitted_exams = StudentExam.query.filter(StudentExam.exam_id.in_(exam_ids), StudentExam.status == 'submitted').all()
    if submitted_exams:
        avg_class_score = round(sum(se.percentage or 0 for se in submitted_exams) / len(submitted_exams), 1)
        passed_count = sum(1 for se in submitted_exams if se.passed)
        pass_rate = round((passed_count / len(submitted_exams)) * 100, 1)
    else:
        avg_class_score = 0
        pass_rate = 0
        
    results_visible_count = sum(1 for e in exams if e.results_published)
    
    # Get recent submissions
    recent_activities = StudentExam.query.filter(StudentExam.exam_id.in_(exam_ids)).order_by(StudentExam.submitted_at.desc()).limit(10).all()
        
    return render_template('faculty/dashboard.html', 
                         exams=exams,
                         total_attempts=total_attempts,
                         unique_students=unique_students,
                         avg_class_score=avg_class_score,
                         pass_rate=pass_rate,
                         results_visible_count=results_visible_count,
                         recent_activities=recent_activities)

@faculty_bp.route('/faculty/set_leaderboard', methods=['POST'])
@login_required
def faculty_set_leaderboard():
    if current_user.role != 'faculty' and current_user.role != 'admin':
        return jsonify({"success": False}), 403
    data = request.get_json()
    exam_id = data.get('exam_id')
    enabled = data.get('enabled') or data.get('show_leaderboard')
    
    if exam_id:
        exam = Exam.query.get(exam_id)
        if exam and (exam.creator_id == current_user.id or current_user.role == 'admin'):
            exam.show_leaderboard = enabled
            db.session.commit()
            return jsonify({"success": True})
    else:
        # Global toggle for all faculty exams
        exams = Exam.query.filter_by(creator_id=current_user.id).all()
        for e in exams:
            e.show_leaderboard = enabled
        db.session.commit()
        return jsonify({"success": True})
        
    return jsonify({"success": False}), 404

@faculty_bp.route('/faculty/set_results_visibility', methods=['POST'])
@login_required
def faculty_set_results_visibility():
    if current_user.role != 'faculty' and current_user.role != 'admin':
        return jsonify({"success": False}), 403
    data = request.get_json()
    exam_id = data.get('exam_id')
    visible = data.get('visible') if 'visible' in data else data.get('show_results')
    
    if exam_id:
        exam = Exam.query.get(exam_id)
        if exam and (exam.creator_id == current_user.id or current_user.role == 'admin'):
            exam.results_published = visible
            db.session.commit()
            return jsonify({"success": True})
    else:
        # Global toggle for all faculty exams
        exams = Exam.query.filter_by(creator_id=current_user.id).all()
        for e in exams:
            e.results_published = visible
        db.session.commit()
        return jsonify({"success": True})
        
    return jsonify({"success": False}), 404

@faculty_bp.route('/faculty/exam/create', methods=['GET', 'POST'])
@login_required
def create_exam():
    if current_user.role != 'faculty' and current_user.role != 'admin':
        return redirect(url_for('auth.index'))
        
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        duration = int(request.form.get('duration', 60))
        passing_score = float(request.form.get('passing_score', 50.0))
        
        proctor_settings = {
            'camera_enabled': request.form.get('camera_enabled') == 'on',
            'face_detection': request.form.get('face_detection') == 'on',
            'head_pose': request.form.get('head_pose') == 'on',
            'object_detection': request.form.get('object_detection') == 'on',
            'voice_detection': request.form.get('voice_detection') == 'on',
            'tab_switch': request.form.get('tab_switch') == 'on',
            'max_warnings': int(request.form.get('max_warnings', 25)),
            'max_tab_switches': int(request.form.get('max_tab_switches', 5))
        }

        new_exam = Exam(
            title=title,
            description=description,
            duration_minutes=duration,
            passing_score=passing_score,
            creator_id=current_user.id,
            proctor_settings=json.dumps(proctor_settings)
        )
        
        db.session.add(new_exam)
        db.session.commit()
        flash('Exam created successfully! Now upload questions.', 'success')
        return redirect(url_for('faculty.upload_questions', exam_id=new_exam.id))
        
    return render_template('faculty/create_exam.html')

@faculty_bp.route('/faculty/import_students', methods=['POST'])
@login_required
def import_students():
    if current_user.role != 'faculty' and current_user.role != 'admin':
        flash('Access denied', 'error')
        return redirect(url_for('student.student_dashboard'))

    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('faculty.faculty_students'))

    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[-1].lower()

    if ext not in ('csv', 'xlsx', 'xls', 'json'):
        flash('Invalid file format. Please upload CSV, Excel, or JSON file.', 'error')
        return redirect(url_for('faculty.faculty_students'))

    try:
        if ext == 'csv':
            try:
                df = pd.read_csv(file, encoding='utf-8', dtype=str)
            except UnicodeDecodeError:
                file.stream.seek(0)
                df = pd.read_csv(file, encoding='utf-8-sig', dtype=str)
        elif ext in ('xlsx', 'xls'):
            df = pd.read_excel(file, dtype=str)
        elif ext == 'json':
            data = json.load(file)
            if not isinstance(data, list):
                flash('JSON must be an array of student objects.', 'error')
                return redirect(url_for('faculty.faculty_students'))
            df = pd.DataFrame(data, dtype=str)

        if df.empty:
            flash('The file is empty or has no data rows.', 'error')
            return redirect(url_for('faculty.faculty_students'))

        df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
        added, skipped = 0, 0
        errors = []

        def clean_string(value):
            if pd.isna(value) or value == '': return ''
            return str(value).strip()

        exam_id = request.args.get('exam_id', type=int)
        exam = Exam.query.get(exam_id) if exam_id else None

        for index, row in df.iterrows():
            try:
                email = clean_string(row.get('email')).lower()
                if not email:
                    errors.append(f"Row {index + 2}: Missing required email")
                    skipped += 1
                    continue

                existing = User.query.filter_by(email=email).first()
                if existing:
                    skipped += 1
                    continue

                prn_raw = clean_string(row.get('prn_number'))
                prn_clean = prn_raw.replace('.', '').replace(' ', '') if prn_raw else ''

                password = clean_string(row.get('password')) or 'Student@123'
                hashed_pw = generate_password_hash(password)

                student = User(
                    username=clean_string(row.get('username')) or email.split('@')[0],
                    email=email,
                    full_name=clean_string(row.get('full_name')),
                    prn_number=prn_clean,
                    roll_id=clean_string(row.get('roll_id')),
                    password_hash=hashed_pw,
                    role='student',
                    is_verified=True
                )
                db.session.add(student)
                db.session.commit()

                if exam:
                    student_exam = StudentExam(student_id=student.id, exam_id=exam.id)
                    db.session.add(student_exam)
                    db.session.commit()
                    assign_shuffle(student_exam)

                added += 1
            except Exception as e:
                errors.append(f"Row {index + 2}: {str(e)}")
                skipped += 1
                db.session.rollback()

        db.session.commit()
        flash(f"✅ {added} added | ⚠️ {skipped} skipped", 'success')
        return redirect(url_for('faculty.faculty_students'))

    except Exception as e:
        db.session.rollback()
        flash(f"Error processing file: {str(e)}", 'error')
        return redirect(url_for('faculty.faculty_students'))

@faculty_bp.route('/faculty/exam/<int:exam_id>/delete', methods=['POST'])
@login_required
def delete_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        flash('Unauthorized access', 'error')
        return redirect(url_for('faculty.faculty_dashboard'))
        
    db.session.delete(exam)
    db.session.commit()
    flash('Exam deleted successfully', 'success')
    return redirect(url_for('faculty.faculty_dashboard'))

@faculty_bp.route('/faculty/exam/<int:exam_id>/upload', methods=['GET', 'POST'])
@login_required
def upload_questions(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        return redirect(url_for('faculty.faculty_dashboard'))
        
    if request.method == 'POST':
        file = request.files.get('file')
        if not file:
            flash('No file selected', 'error')
            return redirect(request.url)
            
        filename = secure_filename(file.filename).lower()
        try:
            questions_data = []
            if filename.endswith('.csv'):
                stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                questions_data = list(csv.DictReader(stream))
            elif filename.endswith('.json'):
                questions_data = json.loads(file.stream.read().decode("UTF8"))
            elif filename.endswith('.xlsx') or filename.endswith('.xls'):
                import pandas as pd
                df = pd.read_excel(file.stream)
                questions_data = df.to_dict(orient='records')
            
            if not questions_data:
                flash('No valid data found in file', 'error')
                return redirect(request.url)

            # Check for questions count
            count = 0
            for row in questions_data:
                # Robust column mapping
                question_text = row.get('question') or row.get('question_text') or row.get('text')
                if not question_text: continue
                
                a = row.get('a') or row.get('option_a') or row.get('option1') or row.get('Option A')
                b = row.get('b') or row.get('option_b') or row.get('option2') or row.get('Option B')
                c = row.get('c') or row.get('option_c') or row.get('option3') or row.get('Option C')
                d = row.get('d') or row.get('option_d') or row.get('option4') or row.get('Option D')
                ans = str(row.get('answer') or row.get('correct_answer') or row.get('correct') or 'A').strip().upper()
                points = float(row.get('points') or row.get('score') or 1.0)

                q = Question(
                    exam_id=exam_id,
                    question_text=question_text,
                    option_a=str(a),
                    option_b=str(b),
                    option_c=str(c),
                    option_d=str(d),
                    correct_answer=ans,
                    points=points
                )
                db.session.add(q)
                count += 1
            
            db.session.commit()
            flash(f'Successfully uploaded {count} questions!', 'success')
            return redirect(url_for('faculty.view_exam', exam_id=exam_id))
        except Exception as e:
            db.session.rollback()
            traceback.print_exc()
            flash(f'Error processing file: {str(e)}', 'error')
            return redirect(request.url)
            
    return render_template('faculty/upload_questions.html', exam=exam)

@faculty_bp.route('/faculty/exam/<int:exam_id>')
@login_required
def view_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        return redirect(url_for('faculty.faculty_dashboard'))
    
    # Get all students for the selection modal
    all_students = User.query.filter_by(role='student').order_by(User.full_name).all()
    
    # Get unique batches
    all_batches = db.session.query(User.batch).filter(User.role == 'student', User.batch != None).distinct().all()
    all_batches = sorted([b[0] for b in all_batches if b[0]])
    
    # Map which exams students are already in for this specific exam
    student_exam_map = {se.student_id: se for se in exam.student_exams}
    
    # Find other active exams for these students
    other_active_enrollments = db.session.query(StudentExam, Exam.title).join(Exam).filter(
        StudentExam.exam_id != exam_id,
        Exam.status == 'active',
        Exam.force_ended == False
    ).all()
    
    other_exam_map = {}
    for se, title in other_active_enrollments:
        if se.student_id not in other_exam_map:
            other_exam_map[se.student_id] = []
        other_exam_map[se.student_id].append(title)
    
    return render_template('faculty/view_exam.html', 
                         exam=exam, 
                         all_students=all_students, 
                         all_batches=all_batches,
                         student_exam_map=student_exam_map,
                         other_exam_map=other_exam_map)

@faculty_bp.route('/faculty/exam/<int:exam_id>/analytics')
@login_required
def exam_analytics(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        return redirect(url_for('faculty.faculty_dashboard'))
    
    student_exams = StudentExam.query.filter_by(exam_id=exam_id).all()
    
    total_attempts = len(student_exams)
    completed_exams = [se for se in student_exams if se.status == 'completed' or se.status == 'submitted']
    passed = sum(1 for se in completed_exams if se.passed)
    failed = sum(1 for se in completed_exams if not se.passed)
    
    avg_score = sum(se.percentage or 0 for se in completed_exams) / len(completed_exams) if completed_exams else 0
    avg_time = sum(se.time_taken_minutes or 0 for se in completed_exams) / len(completed_exams) if completed_exams else 0
    
    flagged_exams = [se for se in student_exams if (se.total_violations or 0) >= 3 or (se.tab_switch_count or 0) > (exam.max_tab_switches or 5)]
    
    # Question-wise performance
    question_stats = []
    for q in exam.questions:
        q_answers = [a for a in q.answers if a.student_exam.exam_id == exam_id]
        total_q = len(q_answers)
        correct_q = sum(1 for a in q_answers if a.is_correct)
        accuracy = (correct_q / total_q * 100) if total_q > 0 else 0
        question_stats.append({
            'question': q.question_text,
            'total_answers': total_q,
            'correct_answers': correct_q,
            'accuracy': accuracy
        })
    
    return render_template('faculty/analytics.html', 
                         exam=exam, 
                         student_exams=student_exams,
                         total_attempts=total_attempts,
                         passed=passed,
                         failed=failed,
                         avg_score=avg_score,
                         avg_time=avg_time,
                         flagged_exams=flagged_exams,
                         question_stats=question_stats)

@faculty_bp.route('/api/faculty/publish-results/<int:exam_id>', methods=['POST'])
@login_required
def faculty_publish_results(exam_id):
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"error": "Access denied"}), 403

    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        return jsonify({"error": "Not your exam"}), 403

    student_exams = StudentExam.query.filter_by(exam_id=exam_id).all()
    deleted_images = 0
    deleted_dirs = set()

    for se in student_exams:
        violations = ExamViolation.query.filter_by(student_exam_id=se.id).all()
        for v in violations:
            if hasattr(v, 'evidence_path') and v.evidence_path:
                try:
                    if os.path.exists(v.evidence_path):
                        os.remove(v.evidence_path)
                        deleted_images += 1
                    parent = os.path.dirname(v.evidence_path)
                    if parent: deleted_dirs.add(parent)
                except Exception:
                    pass
                v.evidence_path = None

        if se.id in PROCTOR_INSTANCES:
            try: del PROCTOR_INSTANCES[se.id]
            except: pass
        _heartbeat_registry.pop(se.id, None)
        _last_frame_processed.pop(se.id, None)

    for d in deleted_dirs:
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                parent = os.path.dirname(d)
                if os.path.isdir(parent) and not os.listdir(parent): os.rmdir(parent)
        except:
            pass

    exam.results_published = True
    exam.results_published_at = datetime.utcnow()
    exam.results_published_by = current_user.id
    db.session.commit()

    return jsonify({"success": True, "message": f"Results published! {deleted_images} images cleaned."})

@faculty_bp.route('/faculty/students')
@login_required
def faculty_students():
    if current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))
        
    q = request.args.get('q', '')
    batch = request.args.get('batch', '')
    dept = request.args.get('department', '')
    sort = request.args.get('sort', '')
    verified = request.args.get('verified', '')

    query = User.query.filter_by(role='student')

    if q:
        search = f"%{q}%"
        query = query.filter(db.or_(
            User.full_name.ilike(search),
            User.username.ilike(search),
            User.email.ilike(search),
            User.prn_number.ilike(search),
            User.roll_id.ilike(search)
        ))
    if batch:
        query = query.filter_by(batch=batch)
    if dept:
        query = query.filter_by(department=dept)
    if verified == 'true':
        query = query.filter_by(is_verified=True)
    elif verified == 'false':
        query = query.filter_by(is_verified=False)

    if sort == 'name':
        query = query.order_by(User.full_name.asc())
    elif sort == 'batch':
        query = query.order_by(User.batch.desc())
    else:
        query = query.order_by(User.id.desc())

    students = query.all()
    
    # Get unique batches and departments for filters
    batches = db.session.query(User.batch).filter(User.role == 'student', User.batch != None, User.batch != '').distinct().all()
    batches = [b[0] for b in batches]
    
    departments = db.session.query(User.department).filter(User.role == 'student', User.department != None, User.department != '').distinct().all()
    departments = [d[0] for d in departments]

    return render_template('faculty/manage_students.html', 
                         students=students, 
                         batches=batches, 
                         departments=departments)

@faculty_bp.route('/faculty/student_report', methods=['GET'])
@login_required
def faculty_student_report():
    if current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))
        
    # Get all distinct batches
    batches_query = db.session.query(User.batch).filter(User.role == 'student', User.batch != None, User.batch != '').distinct().all()
    batches = sorted([b[0] for b in batches_query])
    
    # Get exams for the current faculty (or all if admin)
    if current_user.role == 'admin':
        exams = Exam.query.order_by(Exam.created_at.desc()).all()
    else:
        exams = Exam.query.filter_by(creator_id=current_user.id).order_by(Exam.created_at.desc()).all()
        
    selected_batch = request.args.get('batch')
    selected_exam_ids_str = request.args.getlist('exam_ids')
    selected_exam_ids = [int(eid) for eid in selected_exam_ids_str if eid.isdigit()]

    report_data = []
    exam_titles = []
    summary = {
        'total_students': 0,
        'appeared': 0,
        'passed': 0,
        'failed': 0,
        'absent': 0,
        'average_marks': 0.0,
        'pass_percentage': 0.0
    }
    
    if selected_batch and selected_exam_ids:
        # Get students in the selected batch
        students = User.query.filter_by(role='student', batch=selected_batch).all()
        summary['total_students'] = len(students)
        
        # Get selected exam objects in order
        selected_exams = []
        for eid in selected_exam_ids:
            for ex in exams:
                if ex.id == eid:
                    selected_exams.append(ex)
                    exam_titles.append(ex.title)
                    break
        
        total_marks_all = 0
        
        for student in students:
            student_row = {
                'prn_number': student.prn_number,
                'full_name': student.full_name or student.username,
                'exam_marks': [],
                'total_marks': 0.0,
                'max_possible': 0.0,
                'percentage': 0.0,
                'status': 'absent'
            }
            
            appeared_in_any = False
            passed_all = True
            
            for exam in selected_exams:
                # Find StudentExam for this student and exam
                student_exam = StudentExam.query.filter_by(student_id=student.id, exam_id=exam.id).first()
                
                if student_exam and student_exam.status in ['submitted', 'completed']:
                    appeared_in_any = True
                    score = student_exam.score or 0.0
                    student_row['exam_marks'].append(round(score, 2))
                    student_row['total_marks'] += score
                    student_row['max_possible'] += (student_exam.total_points or 100.0) # Using total_points if available
                    
                    if not student_exam.passed:
                        passed_all = False
                else:
                    student_row['exam_marks'].append('Absent')
                    passed_all = False
                    
            if appeared_in_any:
                summary['appeared'] += 1
                if student_row['max_possible'] > 0:
                    student_row['percentage'] = (student_row['total_marks'] / student_row['max_possible']) * 100
                
                if student_row['percentage'] >= 50:
                    student_row['status'] = 'pass'
                    summary['passed'] += 1
                else:
                    student_row['status'] = 'fail'
                    summary['failed'] += 1
                    
                total_marks_all += student_row['percentage']
            else:
                summary['absent'] += 1
                
            report_data.append(student_row)
            
        if summary['appeared'] > 0:
            summary['average_marks'] = total_marks_all / summary['appeared']
            summary['pass_percentage'] = (summary['passed'] / summary['appeared']) * 100
            
    return render_template('faculty/student_report.html',
                         batches=batches,
                         exams=exams,
                         selected_batch=selected_batch,
                         selected_exam_ids=selected_exam_ids,
                         exam_titles=exam_titles,
                         report_data=report_data,
                         summary=summary)

@faculty_bp.route('/faculty/student_report/pdf', methods=['POST'])
@login_required
def faculty_student_report_pdf():
    # Placeholder for PDF generation
    return "PDF Report generation triggered"

@faculty_bp.route('/faculty/student/<int:student_id>/profile')
@login_required
def faculty_student_profile(student_id):
    if current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))
    student = User.query.get_or_404(student_id)
    student_exams = StudentExam.query.filter_by(student_id=student_id).all()
    return render_template('student/student_profile.html', student=student, student_exams=student_exams)

@faculty_bp.route('/faculty/student/<int:student_id>/edit', methods=['GET', 'POST'])
@login_required
def faculty_edit_student(student_id):
    student = User.query.get_or_404(student_id)
    if request.method == 'POST':
        student.full_name = request.form.get('full_name')
        student.email = request.form.get('email')
        db.session.commit()
        flash('Student updated successfully', 'success')
        return redirect(url_for('faculty.faculty_students'))
    return render_template('faculty/edit_student.html', student=student)

@faculty_bp.route('/faculty/download_template')
@login_required
def download_template():
    return send_file('static/templates/student_import_template.csv')

@faculty_bp.route('/faculty/change-student-password/<int:student_id>', methods=['POST'])
@login_required
def faculty_change_student_password(student_id):
    if current_user.role not in ['faculty', 'admin']:
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    student = User.query.get_or_404(student_id)
    student.password_hash = generate_password_hash("123456789", method='pbkdf2:sha256')
    db.session.commit()
    return jsonify({"success": True, "message": "Password reset successfully"})
@faculty_bp.route('/api/faculty/batch-create-exams', methods=['POST'])
@login_required
def batch_create_exams():
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        exams_data = json.loads(request.form.get('exams_json', '[]'))
        created_exams = []

        for i, exam_info in enumerate(exams_data):
            title = exam_info.get('title')
            if not title: continue

            # Create the exam
            new_exam = Exam(
                title=title,
                description=exam_info.get('description', ''),
                duration_minutes=int(exam_info.get('duration_minutes', 60)),
                passing_score=float(exam_info.get('passing_score', 50.0)),
                creator_id=current_user.id
            )
            db.session.add(new_exam)
            db.session.commit()

            # Handle file upload if present
            file_key = f'file_{i}'
            questions_count = 0
            if file_key in request.files:
                file = request.files[file_key]
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    if filename.endswith('.csv'):
                        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                        csv_input = csv.DictReader(stream)
                        for row in csv_input:
                            q = Question(
                                exam_id=new_exam.id,
                                question_text=row['question'],
                                option_a=row['a'],
                                option_b=row['b'],
                                option_c=row['c'],
                                option_d=row['d'],
                                correct_answer=row['answer'].upper(),
                                points=float(row.get('points', 1.0))
                            )
                            db.session.add(q)
                            questions_count += 1
                        db.session.commit()

            created_exams.append({
                "id": new_exam.id,
                "title": new_exam.title,
                "questions_count": questions_count
            })

        return jsonify({
            "success": True,
            "message": f"Successfully created {len(created_exams)} exams.",
            "created_exams": created_exams
        })

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
@faculty_bp.route('/faculty/delete_student/<int:student_id>', methods=['POST'])
@login_required
def delete_student(student_id):
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    student = User.query.get_or_404(student_id)
    if student.role != 'student':
        return jsonify({"success": False, "error": "Can only delete students"}), 400
        
    try:
        db.session.delete(student)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@faculty_bp.route('/faculty/delete_students', methods=['POST'])
@login_required
def delete_students():
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "error": "Access denied"}), 403
        
    data = request.get_json()
    student_ids = data.get('ids', [])
    
    if not student_ids:
        return jsonify({"success": False, "error": "No students selected"}), 400
        
    try:
        User.query.filter(User.id.in_(student_ids), User.role == 'student').delete(synchronize_session=False)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@faculty_bp.route('/faculty/export_students')
@login_required
def export_students():
    if current_user.role not in ('faculty', 'admin'):
        flash('Access denied', 'error')
        return redirect(url_for('auth.index'))
        
    # Get filters from request args
    q = request.args.get('q', '')
    batch = request.args.get('batch', '')
    dept = request.args.get('department', '')
    ids = request.args.getlist('ids')
    
    query = User.query.filter_by(role='student')
    
    if ids:
        query = query.filter(User.id.in_(ids))
    else:
        if q:
            search = f"%{q}%"
            query = query.filter(db.or_(
                User.full_name.ilike(search),
                User.username.ilike(search),
                User.email.ilike(search),
                User.prn_number.ilike(search),
                User.roll_id.ilike(search)
            ))
        if batch:
            query = query.filter_by(batch=batch)
        if dept:
            query = query.filter_by(department=dept)
            
    students = query.all()
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Full Name', 'Username', 'Email', 'PRN Number', 'Roll ID', 'Batch', 'Department', 'Verified'])
    
    for s in students:
        writer.writerow([
            s.full_name or '',
            s.username or '',
            s.email or '',
            s.prn_number or '',
            s.roll_id or '',
            s.batch or '',
            s.department or '',
            'Yes' if s.is_verified else 'No'
        ])
        
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"students_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
@faculty_bp.route('/faculty/exam/<int:exam_id>/preview_questions', methods=['POST'])
@login_required
def preview_questions(exam_id):
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"error": "Access denied"}), 403
    
    file = request.files.get('file') or request.files.get('csv_file')
    if not file:
        return jsonify({"error": "No file uploaded"}), 400
        
    filename = secure_filename(file.filename).lower()
    try:
        rows = []
        if filename.endswith('.csv'):
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            rows = list(csv.DictReader(stream))
        elif filename.endswith('.json'):
            rows = json.loads(file.stream.read().decode("UTF8"))
            if not isinstance(rows, list): rows = []
        elif filename.endswith('.xlsx') or filename.endswith('.xls'):
            import pandas as pd
            df = pd.read_excel(file.stream)
            rows = df.to_dict(orient='records')
            
        return jsonify({"rows": rows})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Invalid file format"}), 400

@faculty_bp.route('/faculty/exam/<int:exam_id>/force_end', methods=['POST'])
@login_required
def faculty_force_end_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        flash('Unauthorized', 'error')
        return redirect(url_for('faculty.faculty_dashboard'))
        
    # Force end all active student exams
    active_exams = StudentExam.query.filter_by(exam_id=exam_id, status='started').all()
    for se in active_exams:
        se.status = 'submitted'
        se.submitted_at = datetime.utcnow()
        # Trigger score calculation
        from backend.services.exam_service import calculate_student_score
        calculate_student_score(se.id)
        
    db.session.commit()
    flash(f'Force ended {len(active_exams)} active attempts.', 'success')
    return redirect(url_for('faculty.view_exam', exam_id=exam_id))

@faculty_bp.route('/faculty/exam/<int:exam_id>/force_end_student/<int:student_exam_id>', methods=['POST'])
@login_required
def faculty_force_end_student(exam_id, student_exam_id):
    se = StudentExam.query.get_or_404(student_exam_id)
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    se.status = 'submitted'
    se.submitted_at = datetime.utcnow()
    from backend.services.exam_service import calculate_student_score
    calculate_student_score(se.id)
    db.session.commit()
    return redirect(url_for('faculty.view_exam', exam_id=exam_id))
@faculty_bp.route('/faculty/exam/<int:exam_id>/generate_key', methods=['POST'])
@login_required
def generate_exam_key(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    import secrets
    import string
    alphabet = string.ascii_uppercase + string.digits
    key = ''.join(secrets.choice(alphabet) for i in range(6))
    
    exam.access_key = key
    db.session.commit()
    return jsonify({"success": True, "key": key})

@faculty_bp.route('/faculty/exam/<int:exam_id>/clear_key', methods=['POST'])
@login_required
def clear_exam_key(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    exam.access_key = None
    db.session.commit()
    return jsonify({"success": True})

@faculty_bp.route('/faculty/exam/<int:exam_id>/update_access', methods=['POST'])
@login_required
def update_exam_access(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        flash('Unauthorized', 'error')
        return redirect(url_for('faculty.faculty_dashboard'))
        
    exam.is_active = request.form.get('is_active') == 'true'
    db.session.commit()
    flash('Exam access updated.', 'success')
    return redirect(url_for('faculty.view_exam', exam_id=exam_id))

@faculty_bp.route('/faculty/exam/<int:exam_id>/extend_time', methods=['POST'])
@login_required
def faculty_extend_exam_time(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.creator_id != current_user.id and current_user.role != 'admin':
        flash('Unauthorized', 'error')
        return redirect(url_for('faculty.faculty_dashboard'))
        
    minutes = int(request.form.get('minutes') or request.form.get('extra_minutes') or 0)
    if minutes > 0:
        # Extend for all currently active students
        active_exams = StudentExam.query.filter_by(exam_id=exam_id, status='started').all()
        # Note: If we don't have a per-student end_time, we might need to store it
        # For now, let's just log it or update the exam duration if that's the intent
        exam.duration_minutes += minutes
        db.session.commit()
        flash(f'Exam duration extended by {minutes} minutes.', 'success')
        
    return redirect(url_for('faculty.view_exam', exam_id=exam_id))
