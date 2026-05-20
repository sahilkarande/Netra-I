from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text
import traceback

from backend.database import db
from backend.models import User, Exam

admin_bp = Blueprint('admin', __name__)

def is_admin_user():
    """Check if current user is admin"""
    return getattr(current_user, "role", None) == "admin"

@admin_bp.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if not is_admin_user():
        return redirect(url_for('auth.index'))
    
    faculty_count = User.query.filter_by(role='faculty').count()
    student_count = User.query.filter_by(role='student').count()
    exam_count = Exam.query.count()
    
    recent_faculty = User.query.filter_by(role='faculty').order_by(User.id.desc()).limit(5).all()
    
    return render_template('admin/dashboard.html', 
                         faculty_count=faculty_count,
                         student_count=student_count,
                         exam_count=exam_count,
                         recent_faculty=recent_faculty)

@admin_bp.route('/admin/manage-faculty')
@login_required
def manage_faculty():
    if not is_admin_user():
        return "Access Denied", 403
    faculties = User.query.filter_by(role='faculty').all()
    return render_template('admin/manage_faculty.html', faculties=faculties)

@admin_bp.route('/admin/create-faculty', methods=['POST'])
@login_required
def create_faculty():
    if not is_admin_user():
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    from werkzeug.security import generate_password_hash
    
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    full_name = request.form.get('full_name')
    employee_id = request.form.get('employee_id')
    
    if User.query.filter_by(username=username).first():
        flash('Username already exists', 'error')
        return redirect(url_for('admin.manage_faculty'))

    new_faculty = User(
        username=username,
        email=email,
        password_hash=generate_password_hash(password, method='pbkdf2:sha256'),
        role='faculty',
        full_name=full_name,
        employee_id=employee_id,
        is_verified=True
    )
    
    db.session.add(new_faculty)
    db.session.commit()
    flash('Faculty created successfully!', 'success')
    return redirect(url_for('admin.manage_faculty'))

@admin_bp.route('/admin/delete-faculty/<int:faculty_id>', methods=['POST'])
@login_required
def delete_faculty(faculty_id):
    if not is_admin_user():
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    faculty = User.query.get_or_404(faculty_id)
    if faculty.role != 'faculty':
        return jsonify({"success": False, "error": "User is not faculty"}), 400
        
    db.session.delete(faculty)
    db.session.commit()
    flash('Faculty deleted successfully!', 'success')
    return redirect(url_for('admin.manage_faculty'))

@admin_bp.route('/admin/students')
@login_required
def admin_students():
    if not is_admin_user():
        return "Access Denied", 403
    students = User.query.filter_by(role='student').all()
    return render_template('admin/students.html', students=students)

@admin_bp.route('/admin/all-exams')
@login_required
def admin_all_exams():
    if not is_admin_user():
        return "Access Denied", 403
    exams = Exam.query.all()
    return render_template('admin/all_exams.html', exams=exams)

@admin_bp.route('/admin/sql_console', methods=['GET'])
@login_required
def admin_sql_console():
    if getattr(current_user, "role", None) not in ("admin", "faculty"):
        return "Access denied", 403
    return render_template('admin_sql_console.html')

@admin_bp.route('/admin/sql_console/run', methods=['POST'])
@login_required
def admin_sql_run():
    payload = request.get_json() or {}
    sql = (payload.get('sql') or '').strip()
    if not sql:
        return jsonify({"success": False, "error": "No SQL provided"}), 400

    role = getattr(current_user, "role", None)
    first_word = sql.split(None, 1)[0].lower()

    if role == "faculty" and first_word not in ("select", "pragma", "with"):
        return jsonify({
            "success": False,
            "error": "Faculty can only run SELECT queries"
        }), 403

    try:
        engine = db.engine
        if first_word in ("select", "pragma", "with"):
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                columns = result.keys()
                rows = [dict(r._mapping) for r in result.fetchall()]
            return jsonify({
                "success": True,
                "type": "select",
                "columns": columns,
                "rows": rows,
                "rowcount": len(rows)
            })
        else:
            if role != "admin":
                return jsonify({"success": False, "error": "Access denied"}), 403
            with engine.begin() as conn:
                result = conn.execute(text(sql))
                affected = result.rowcount or 0
            return jsonify({
                "success": True,
                "type": "update",
                "message": f"✅ Query executed. Rows affected: {affected}"
            })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
