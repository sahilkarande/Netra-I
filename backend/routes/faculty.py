import os
import csv
import io
import json
import pandas as pd
import shutil
import traceback
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, current_app, session, abort
from functools import wraps
from flask_login import login_required, current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash

from backend.database import db
from backend.models import User, Exam, Question, StudentExam, StudentAnswer, ExamViolation
from backend.services.pdf_generator import generate_batch_report_pdf
from backend.services.proctor_service import PROCTOR_INSTANCES, _heartbeat_registry, _last_frame_processed
from backend.services.exam_service import assign_shuffle, finalize_overdue_attempts
from backend.socket_io import socketio
from backend.services import account_service

faculty_bp = Blueprint('faculty', __name__)

STAFF_ROLES = ('faculty', 'admin')


def _is_staff():
    return current_user.is_authenticated and current_user.role in STAFF_ROLES


def staff_required(view):
    """Faculty/admin only. Every faculty route carries this, so a missing role
    check inside a view can never hand a student staff powers."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_staff():
            wants_json = (request.is_json
                          or request.accept_mimetypes.best == 'application/json'
                          or request.path.startswith('/api/'))
            if wants_json or request.method != 'GET':
                return jsonify({"success": False, "error": "Access denied"}), 403
            flash('Access denied', 'error')
            return redirect(url_for('auth.index'))
        return view(*args, **kwargs)
    return wrapped


def _get_faculty_exams():
    """Retrieve exams accessible to current faculty (own exams + same course/department exams) or all if admin"""
    if not _is_staff():
        return []
    if current_user.role == 'admin':
        return Exam.query.order_by(Exam.created_at.desc()).all()
    user_course = current_user.course or current_user.department
    if user_course:
        return Exam.query.join(User, Exam.creator_id == User.id).filter(
            db.or_(
                Exam.creator_id == current_user.id,
                User.course == user_course,
                User.department == user_course
            )
        ).order_by(Exam.created_at.desc()).all()
    return Exam.query.filter_by(creator_id=current_user.id).order_by(Exam.created_at.desc()).all()

def _faculty_can_access_exam(exam):
    """Check if current user can access/manage the exam"""
    # Students carry course/department too, so the department match below must
    # never be reached for them.
    if not exam or not _is_staff():
        return False
    if current_user.role == 'admin':
        return True
    if exam.creator_id == current_user.id:
        return True
    user_course = current_user.course or current_user.department
    if user_course and exam.creator:
        creator_course = exam.creator.course or exam.creator.department
        if creator_course and creator_course == user_course:
            return True
    return False

def _managed_student_or_404(student_id):
    """The student, if the current staff member may manage them; else 404.

    404 rather than 403 so other departments' student IDs cannot be probed.
    """
    student = User.query.get(student_id)
    if not account_service.can_manage_student(current_user, student):
        abort(404)
    return student


def _faculty_can_access_student(student_id):
    """Check if faculty or course faculty created an exam that this student has taken"""
    accessible_exams = _get_faculty_exams()
    accessible_ids = [e.id for e in accessible_exams]
    if not accessible_ids:
        return False
    return db.session.query(StudentExam).filter(
        StudentExam.exam_id.in_(accessible_ids),
        StudentExam.student_id == student_id
    ).first() is not None

@faculty_bp.route('/faculty/dashboard')
@login_required
@staff_required
def faculty_dashboard():
    if current_user.role != 'faculty' and current_user.role != 'admin':
        return redirect(url_for('auth.index'))
    
    exams = _get_faculty_exams()
    
    exam_ids = [e.id for e in exams]
    finalize_overdue_attempts(exam_ids=exam_ids)
    
    # Calculate statistics for the dashboard
    total_attempts = StudentExam.query.filter(StudentExam.exam_id.in_(exam_ids)).count() if exam_ids else 0
    unique_students = db.session.query(func.count(func.distinct(StudentExam.student_id))).filter(StudentExam.exam_id.in_(exam_ids)).scalar() or 0
    
    submitted_exams = StudentExam.query.filter(
        StudentExam.exam_id.in_(exam_ids), 
        db.or_(StudentExam.status.in_(['submitted', 'completed']), StudentExam.completed == True)
    ).all() if exam_ids else []
    if submitted_exams:
        avg_class_score = round(sum(se.percentage or 0 for se in submitted_exams) / len(submitted_exams), 1)
        passed_count = sum(1 for se in submitted_exams if se.passed)
        pass_rate = round((passed_count / len(submitted_exams)) * 100, 1)
    else:
        avg_class_score = 0
        pass_rate = 0
        
    results_visible_count = sum(1 for e in exams if e.results_published)
    leaderboard_visible_count = sum(1 for e in exams if e.show_leaderboard)
    visibility = _visibility_state(exams)
    
    # Get recent submissions
    recent_activities = StudentExam.query.filter(StudentExam.exam_id.in_(exam_ids)).order_by(StudentExam.submitted_at.desc()).limit(10).all()
        
    return render_template('faculty/dashboard.html', 
                         exams=exams,
                         total_attempts=total_attempts,
                         unique_students=unique_students,
                         avg_class_score=avg_class_score,
                         pass_rate=pass_rate,
                         results_visible_count=results_visible_count,
                         leaderboard_visible_count=leaderboard_visible_count,
                         visibility=visibility,
                         recent_activities=recent_activities)

def _parse_bool(value, default=False):
    """Accept true/false, 1/0, "on"/"off", "yes"/"no" from JSON or form posts."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _visibility_state(exams):
    """Summary of the current toggles, used to keep the dashboard UI in sync.

    With no exams yet the toggles still mean something: they show the faculty's
    saved defaults, which every exam they create will inherit.
    """
    total = len(exams)
    leaderboard_on = sum(1 for e in exams if e.show_leaderboard)
    results_on = sum(1 for e in exams if e.results_published)

    if total == 0:
        return {
            "total_exams": 0,
            "leaderboard_on": 0,
            "results_on": 0,
            "leaderboard_enabled": bool(getattr(current_user, 'default_show_leaderboard', True)),
            "results_enabled": bool(getattr(current_user, 'default_results_published', False)),
            "is_default_only": True,
        }

    return {
        "total_exams": total,
        "leaderboard_on": leaderboard_on,
        "results_on": results_on,
        "leaderboard_enabled": leaderboard_on == total,
        "results_enabled": results_on == total,
        "is_default_only": False,
    }


