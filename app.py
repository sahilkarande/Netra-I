"""
Secure Proctored Exam Platform
Main Application File - Enhanced Version with Socket.IO Binary Proctoring
Now with: HTTPS/LAN support, camera permission headers, scalability for ~300 users
"""

import eventlet
eventlet.monkey_patch()


from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from flask_login import login_required, current_user, logout_user
import os
import sys
import socket
from dotenv import load_dotenv
from sqlalchemy import text
from backend.database import db, login_manager
import traceback
from datetime import timedelta
from backend.models import User, Exam, Question, StudentExam, StudentAnswer, ActivityLog

# Load environment variables
load_dotenv()


def get_lan_ip():
    """Detect the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


# Columns added after a table's first creation. db.create_all() creates missing
# TABLES but never adds columns to a table that already exists, so anything
# introduced later has to be ALTERed in here.
LATE_COLUMNS = {
    'users': {
        'password_changed': 'BOOLEAN DEFAULT 0',
        'password_changed_at': 'DATETIME',
        'password_hint': 'VARCHAR(255)',
        'default_results_published': 'BOOLEAN DEFAULT 0',
        'default_show_leaderboard': 'BOOLEAN DEFAULT 1',
    },
    'face_enrollment': {
        'is_locked': 'BOOLEAN DEFAULT 1',
        'enrolled_by': 'INTEGER',
        'reset_by': 'INTEGER',
        'reset_at': 'DATETIME',
        'liveness_score': 'FLOAT',
        'liveness_method': 'VARCHAR(40)',
    },
    'face_login_attempt': {
        'liveness_passed': 'BOOLEAN',
        'spoof_score': 'FLOAT',
        'liveness_detail': 'TEXT',
        'stage': 'VARCHAR(20)',
    },
}


def ensure_schema_columns():
    """Lightweight additive migration for columns added after first deploy."""
    from sqlalchemy import inspect

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    for table, additions in LATE_COLUMNS.items():
        if table not in tables:
            continue        # create_all() will have made it with every column

        existing = {col['name'] for col in inspector.get_columns(table)}
        for name, ddl in additions.items():
            if name in existing:
                continue
            try:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                db.session.commit()
                print(f"✅ Added {table}.{name}")
            except Exception as e:
                db.session.rollback()
                print(f"⚠️  Could not add {table}.{name}: {e}")


# Kept as an alias so older call sites keep working.
ensure_user_columns = ensure_schema_columns


def load_secret_key(instance_path):
    """SECRET_KEY from the environment, else a random key persisted on disk.

    A hard-coded fallback would let anyone who has read this repository forge
    session cookies, so the fallback is generated once per installation.
    """
    key = os.getenv('SECRET_KEY')
    if key:
        return key

    import secrets
    os.makedirs(instance_path, exist_ok=True)
    key_file = os.path.join(instance_path, '.secret_key')
    try:
        with open(key_file) as f:
            key = f.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass

    key = secrets.token_hex(32)
    with open(key_file, 'w') as f:
        f.write(key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    print(f"🔑 Generated a new SECRET_KEY in {key_file}")
    return key


STAFF_ROLES = ('faculty', 'admin')


def create_app():
    """Application factory"""
    app = Flask(
        __name__,
        template_folder='frontend/templates',
        static_folder='frontend/static'
    )
    
    # Configuration
    app.config['SECRET_KEY'] = load_secret_key(app.instance_path)
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///exam_platform.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
    app.config['TEMPLATES_AUTO_RELOAD'] = True  # Always reload templates (even with SSL/no-debug)
    
    # ──────────────────────────────────────────────────────────────
    # Session configuration
    # ──────────────────────────────────────────────────────────────
    use_ssl = '--ssl' in sys.argv or os.getenv('USE_SSL', 'false').lower() == 'true'
    
    app.config['SESSION_COOKIE_SECURE'] = use_ssl        # True when HTTPS is enabled
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = 3600       # 1 hour
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True

    # ──────────────────────────────────────────────────────────────
    # INITIALIZE EXTENSIONS
    # ──────────────────────────────────────────────────────────────
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    login_manager.session_protection = 'strong'

    # ──────────────────────────────────────────────────────────────
    # INITIALIZE SOCKET.IO ⚠️ CRITICAL FOR BINARY PROCTORING
    # ──────────────────────────────────────────────────────────────
    from backend.socket_io import socketio
    socketio.init_app(app)
    print("✅ Socket.IO initialized for binary proctoring")

    # ──────────────────────────────────────────────────────────────
    # SECURITY & CAMERA PERMISSION HEADERS
    # ──────────────────────────────────────────────────────────────
    @app.after_request
    def add_security_headers(response):
        """
        Add headers that:
        1. Allow camera/microphone access (Permissions-Policy)
        2. Prevent caching of auth pages
        3. Support cross-origin for LAN access
        """
        endpoint = request.endpoint or ""

        # ── Camera & Feature Permissions ──
        # This tells the browser that camera and microphone are allowed
        # for the current origin (self). CRITICAL for getUserMedia() to work.
        response.headers['Permissions-Policy'] = (
            'camera=(self), microphone=(self), fullscreen=(self), '
            'display-capture=(self)'
        )
        
        # ── Feature-Policy (legacy browsers) ──
        response.headers['Feature-Policy'] = (
            "camera 'self'; microphone 'self'; fullscreen 'self'"
        )

        # ── Cache Control ──
        # Never cache authentication or dashboard routes
        if endpoint in [
            'auth.login', 'auth.logout', 'auth.register', 'auth.verify_otp',
            'student.student_dashboard', 'faculty.faculty_dashboard', 'admin.admin_dashboard'
        ]:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            return response

        # Public pages
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    # ──────────────────────────────────────────────────────────────
    # LOGIN MANAGER HELPERS
    # ──────────────────────────────────────────────────────────────
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('auth.login'))

    # ──────────────────────────────────────────────────────────────
    # ROUTE REGISTRATION
    # ──────────────────────────────────────────────────────────────
    with app.app_context():
        from backend.routes import register_blueprints
        register_blueprints(app)
        db.create_all()
        ensure_schema_columns()
        print("✅ Database initialized!")

    # ──────────────────────────────────────────────────────────────
    # FIRST-LOGIN PASSWORD CHANGE ENFORCEMENT
    # ──────────────────────────────────────────────────────────────
    @app.before_request
    def protect_violation_evidence():
        """Evidence photos are served from /static but must not be public.

        They are filed under guessable paths (exam title + PRN), so without this
        anyone on the network could fetch another student's webcam frames.
        Staff view them through faculty.violation_evidence, which also checks
        exam ownership.
        """
        if not request.path.startswith('/static/violations'):
            return None
        if current_user.is_authenticated and current_user.role in STAFF_ROLES:
            return None
        return 'Forbidden', 403

    @app.before_request
    def force_password_change():
        """Students logging in for the first time must set their own password."""
        if request.path.startswith('/socket.io') or request.path.startswith('/static'):
            return None

        if not current_user.is_authenticated:
            return None

        if not getattr(current_user, 'must_change_password', False):
            return None

        allowed = {
            'static',
            'auth.change_password',
            'auth.check_password_status',
            'auth.logout',
            'auth.login',
            'auth.password_hint',
            # Face enrolment lives on the change-password page itself
            'auth.face_enroll',
            'auth.face_status',
            'auth.face_delete',
            'auth.face_challenge',
        }
        if (request.endpoint or 'static') in allowed:
            return None

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
                request.accept_mimetypes.best == 'application/json':
            return jsonify({
                'success': False,
                'must_change_password': True,
                'redirect': url_for('auth.change_password')
            }), 403

        flash('Please set your own password before continuing.', 'warning')
        return redirect(url_for('auth.change_password'))



    # ──────────────────────────────────────────────────────────────
    # ROUTE LIST DEBUG INFO
    # ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("REGISTERED ROUTES")
    print("=" * 70)
    for rule in app.url_map.iter_rules():
        if rule.endpoint != 'static':
            methods = ','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
            print(f"{rule.endpoint:40s} {methods:10s} {rule.rule}")
    print("=" * 70 + "\n")

    return app


# ──────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = create_app()
    
    # Parse CLI args
    use_ssl = '--ssl' in sys.argv
    port = int(os.getenv('PORT', 8800))
    lan_ip = get_lan_ip()

    if use_ssl:
        debug = False
        print("ℹ️  Debug mode disabled (required for SSL stability)")
    else:
        debug = os.getenv('FLASK_DEBUG', 'True') == 'True'
    
    # SSL context
    ssl_context = None
    protocol = "http"
    if use_ssl:
        cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certs')
        cert_file = os.path.join(cert_dir, 'cert.pem')
        key_file = os.path.join(cert_dir, 'key.pem')
        
        if os.path.exists(cert_file) and os.path.exists(key_file):
            ssl_context = (cert_file, key_file)
            protocol = "https"
            print("✅ SSL enabled with certs/cert.pem + certs/key.pem")
        else:
            print("⚠️  SSL flag set but certs/cert.pem / certs/key.pem not found!")
            print("    Run: bash scripts/generate_lan_cert.sh")
            print("    Falling back to HTTP (camera may not work over LAN)")
            print("")

    print("\n" + "=" * 70)
    print("  PROCTORED EXAM PLATFORM - PRODUCTION READY")
    print("=" * 70)
    print(f"  Local URL:   {protocol}://localhost:{port}")
    print(f"  Network URL: {protocol}://{lan_ip}:{port}")
    print(f"  Protocol:    {protocol.upper()}")
    if use_ssl:
        print(f"")
    else:
        print(f"")

    if use_ssl:
        print("")
    print("")
    if not use_ssl:
        print("  ⚠️  To enable camera over LAN, start with:")
    else:
        print(f"    2. Navigate to: {protocol}://{lan_ip}:{port}")

        print("")
    print("=" * 70 + "\n")

    # ──────────────────────────────────────────────────────────────
    # ⚠️ CRITICAL: USE SOCKETIO.RUN() NOT APP.RUN()
    # ──────────────────────────────────────────────────────────────
    from backend.socket_io import socketio
    
    run_kwargs = {
        "debug": debug,
        "host": '0.0.0.0',
        "port": port,
        "allow_unsafe_werkzeug": True,
        "use_reloader": not use_ssl
    }
    
    if use_ssl and ssl_context:
        run_kwargs["certfile"] = cert_file
        run_kwargs["keyfile"] = key_file
        
    socketio.run(app, **run_kwargs)
