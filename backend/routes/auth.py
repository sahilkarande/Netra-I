from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

from backend.database import db
from backend.models import User
from backend.utils.email_utils import send_otp_email

auth_bp = Blueprint('auth', __name__)

@auth_bp.after_app_request
def add_cache_control(response):
    """Cache control to prevent back button showing login after logout"""
    endpoint = request.endpoint or ""

    # Auth routes should never cache
    if endpoint in ['auth.login', 'auth.logout', 'auth.register', 'auth.verify_otp']:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    # Allow short private caching for dashboards
    if current_user.is_authenticated:
        response.headers['Cache-Control'] = 'private, max-age=600, must-revalidate'
    else:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'

    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@auth_bp.route('/')
def index():
    print(f"DEBUG: Index hit. Auth: {current_user.is_authenticated}")
    if current_user.is_authenticated:
        if current_user.role == 'faculty':
            return redirect(url_for('faculty.faculty_dashboard'))
        elif current_user.role == 'student':
            return redirect(url_for('student.student_dashboard'))
        elif current_user.role == 'admin':
            return redirect(url_for('admin.admin_dashboard'))
    return render_template('index.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'student')
        
        # Additional fields for students
        full_name = request.form.get('full_name')
        prn_number = request.form.get('prn_number')
        roll_id = request.form.get('roll_id')
        
        # Additional fields for faculty
        employee_id = request.form.get('employee_id')

        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
            return render_template('register.html')
            
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return render_template('register.html')

        # Create new user but mark as not verified
        new_user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password, method='pbkdf2:sha256'),
            role=role,
            full_name=full_name,
            prn_number=prn_number,
            roll_id=roll_id,
            employee_id=employee_id,
            is_verified=False
        )
        
        # Generate OTP
        import random
        otp = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        new_user.otp = otp
        new_user.otp_expiry = datetime.utcnow() # Add expiry logic if needed
        
        try:
            db.session.add(new_user)
            db.session.commit()
            
            # Send OTP email
            if send_otp_email(email, otp):
                session['verify_email'] = email
                flash('An OTP has been sent to your email. Please verify to continue.', 'info')
                return redirect(url_for('auth.verify_otp'))
            else:
                flash('Failed to send OTP. Please try again.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Registration failed: {str(e)}', 'error')
            
    return render_template('register.html')

@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    email = session.get('verify_email')
    if not email:
        return redirect(url_for('auth.register'))
        
    if request.method == 'POST':
        otp_input = request.form.get('otp')
        user = User.query.filter_by(email=email).first()
        
        if user and user.otp == otp_input:
            user.is_verified = True
            user.otp = None
            db.session.commit()
            flash('Email verified! You can now login.', 'success')
            session.pop('verify_email', None)
            return redirect(url_for('auth.login'))
        else:
            flash('Invalid OTP', 'error')
            
    return render_template('verify_otp.html', email=email)

@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    email = session.get('verify_email')
    if not email:
        return jsonify({"success": False, "message": "Session expired"}), 400
        
    user = User.query.filter_by(email=email).first()
    if user:
        import random
        otp = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        user.otp = otp
        db.session.commit()
        if send_otp_email(email, otp):
            return jsonify({"success": True, "message": "OTP resent"})
    return jsonify({"success": False, "message": "Failed to resend OTP"}), 500

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            if not user.is_verified:
                session['verify_email'] = user.email
                flash('Please verify your email first.', 'warning')
                return redirect(url_for('auth.verify_otp'))
                
            login_user(user)
            return redirect(url_for('auth.index'))
        else:
            flash('Invalid username or password', 'error')
            
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Change password page - forced for first-time students, optional for faculty"""
    is_forced = (current_user.role == 'student' and not getattr(current_user, 'password_changed', False))
    
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not old_password or not new_password or not confirm_password:
            flash('❌ All fields are required!', 'error')
            return redirect(url_for('auth.change_password'))
        
        if not check_password_hash(current_user.password_hash, old_password):
            flash('❌ Current password is incorrect!', 'error')
            return redirect(url_for('auth.change_password'))
        
        if new_password != confirm_password:
            flash('❌ New passwords do not match!', 'error')
            return redirect(url_for('auth.change_password'))
        
        if len(new_password) < 6:
            flash('❌ New password must be at least 6 characters long!', 'error')
            return redirect(url_for('auth.change_password'))
        
        if check_password_hash(current_user.password_hash, new_password):
            flash('❌ New password must be different from current password!', 'error')
            return redirect(url_for('auth.change_password'))
        
        try:
            current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
            current_user.password_changed = True
            current_user.password_changed_at = datetime.utcnow()
            db.session.commit()
            
            flash('✅ Password changed successfully!', 'success')
            return redirect(url_for('auth.index'))
                
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Error changing password: {str(e)}', 'error')
            return redirect(url_for('auth.change_password'))
    
    return render_template('change_password.html', is_forced=is_forced)

@auth_bp.route('/check-password-status')
@login_required
def check_password_status():
    """API endpoint to check if user needs to change password"""
    needs_change = (current_user.role == 'student' and not current_user.password_changed)
    return jsonify({
        'needs_change': needs_change,
        'role': current_user.role,
        'password_changed': current_user.password_changed
    })