@faculty_bp.route('/faculty/visibility-state')
@login_required
@staff_required
def faculty_visibility_state():
    """Current results / leaderboard visibility for this faculty's exams."""
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    state = _visibility_state(_get_faculty_exams())
    state["success"] = True
    return jsonify(state)


@faculty_bp.route('/faculty/set_leaderboard', methods=['POST'])
@login_required
@staff_required
def faculty_set_leaderboard():
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    data = request.get_json(silent=True) or request.form
    exam_id = data.get('exam_id')
    raw = data.get('enabled') if 'enabled' in data else data.get('show_leaderboard')
    enabled = _parse_bool(raw)

    if exam_id:
        exam = Exam.query.get(exam_id)
        if not exam or not _faculty_can_access_exam(exam):
            return jsonify({"success": False, "message": "Exam not found"}), 404
        exam.show_leaderboard = enabled
        updated = 1
    else:
        exams = _get_faculty_exams()
        for e in exams:
            e.show_leaderboard = enabled
        updated = len(exams)
        # Remember it as this faculty's default for exams created later
        current_user.default_show_leaderboard = enabled

    db.session.commit()

    state = _visibility_state(_get_faculty_exams())
    state.update({
        "success": True,
        "enabled": enabled,
        "updated": updated,
        "message": (
            ("Saved — the leaderboard will be open on exams you create" if enabled
             else "Saved — the leaderboard will stay closed on exams you create")
            if updated == 0 and not exam_id else
            (f"Leaderboard opened for {updated} exam(s)" if enabled
             else f"Leaderboard closed for {updated} exam(s)")
        )
    })
    return jsonify(state)


@faculty_bp.route('/faculty/set_results_visibility', methods=['POST'])
@login_required
@staff_required
def faculty_set_results_visibility():
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    data = request.get_json(silent=True) or request.form
    exam_id = data.get('exam_id')
    if 'visible' in data:
        raw = data.get('visible')
    elif 'enabled' in data:
        raw = data.get('enabled')
    else:
        raw = data.get('show_results')
    visible = _parse_bool(raw)

    def _apply(exam):
        exam.results_published = visible
        exam.results_published_at = datetime.utcnow() if visible else None
        exam.results_published_by = current_user.id if visible else None

    if exam_id:
        exam = Exam.query.get(exam_id)
        if not exam or not _faculty_can_access_exam(exam):
            return jsonify({"success": False, "message": "Exam not found"}), 404
        _apply(exam)
        updated = 1
    else:
        exams = _get_faculty_exams()
        for e in exams:
            _apply(e)
        updated = len(exams)
        current_user.default_results_published = visible

    db.session.commit()

    state = _visibility_state(_get_faculty_exams())
    state.update({
        "success": True,
        "enabled": visible,
        "updated": updated,
        "message": (
            ("Saved — results will be published on exams you create" if visible
             else "Saved — results will stay hidden on exams you create")
            if updated == 0 and not exam_id else
            (f"Results published for {updated} exam(s)" if visible
             else f"Results hidden for {updated} exam(s)")
        )
    })
    return jsonify(state)


