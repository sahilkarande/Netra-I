from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import json
import secrets
import string
from datetime import datetime

from backend.database import db
from backend.models import User
from backend.services import face_service
from backend.services import liveness_service
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

def _find_user_by_identifier(login_input: str):
    """Resolve a login box entry against username, email, PRN or roll ID."""
    if not login_input:
        return None
    return User.query.filter(
        db.or_(
            User.username == login_input,
            User.email == login_input.lower(),
            User.prn_number == login_input,
            User.roll_id == login_input,
        )
    ).first()


@auth_bp.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'faculty':
            return redirect(url_for('faculty.faculty_dashboard'))
        elif current_user.role == 'student':
            return redirect(url_for('student.student_dashboard'))
        elif current_user.role == 'admin':
            return redirect(url_for('admin.admin_dashboard'))
    return render_template('index.html')

def _new_otp():
    """Six digits from a CSPRNG — `random` is predictable."""
    return ''.join(secrets.choice(string.digits) for _ in range(6))


def _otp_key(email):
    return f'otp:{(email or "").lower()}'


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        full_name = (request.form.get('full_name') or '').strip() or None
        prn_number = (request.form.get('prn_number') or '').strip() or None
        roll_id = (request.form.get('roll_id') or '').strip() or None

        # Self-registration only ever creates students. Faculty accounts are
        # made by an admin; trusting a posted 'role' would let anyone register
        # as admin.
        role = 'student'

        if not username or not email or not password:
            flash('Username, email and password are required.', 'error')
            return render_template('register.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template('register.html')

        if prn_number and not (prn_number.isdigit() and len(prn_number) == 12):
            flash('PRN must be exactly 12 digits.', 'error')
            return render_template('register.html')

        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
            return render_template('register.html')

        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return render_template('register.html')

        if prn_number and User.query.filter_by(prn_number=prn_number).first():
            flash('PRN already registered', 'error')
            return render_template('register.html')

        otp = _new_otp()
        new_user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password, method='pbkdf2:sha256'),
            role=role,
            full_name=full_name,
            prn_number=prn_number,
            roll_id=roll_id,
            is_verified=False,
            # They chose this password themselves, so no forced change.
            password_changed=True,
            password_changed_at=datetime.utcnow(),
            otp=otp,
            otp_created_at=datetime.utcnow(),
        )

        try:
            db.session.add(new_user)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Registration failed. Please try again.', 'error')
            return render_template('register.html')

        session['verify_email'] = email
        if send_otp_email(email, otp, username, role):
            flash('An OTP has been sent to your email. Please verify to continue.', 'info')
        else:
            flash('Account created, but the OTP email could not be sent. Use "Resend OTP".', 'warning')
        return redirect(url_for('auth.verify_otp'))

    return render_template('register.html')

@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    email = session.get('verify_email')
    if not email:
        return redirect(url_for('auth.register'))

    if request.method == 'POST':
        locked, minutes = face_service.is_locked_out(_otp_key(email))
        if locked:
            flash(f'Too many wrong codes. Try again in {minutes} minute(s).', 'error')
            return render_template('verify_otp.html', email=email)

        otp_input = (request.form.get('otp') or '').strip()
        user = User.query.filter_by(email=email).first()

        # verify_otp() also enforces the 10-minute expiry
        if user and user.verify_otp(otp_input):
            db.session.commit()
            face_service.clear_failures(_otp_key(email))
            flash('Email verified! You can now login.', 'success')
            session.pop('verify_email', None)
            return redirect(url_for('auth.login'))

        face_service.record_failure(_otp_key(email))
        flash('Invalid or expired OTP', 'error')

    return render_template('verify_otp.html', email=email)

