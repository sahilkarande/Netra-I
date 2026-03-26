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
from models import User, Exam, Question, StudentExam, StudentAnswer, ActivityLog

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


def create_app():
    """Application factory"""
    app = Flask(
        __name__,
        template_folder='frontend/templates',
        static_folder='frontend/static'
    )
    
    # Configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
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
    login_manager.login_view = 'login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    login_manager.session_protection = 'strong'

    # ──────────────────────────────────────────────────────────────
    # INITIALIZE SOCKET.IO ⚠️ CRITICAL FOR BINARY PROCTORING
    # ──────────────────────────────────────────────────────────────
    from backend.routes import socketio
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
            'login', 'logout', 'register', 'verify_otp',
            'student_dashboard', 'faculty_dashboard', 'admin_dashboard'
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
        return redirect(url_for('login'))

    # ──────────────────────────────────────────────────────────────
    # ROUTE REGISTRATION
    # ──────────────────────────────────────────────────────────────
    with app.app_context():
        from backend.routes import register_routes
        register_routes(app)
        db.create_all()
        print("✅ Database initialized!")

    # ──────────────────────────────────────────────────────────────
    # ADMIN SQL CONSOLE (For Admin Only)
    # ──────────────────────────────────────────────────────────────
    def is_admin_user():
        """Check if current user is admin"""
        return getattr(current_user, "role", None) == "admin"

    @app.route("/admin/sql_console", methods=["GET"])
    @login_required
    def admin_sql_console():
        if not is_admin_user():
            return "Access Denied", 403
        return render_template("admin_sql_console.html")

    @app.route("/admin/sql_console/run", methods=["POST"])
    @login_required
    def admin_sql_run():
        if not is_admin_user():
            return jsonify({"success": False, "error": "Access denied"}), 403

        payload = request.get_json() or {}
        sql = (payload.get("sql") or "").strip()
        if not sql:
            return jsonify({"success": False, "error": "Empty SQL query"}), 400

        try:
            engine = db.engine
            first_word = sql.split(None, 1)[0].lower()

            # SELECT / DQL
            if first_word in ("select", "with", "pragma"):
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

            # DML / DDL
            else:
                with engine.begin() as conn:
                    result = conn.execute(text(sql))
                    affected = result.rowcount if result.rowcount is not None else 0
                return jsonify({
                    "success": True,
                    "type": "update",
                    "message": f"Statement executed successfully. Rows affected: {affected}"
                })

        except Exception as e:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

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
        cert_file = os.path.join(os.path.dirname(__file__), 'cert.pem')
        key_file = os.path.join(os.path.dirname(__file__), 'key.pem')
        
        if os.path.exists(cert_file) and os.path.exists(key_file):
            ssl_context = (cert_file, key_file)
            protocol = "https"
            print("✅ SSL enabled with cert.pem / key.pem")
        else:
            print("⚠️  SSL flag set but cert.pem/key.pem not found!")
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
        print(f"  Camera:      ENABLED (secure context via HTTPS)")
    else:
        print(f"  Camera:      Localhost only (use --ssl for LAN)")
    print("")
    print("  Features:")
    print("   ✓ PRN Validation (12-digit)")
    print("   ✓ Roll ID & Employee ID Support")
    print("   ✓ OTP Email Verification")
    print("   ✓ Smart Session Management")
    print("   ✓ Back Button Fix (no reload to login)")
    print("   ✓ PDF Result Download")
    print("   ✓ Student Analytics Dashboard")
    print("   ✓ Real-time Binary Proctoring (Socket.IO)")
    print("   ✓ Fast Face Calibration (<0.5s)")
    print("   ✓ Violation Detection & Auto-Submit")
    print("   ✓ Admin SQL Console")
    print("   ✓ Bulk Import/Export (CSV/Excel)")
    print("   ✓ Multi-Faculty Data Isolation")
    print("   ✓ Secure Cache Control")
    if use_ssl:
        print("   ✓ HTTPS / SSL for LAN camera access")
        print("   ✓ Permissions-Policy camera headers")
    print("")
    if not use_ssl:
        print("  ⚠️  To enable camera over LAN, start with:")
        print(f"     python app.py --ssl")
        print(f"     OR bash scripts/start_lan_exam.sh")
        print("")
    else:
        print("  Student Instructions:")
        print(f"    1. Open Chrome/Edge browser")
        print(f"    2. Navigate to: {protocol}://{lan_ip}:{port}")
        print(f"    3. Click 'Advanced' > 'Proceed to site'")
        print(f"    4. Allow camera when prompted")
        print("")
    print("=" * 70 + "\n")

    # ──────────────────────────────────────────────────────────────
    # ⚠️ CRITICAL: USE SOCKETIO.RUN() NOT APP.RUN()
    # ──────────────────────────────────────────────────────────────
    from backend.routes import socketio
    
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