@faculty_bp.route('/faculty/exam/create', methods=['GET', 'POST'])
@login_required
@staff_required
def create_exam():
    """Create a single exam.

    All field handling lives in :mod:`backend.services.exam_builder` so the
    batch path below applies exactly the same rules.
    """
    if current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))

    from backend.services import exam_builder

    if request.method == 'POST':
        errors, new_exam = exam_builder.build_exam(request.form, current_user)

        if errors:
            for message in errors:
                flash(message, 'error')
            # Hand the submitted values back so nothing typed is lost.
            return render_template('faculty/create_exam.html',
                                   form=request.form), 400

        try:
            db.session.add(new_exam)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('create_exam failed')
            flash('Could not create the exam. Please try again.', 'error')
            return render_template('faculty/create_exam.html',
                                   form=request.form), 500

        summary = exam_builder.summarise(new_exam)
        key_note = (f" Access key: {summary['access_key']}."
                    if summary['access_key'] else '')
        flash(f"Exam \"{new_exam.title}\" created.{key_note} Now add questions.", 'success')
        return redirect(url_for('faculty.upload_questions', exam_id=new_exam.id))

    return render_template('faculty/create_exam.html', form={})


@faculty_bp.route('/faculty/import_students', methods=['POST'])
@login_required
@staff_required
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
        credentials = []
        errors = []

        def clean_string(value):
            if pd.isna(value) or value == '': return ''
            return str(value).strip()

        exam_id = request.args.get('exam_id', type=int)
        exam = Exam.query.get(exam_id) if exam_id else None
        if exam and not _faculty_can_access_exam(exam):
            flash('You cannot enrol students into that exam.', 'error')
            return redirect(url_for('faculty.faculty_students'))

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

                # A password from the file is the faculty's choice; otherwise a
                # random one per student, never a shared default.
                password = clean_string(row.get('password'))
                generated = not password
                if generated:
                    password = account_service.generate_temp_password()
                hashed_pw = generate_password_hash(password)

                student = User(
                    username=clean_string(row.get('username')) or email.split('@')[0],
                    email=email,
                    full_name=clean_string(row.get('full_name')),
                    # Blank PRN/roll must be NULL: PRN is unique, and '' would
                    # collide with the next student who also has none.
                    prn_number=prn_clean or None,
                    roll_id=clean_string(row.get('roll_id')) or None,
                    password_hash=hashed_pw,
                    role='student',
                    is_verified=True,
                    created_by=current_user.id,
                )
                db.session.add(student)
                db.session.commit()

                if generated:
                    credentials.append({
                        'full_name': student.full_name, 'username': student.username,
                        'prn_number': student.prn_number, 'email': student.email,
                        'temporary_password': password,
                    })

                if exam:
                    student_exam = StudentExam(student_id=student.id, exam_id=exam.id)
                    db.session.add(student_exam)
                    db.session.commit()
                    assign_shuffle(student_exam)

                added += 1
            except Exception as e:
                current_app.logger.warning("import row %s failed: %s", index + 2, e)
                errors.append(f"Row {index + 2}: could not be imported")
                skipped += 1
                db.session.rollback()

        db.session.commit()
        flash(f"✅ {added} added | ⚠️ {skipped} skipped", 'success')
        if credentials:
            _stash_credentials(credentials)
            flash(f"{len(credentials)} student(s) were given a random temporary password. "
                  "Download the credentials sheet now — it can only be downloaded once.", 'warning')
        return redirect(url_for('faculty.faculty_students'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('import_students failed')
        flash("Could not read the file. Check it is a valid CSV, Excel or JSON file.", 'error')
        return redirect(url_for('faculty.faculty_students'))

@faculty_bp.route('/faculty/exam/<int:exam_id>/delete', methods=['POST'])
@login_required
@staff_required
def delete_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
        flash('Unauthorized access', 'error')
        return redirect(url_for('faculty.faculty_dashboard'))
        
    db.session.delete(exam)
    db.session.commit()
    flash('Exam deleted successfully', 'success')
    return redirect(url_for('faculty.faculty_dashboard'))

@faculty_bp.route('/faculty/exam/<int:exam_id>/upload', methods=['GET', 'POST'])
@login_required
@staff_required
def upload_questions(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
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
            current_app.logger.exception('upload_questions failed')
            flash('Could not read the question file. Check its columns and values.', 'error')
            return redirect(request.url)
            
    return render_template('faculty/upload_questions.html', exam=exam)

@faculty_bp.route('/faculty/exam/<int:exam_id>')
@login_required
@staff_required
def view_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
        return redirect(url_for('faculty.faculty_dashboard'))
    finalize_overdue_attempts(exam_ids=[exam.id])

    # Get all students for the selection modal
    all_students = account_service.scoped_students_query(current_user).order_by(User.full_name).all()
    
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
@staff_required
def exam_analytics(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
        return redirect(url_for('faculty.faculty_dashboard'))
    finalize_overdue_attempts(exam_ids=[exam.id])

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
@staff_required
def faculty_publish_results(exam_id):
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"error": "Access denied"}), 403

    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
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

def _credentials_dir():
    path = os.path.join(current_app.instance_path, 'credentials')
    os.makedirs(path, exist_ok=True)
    return path


def _stash_credentials(rows):
    """Keep the generated passwords just long enough for one download.

    They are written to instance/ (never served statically), bound to this
    session, and deleted on download.
    """
    import secrets as _secrets
    token = _secrets.token_urlsafe(16)
    with open(os.path.join(_credentials_dir(), f'{token}.csv'), 'wb') as f:
        f.write(account_service.credentials_csv(rows))
    try:
        os.chmod(os.path.join(_credentials_dir(), f'{token}.csv'), 0o600)
    except OSError:
        pass
    session['credentials_token'] = token


@faculty_bp.route('/faculty/credentials/<token>')
@login_required
@staff_required
def download_credentials(token):
    """One-time download of the temporary passwords from the last import."""
    if not token or session.get('credentials_token') != token:
        flash('That credentials sheet is no longer available.', 'error')
        return redirect(url_for('faculty.faculty_students'))
    path = os.path.join(_credentials_dir(), f'{secure_filename(token)}.csv')
    session.pop('credentials_token', None)
    try:
        with open(path, 'rb') as f:
            data = f.read()
        os.remove(path)
    except OSError:
        flash('That credentials sheet is no longer available.', 'error')
        return redirect(url_for('faculty.faculty_students'))
    return send_file(io.BytesIO(data), mimetype='text/csv', as_attachment=True,
                     download_name=f"student_credentials_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")


@faculty_bp.route('/faculty/students')
@login_required
@staff_required
def faculty_students():
    if current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))
        
    q = request.args.get('q', '')
    batch = request.args.get('batch', '')
    dept = request.args.get('department', '')
    sort = request.args.get('sort', '')
    verified = request.args.get('verified', '')

    query = account_service.scoped_students_query(current_user)

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
                         departments=departments,
                         credentials_token=session.get('credentials_token'))