@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    email = session.get('verify_email')
    if not email:
        return jsonify({"success": False, "message": "Session expired"}), 400

    user = User.query.filter_by(email=email).first()
    if user and not user.is_verified:
        otp = user.generate_otp()
        db.session.commit()
        if send_otp_email(email, otp, user.username, user.role):
            return jsonify({"success": True, "message": "OTP resent"})
    return jsonify({"success": False, "message": "Failed to resend OTP"}), 500

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))
        
    if request.method == 'POST':
        login_input = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        
        # Throttle password guessing per identifier
        lock_key = f'pw:{login_input.lower()}'
        locked, minutes = face_service.is_locked_out(lock_key)
        if locked:
            flash(f'Too many failed attempts. Try again in {minutes} minute(s).', 'error')
            return render_template('login.html'), 429

        # Support login by username, email, PRN, or roll ID
        user = _find_user_by_identifier(login_input)
        
        if user and check_password_hash(user.password_hash, password):
            face_service.clear_failures(lock_key)
            if not user.is_verified:
                session['verify_email'] = user.email
                flash('Please verify your email first.', 'warning')
                return redirect(url_for('auth.verify_otp'))
                
            # No persistent cookie: on a shared lab PC the next person must not
            # inherit this account. The session cookie dies with the browser and
            # an idle timeout (app.enforce_idle_timeout) covers browsers left open.
            session.clear()
            login_user(user, remember=False)

            # First-time student login -> force a password change before anything else
            if user.must_change_password:
                flash('Welcome! Please set your own password before continuing.', 'info')
                return redirect(url_for('auth.change_password'))

            if user.role == 'faculty':
                return redirect(url_for('faculty.faculty_dashboard'))
            elif user.role == 'student':
                return redirect(url_for('student.student_dashboard'))
            elif user.role == 'admin':
                return redirect(url_for('admin.admin_dashboard'))
            return redirect(url_for('auth.index'))
        else:
            face_service.record_failure(lock_key)
            flash('Invalid username/email/PRN or password', 'error')
            
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('auth.login'))

