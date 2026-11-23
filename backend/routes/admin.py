from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import text
import traceback

from backend.database import db
from backend.models import User, Exam, StudentExam

admin_bp = Blueprint('admin', __name__)

def _json_cell(value):
    """Make a DB value JSON-safe (binary columns such as face templates)."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f'<{len(bytes(value))} bytes>'
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def is_admin_user():
    """Check if current user is admin"""
    return getattr(current_user, "role", None) == "admin"

@admin_bp.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if not is_admin_user():
        return redirect(url_for('auth.index'))
    
    total_faculty = User.query.filter_by(role='faculty').count()
    total_students = User.query.filter_by(role='student').count()
    total_exams = Exam.query.count()
    active_exams = Exam.query.filter_by(is_active=True).count()
    total_attempts = StudentExam.query.count()
    submitted_attempts = StudentExam.query.filter_by(status='submitted').count()
    
    # Calculate faculty stats
    faculty_list = User.query.filter_by(role='faculty').all()
    faculty_stats = []
    for f in faculty_list:
        exams = Exam.query.filter_by(creator_id=f.id).all()
        exam_ids = [e.id for e in exams]
        student_count = 0
        avg_score = 0
        if exam_ids:
            student_count = StudentExam.query.filter(StudentExam.exam_id.in_(exam_ids)).count()
            submitted = StudentExam.query.filter(
                StudentExam.exam_id.in_(exam_ids),
                StudentExam.status == 'submitted'
            ).all()
            if submitted:
                avg_score = round(sum(se.percentage or 0 for se in submitted) / len(submitted), 1)
        faculty_stats.append({
            'user': f,
            'department': f.department or '—',
            'exam_count': len(exams),
            'student_count': student_count,
            'avg_score': avg_score
        })
        
    # Calculate course stats
    departments = db.session.query(User.department).filter(User.role == 'faculty', User.department != None, User.department != '').distinct().all()
    departments = [d[0] for d in departments]
    course_stats = []
    for dept in departments:
        dept_faculty = User.query.filter_by(role='faculty', department=dept).all()
        dept_faculty_ids = [f.id for f in dept_faculty]
        dept_exams = Exam.query.filter(Exam.creator_id.in_(dept_faculty_ids)).all() if dept_faculty_ids else []
        dept_exam_ids = [e.id for e in dept_exams]
        avg_score = 0
        if dept_exam_ids:
            submitted = StudentExam.query.filter(
                StudentExam.exam_id.in_(dept_exam_ids),
                StudentExam.status == 'submitted'
            ).all()
            if submitted:
                avg_score = round(sum(se.percentage or 0 for se in submitted) / len(submitted), 1)
        course_stats.append({
            'department': dept,
            'faculty_count': len(dept_faculty),
            'exam_count': len(dept_exams),
            'avg_score': avg_score
        })

    # Recent exams, with where each stands right now
    from backend.services.dashboard_service import exam_status, STATUS_LABELS
    in_progress = dict(db.session.query(StudentExam.exam_id, db.func.count(StudentExam.id))
                       .filter(StudentExam.status == 'started').group_by(StudentExam.exam_id))
    recent_exams_data = []
    for e in Exam.query.order_by(Exam.id.desc()).limit(8).all():
        status = exam_status(e, in_progress.get(e.id, 0))
        recent_exams_data.append({
            'exam': e,
            'creator': e.creator,
            'attempt_count': StudentExam.query.filter_by(exam_id=e.id).count(),
            'live': in_progress.get(e.id, 0),
            'status': status,
            'status_label': STATUS_LABELS[status],
        })
    live_exams = sum(1 for n in in_progress.values() if n)
    students_writing = sum(in_progress.values())
    no_department = User.query.filter(User.role == 'faculty',
                                      db.or_(User.department == None, User.department == '')).count()  # noqa: E711

    return render_template('admin/dashboard.html',
                          total_faculty=total_faculty,
                          total_students=total_students,
                          total_exams=total_exams,
                          active_exams=active_exams,
                          total_attempts=total_attempts,
                          submitted_attempts=submitted_attempts,
                          faculty_stats=faculty_stats,
                          course_stats=course_stats,
                          recent_exams_data=recent_exams_data,
                          live_exams=live_exams,
                          students_writing=students_writing,
                          no_department=no_department)

@admin_bp.route('/admin/manage-faculty')
@login_required
def manage_faculty():
    if not is_admin_user():
        return "Access Denied", 403
    
    faculties = User.query.filter_by(role='faculty').all()
    faculty_data = []
    for f in faculties:
        exam_count = Exam.query.filter_by(creator_id=f.id).count()
        faculty_data.append({
            'user': f,
            'exam_count': exam_count
        })
        
    departments = sorted({f.department for f in faculties if f.department})
    return render_template('admin/manage_faculty.html', faculty_data=faculty_data,
                           departments=departments)

@admin_bp.route('/admin/create-faculty', methods=['POST'])
@login_required
def create_faculty():
    is_json = request.is_json or request.headers.get('Accept') == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if not is_admin_user():
        if is_json:
            return jsonify({"success": False, "error": "Access denied"}), 403
        flash('Access denied', 'error')
        return redirect(url_for('admin.manage_faculty'))
    
    from werkzeug.security import generate_password_hash
    
    if request.is_json:
        data = request.get_json() or {}
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        full_name = data.get('full_name')
        employee_id = data.get('employee_id')
        phone = data.get('phone')
        department = data.get('department')
    else:
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        full_name = request.form.get('full_name')
        employee_id = request.form.get('employee_id')
        phone = request.form.get('phone')
        department = request.form.get('department')
    
    username = (username or '').strip()
    email = (email or '').strip().lower()
    password = password or ''
    if not username or not email:
        if is_json:
            return jsonify({"success": False, "error": "Username and email are required."}), 400
        flash('Username and email are required.', 'error')
        return redirect(url_for('admin.manage_faculty'))
    if password and len(password) < 8:
        if is_json:
            return jsonify({"success": False, "error": "A password needs at least 8 characters, or leave it blank for a temporary one."}), 400
        flash('A password needs at least 8 characters, or leave it blank for a temporary one.', 'error')
        return redirect(url_for('admin.manage_faculty'))
    # Blank password: a random temporary one, shown once to the admin. Either
    # way the new faculty member chooses their own at first sign-in.
    from backend.services import account_service
    temporary = None
    if not password:
        temporary = password = account_service.generate_temp_password()
        
    if User.query.filter_by(username=username).first():
        if is_json:
            return jsonify({"success": False, "error": "Username already exists"}), 400
        flash('Username already exists', 'error')
        return redirect(url_for('admin.manage_faculty'))

    if User.query.filter_by(email=email).first():
        if is_json:
            return jsonify({"success": False, "error": "Email already exists"}), 400
        flash('Email already exists', 'error')
        return redirect(url_for('admin.manage_faculty'))

    emp_id = employee_id.strip() if employee_id else None
    if not emp_id:
        emp_id = None

    if emp_id and User.query.filter_by(employee_id=emp_id).first():
        if is_json:
            return jsonify({"success": False, "error": "Employee ID already exists"}), 400
        flash('Employee ID already exists', 'error')
        return redirect(url_for('admin.manage_faculty'))

    new_faculty = User(
        username=username,
        email=email,
        password_hash=generate_password_hash(password, method='pbkdf2:sha256'),
        role='faculty',
        full_name=full_name,
        employee_id=emp_id,
        phone=phone.strip() if phone else None,
        department=department.strip() if department else None,
        is_verified=True,
        password_changed=False,
    )

    db.session.add(new_faculty)
    db.session.commit()
    current_app.logger.info('admin %s created faculty %s', current_user.id, new_faculty.id)

    if is_json:
        return jsonify({"success": True, "id": new_faculty.id,
                        "message": f"{new_faculty.full_name or username} can now sign in.",
                        "temporary_password": temporary})
    flash(f"{new_faculty.full_name or username} can now sign in"
          + (f" with the temporary password {temporary}." if temporary else '.'), 'success')
    return redirect(url_for('admin.manage_faculty'))


@admin_bp.route('/admin/faculty/<int:faculty_id>/reset-password', methods=['POST'])
@login_required
def reset_faculty_password(faculty_id):
    """New temporary password for a faculty member, shown once to the admin."""
    if not is_admin_user():
        return jsonify({"success": False, "error": "Access denied"}), 403
    faculty = User.query.get_or_404(faculty_id)
    if faculty.role != 'faculty':
        return jsonify({"success": False, "error": "Only faculty passwords can be reset here."}), 400
    from werkzeug.security import generate_password_hash
    from backend.services import account_service
    temporary = account_service.generate_temp_password()
    faculty.password_hash = generate_password_hash(temporary, method='pbkdf2:sha256')
    faculty.password_changed = False
    faculty.password_changed_at = None
    faculty.password_hint = None
    db.session.commit()
    current_app.logger.info('admin %s reset password of faculty %s', current_user.id, faculty.id)
    return jsonify({"success": True, "temporary_password": temporary})

@admin_bp.route('/admin/delete-faculty/<int:faculty_id>', methods=['POST'])
@login_required
def delete_faculty(faculty_id):
    if not is_admin_user():
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    faculty = User.query.get_or_404(faculty_id)
    if faculty.role != 'faculty':
        return jsonify({"success": False, "error": "User is not faculty"}), 400

    from backend.services import account_service
    ok, message = account_service.delete_faculty(faculty, current_user)
    if not ok:
        return jsonify({"success": False, "error": message}), 409
    return jsonify({"success": True, "message": message})

@admin_bp.route('/admin/students')
@login_required
def admin_students():
    if not is_admin_user():
        return "Access Denied", 403
    q = (request.args.get('q') or '').strip()
    batch = (request.args.get('batch') or '').strip()
    department = (request.args.get('department') or '').strip()

    query = User.query.filter_by(role='student')
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            User.full_name.ilike(like), User.username.ilike(like),
            User.email.ilike(like), User.prn_number.ilike(like),
            User.batch.ilike(like),
        ))
    if batch:
        query = query.filter_by(batch=batch)
    if department:
        query = query.filter_by(department=department)
    students = query.order_by(User.full_name).all()

    # One grouped query for every student's submitted-attempt stats
    stats = dict(
        (sid, (count, avg)) for sid, count, avg in db.session.query(
            StudentExam.student_id,
            db.func.count(StudentExam.id),
            db.func.avg(StudentExam.percentage),
        ).filter(StudentExam.status == 'submitted').group_by(StudentExam.student_id)
    )
    student_data = []
    for s in students:
        count, avg = stats.get(s.id, (0, None))
        student_data.append({
            'user': s,
            'exam_count': count,
            'avg_score': round(avg or 0, 1),
        })

    def _distinct(col):
        rows = db.session.query(col).filter(
            User.role == 'student', col != None, col != '').distinct().all()
        return sorted(r[0] for r in rows)

    return render_template('admin/students.html',
                           student_data=student_data,
                           batches=_distinct(User.batch),
                           departments=_distinct(User.department),
                           q=q,
                           selected_batch=batch,
                           selected_department=department)

@admin_bp.route('/admin/all-exams')
@login_required
def admin_all_exams():
    if not is_admin_user():
        return "Access Denied", 403
    from backend.models import Question
    exams = Exam.query.order_by(Exam.created_at.desc()).all()
    question_counts = dict(db.session.query(
        Question.exam_id, db.func.count(Question.id)).group_by(Question.exam_id))
    attempt_stats = dict(
        (eid, (count, avg)) for eid, count, avg in db.session.query(
            StudentExam.exam_id,
            db.func.count(StudentExam.id),
            db.func.avg(db.case((StudentExam.status == 'submitted', StudentExam.percentage))),
        ).group_by(StudentExam.exam_id)
    )
    from backend.services.dashboard_service import exam_status, STATUS_LABELS
    in_progress = dict(db.session.query(StudentExam.exam_id, db.func.count(StudentExam.id))
                       .filter(StudentExam.status == 'started').group_by(StudentExam.exam_id))
    exam_data = []
    for e in exams:
        count, avg = attempt_stats.get(e.id, (0, None))
        status = exam_status(e, in_progress.get(e.id, 0))
        exam_data.append({
            'status': status,
            'status_label': STATUS_LABELS[status],
            'live': in_progress.get(e.id, 0),
            'exam': e,
            'creator': e.creator,
            'question_count': question_counts.get(e.id, 0),
            'attempt_count': count,
            'avg_score': round(avg or 0, 1),
        })
    return render_template('admin/all_exams.html', exam_data=exam_data)

@admin_bp.route('/admin/sql_console', methods=['GET'])
@login_required
def admin_sql_console():
    if not is_admin_user():
        return "Access denied", 403
    return render_template('admin/sql_console.html')

@admin_bp.route('/admin/sql_console/run', methods=['POST'])
@login_required
def admin_sql_run():
    # Raw SQL reads every table — password hashes, face templates, other
    # faculty's papers — so it is admin-only. Faculty isolation is meaningless
    # if faculty can SELECT around it.
    if not is_admin_user():
        return jsonify({"success": False, "error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    sql = (payload.get('sql') or '').strip()
    if not sql:
        return jsonify({"success": False, "error": "No SQL provided"}), 400

    first_word = sql.split(None, 1)[0].lower()
    read_only = first_word in ("select", "pragma", "with", "explain")

    # Writes need an explicit opt-in for this one run: a stray UPDATE or
    # DELETE typed into a query box should not change exam data.
    if not read_only and not payload.get('allow_write'):
        return jsonify({"success": False, "needs_write": True,
                        "error": "This statement changes data. Turn on Allow changes to run it."}), 400

    try:
        engine = db.engine
        if read_only:
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                columns = list(result.keys())
                rows = [{k: _json_cell(v) for k, v in r._mapping.items()}
                        for r in result.fetchmany(1000)]
            return jsonify({
                "success": True,
                "type": "select",
                "columns": columns,
                "rows": rows,
                "rowcount": len(rows)
            })
        else:
            with engine.begin() as conn:
                result = conn.execute(text(sql))
                affected = result.rowcount or 0
            current_app.logger.warning('admin %s ran SQL write (%s rows): %s',
                                       current_user.id, affected, sql[:500])
            return jsonify({
                "success": True,
                "type": "update",
                "message": f"Done. Rows affected: {affected}"
            })
    except Exception as e:
        current_app.logger.info('admin SQL error: %s', e)
        return jsonify({"success": False, "error": str(e).split('\n')[0]}), 400