@faculty_bp.route('/faculty/student_report', methods=['GET'])
@login_required
@staff_required
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
        exams = _get_faculty_exams()
        
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
        students = account_service.scoped_students_query(current_user).filter(User.batch == selected_batch).all()
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
@staff_required
def faculty_student_report_pdf():
    # Placeholder for PDF generation
    return "PDF Report generation triggered"

@faculty_bp.route('/faculty/student/<int:student_id>/profile')
@login_required
@staff_required
def faculty_student_profile(student_id):
    if current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))
    student = _managed_student_or_404(student_id)
    # Only attempts on exams this faculty can see — not another department's marks
    accessible = [e.id for e in _get_faculty_exams()]
    student_exams = (StudentExam.query.filter(StudentExam.student_id == student_id,
                                              StudentExam.exam_id.in_(accessible)).all()
                     if accessible else [])
    return render_template('student/student_profile.html', student=student, student_exams=student_exams)

@faculty_bp.route('/faculty/student/<int:student_id>/edit', methods=['GET', 'POST'])
@login_required
@staff_required
def faculty_edit_student(student_id):
    if current_user.role not in ('faculty', 'admin'):
        flash('Access denied', 'error')
        return redirect(url_for('auth.index'))

    student = User.query.get_or_404(student_id)
    # Only student accounts — editing a staff email here would be an account takeover
    if student.role != 'student':
        flash('Only student accounts can be edited here.', 'error')
        return redirect(url_for('faculty.faculty_students'))
    if not account_service.can_manage_student(current_user, student):
        abort(404)

    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        if not email:
            flash('Email is required.', 'error')
            return render_template('faculty/edit_student.html', student=student)
        clash = User.query.filter(User.email == email, User.id != student.id).first()
        if clash:
            flash('That email is already used by another account.', 'error')
            return render_template('faculty/edit_student.html', student=student)

        student.full_name = full_name or None
        student.email = email
        db.session.commit()
        flash('Student updated successfully', 'success')
        return redirect(url_for('faculty.faculty_students'))
    return render_template('faculty/edit_student.html', student=student)

@faculty_bp.route('/faculty/download_template')
@login_required
@staff_required
def download_template():
    if current_user.role not in ('faculty', 'admin'):
        return redirect(url_for('auth.index'))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['full_name', 'username', 'email', 'prn_number', 'roll_id', 'password'])
    writer.writerow(['Asha Patil', 'asha.patil', 'asha.patil@example.com', '250840125001', '01', ''])
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='student_import_template.csv'
    )

@faculty_bp.route('/faculty/change-student-password/<int:student_id>', methods=['POST'])
@login_required
@staff_required
def faculty_change_student_password(student_id):
    if current_user.role not in ['faculty', 'admin']:
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    student = User.query.get(student_id)
    # Never let faculty reset a staff password — that would hand over the account
    if student is not None and student.role != 'student':
        return jsonify({"success": False, "message": "Only student passwords can be reset here."}), 403
    if not account_service.can_manage_student(current_user, student):
        return jsonify({"success": False, "message": "Student not found."}), 404

    # A fresh random password, shown to the faculty once. A fixed default
    # would let anyone who knows it take the account before the student does.
    temp_password = account_service.generate_temp_password()
    student.password_hash = generate_password_hash(temp_password, method='pbkdf2:sha256')
    student.password_changed = False
    student.password_changed_at = None
    student.password_hint = None
    db.session.commit()
    current_app.logger.info('user %s reset password of student %s', current_user.id, student.id)
    return jsonify({
        "success": True,
        "temporary_password": temp_password,
        "message": (f"Password reset. Temporary password: {temp_password}\n"
                    "Give it to the student — it is not shown again. "
                    "They must set their own password on next login."),
    })