@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Change password page - forced for first-time students, optional for everyone else"""
    is_forced = current_user.must_change_password

    if request.method == 'POST':
        old_password = request.form.get('old_password') or ''
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        password_hint = (request.form.get('password_hint') or '').strip()

        errors = []

        if not old_password or not new_password or not confirm_password:
            errors.append('All password fields are required.')
        elif not check_password_hash(current_user.password_hash, old_password):
            errors.append('Your current password is incorrect.')
        elif new_password != confirm_password:
            errors.append('New password and confirmation do not match.')
        elif len(new_password) < 6:
            errors.append('New password must be at least 6 characters long.')
        elif new_password == old_password:
            errors.append('New password must be different from your current password.')

        # Password hint rules (mandatory for students, optional otherwise)
        if password_hint:
            if len(password_hint) > 255:
                errors.append('Password hint must be 255 characters or fewer.')
            elif new_password and password_hint.lower().find(new_password.lower()) != -1:
                errors.append('Password hint must not contain your password.')
        elif current_user.role == 'student':
            errors.append('Please add a password hint to help you remember your password.')

        if errors:
            for message in errors:
                flash(message, 'error')
            return render_template(
                'change_password.html',
                is_forced=is_forced,
                hint_value=password_hint
            )

        try:
            current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
            current_user.password_changed = True
            current_user.password_changed_at = datetime.utcnow()
            current_user.password_hint = password_hint or None
            db.session.commit()

            flash('Password changed successfully!', 'success')
            return redirect(url_for('auth.index'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('change_password failed')
            flash('Could not change your password. Please try again.', 'error')
            return render_template(
                'change_password.html',
                is_forced=is_forced,
                hint_value=password_hint
            )

    return render_template(
        'change_password.html',
        is_forced=is_forced,
        hint_value=current_user.password_hint or ''
    )


@auth_bp.route('/password-hint', methods=['POST'])
def password_hint():
    """Return the hint a user saved for themselves (login helper)."""
    login_input = ((request.get_json(silent=True) or request.form).get('username') or '').strip()

    if not login_input:
        return jsonify({'success': False, 'message': 'Enter your username, email or PRN first.'}), 400

    user = _find_user_by_identifier(login_input)

    if user and user.password_hint:
        return jsonify({'success': True, 'hint': user.password_hint})

    # Same response whether the account exists or simply has no hint
    return jsonify({
        'success': False,
        'message': 'No password hint is saved for that account. Please contact your faculty or admin.'
    }), 404


def _client_ip():
    return (request.headers.get('X-Forwarded-For', request.remote_addr or '')
            .split(',')[0].strip())


def _log_face(identifier, stage, user_id=None, success=False, distance=None,
              reason=None, liveness_passed=None, detail=None):
    """Audit a face attempt, including why liveness failed."""
    from backend.models import FaceLoginAttempt
    try:
        spoof = None
        if isinstance(detail, dict):
            spoof = (detail.get('verdict') or {}).get('mean')
        db.session.add(FaceLoginAttempt(
            user_id=user_id,
            identifier=(identifier or '')[:100],
            success=success,
            distance=distance,
            reason=(reason or '')[:100] or None,
            ip_address=_client_ip()[:45] or None,
            liveness_passed=liveness_passed,
            spoof_score=spoof,
            liveness_detail=(json.dumps(detail)[:4000] if detail else None),
            stage=stage,
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Could not log face attempt: {e}")


def _match_sequence(user, detail):
    """Match a liveness sequence against a user's enrolment.

    Identity is decided by the CENTERED frame at the frontal threshold. The
    turned-head frames are only checked at a looser bar, enough to catch a
    person swapping mid-challenge without failing honest users whose descriptor
    drifts when they turn.

    Returns (ok, best_distance, reason).
    """
    descriptors = detail['descriptors']
    center = detail.get('center_index', 0)

    matched, distance, reason = face_service.verify_face(user, descriptors[center])
    if not matched:
        return False, distance, reason

    best = distance if distance is not None else 99.0
    for i, probe in enumerate(descriptors):
        if i == center:
            continue
        ok, d, _r = face_service.verify_face(
            user, probe, threshold=face_service.POSE_MATCH_THRESHOLD)
        if d is not None:
            best = min(best, d)
        if not ok:
            return False, d, 'pose_frame_person_mismatch'

    return True, best, 'match'


@auth_bp.route('/face/challenge', methods=['POST'])
def face_challenge():
    """Issue a single-use liveness challenge.

    The movement sequence is random and server-held, so a pre-recorded video
    cannot satisfy it — the attacker does not know what will be asked.
    """
    purpose = (request.get_json(silent=True) or {}).get('purpose', 'login')
    if purpose not in ('enroll', 'login', 'exam'):
        return jsonify({'success': False, 'message': 'Unknown purpose.'}), 400

    # Enrolment and exam challenges are bound to the signed-in user
    if purpose in ('enroll', 'exam'):
        if not current_user.is_authenticated:
            return jsonify({'success': False, 'message': 'Sign in first.'}), 401
        challenge = liveness_service.issue_challenge(purpose, user_id=current_user.id)
    else:
        challenge = liveness_service.issue_challenge(purpose)

    return jsonify({
        'success': True,
        'deep_pad': liveness_service.DEEP_PAD_AVAILABLE,
        **challenge,
    })


@auth_bp.route('/face/enroll', methods=['POST'])
@login_required
def face_enroll():
    """Register the signed-in user's face, but only after a liveness check.

    Enrolment is one-shot: once stored it is locked, and only faculty or an
    admin can reset it.
    """
    payload = request.get_json(silent=True) or {}
    ip = _client_ip()

    if face_service.enrollment_locked(current_user):
        return jsonify({
            'success': False,
            'locked': True,
            'message': ('Your face is already registered and cannot be changed. '
                        'Ask your faculty or an administrator if it needs resetting.'),
        }), 409

    ok, reason, challenge = liveness_service.consume_challenge(
        payload.get('nonce'), 'enroll', user_id=current_user.id)
    if not ok:
        _log_face(current_user.username, 'enroll', user_id=current_user.id,
                  reason=reason, liveness_passed=False)
        return jsonify({'success': False,
                        'message': 'That liveness check expired. Please start again.'}), 400

    live, reason, detail = face_service.process_liveness_sequence(
        payload.get('steps'), challenge)

    if not live:
        _log_face(current_user.username, 'enroll', user_id=current_user.id,
                  reason=reason, liveness_passed=False, detail=detail)
        return jsonify({
            'success': False,
            'spoof': True,
            'message': _liveness_message(reason),
            'reason': reason,
        }), 403

    mean_spoof = (detail.get('verdict') or {}).get('mean')
    ok, message = face_service.enroll_face(
        current_user,
        detail['descriptors'],
        actor=current_user,
        liveness_score=mean_spoof,
        liveness_method=('deep+heuristic' if liveness_service.DEEP_PAD_AVAILABLE
                         else 'challenge+heuristic'),
        ip_address=ip,
    )

    _log_face(current_user.username, 'enroll', user_id=current_user.id,
              success=ok, reason='enrolled' if ok else 'enroll_failed',
              liveness_passed=True, detail=detail)

    return jsonify({
        'success': ok,
        'message': message,
        'info': face_service.enrollment_info(current_user),
    }), (200 if ok else 400)


def _liveness_message(reason: str) -> str:
    """Turn an internal reason code into something a student can act on."""
    if reason.startswith('presentation_attack'):
        return ('This looks like a photo or a screen rather than a live person. '
                'Please face the camera directly — do not hold up a phone or a printed photo.')
    if 'frames_nearly_identical' in reason:
        return 'The camera image did not change. Please move your head as instructed.'
    if 'action_not_performed' in reason:
        return 'The requested head movement was not detected. Please follow the on-screen steps.'
    if 'no_face_detected' in reason:
        return 'No face was visible in one of the captures. Check your lighting and try again.'
    if 'person_changed_mid_sequence' in reason:
        return 'A different face appeared during the check. Please try again alone.'
    if 'completed_impossibly_fast' in reason:
        return 'The check completed too quickly. Please follow each step in turn.'
    if 'liveness_unavailable' in reason:
        return ('Liveness checking is unavailable on this server, so face sign-in is '
                'disabled. Please use your password.')
    return 'Liveness check failed. Please try again, or sign in with your password.'


@auth_bp.route('/face/status')
@login_required
def face_status():
    """Enrolment state for the signed-in user."""
    return jsonify({
        'success': True,
        'liveness_required': True,
        'deep_pad': liveness_service.DEEP_PAD_AVAILABLE,
        **face_service.enrollment_info(current_user),
    })


@auth_bp.route('/face/delete', methods=['POST'])
@login_required
def face_delete():
    """Students cannot delete a locked enrolment; staff can."""
    ok, message = face_service.delete_enrollment(
        current_user, actor=current_user, ip_address=_client_ip())
    return jsonify({'success': ok, 'message': message}), (200 if ok else 403)


@auth_bp.route('/face/available', methods=['POST'])
def face_available():
    """Does this identifier have a face enrolled? Drives the login UI.

    Deliberately vague: the same answer is given for "no such account" and
    "account exists but has no face", so this cannot enumerate users.
    """
    login_input = ((request.get_json(silent=True) or request.form).get('username') or '').strip()
    if not login_input:
        return jsonify({'success': False, 'available': False}), 400

    user = _find_user_by_identifier(login_input)
    available = bool(
        user
        and user.is_verified
        and face_service.FaceEnrollment.query.filter_by(user_id=user.id, is_active=True).first()
    )
    return jsonify({'success': True, 'available': available})


@auth_bp.route('/face/login', methods=['POST'])
def face_login():
    """Sign in by face: liveness FIRST, then identity.

    Matching never runs on a sequence that failed liveness, so a photo of an
    enrolled student is rejected before its descriptor is ever compared.
    """
    payload = request.get_json(silent=True) or {}
    login_input = (payload.get('username') or '').strip()

    if current_user.is_authenticated:
        return jsonify({'success': False, 'message': 'Already signed in.'}), 400

    if not login_input:
        return jsonify({'success': False,
                        'message': 'Enter your username, email or PRN first.'}), 400

    locked, minutes = face_service.is_locked_out(login_input)
    if locked:
        _log_face(login_input, 'login', reason='locked_out')
        return jsonify({
            'success': False,
            'message': f'Too many failed attempts. Try again in {minutes} minute(s), '
                       'or use your password.',
        }), 429

    ok, reason, challenge = liveness_service.consume_challenge(payload.get('nonce'), 'login')
    if not ok:
        _log_face(login_input, 'login', reason=reason, liveness_passed=False)
        return jsonify({'success': False,
                        'message': 'That liveness check expired. Please start again.'}), 400

    # ── Layer 1-3: prove a live person is in front of the camera ──
    live, reason, detail = face_service.process_liveness_sequence(
        payload.get('steps'), challenge)

    if not live:
        face_service.record_failure(login_input)
        user = _find_user_by_identifier(login_input)
        _log_face(login_input, 'login', user_id=getattr(user, 'id', None),
                  reason=reason, liveness_passed=False, detail=detail)
        return jsonify({
            'success': False,
            'spoof': True,
            'message': _liveness_message(reason),
        }), 403

    # ── Only now does identity matter ──
    user = _find_user_by_identifier(login_input)
    generic = 'Face not recognised. Please try again or sign in with your password.'

    if not user:
        face_service.record_failure(login_input)
        _log_face(login_input, 'login', reason='no_such_user', liveness_passed=True)
        return jsonify({'success': False, 'message': generic}), 401

    if not user.is_verified:
        _log_face(login_input, 'login', user_id=user.id, reason='unverified',
                  liveness_passed=True)
        session['verify_email'] = user.email
        return jsonify({'success': False, 'message': 'Please verify your email first.',
                        'redirect': url_for('auth.verify_otp')}), 403

    ok, distance, match_reason = _match_sequence(user, detail)
    if not ok:
        face_service.record_failure(login_input)
        _log_face(login_input, 'login', user_id=user.id, distance=distance,
                  reason=match_reason, liveness_passed=True, detail=detail)
        return jsonify({'success': False, 'message': generic}), 401

    face_service.clear_failures(login_input)
    _log_face(login_input, 'login', user_id=user.id, success=True,
              distance=distance, reason='match', liveness_passed=True, detail=detail)

    session.clear()
    login_user(user, remember=False)

    if user.must_change_password:
        return jsonify({'success': True, 'redirect': url_for('auth.change_password')})

    dest = {
        'faculty': 'faculty.faculty_dashboard',
        'student': 'student.student_dashboard',
        'admin': 'admin.admin_dashboard',
    }.get(user.role, 'auth.index')

    return jsonify({'success': True, 'redirect': url_for(dest)})


@auth_bp.route('/face/verify-exam', methods=['POST'])
@login_required
def face_verify_exam():
    """Liveness + identity gate before an exam starts.

    Same engine as login, scoped to the signed-in student, so a photo cannot be
    used to hand an exam to someone else.
    """
    payload = request.get_json(silent=True) or {}

    ok, reason, challenge = liveness_service.consume_challenge(
        payload.get('nonce'), 'exam', user_id=current_user.id)
    if not ok:
        _log_face(current_user.username, 'exam', user_id=current_user.id,
                  reason=reason, liveness_passed=False)
        return jsonify({'success': False,
                        'message': 'That liveness check expired. Please start again.'}), 400

    live, reason, detail = face_service.process_liveness_sequence(
        payload.get('steps'), challenge)

    if not live:
        _log_face(current_user.username, 'exam', user_id=current_user.id,
                  reason=reason, liveness_passed=False, detail=detail)
        return jsonify({'success': False, 'spoof': True,
                        'message': _liveness_message(reason), 'reason': reason}), 403

    if not face_service.enrollment_locked(current_user):
        _log_face(current_user.username, 'exam', user_id=current_user.id,
                  reason='not_enrolled', liveness_passed=True, detail=detail)
        return jsonify({
            'success': False,
            'not_enrolled': True,
            'message': 'No registered face found for your account. Please contact your faculty.',
        }), 409

    ok, distance, match_reason = _match_sequence(current_user, detail)
    if not ok:
        _log_face(current_user.username, 'exam', user_id=current_user.id,
                  distance=distance, reason=match_reason,
                  liveness_passed=True, detail=detail)
        _flag_exam_identity(payload.get('student_exam_id'),
                            'face_mismatch_at_start',
                            'Pre-exam identity check failed: the person at the camera '
                            'did not match the registered student.')
        return jsonify({
            'success': False,
            'mismatch': True,
            'message': ('The person at the camera does not match the registered '
                        'student for this account. This has been logged.'),
        }), 403

    _log_face(current_user.username, 'exam', user_id=current_user.id, success=True,
              distance=distance, reason='match', liveness_passed=True, detail=detail)

    return jsonify({'success': True, 'message': 'Identity confirmed.',
                    'distance': round(distance, 4)})


def _flag_exam_identity(student_exam_id, violation_type, message):
    """Record an identity problem against the attempt so faculty can review it."""
    if not student_exam_id:
        return
    try:
        from backend.models import StudentExam
        from backend.services.proctor_service import log_proctor_event

        attempt = StudentExam.query.get(int(student_exam_id))
        if not attempt or attempt.student_id != current_user.id:
            return

        attempt.total_violations = (attempt.total_violations or 0) + 1
        db.session.commit()
        log_proctor_event(attempt.id, violation_type, 'high', message)
    except Exception as e:
        db.session.rollback()
        print(f'Could not flag exam identity issue: {e}')


@auth_bp.route('/face/exam-check', methods=['POST'])
@login_required
def face_exam_check():
    """Periodic in-exam identity check against the REGISTERED face.

    The in-page check compares against a reference captured at calibration,
    which is self-referential — if a substitute sits down before calibration,
    they become their own reference. This compares against the enrolment
    instead, so a swap is caught and flagged for faculty.
    """
    payload = request.get_json(silent=True) or {}
    ok, message, probe = face_service.validate_descriptor(payload.get('descriptor'))
    if not ok:
        return jsonify({'success': False, 'message': message}), 400

    if not face_service.enrollment_locked(current_user):
        return jsonify({'success': True, 'checked': False,
                        'message': 'No registered face to compare against.'})

    # Mid-exam the student is looking at questions, not posing, so this uses the
    # off-pose bar. It is a swap detector, not an authentication gate.
    matched, distance, reason = face_service.verify_face(
        current_user, probe, threshold=face_service.POSE_MATCH_THRESHOLD)

    if not matched:
        _log_face(current_user.username, 'exam', user_id=current_user.id,
                  distance=distance, reason='in_exam_face_mismatch',
                  liveness_passed=None)
        _flag_exam_identity(payload.get('student_exam_id'), 'face_mismatch',
                            f'In-exam face does not match the registered student '
                            f'(distance {distance:.2f}).')
        return jsonify({'success': True, 'checked': True, 'match': False,
                        'distance': round(distance, 4) if distance else None,
                        'message': 'Face does not match the registered student.'})

    return jsonify({'success': True, 'checked': True, 'match': True,
                    'distance': round(distance, 4) if distance else None})


@auth_bp.route('/check-password-status')
@login_required
def check_password_status():
    """API endpoint to check if user needs to change password"""
    needs_change = current_user.must_change_password
    return jsonify({
        'needs_change': needs_change,
        'role': current_user.role,
        'password_changed': current_user.password_changed,
        'has_hint': bool(current_user.password_hint)
    })