@faculty_bp.route('/faculty/split-questions')
@login_required
@staff_required
def split_questions_page():
    """Standalone tool: split one question file into several set files."""
    if current_user.role not in ('faculty', 'admin'):
        flash('Access denied', 'error')
        return redirect(url_for('auth.index'))
    return render_template('faculty/split_questions.html')


@faculty_bp.route('/faculty/split-questions/preview', methods=['POST'])
@login_required
@staff_required
def split_questions_preview():
    """Parse the uploaded bank and show how it would divide. Writes nothing."""
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    from backend.services import question_splitter as QS

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"success": False, "message": "Choose a question file first."}), 400

    ok, message, questions = QS.parse_questions(secure_filename(file.filename), file.read())
    if not ok:
        return jsonify({"success": False, "message": message}), 400

    try:
        num_sets = int(request.form.get('num_sets') or 0)
        per_set = int(request.form.get('per_set') or 0) or None
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Set count must be a number."}), 400

    split_ok, split_msg, result = QS.split_questions(
        questions, num_sets, per_set,
        strategy=request.form.get('strategy', 'unique'),
        balance_marks=request.form.get('balance_marks', 'true') == 'true',
    )

    if not split_ok:
        return jsonify({"success": False, "message": split_msg, "parsed": message}), 400

    # Preview shows shape only — the questions themselves come with the download.
    return jsonify({
        "success": True,
        "parsed": message,
        "message": split_msg,
        "plan": {
            "bank_total": result['bank_total'],
            "num_sets": result['num_sets'],
            "per_set": result['per_set'],
            "leftover": result['leftover'],
            "strategy": result['strategy'],
            "sets": [{"label": x['label'], "count": x['count'],
                      "total_marks": x['total_marks']} for x in result['sets']],
            "sample": result['sets'][0]['questions'][:2],
        },
    })


@faculty_bp.route('/faculty/split-questions/download', methods=['POST'])
@login_required
@staff_required
def split_questions_download():
    """Split the uploaded bank and return the sets as a ZIP."""
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    from backend.services import question_splitter as QS

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"success": False, "message": "Choose a question file first."}), 400

    original = secure_filename(file.filename)
    ok, message, questions = QS.parse_questions(original, file.read())
    if not ok:
        return jsonify({"success": False, "message": message}), 400

    try:
        num_sets = int(request.form.get('num_sets') or 0)
        per_set = int(request.form.get('per_set') or 0) or None
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Set count must be a number."}), 400

    fmt = 'csv' if request.form.get('format') == 'csv' else 'json'

    split_ok, split_msg, result = QS.split_questions(
        questions, num_sets, per_set,
        strategy=request.form.get('strategy', 'unique'),
        balance_marks=request.form.get('balance_marks', 'true') == 'true',
    )

    if not split_ok:
        return jsonify({"success": False, "message": split_msg}), 400

    base = original.rsplit('.', 1)[0] or 'question_bank'
    payload = QS.build_zip(result, base, fmt)

    return send_file(
        io.BytesIO(payload),
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{base}_sets.zip',
    )


@faculty_bp.route('/faculty/student/<int:student_id>/reset-face', methods=['POST'])
@login_required
@staff_required
def faculty_reset_student_face(student_id):
    """Clear a student's registered face so they can enrol again.

    Students cannot change their own face once locked, so this is the only
    route by which a face template can be replaced. Every use is audited with
    the acting staff member's id.
    """
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    student = User.query.get_or_404(student_id)
    if student.role != 'student':
        return jsonify({"success": False, "message": "Not a student account."}), 400
    if not account_service.can_manage_student(current_user, student):
        return jsonify({"success": False, "message": "Student not found."}), 404

    payload = request.get_json(silent=True) or request.form or {}
    reason = (payload.get('reason') or '').strip()
    if not reason:
        return jsonify({
            "success": False,
            "message": "A reason is required — face resets are permanently logged.",
        }), 400

    from backend.services import face_service
    ip = (request.headers.get('X-Forwarded-For', request.remote_addr or '')
          .split(',')[0].strip())

    ok, message = face_service.reset_enrollment(student, current_user,
                                                reason=reason, ip_address=ip)
    return jsonify({"success": ok, "message": message}), (200 if ok else 400)


@faculty_bp.route('/faculty/student/<int:student_id>/face-history')
@login_required
@staff_required
def faculty_student_face_history(student_id):
    """Who changed this student's face template, and when."""
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    from backend.models import FaceAuditLog
    from backend.services import face_service

    rows = (FaceAuditLog.query
            .filter_by(subject_user_id=student_id)
            .order_by(FaceAuditLog.created_at.desc())
            .limit(50).all())

    student = _managed_student_or_404(student_id)
    return jsonify({
        "success": True,
        "enrollment": face_service.enrollment_info(student),
        "history": [{
            "action": r.action,
            "actor": (r.actor.full_name or r.actor.username) if r.actor else None,
            "actor_role": r.actor_role,
            "reason": r.reason,
            "liveness_score": r.liveness_score,
            "at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    })


@faculty_bp.route('/api/faculty/batch-create-exams', methods=['POST'])
@login_required
@staff_required
def batch_create_exams():
    """Create several exams at once, each optionally with its own question file.

    Shares validation, proctoring and access-key generation with the single
    path, and reuses the tolerant question parser so a batch file does not have
    to use stricter column names than a normal upload.
    """
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "error": "Access denied"}), 403

    from backend.services import exam_builder
    from backend.services import question_splitter

    try:
        exams_data = json.loads(request.form.get('exams_json', '[]'))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Malformed batch payload."}), 400

    if not isinstance(exams_data, list) or not exams_data:
        return jsonify({"success": False, "error": "No exams to create."}), 400

    # Shared settings posted once alongside the batch (proctoring, mode, …)
    shared = {k: v for k, v in request.form.items() if k != 'exams_json'}

    created, failed = [], []

    try:
        for index, row in enumerate(exams_data):
            if not isinstance(row, dict):
                continue

            fields = dict(shared)
            fields.update({k: v for k, v in row.items() if v not in (None, '')})

            errors, new_exam = exam_builder.build_exam(fields, current_user)
            if errors:
                failed.append({"row": index + 1,
                               "title": row.get('title') or f'Row {index + 1}',
                               "errors": errors})
                continue

            db.session.add(new_exam)
            db.session.flush()              # need the id for question rows

            questions_count = 0
            upload = request.files.get(f'file_{index}')
            if upload and upload.filename:
                ok, _message, questions = question_splitter.parse_questions(
                    secure_filename(upload.filename), upload.read())
                if ok:
                    for order, q in enumerate(questions, start=1):
                        db.session.add(Question(
                            exam_id=new_exam.id,
                            question_text=q['question'],
                            option_a=q['option_a'],
                            option_b=q['option_b'],
                            option_c=q['option_c'],
                            option_d=q['option_d'],
                            correct_answer=q['correct_answer'],
                            points=q['points'],
                            order_number=order,
                        ))
                        questions_count += 1

            created.append({**exam_builder.summarise(new_exam),
                            "questions_count": questions_count})

        # One commit for the whole batch: either every exam lands or none does.
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        current_app.logger.exception('request failed')
        return jsonify({"success": False, "error": "The operation failed. Nothing was changed."}), 500

    if not created:
        return jsonify({"success": False,
                        "error": "No exams could be created.",
                        "failed": failed}), 400

    message = f"Created {len(created)} exam(s)."
    if failed:
        message += f" {len(failed)} row(s) were skipped."

    return jsonify({"success": True, "message": message,
                    "created_exams": created, "failed": failed})


@faculty_bp.route('/faculty/delete_student/<int:student_id>', methods=['POST'])
@login_required
@staff_required
def delete_student(student_id):
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    student = User.query.get_or_404(student_id)
    if student.role != 'student':
        return jsonify({"success": False, "error": "Can only delete students"}), 400

    try:
        deleted, refused = account_service.delete_students([student_id], current_user)
        if not deleted:
            return jsonify({"success": False, "error": "Student not found."}), 404
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('request failed')
        return jsonify({"success": False, "error": "The operation failed. Nothing was changed."}), 500

@faculty_bp.route('/faculty/delete_students', methods=['POST'])
@login_required
@staff_required
def delete_students():
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"success": False, "error": "Access denied"}), 403
        
    data = request.get_json(silent=True) or {}
    try:
        student_ids = [int(i) for i in (data.get('ids') or [])]
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid selection"}), 400

    if not student_ids:
        return jsonify({"success": False, "error": "No students selected"}), 400

    try:
        deleted, refused = account_service.delete_students(student_ids, current_user)
        return jsonify({"success": True, "deleted": deleted, "skipped": len(refused)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('request failed')
        return jsonify({"success": False, "error": "The operation failed. Nothing was changed."}), 500

@faculty_bp.route('/faculty/export_students')
@login_required
@staff_required
def export_students():
    if current_user.role not in ('faculty', 'admin'):
        flash('Access denied', 'error')
        return redirect(url_for('auth.index'))
        
    # Get filters from request args
    q = request.args.get('q', '')
    batch = request.args.get('batch', '')
    dept = request.args.get('department', '')
    ids = request.args.getlist('ids')
    
    query = account_service.scoped_students_query(current_user)

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
@staff_required
def preview_questions(exam_id):
    if current_user.role not in ('faculty', 'admin'):
        return jsonify({"error": "Access denied"}), 403
    if not _faculty_can_access_exam(Exam.query.get(exam_id)):
        return jsonify({"error": "Exam not found"}), 404
    
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
        current_app.logger.exception('preview_questions failed')
        return jsonify({"error": "Could not read the file."}), 500
    return jsonify({"error": "Invalid file format"}), 400

@faculty_bp.route('/faculty/exam/<int:exam_id>/force_end', methods=['POST'])
@login_required
@staff_required
def faculty_force_end_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
        flash('Unauthorized', 'error')
        return redirect(url_for('faculty.faculty_dashboard'))
        
    # Force end all active student exams
    active_exams = StudentExam.query.filter_by(exam_id=exam_id, status='started').all()
    from backend.services.exam_service import calculate_student_score
    from backend.services.proctor_service import cleanup_proctor_instance

    for se in active_exams:
        se.status = 'submitted'
        se.submitted_at = datetime.utcnow()
        calculate_student_score(se.id)
        cleanup_proctor_instance(se.id)

    # Closed for everyone: students polling the status endpoint see this, and
    # nobody can open a fresh attempt until the exam is reactivated.
    exam.force_ended = True
    exam.status = 'ended'
    db.session.commit()

    from backend.routes.exam import exam_room
    socketio.emit('exam_force_ended', {'exam_id': exam.id}, to=exam_room(exam.id))

    flash(f'Force ended {len(active_exams)} active attempts.', 'success')
    return redirect(url_for('faculty.view_exam', exam_id=exam_id))

@faculty_bp.route('/faculty/exam/<int:exam_id>/force_end_student/<int:student_exam_id>', methods=['POST'])
@login_required
@staff_required
def faculty_force_end_student(exam_id, student_exam_id):
    se = StudentExam.query.get_or_404(student_exam_id)
    exam = Exam.query.get_or_404(exam_id)
    # The attempt must belong to this exam, or access to one exam would let a
    # faculty end attempts on any other.
    if se.exam_id != exam.id or not _faculty_can_access_exam(exam):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    if not se.completed:
        se.status = 'submitted'
        se.submitted_at = datetime.utcnow()
        from backend.services.exam_service import calculate_student_score
        from backend.services.proctor_service import cleanup_proctor_instance
        calculate_student_score(se.id)
        cleanup_proctor_instance(se.id)
        db.session.commit()

        from backend.routes.exam import attempt_room
        socketio.emit('student_force_ended', {'student_exam_id': se.id}, to=attempt_room(se.id))
    return redirect(url_for('faculty.view_exam', exam_id=exam_id))
@faculty_bp.route('/faculty/exam/<int:exam_id>/generate_key', methods=['POST'])
@login_required
@staff_required
def generate_exam_key(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
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
@staff_required
def clear_exam_key(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    exam.access_key = None
    db.session.commit()
    return jsonify({"success": True})

@faculty_bp.route('/faculty/exam/<int:exam_id>/update_access', methods=['POST'])
@login_required
@staff_required
def update_exam_access(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
        flash('Unauthorized', 'error')
        return redirect(url_for('faculty.faculty_dashboard'))
        
    if 'access_mode' in request.form:
        access_mode = request.form.get('access_mode')
        exam.allow_all_students = (access_mode == 'all')
        
        if 'allowed_students' in request.form:
            allowed_str = request.form.get('allowed_students', '').strip()
            allowed_ids = [sid.strip() for sid in allowed_str.split(',') if sid.strip().isdigit()]
            exam.allowed_students = ','.join(allowed_ids) if allowed_ids else None
        
        db.session.commit()
        flash('Student eligibility settings saved.', 'success')
    elif 'is_active' in request.form:
        exam.is_active = request.form.get('is_active') == 'true'
        reactivated = exam.is_active and exam.force_ended
        if exam.is_active:
            exam.force_ended = False
            exam.status = 'active'
        db.session.commit()
        if reactivated:
            from backend.routes.exam import exam_room
            socketio.emit('exam_reactivated', {'exam_id': exam.id}, to=exam_room(exam.id))
        flash('Exam access updated.', 'success')
    else:
        flash('No changes made.', 'info')
    return redirect(url_for('faculty.view_exam', exam_id=exam_id))

@faculty_bp.route('/faculty/exam/<int:exam_id>/extend_time', methods=['POST'])
@login_required
@staff_required
def faculty_extend_exam_time(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if not _faculty_can_access_exam(exam):
        flash('Unauthorized', 'error')
        return redirect(url_for('faculty.faculty_dashboard'))
        
    try:
        minutes = int(request.form.get('minutes') or request.form.get('extra_minutes') or 0)
    except (TypeError, ValueError):
        minutes = 0
    if 0 < minutes <= 600:
        # Extend for all currently active students
        active_exams = StudentExam.query.filter_by(exam_id=exam_id, status='started').all()
        # Note: If we don't have a per-student end_time, we might need to store it
        # For now, let's just log it or update the exam duration if that's the intent
        exam.duration_minutes += minutes
        db.session.commit()
        # Open exam pages re-read their remaining time from the server
        from backend.routes.exam import exam_room
        socketio.emit('time_extended', {'exam_id': exam.id, 'minutes': minutes}, to=exam_room(exam.id))
        flash(f'Exam duration extended by {minutes} minutes.', 'success')
        
    return redirect(url_for('faculty.view_exam', exam_id=exam_id))


# ═══════════════════════════════════════════════════════════════════════
# Proctoring review
# ═══════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_EVIDENCE_ROOT = os.path.join(_PROJECT_ROOT, 'frontend', 'static', 'violations')


def _staff_attempt_or_none(student_exam_id):
    """The attempt, if the current user is staff who can access its exam."""
    if current_user.role not in ('faculty', 'admin'):
        return None
    se = StudentExam.query.get(student_exam_id)
    if not se or not _faculty_can_access_exam(se.exam):
        return None
    return se


def _evidence_file(violation):
    """Absolute path of a violation's evidence image, only if it is inside the
    evidence directory — the stored path is never trusted to point elsewhere."""
    if not violation or not violation.evidence_path:
        return None
    path = violation.evidence_path
    if not os.path.isabs(path):
        path = os.path.join(_PROJECT_ROOT, path)
    path = os.path.realpath(path)
    root = os.path.realpath(_EVIDENCE_ROOT)
    if os.path.commonpath([path, root]) != root or not os.path.isfile(path):
        return None
    return path


@faculty_bp.route('/api/proctor/status/<int:student_exam_id>')
@login_required
@staff_required
def proctor_status(student_exam_id):
    """Violation summary and timeline for one attempt (analytics review modal)."""
    se = _staff_attempt_or_none(student_exam_id)
    if not se:
        return jsonify({"success": False, "error": "Not found"}), 404

    violations = (ExamViolation.query
                  .filter_by(student_exam_id=se.id)
                  .order_by(ExamViolation.timestamp.desc())
                  .limit(100).all())

    return jsonify({
        "success": True,
        "total_violations": se.total_violations or 0,
        "proctoring_status": se.proctoring_status or 'active',
        "calibration_completed": bool(se.calibration_completed),
        "is_invalidated": bool(se.is_invalidated),
        "faculty_review_note": se.faculty_review_note,
        "recent_violations": [{
            "id": v.id,
            "type": v.violation_type,
            "severity": v.severity,
            "message": v.message,
            "timestamp": (v.timestamp.isoformat() + 'Z') if v.timestamp else None,
            "evidence_url": (url_for('faculty.violation_evidence', violation_id=v.id)
                             if _evidence_file(v) else None),
        } for v in violations],
    })


@faculty_bp.route('/api/faculty/review-exam/<int:student_exam_id>', methods=['POST'])
@login_required
@staff_required
def review_exam(student_exam_id):
    """Accept or invalidate an attempt after reviewing its proctoring flags."""
    se = _staff_attempt_or_none(student_exam_id)
    if not se:
        return jsonify({"success": False, "error": "Not found"}), 404

    data = request.get_json(silent=True) or request.form
    action = (data.get('action') or '').strip().lower()
    note = (data.get('note') or '').strip()[:2000]

    if action == 'invalidate':
        if not note:
            return jsonify({"success": False,
                            "error": "Please give a reason for invalidating this attempt."}), 400
        se.is_invalidated = True
    elif action == 'accept':
        se.is_invalidated = False
        note = note or 'Reviewed — no issue found'
    else:
        return jsonify({"success": False, "error": "Unknown action."}), 400

    se.faculty_review_note = note
    se.reviewed_by = current_user.id
    se.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"success": True, "is_invalidated": se.is_invalidated})


@faculty_bp.route('/faculty/violation/<int:violation_id>/evidence')
@login_required
@staff_required
def violation_evidence(violation_id):
    """Serve an evidence image to staff who can access the exam."""
    v = ExamViolation.query.get_or_404(violation_id)
    if not _staff_attempt_or_none(v.student_exam_id):
        return "Not found", 404
    path = _evidence_file(v)
    if not path:
        return "Not found", 404
    return send_file(path, mimetype='image/jpeg', max_age=0)


@faculty_bp.route('/api/faculty/violation/<int:violation_id>/evidence', methods=['DELETE', 'POST'])
@login_required
@staff_required
def delete_violation_evidence(violation_id):
    """Delete one evidence image. The violation row itself is kept."""
    v = ExamViolation.query.get_or_404(violation_id)
    if not _staff_attempt_or_none(v.student_exam_id):
        return jsonify({"success": False, "error": "Not found"}), 404
    path = _evidence_file(v)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    v.evidence_path = None
    db.session.commit()
    return jsonify({"success": True})
