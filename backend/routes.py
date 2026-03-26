"""
Application Routes - Fixed Version
Bug fixes: PRN/Roll number formatting, delete operations, selection handling
"""

# ============================
# Standard Library Imports
# ============================
import csv
import io
import json
import os
import random
import sqlite3
import string
import time
import traceback

from datetime import datetime
from io import StringIO, BytesIO
from typing import Dict
import base64

# ============================
# Third-Party Library Imports
# ============================
import cv2
import numpy as np
import pandas as pd
import eventlet
from sqlalchemy import func, text

# Flask imports
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, session, send_file, Response, current_app
)
from flask_login import (
    login_user, login_required, logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import Flask


# ============================
# Local Application Imports
# ============================
from backend.database import db
from models import (
    User, Exam, Question, StudentExam,
    StudentAnswer, ActivityLog,
    ExamCalibration, ExamViolation
)

from backend.utils.email_utils import send_otp_email
from backend.services.pdf_generator import (
    generate_result_pdf,
    generate_batch_report_pdf
)

# Proctoring system (Enhanced v2.0)
from backend.services.proctor_vision.openvino_vision import (
    ProctorState,
    OpenVINOProctor,
    Severity,
    CHEATING_WEIGHTS,
)

from models import StudentAnswer



# ═══════════════════════════════════════════════════════════════════════
# SOCKET.IO FOR BINARY PROCTORING
# ═══════════════════════════════════════════════════════════════════════
from flask_socketio import SocketIO, emit, join_room

# Initialize Socket.IO (will be bound to app in register_routes)
socketio = SocketIO(
    cors_allowed_origins="*", 
    async_mode='eventlet',
    engineio_logger=False,
    logger=False,
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=10000000
)
print("✅ Socket.IO initialized for binary proctoring")


admin_sql_bp = Blueprint('admin_sql', __name__)

def is_authorized_sql_user():
    """Allow admin full access, faculty read-only."""
    role = getattr(current_user, "role", None)
    return role in ("admin", "faculty")

@admin_sql_bp.route('/admin/sql_console', methods=['GET'])
@login_required
def admin_sql_console():
    if not is_authorized_sql_user():
        return "Access denied", 403
    return render_template('admin_sql_console.html')

@admin_sql_bp.route('/admin/sql_console/run', methods=['POST'])
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

    if role not in ("admin", "faculty"):
        return jsonify({"success": False, "error": "Access denied"}), 403

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

# ═══════════════════════════════════════════════════════════════════════
# SHARED RESOURCES FOR SCALABILITY (~300 concurrent students)
# ═══════════════════════════════════════════════════════════════════════
import threading
from collections import defaultdict

# Global proctoring instances (one per student_exam_id)
PROCTOR_INSTANCES = {}
_proctor_lock = threading.Lock()

# ── Heartbeat tracking for network security ──
_heartbeat_registry: Dict = {}  # student_exam_id -> last_heartbeat_time
_last_frame_processed: Dict = {} # student_exam_id -> last frame time
HEARTBEAT_TIMEOUT = 30  # seconds before considering student disconnected

# ── CRITICAL: Load face detector ONCE at module level ──
# Previously this was loaded on EVERY frame for EVERY student.
# With 300 students * 1 frame/3s = 100 classifier loads/second from disk!
# Now we load it once and share across all threads.
_FACE_CASCADE = None
try:
    _FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    if _FACE_CASCADE.empty():
        print("⚠️ WARNING: Face cascade classifier failed to load")
        _FACE_CASCADE = None
    else:
        print("✅ Face cascade classifier loaded (shared for all students)")
except Exception as e:
    print(f"⚠️ WARNING: Could not load face cascade: {e}")
    _FACE_CASCADE = None


def detect_faces(frame):
    """Thread-safe face detection using the shared cascade classifier."""
    if _FACE_CASCADE is None:
        return []
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _FACE_CASCADE.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30)
        )
        return faces
    except Exception as e:
        print(f"Face detection error: {e}")
        return []


def decode_base64_image(data_url: str):
    """Convert base64 image to OpenCV format"""
    try:
        if "," in data_url:
            _, encoded = data_url.split(",", 1)
        else:
            encoded = data_url
        img_bytes = base64.b64decode(encoded)
        img_array = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"Error decoding image: {e}")
        return None


def safe_get_proctor_settings(exam):
    """
    Safely extract proctor settings from an Exam object.
    Works whether the model has get_proctor_settings() or not.
    Falls back to parsing exam.proctor_settings JSON string directly.
    Returns a dict with sensible, STUDENT-FRIENDLY defaults.
    """
    DEFAULTS = {
        'camera_enabled': True,
        'face_detection': True,
        'head_pose': True,         # Enabled for AI tracking
        'object_detection': True,  # Enabled to detect phones
        'voice_detection': True,   # Enabled for audio
        'tab_switch': True,
        'max_warnings': 25,
        'max_tab_switches': 5,
        'sensitivity': 'medium',
        'no_face_timeout_sec': 30,
        'warning_cooldown_sec': 30,
        'deviation_min_duration': 7,
    }
    
    settings = {}
    
    # Method 1: Try get_proctor_settings() method on model
    if hasattr(exam, 'get_proctor_settings') and callable(getattr(exam, 'get_proctor_settings', None)):
        try:
            settings = exam.get_proctor_settings() or {}
            print(f"📋 [Proctor] Method 1 (model): {settings}")
        except Exception as e:
            print(f"⚠️ get_proctor_settings() failed: {e}")
    
    # Method 2: Try parsing proctor_settings JSON string directly
    if not settings and hasattr(exam, 'proctor_settings'):
        raw = getattr(exam, 'proctor_settings', None)
        print(f"📋 [Proctor] Method 2 raw JSON: {repr(raw)}")
        if raw and isinstance(raw, str) and raw.strip() and raw.strip() != '{}':
            try:
                settings = json.loads(raw)
                print(f"📋 [Proctor] Method 2 parsed: {settings}")
            except (json.JSONDecodeError, TypeError) as e:
                print(f"⚠️ Failed to parse proctor_settings JSON: {e}")
    
    # Merge with defaults — faculty settings override defaults
    merged = {**DEFAULTS, **settings}
    print(f"📋 [Proctor] Final merged settings: head_pose={merged.get('head_pose')}, object_detection={merged.get('object_detection')}, voice_detection={merged.get('voice_detection')}")
    return merged


def get_proctor_instance(student_exam_id: int, exam):
    """Get or create a ProctorState instance (thread-safe)"""
    with _proctor_lock:
        if student_exam_id not in PROCTOR_INSTANCES:
            # Always use safe helper to get settings
            feature_flags = safe_get_proctor_settings(exam)
            max_warnings = feature_flags.get('max_warnings', 25)
            
            # Build structured evidence directory: violations/{exam_title}_{date}/{student_prn}/
            import re as _re
            student_exam = StudentExam.query.get(student_exam_id)
            student = User.query.get(student_exam.student_id) if student_exam else None
            
            # Sanitize exam title for filesystem
            exam_title_safe = _re.sub(r'[^a-zA-Z0-9_\- ]', '', exam.title or 'exam').strip().replace(' ', '_')
            exam_date = (student_exam.started_at or datetime.utcnow()).strftime('%Y-%m-%d_%H-%M')
            student_prn = (student.prn_number if student and student.prn_number else
                          (student.username if student else f'student_{student_exam_id}'))
            
            evidence_dir = os.path.join('frontend', 'static', 'violations', f'{exam_title_safe}_{exam_date}', str(student_prn))
            
            proctor_state = ProctorState(
                session_id=f"exam-{student_exam_id}",
                max_warnings=max_warnings,
                evidence_dir=evidence_dir
            )
            vision = OpenVINOProctor(
                proctor_state,
                model_dir="models",
                device="CPU",
                feature_flags=feature_flags,
            )
            print(f"📁 Evidence dir: {evidence_dir}")
            PROCTOR_INSTANCES[student_exam_id] = (proctor_state, vision)
        return PROCTOR_INSTANCES[student_exam_id]


def cleanup_proctor_instance(student_exam_id: int):
    """Remove proctor instance when exam ends to free memory."""
    with _proctor_lock:
        if student_exam_id in PROCTOR_INSTANCES:
            del PROCTOR_INSTANCES[student_exam_id]
            print(f"🧹 Cleaned up proctor instance for StudentExam {student_exam_id}")
    # Clean up heartbeat and frame cache
    _heartbeat_registry.pop(student_exam_id, None)
    _last_frame_processed.pop(student_exam_id, None)


# ═══════════════════════════════════════════════════════════════════════
# EVENT LOGGING HELPER
# ═══════════════════════════════════════════════════════════════════════

def log_proctor_event(student_exam_id: int, event_type: str, severity: str,
                      message: str, cheating_score: float = 0.0,
                      evidence_path: str = None):
    """
    Centralized event logging for proctoring violations.
    Logs: timestamp, violation type, severity, cheating score, evidence.
    """
    try:
        violation = ExamViolation(
            student_exam_id=student_exam_id,
            violation_type=event_type,
            message=message,
            severity=severity,
            evidence_path=evidence_path,
            timestamp=datetime.utcnow()
        )
        db.session.add(violation)

        # Also log to ActivityLog for comprehensive audit trail
        try:
            activity = ActivityLog(
                student_exam_id=student_exam_id,
                activity_type=f"proctor_{event_type}",
                description=(
                    f"[{severity.upper()}] {message} | "
                    f"Score: {cheating_score:.2f}"
                    f"{f' | Evidence: {evidence_path}' if evidence_path else ''}"
                ),
                severity=severity,
                timestamp=datetime.utcnow()
            )
            db.session.add(activity)
        except Exception:
            pass  # ActivityLog model might not have all fields

        db.session.commit()
    except Exception as e:
        print(f"⚠️ Event logging error: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass




# ═══════════════════════════════════════════════════════════════════════
# SOCKET.IO EVENT HANDLERS FOR BINARY PROCTORING (Enhanced v2.0)
# ═══════════════════════════════════════════════════════════════════════

@socketio.on('calibrationBinary')
def handle_calibration_binary(data):
    """Handle binary calibration frame — stores face identity baseline."""
    try:
        student_exam_id = data.get('studentExamId')
        frame_buffer = data.get('frame')
        
        if not frame_buffer or not student_exam_id:
            emit('calibration_result', {
                'success': True,
                'message': 'Face calibrated successfully!'
            }, binary=True)
            return
        
        print(f"📸 Received calibration frame: {len(frame_buffer)} bytes")
        
        # Convert ArrayBuffer to numpy array
        nparr = np.frombuffer(frame_buffer, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            emit('calibration_result', {'success': False, 'message': 'Failed to decode frame'}, binary=True)
            return
        
        # Use shared face detector (loaded once at module level)
        faces = detect_faces(frame)
        
        print(f"👤 Detected {len(faces)} face(s)")
        
        # Update StudentExam
        student_exam = StudentExam.query.get(student_exam_id)
        
        if len(faces) == 0:
            emit('calibration_result', {
                'success': False,
                'message': 'No face detected. Please position yourself clearly in front of the camera.'
            }, binary=True)
            
        elif len(faces) > 1:
            emit('calibration_result', {
                'success': False,
                'message': 'Multiple faces detected. Please ensure only you are visible.'
            }, binary=True)
            
        else:
            # Get proctor instance and run calibration with identity embedding
            if student_exam:
                try:
                    exam = Exam.query.get(student_exam.exam_id)
                    proctor_state, vision = get_proctor_instance(student_exam_id, exam)
                    
                    # Use multiple copies of frame for calibration baseline
                    calib_frames = [frame] * 10
                    success, msg = vision.calibrate(calib_frames)
                    print(f"📸 Calibration result: success={success}, msg={msg}")
                    
                except Exception as e:
                    print(f"⚠️ Calibration with OpenVINO failed: {e}")
                    import traceback
                    traceback.print_exc()
                    # Even on error, set a default baseline so check_frame works
                    try:
                        proctor_state, vision = get_proctor_instance(student_exam_id, exam)
                        if vision.state.baseline_yaw is None:
                            vision.state.baseline_yaw = 0.0
                            vision.state.baseline_pitch = 0.0
                            vision.state.baseline_roll = 0.0
                            print("⚠️ Set default baseline after calibration error")
                    except:
                        pass
                
                student_exam.calibration_completed = True
                student_exam.calibration_timestamp = datetime.utcnow()
                db.session.commit()
                print(f"✅ Calibration completed for StudentExam {student_exam_id}")
                
                # Log calibration event
                log_proctor_event(
                    student_exam_id, 'calibration_complete', 'low',
                    'Face calibration and identity baseline stored'
                )
            
            # Initialize heartbeat
            _heartbeat_registry[student_exam_id] = time.time()
            
            emit('calibration_result', {
                'success': True,
                'message': 'Face calibrated successfully!'
            })
        
    except Exception as e:
        print(f"❌ Calibration error: {e}")
        traceback.print_exc()
        emit('calibration_result', {'success': False, 'message': 'Calibration failed. Please try again.'}, binary=True)


@socketio.on('frameBinary')
def handle_frame_binary(data):
    """
    Handle binary proctoring frame — ENHANCED CONTINUOUS MONITORING
    
    Pipeline:
    1. Decode frame
    2. Run OpenVINO proctor (face, pose, objects, identity, gaze)
    3. Log violations with severity + cheating score
    4. Auto-terminate if thresholds exceeded
    """
    try:
        student_exam_id = data.get('studentExamId')
        frame_buffer = data.get('frame')
        
        if not frame_buffer or not student_exam_id:
            return
        
        # Update heartbeat
        _heartbeat_registry[student_exam_id] = time.time()
        
        # Convert to numpy array
        nparr = np.frombuffer(frame_buffer, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return
            
        # ── Frame Throttling ──
        # Block if we've processed a frame for this student < 4.0s ago
        current_time = time.time()
        if current_time - _last_frame_processed.get(student_exam_id, 0) < 4.0:
            return
        _last_frame_processed[student_exam_id] = current_time
        
        # Get StudentExam
        student_exam = StudentExam.query.get(student_exam_id)
        if not student_exam:
            return
        
        # Initialize violation counts if None
        if student_exam.no_face_count is None:
            student_exam.no_face_count = 0
        if student_exam.multiple_faces_count is None:
            student_exam.multiple_faces_count = 0
        if student_exam.total_violations is None:
            student_exam.total_violations = 0
        
        # ── Run Enhanced OpenVINO Proctor ──
        try:
            exam = Exam.query.get(student_exam.exam_id)
            proctor_state, vision = get_proctor_instance(student_exam_id, exam)
            
            # Check if camera is disabled (from cached feature flags)
            if not vision.feature_flags.get('camera_enabled', True):
                emit('proctor_result', {'success': True, 'faces': 1, 'message': 'Camera monitoring disabled'})
                return
            
            # Run AI check on frame
            status, details = vision.check_frame(frame)
        except Exception as e:
            print(f"⚠️ OpenVINO check_frame error: {e}")
            # Fallback to basic face detection
            try:
                faces = detect_faces(frame)
            except Exception:
                faces = []
            if len(faces) == 0:
                status, details = "NO_FACE", {"message": "No face detected", "violation_type": "no_face"}
            elif len(faces) > 1:
                status, details = "WARNING", {"message": "Multiple faces", "violation_type": "multiple_faces"}
            else:
                status, details = "NORMAL", {"message": "OK"}
        
        violation_type = details.get('violation_type', '')
        severity = details.get('severity', 'medium')
        cheating_score = details.get('cheating_score', 0)
        evidence_path = details.get('evidence_path')
        
        # ── Handle based on status ──
        if status == "WARNING" or status == "TERMINATE":
            student_exam.total_violations += 1
            
            # Update specific counters
            if violation_type == 'no_face':
                student_exam.no_face_count += 1
            elif violation_type == 'multiple_faces':
                student_exam.multiple_faces_count += 1
            
            # Log violation with full details
            log_proctor_event(
                student_exam_id,
                event_type=violation_type or 'unknown',
                severity=severity,
                message=details.get('message', 'Violation detected'),
                cheating_score=cheating_score,
                evidence_path=evidence_path,
            )
            
            emit('proctor_result', {
                'success': False,
                'violation': violation_type,
                'message': details.get('message', 'Violation detected'),
                'severity': severity,
                'cheating_score': cheating_score,
                'count': student_exam.total_violations,
                'total_violations': student_exam.total_violations,
                'warning_count': details.get('warning_count', 0),
                'max_warnings': details.get('max_warnings', 25),
            })
            
        elif status == "NO_FACE":
            # Brief no-face — inform but don't count as violation yet
            emit('proctor_result', {
                'success': False,
                'violation': 'no_face_brief',
                'message': details.get('message', 'Face temporarily not detected'),
                'severity': 'low',
                'cheating_score': cheating_score,
                'total_violations': student_exam.total_violations,
            })
            
        elif status == "ERROR":
            print(f"⚠️ Proctor error for {student_exam_id}: {details.get('message')}")
            # Don't penalize student for system errors
            emit('proctor_result', {
                'success': True,
                'faces': 1,
                'message': 'Processing...',
            })
            
        else:
            # NORMAL — all good
            emit('proctor_result', {
                'success': True,
                'faces': details.get('faces_detected', 1),
                'cheating_score': cheating_score,
            })
        
        # We explicitly omit a general db.session.commit() here to prevent SQLite locking.
        # Warnings and Terminations call log_proctor_event which already commits the session safely.
        
        # ── Violations are logged but the exam NEVER auto-submits ──
        # Faculty can review all violations in the analytics dashboard.
        # If the AI returned TERMINATE we treat it the same as a WARNING:
        # record it and notify the student, but keep the exam running.
        if status == "TERMINATE":
            log_proctor_event(
                student_exam_id, 'violation_threshold_reached', 'high',
                details.get('message', 'Violation threshold reached — exam continues'),
                cheating_score=cheating_score,
            )
            db.session.commit()
            print(f"⚠️  Violation threshold reached for StudentExam {student_exam_id} — exam continues (no auto-submit)")
        
    except Exception as e:
        print(f"❌ Frame processing error: {e}")
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════
# NEW: HEARTBEAT & AUDIO MONITORING SOCKET EVENTS
# ═══════════════════════════════════════════════════════════════════════

@socketio.on('heartbeat')
def handle_heartbeat(data):
    """Network security: track student connection liveness."""
    try:
        student_exam_id = data.get('studentExamId')
        if student_exam_id:
            _heartbeat_registry[student_exam_id] = time.time()
            emit('heartbeat_ack', {'timestamp': time.time()})
    except Exception:
        pass


@socketio.on('audioLevel')
def handle_audio_level(data):
    """
    Audio monitoring: receive voice activity detection results from client.
    Client-side VAD sends audio level periodically.
    Only acts if faculty enabled voice_detection in proctor settings.
    """
    try:
        student_exam_id = data.get('studentExamId')
        audio_level = data.get('level', 0)
        is_voice = data.get('isVoice', False)
        
        if not student_exam_id:
            return
        
        # Check if voice detection is enabled by faculty
        student_exam = StudentExam.query.get(student_exam_id)
        if not student_exam:
            return
        
        exam = Exam.query.get(student_exam.exam_id)
        if exam:
            settings = safe_get_proctor_settings(exam)
            if not settings.get('voice_detection', False):
                return  # Faculty disabled voice detection — ignore
        
        # Only act on sustained voice activity (client handles duration)
        if is_voice and audio_level > 0.3:
            if student_exam.total_violations is None:
                student_exam.total_violations = 0
            student_exam.total_violations += 1
            
            log_proctor_event(
                student_exam_id, 'voice_detected', 'medium',
                f'Voice activity detected (level: {audio_level:.2f})',
                cheating_score=0.2,
            )
            
            emit('proctor_result', {
                'success': False,
                'violation': 'voice_detected',
                'message': 'Voice/talking detected — please remain silent.',
                'severity': 'medium',
                'total_violations': student_exam.total_violations,
            })
            db.session.commit()
    except Exception as e:
        print(f"⚠️ Audio monitoring error: {e}")


@socketio.on('proctorStatus')
def handle_proctor_status(data):
    """Return current proctoring status (cheating score, violations, etc.)."""
    try:
        student_exam_id = data.get('studentExamId')
        if not student_exam_id:
            return
        
        with _proctor_lock:
            if student_exam_id in PROCTOR_INSTANCES:
                _, vision = PROCTOR_INSTANCES[student_exam_id]
                status = vision.get_status()
                emit('proctor_status', status)
    except Exception:
        pass


@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print("✅ Socket.IO client connected")
    

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print("⚠️ Socket.IO client disconnected")


def calculate_student_score(student_exam_id):
    """Calculate and update the score for a completed student exam"""
    try:
        student_exam = StudentExam.query.get(student_exam_id)
        if not student_exam:
            return None
        
        exam = student_exam.exam
        questions = Question.query.filter_by(exam_id=exam.id).all()
        
        if not questions:
            student_exam.score = 0
            student_exam.total_points = 0
            student_exam.percentage = 0
            student_exam.passed = False
            db.session.commit()
            return {'score': 0, 'total_points': 0}
        
        student_answers = StudentAnswer.query.filter_by(student_exam_id=student_exam_id).all()
        answer_dict = {ans.question_id: ans for ans in student_answers}
        
        earned_points = 0
        total_points = 0
        
        for question in questions:
            points = question.points or 1.0
            total_points += points
            
            student_answer = answer_dict.get(question.id)
            
            if student_answer and student_answer.selected_answer:
                is_correct = (student_answer.selected_answer.upper() == question.correct_answer.upper())
                
                if is_correct:
                    earned_points += points
                    student_answer.is_correct = True
                    student_answer.points_earned = points
                else:
                    student_answer.is_correct = False
                    student_answer.points_earned = 0
            else:
                if not student_answer:
                    student_answer = StudentAnswer(
                        student_exam_id=student_exam_id,
                        question_id=question.id,
                        selected_answer="0",
                        is_correct=False,
                        points_earned=0
                    )
                    db.session.add(student_answer)
        
        percentage = (earned_points / total_points * 100) if total_points > 0 else 0
        passing_score = exam.passing_score or 50.0
        passed = percentage >= passing_score
        
        student_exam.score = round(earned_points, 2)
        student_exam.total_points = round(total_points, 2)
        student_exam.percentage = round(percentage, 2)
        student_exam.passed = passed
        student_exam.status = 'completed'
        student_exam.completed = True
        
        if student_exam.started_at and student_exam.submitted_at:
            time_taken = (student_exam.submitted_at - student_exam.started_at).total_seconds() / 60
            student_exam.time_taken_minutes = int(time_taken)
        
        db.session.commit()
        
        return {
            'score': earned_points,
            'total_points': total_points,
            'percentage': percentage,
            'passed': passed
        }
        
    except Exception as e:
        print(f"❌ Error: {e}")
        db.session.rollback()
        return None

def register_routes(app):
    """Register all routes with the Flask app"""

    # ═══════════════════════════════════════════════════════════════════
    # INITIALIZE SOCKET.IO WITH APP
    # ═══════════════════════════════════════════════════════════════════
    socketio.init_app(app)
    print("✅ Socket.IO bound to Flask app for binary proctoring")
    
    print("📝 Registering enhanced routes...")

    # ============================================================
    # Cache Control Middleware
    # ============================================================
    @app.after_request
    def add_cache_control(response):
        """Cache control to prevent back button showing login after logout"""
        endpoint = request.endpoint or ""

        # Auth routes should never cache
        if endpoint in ['login', 'logout', 'register', 'verify_otp']:
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



    # ============= Authentication Routes =============
    @app.route('/')
    def index():
        """Smart redirect based on login state"""
        if current_user.is_authenticated:
            if current_user.role == 'faculty':
                return redirect(url_for('faculty_dashboard'))
            elif current_user.role == 'student':
                return redirect(url_for('student_dashboard'))
            elif current_user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
        return render_template('index.html')

    def clean_for_insert(self):
        """
        Cleans empty string fields before saving.
        Converts blank values ('') to None to prevent UNIQUE constraint issues.
        """
        string_fields = [
            "prn_number", "employee_id", "roll_id",
            "batch", "department", "phone"
        ]
        for field in string_fields:
            value = getattr(self, field, None)
            if isinstance(value, str) and value.strip() == "":
                setattr(self, field, None)


    @app.route('/register', methods=['GET', 'POST'])
    def register():
        """Public registration is disabled. Accounts are created by admin (faculty) or faculty (students)."""
        flash('Registration is disabled. Contact your administrator.', 'warning')
        return redirect(url_for('login'))

    # ═══════════════════════════════════════════════════════════════
    # ADMIN: Manage Faculty
    # ═══════════════════════════════════════════════════════════════
    @app.route('/admin/manage-faculty')
    @login_required
    def admin_manage_faculty():
        """Admin panel to view and create faculty accounts."""
        if current_user.role != 'admin':
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('index'))

        faculties = User.query.filter_by(role='faculty').order_by(User.created_at.desc()).all()

        # Enrich with stats
        faculty_data = []
        for f in faculties:
            exam_count = Exam.query.filter_by(creator_id=f.id).count()
            faculty_data.append({
                'user': f,
                'exam_count': exam_count
            })

        return render_template('admin/manage_faculty.html', faculty_data=faculty_data)

    @app.route('/admin/create-faculty', methods=['POST'])
    @login_required
    def admin_create_faculty():
        """Admin creates a new faculty account."""
        if current_user.role != 'admin':
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        try:
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '').strip()
            full_name = request.form.get('full_name', '').strip()
            phone = request.form.get('phone', '').strip() or None
            employee_id = request.form.get('employee_id', '').strip() or None
            department = request.form.get('department', '').strip() or 'Education & Training'

            # Validation
            if not username or not email or not password:
                flash('Username, email, and password are required.', 'error')
                return redirect(url_for('admin_manage_faculty'))

            if User.query.filter_by(username=username).first():
                flash(f'Username "{username}" already exists.', 'error')
                return redirect(url_for('admin_manage_faculty'))

            if User.query.filter_by(email=email).first():
                flash(f'Email "{email}" already registered.', 'error')
                return redirect(url_for('admin_manage_faculty'))

            if employee_id and User.query.filter_by(employee_id=employee_id).first():
                flash(f'Employee ID "{employee_id}" already registered.', 'error')
                return redirect(url_for('admin_manage_faculty'))

            new_faculty = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password, method='pbkdf2:sha256'),
                role='faculty',
                full_name=full_name,
                phone=phone,
                employee_id=employee_id,
                department=department,
                is_verified=True,
                password_changed=True
            )

            # Clean empty strings
            for field in ['employee_id', 'phone', 'department']:
                val = getattr(new_faculty, field)
                if isinstance(val, str) and val.strip() == '':
                    setattr(new_faculty, field, None)

            db.session.add(new_faculty)
            db.session.commit()

            print(f"✅ Admin {current_user.username} created faculty: {username}")
            flash(f'Faculty "{full_name or username}" created successfully!', 'success')
            return redirect(url_for('admin_manage_faculty'))

        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            flash(f'Error creating faculty: {str(e)}', 'error')
            return redirect(url_for('admin_manage_faculty'))

    @app.route('/admin/delete-faculty/<int:faculty_id>', methods=['POST'])
    @login_required
    def admin_delete_faculty(faculty_id):
        """Admin deletes a faculty account."""
        if current_user.role != 'admin':
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        faculty = User.query.get(faculty_id)
        if not faculty or faculty.role != 'faculty':
            return jsonify({'success': False, 'error': 'Faculty not found'}), 404

        try:
            # Delete faculty's exams and related data
            exams = Exam.query.filter_by(creator_id=faculty_id).all()
            for exam in exams:
                StudentExam.query.filter_by(exam_id=exam.id).delete()
                Question.query.filter_by(exam_id=exam.id).delete()
                db.session.delete(exam)

            db.session.delete(faculty)
            db.session.commit()

            print(f"✅ Admin {current_user.username} deleted faculty: {faculty.username}")
            return jsonify({'success': True, 'message': f'Faculty "{faculty.full_name or faculty.username}" deleted'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500






    @app.route('/verify-otp', methods=['GET', 'POST'])
    def verify_otp():
        """OTP verification page"""
        user_id = session.get('pending_user_id')
        
        if not user_id:
            flash('Please register first', 'error')
            return redirect(url_for('register'))
        
        user = User.query.get(user_id)
        
        if not user:
            flash('User not found', 'error')
            session.pop('pending_user_id', None)
            return redirect(url_for('register'))
        
        if user.is_verified:
            flash('Account already verified', 'info')
            session.pop('pending_user_id', None)
            return redirect(url_for('login'))
        
        if request.method == 'POST':
            otp = request.form.get('otp')
            
            if user.verify_otp(otp):
                db.session.commit()
                session.pop('pending_user_id', None)
                flash('Email verified! You can now login.', 'success')
                return redirect(url_for('login'))
            else:
                flash('Invalid or expired OTP. Please try again.', 'error')
        
        return render_template('verify_otp.html', email=user.email, username=user.username)

    @app.route('/resend-otp', methods=['POST'])
    def resend_otp():
        """Resend OTP"""
        user_id = session.get('pending_user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'No pending verification'})
        
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'})
        
        if user.is_verified:
            return jsonify({'success': False, 'error': 'Already verified'})
        
        otp = user.generate_otp()
        send_otp_email(user.email, otp, user.username, user.role)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'OTP sent! Check your email.'})

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """User login with verification check, password change enforcement, and smart session handling."""
        
        # ✅ If user is already logged in, redirect them immediately
        if current_user.is_authenticated:
            if current_user.role == 'faculty':
                return redirect(url_for('faculty_dashboard'))
            elif current_user.role == 'student':
                return redirect(url_for('student_dashboard'))
            elif current_user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('index'))

        # Normal login flow
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')

            # Validate input
            if not username or not password:
                flash('❌ Please enter both username and password', 'error')
                return redirect(url_for('login'))

            user = User.query.filter_by(username=username).first()

            # 🔑 PRN fallback: if username not found, try looking up by PRN number
            if not user:
                user = User.query.filter_by(prn_number=username).first()

            # FIXED: use password_hash
            if user and check_password_hash(user.password_hash, password):
                # ✅ Check if user is verified (Bypass for admins)
                if not user.is_verified and user.role != 'admin':
                    session['pending_user_id'] = user.id
                    flash('⚠️ Please verify your email first. Check inbox for OTP.', 'warning')
                    return redirect(url_for('verify_otp'))

                # ✅ Login user
                login_user(user)
                session.permanent = True  # ensures session lasts until logout
                
                # ✅ CRITICAL: Check if student needs to change password (first-time login)
                if user.role == 'student' and not getattr(user, 'password_changed', False):
                    flash('⚠️ Welcome! For security, please change your password to continue.', 'warning')
                    return redirect(url_for('change_password'))
                
                # ✅ Success message
                flash(f'✅ Welcome back, {user.full_name or user.username}!', 'success')

                # ✅ Redirect based on role
                if user.role == 'faculty':
                    return redirect(url_for('faculty_dashboard'))
                elif user.role == 'student':
                    return redirect(url_for('student_dashboard'))
                elif user.role == 'admin':
                    return redirect(url_for('admin_dashboard'))
                else:
                    return redirect(url_for('index'))
            else:
                flash('❌ Invalid username or password', 'error')
                return redirect(url_for('login'))

        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        """Secure logout: clear session and prevent cache."""
        from flask import make_response, session
        logout_user()
        session.clear()

        # Create response
        response = make_response(redirect(url_for('login')))

        # No caching after logout
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'

        flash('You have been logged out securely.', 'info')
        return response





    # ============= Admin Routes =============

    @app.route('/admin/dashboard')
    @login_required
    def admin_dashboard():
        """Full admin dashboard with global overview."""
        if current_user.role != 'admin':
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('index'))

        # ── Global Stats ──
        total_faculty = User.query.filter_by(role='faculty').count()
        total_students = User.query.filter_by(role='student').count()
        total_exams = Exam.query.count()
        total_attempts = StudentExam.query.count()
        submitted_attempts = StudentExam.query.filter_by(status='submitted').count()
        active_exams = Exam.query.filter_by(is_active=True).count()

        # ── Faculty-wise breakdown ──
        faculties = User.query.filter_by(role='faculty').order_by(User.full_name).all()
        faculty_stats = []
        for f in faculties:
            f_exams = Exam.query.filter_by(creator_id=f.id).all()
            f_exam_ids = [e.id for e in f_exams]
            f_attempts = StudentExam.query.filter(StudentExam.exam_id.in_(f_exam_ids)).count() if f_exam_ids else 0
            f_submitted = StudentExam.query.filter(
                StudentExam.exam_id.in_(f_exam_ids),
                StudentExam.status == 'submitted'
            ).all() if f_exam_ids else []
            f_avg = 0
            if f_submitted:
                scores = [s.percentage for s in f_submitted if s.percentage is not None]
                f_avg = round(sum(scores) / len(scores), 1) if scores else 0

            # Student count for this faculty's exams
            f_student_ids = set()
            if f_exam_ids:
                sids = db.session.query(StudentExam.student_id).filter(
                    StudentExam.exam_id.in_(f_exam_ids)
                ).distinct().all()
                f_student_ids = set(s[0] for s in sids)

            faculty_stats.append({
                'user': f,
                'exam_count': len(f_exams),
                'student_count': len(f_student_ids),
                'attempt_count': f_attempts,
                'avg_score': f_avg,
                'department': f.department or 'Unassigned'
            })

        # ── Course/Department-wise breakdown ──
        departments = db.session.query(User.department).filter(
            User.role == 'faculty',
            User.department.isnot(None),
            User.department != ''
        ).distinct().all()
        course_stats = []
        for (dept,) in departments:
            dept_faculty = User.query.filter_by(role='faculty', department=dept).all()
            dept_faculty_ids = [f.id for f in dept_faculty]
            dept_exams = Exam.query.filter(Exam.creator_id.in_(dept_faculty_ids)).all() if dept_faculty_ids else []
            dept_exam_ids = [e.id for e in dept_exams]
            dept_attempts = StudentExam.query.filter(
                StudentExam.exam_id.in_(dept_exam_ids),
                StudentExam.status == 'submitted'
            ).all() if dept_exam_ids else []
            dept_avg = 0
            if dept_attempts:
                scores = [s.percentage for s in dept_attempts if s.percentage is not None]
                dept_avg = round(sum(scores) / len(scores), 1) if scores else 0

            course_stats.append({
                'department': dept,
                'faculty_count': len(dept_faculty),
                'exam_count': len(dept_exams),
                'attempt_count': len(dept_attempts),
                'avg_score': dept_avg
            })

        # ── Recent exams (last 10) ──
        recent_exams = Exam.query.order_by(Exam.created_at.desc()).limit(10).all()
        recent_exams_data = []
        for exam in recent_exams:
            creator = User.query.get(exam.creator_id)
            attempt_count = StudentExam.query.filter_by(exam_id=exam.id).count()
            recent_exams_data.append({
                'exam': exam,
                'creator': creator,
                'attempt_count': attempt_count
            })

        return render_template('admin/dashboard.html',
            total_faculty=total_faculty,
            total_students=total_students,
            total_exams=total_exams,
            total_attempts=total_attempts,
            submitted_attempts=submitted_attempts,
            active_exams=active_exams,
            faculty_stats=faculty_stats,
            course_stats=course_stats,
            recent_exams_data=recent_exams_data
        )

    @app.route('/admin/students')
    @login_required
    def admin_student_list():
        """Admin view: all students across all faculties."""
        if current_user.role != 'admin':
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('index'))

        q = request.args.get('q', '').strip()
        batch = request.args.get('batch')
        department = request.args.get('department')

        query = User.query.filter(User.role == 'student')

        if q:
            like = f"%{q}%"
            query = query.filter(
                db.or_(
                    User.full_name.ilike(like),
                    User.username.ilike(like),
                    User.email.ilike(like),
                    User.prn_number.ilike(like),
                    User.batch.ilike(like)
                )
            )
        if batch:
            query = query.filter(User.batch == batch)
        if department:
            query = query.filter(User.department == department)

        students = query.order_by(User.created_at.desc()).all()
        batches = [b[0] for b in db.session.query(User.batch).filter(User.role == 'student', User.batch.isnot(None)).distinct().all() if b[0]]
        departments = [d[0] for d in db.session.query(User.department).filter(User.role == 'student', User.department.isnot(None)).distinct().all() if d[0]]

        # Enrich with exam stats
        student_data = []
        for s in students:
            se = StudentExam.query.filter_by(student_id=s.id, status='submitted').all()
            total = len(se)
            avg = round(sum(x.percentage for x in se if x.percentage) / total, 1) if total else 0
            student_data.append({'user': s, 'exam_count': total, 'avg_score': avg})

        return render_template('admin/students.html',
            student_data=student_data,
            batches=batches,
            departments=departments,
            q=q,
            selected_batch=batch,
            selected_department=department
        )

    @app.route('/admin/all-exams')
    @login_required
    def admin_all_exams():
        """Admin view: all exams with faculty info."""
        if current_user.role != 'admin':
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('index'))

        exams = Exam.query.order_by(Exam.created_at.desc()).all()
        exam_data = []
        for e in exams:
            creator = User.query.get(e.creator_id)
            q_count = Question.query.filter_by(exam_id=e.id).count()
            attempt_count = StudentExam.query.filter_by(exam_id=e.id).count()
            submitted = StudentExam.query.filter_by(exam_id=e.id, status='submitted').all()
            avg = 0
            if submitted:
                scores = [s.percentage for s in submitted if s.percentage is not None]
                avg = round(sum(scores) / len(scores), 1) if scores else 0
            exam_data.append({
                'exam': e,
                'creator': creator,
                'question_count': q_count,
                'attempt_count': attempt_count,
                'avg_score': avg
            })

        return render_template('admin/all_exams.html', exam_data=exam_data)

    # ============= Faculty Routes =============

    # ═══════════════════════════════════════════════════════════════
    # HELPER: Course-based student visibility
    # Faculty sees students whose 'course' matches faculty's 'department'
    # ═══════════════════════════════════════════════════════════════
    def _get_faculty_course(faculty_id=None):
        """Get the course (department) this faculty is mapped to."""
        if faculty_id:
            faculty = User.query.get(faculty_id)
        else:
            faculty = current_user
        return (faculty.department or '').strip() if faculty else ''

    def _get_faculty_student_ids(faculty_id):
        """Get IDs of students whose course matches this faculty's department."""
        faculty_course = _get_faculty_course(faculty_id)
        if not faculty_course:
            return set()
        # Students whose 'course' field matches the faculty's 'department'
        students = User.query.filter(
            User.role == 'student',
            User.course == faculty_course
        ).all()
        return set(s.id for s in students)

    def _get_faculty_students_query(faculty_id=None):
        """Return a query of students visible to this faculty (course-based)."""
        if faculty_id:
            faculty_course = _get_faculty_course(faculty_id)
        else:
            faculty_course = _get_faculty_course()
        if not faculty_course:
            return User.query.filter(db.false())
        return User.query.filter(
            User.role == 'student',
            User.course == faculty_course
        )

    def _is_faculty_or_admin():
        """Check if current user is faculty or admin."""
        return current_user.role in ('faculty', 'admin')

    def _faculty_owns_exam(exam):
        """Check if current faculty owns this exam (admin always passes)."""
        if current_user.role == 'admin':
            return True
        return exam.creator_id == current_user.id

    def _faculty_can_access_student(student_id):
        """Check if student's course matches this faculty's department (admin always passes)."""
        if current_user.role == 'admin':
            return True
        student = User.query.get(student_id)
        if not student:
            return False
        faculty_course = _get_faculty_course()
        if not faculty_course:
            return False
        return (student.course or '').strip() == faculty_course

    @app.route('/faculty/dashboard')
    @login_required
    def faculty_dashboard():
        """Faculty dashboard"""
        if current_user.role not in ('faculty', 'admin'):
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))
        
        if current_user.role == 'admin':
            exams = Exam.query.order_by(Exam.created_at.desc()).all()
        else:
            exams = Exam.query.filter_by(creator_id=current_user.id).order_by(Exam.created_at.desc()).all()
        
        total_exams = len(exams)
        active_exams = len([e for e in exams if e.is_active])
        total_attempts = sum([len(e.student_exams) for e in exams])
        
        return render_template('faculty/dashboard.html', 
                             exams=exams,
                             total_exams=total_exams,
                             active_exams=active_exams,
                             total_attempts=total_attempts)

    @app.route('/faculty/set_leaderboard', methods=['POST'])
    @login_required
    def faculty_set_leaderboard():
        """Toggle show_leaderboard on all exams belonging to this faculty."""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        try:
            data = request.get_json(force=True) or {}
            enabled = bool(data.get('show_leaderboard', True))
            if current_user.role == 'admin':
                exams = Exam.query.all()
            else:
                exams = Exam.query.filter_by(creator_id=current_user.id).all()
            for exam in exams:
                exam.show_leaderboard = enabled
            db.session.commit()
            return jsonify({'success': True, 'updated': len(exams), 'show_leaderboard': enabled})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/faculty/exam/create', methods=['GET', 'POST'])
    @login_required
    def create_exam():
        """Create a new exam"""
        if current_user.role != 'faculty':
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))
        
        if request.method == 'POST':
            try:
                title = request.form.get('title', '').strip()
                description = request.form.get('description', '')
                duration_minutes = int(request.form.get('duration_minutes') or 60)
                passing_score = float(request.form.get('passing_score') or 50.0)
                max_tab_switches = int(request.form.get('max_tab_switches') or 5)
                exam_mode = request.form.get('exam_mode', 'classroom')
                proctor_settings_json = request.form.get('proctor_settings_json', '{}') or '{}'

                # ── New faculty control fields ────────────────────────────────
                show_leaderboard   = request.form.get('show_leaderboard', 'true').lower() == 'true'
                post_exam_redirect = request.form.get('post_exam_redirect', 'results')

                if not title:
                    flash('Exam title is required.', 'error')
                    return redirect(request.url)

                # ── Auto-generate exam access key ─────────────────────────────
                chars   = string.ascii_uppercase + string.digits
                new_key = ''.join(random.choices(chars, k=8))

                exam = Exam(
                    title=title,
                    description=description,
                    duration_minutes=duration_minutes,
                    passing_score=passing_score,
                    max_tab_switches=max_tab_switches,
                    exam_mode=exam_mode,
                    proctor_settings=proctor_settings_json,
                    creator_id=current_user.id,
                    access_key=new_key,
                    show_leaderboard=show_leaderboard,
                    post_exam_redirect=post_exam_redirect,
                    status='inactive',
                    force_ended=False,
                )

                db.session.add(exam)
                db.session.commit()

                flash(f'Exam "{title}" created! Access key: {new_key} — share this with your students before the exam.', 'success')
                return redirect(url_for('upload_questions', exam_id=exam.id))

            except Exception as e:
                db.session.rollback()
                import traceback
                current_app.logger.error(f'❌ create_exam error: {traceback.format_exc()}')
                flash(f'Error creating exam: {str(e)}', 'error')
                return redirect(request.url)

        return render_template('faculty/create_exam.html')

    @app.route('/faculty/exam/batch-create', methods=['POST'])
    @login_required
    def batch_create_exams():
        """Batch create multiple exams with optional question file uploads."""
        if current_user.role != 'faculty':
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        try:
            # Parse exam metadata from FormData
            exams_json_str = request.form.get('exams_json', '[]')
            try:
                exams_data = json.loads(exams_json_str)
            except (json.JSONDecodeError, TypeError):
                return jsonify({'success': False, 'error': 'Invalid exam data'}), 400
            
            if not isinstance(exams_data, list) or len(exams_data) == 0:
                return jsonify({'success': False, 'error': 'At least one exam is required'}), 400
            
            if len(exams_data) > 50:
                return jsonify({'success': False, 'error': 'Maximum 50 exams at once'}), 400
            
            created_exams_info = []
            
            for i, exam_data in enumerate(exams_data):
                title = str(exam_data.get('title', '')).strip()
                if not title:
                    return jsonify({
                        'success': False,
                        'error': f'Exam #{i+1}: Title is required'
                    }), 400
                
                description = str(exam_data.get('description', '')).strip()
                
                try:
                    duration_minutes = int(exam_data.get('duration_minutes', 60))
                except (ValueError, TypeError):
                    duration_minutes = 60
                duration_minutes = max(1, duration_minutes)
                
                try:
                    passing_score = float(exam_data.get('passing_score', 50.0))
                except (ValueError, TypeError):
                    passing_score = 50.0
                passing_score = max(0, min(100, passing_score))
                
                # Create the exam
                exam = Exam(
                    title=title,
                    description=description if description else None,
                    duration_minutes=duration_minutes,
                    passing_score=passing_score,
                    creator_id=current_user.id
                )
                db.session.add(exam)
                db.session.flush()  # Get the exam.id
                
                # Process question file if attached
                questions_count = 0
                file_key = f'file_{i}'
                
                if file_key in request.files:
                    file = request.files[file_key]
                    filename = secure_filename(file.filename or "")
                    
                    if filename:
                        ext = filename.rsplit('.', 1)[-1].lower()
                        df = None
                        
                        try:
                            if ext == 'csv':
                                df = pd.read_csv(file)
                            elif ext in ('xls', 'xlsx'):
                                df = pd.read_excel(file)
                            elif ext == 'json':
                                data = json.load(file)
                                if isinstance(data, list):
                                    df = pd.DataFrame(data)
                        except Exception as parse_err:
                            print(f"⚠️ File parse error for exam #{i+1} ({title}): {parse_err}")
                        
                        if df is not None and len(df) > 0:
                            df.columns = [c.strip().lower() for c in df.columns]
                            required_cols = {'question', 'option_a', 'option_b', 'correct_answer'}
                            
                            if required_cols.issubset(set(df.columns)):
                                MAX_IMPORT = 20000
                                if len(df) > MAX_IMPORT:
                                    df = df.head(MAX_IMPORT)
                                
                                for idx_row, row in df.iterrows():
                                    q_text = str(row.get('question', '')).strip()
                                    a = str(row.get('option_a', '')).strip()
                                    b = str(row.get('option_b', '')).strip()
                                    c = str(row.get('option_c', '')).strip() if 'option_c' in df.columns else None
                                    d = str(row.get('option_d', '')).strip() if 'option_d' in df.columns else None
                                    correct = str(row.get('correct_answer', '')).strip().upper()
                                    
                                    try:
                                        points_val = float(row.get('points', 1.0))
                                    except Exception:
                                        points_val = 1.0
                                    
                                    if not q_text or not a or not b or correct == "":
                                        continue
                                    
                                    if correct not in ('A', 'B', 'C', 'D'):
                                        correct = correct[0].upper() if correct else 'A'
                                    
                                    def none_if_blank(x):
                                        return x if x and x.strip() != "" else None
                                    
                                    a, b, c, d = map(none_if_blank, [a, b, c, d])
                                    
                                    new_q = Question(
                                        exam_id=exam.id,
                                        question_text=q_text,
                                        option_a=a,
                                        option_b=b,
                                        option_c=c,
                                        option_d=d,
                                        correct_answer=correct,
                                        points=points_val,
                                        order_number=idx_row + 1,
                                        enhanced=False
                                    )
                                    db.session.add(new_q)
                                    questions_count += 1
                
                created_exams_info.append({
                    'id': exam.id,
                    'title': exam.title,
                    'questions_count': questions_count
                })
            
            db.session.commit()
            
            total_exams = len(created_exams_info)
            total_questions = sum(e['questions_count'] for e in created_exams_info)
            
            print(f"✅ Batch created {total_exams} exams ({total_questions} total questions) by {current_user.username}")
            
            return jsonify({
                'success': True,
                'created_count': total_exams,
                'total_questions': total_questions,
                'message': f'Successfully created {total_exams} exam(s) with {total_questions} questions',
                'created_exams': created_exams_info
            })
            
        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            print(f"❌ Batch exam creation error: {e}")
            return jsonify({
                'success': False,
                'error': f'Server error: {str(e)}'
            }), 500



    @app.route('/faculty/exam/<int:exam_id>/delete', methods=['POST'])
    @login_required
    def delete_exam(exam_id):
        """Securely delete an exam and all related data."""
        if current_user.role != 'faculty':
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))

        exam = Exam.query.get_or_404(exam_id)

        if exam.creator_id != current_user.id:
            flash('Access denied: You can only delete your own exams.', 'error')
            return redirect(url_for('faculty_dashboard'))

        try:
            # 🔥 Step 1: Manually delete all related data (defensive cleanup)
            from models import Question, StudentExam, StudentAnswer, ActivityLog
            
            # Delete related questions, student exams, and logs
            Question.query.filter_by(exam_id=exam.id).delete()
            StudentExam.query.filter_by(exam_id=exam.id).delete()
            ActivityLog.query.filter(ActivityLog.student_exam_id.in_(
                db.session.query(StudentExam.id).filter_by(exam_id=exam.id)
            )).delete()

            # 🔥 Step 2: Delete the exam itself
            db.session.delete(exam)
            db.session.commit()

            flash('✅ Exam and all related questions, attempts, and logs deleted successfully.', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'❌ Error deleting exam: {str(e)}', 'error')

        return redirect(url_for('faculty_dashboard'))

    @app.route('/faculty/exam/<int:exam_id>/preview', methods=['POST'])
    @login_required
    def preview_questions(exam_id):
        """
        Secure server-side preview of uploaded exam questions before final import.
        Supports CSV, Excel, and JSON files.
        Returns up to 100 rows for client-side validation and display.
        """
        # ---------- Access Control ----------
        if current_user.role != 'faculty':
            return jsonify({"success": False, "error": "Access denied"}), 403

        exam = Exam.query.get_or_404(exam_id)
        if exam.creator_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        # ---------- File Validation ----------
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file uploaded"}), 400

        file = request.files['file']
        filename = secure_filename(file.filename or "")
        if filename == "":
            return jsonify({"success": False, "error": "Empty filename"}), 400

        ext = filename.rsplit('.', 1)[-1].lower()

        try:
            rows = []
            max_preview = 100  # preview limit

            # ---------- CSV ----------
            if ext == 'csv':
                df = pd.read_csv(file, nrows=max_preview)
                df.columns = [c.strip().lower() for c in df.columns]
                rows = df.to_dict(orient='records')

            # ---------- Excel ----------
            elif ext in ('xls', 'xlsx'):
                df = pd.read_excel(file, nrows=max_preview)
                df.columns = [c.strip().lower() for c in df.columns]
                rows = df.to_dict(orient='records')

            # ---------- JSON ----------
            elif ext == 'json':
                try:
                    data = json.load(file)
                except Exception as e:
                    return jsonify({"success": False, "error": f"Invalid JSON: {str(e)}"}), 400

                if not isinstance(data, list):
                    return jsonify({"success": False, "error": "JSON must be an array of objects"}), 400

                # convert only first N records to DataFrame (for consistency)
                df = pd.DataFrame(data[:max_preview])
                df.columns = [c.strip().lower() for c in df.columns]
                rows = df.to_dict(orient='records')

            # ---------- Unsupported ----------
            else:
                return jsonify({
                    "success": False,
                    "error": "Unsupported file type. Please upload CSV, Excel, or JSON."
                }), 400

            # ---------- Response ----------
            return jsonify({
                "success": True,
                "count": len(rows),
                "rows": rows
            })

        except Exception as e:
            current_app.logger.exception("❌ Error in question preview:")
            return jsonify({
                "success": False,
                "error": f"Server error: {str(e)}"
            }), 500



    # ---------------------
    # Real upload endpoint (FIXED + CLEANED)
    # ---------------------
    @app.route('/faculty/exam/<int:exam_id>/upload', methods=['GET', 'POST'])
    @login_required
    def upload_questions(exam_id):
        """Upload questions from CSV / Excel / JSON (production-ready, fully cleaned)."""
        if current_user.role != 'faculty':
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))

        exam = Exam.query.get_or_404(exam_id)
        if exam.creator_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('faculty_dashboard'))

        if request.method == 'POST':
            if 'file' not in request.files:
                flash('No file uploaded.', 'error')
                return redirect(request.url)

            file = request.files['file']
            filename = secure_filename(file.filename or "")
            if filename == "":
                flash('No file selected.', 'error')
                return redirect(request.url)

            ext = filename.rsplit('.', 1)[-1].lower()

            try:
                if ext == 'csv':
                    df = pd.read_csv(file)
                elif ext in ('xls', 'xlsx'):
                    df = pd.read_excel(file)
                elif ext == 'json':
                    try:
                        data = json.load(file)
                    except Exception:
                        flash('Invalid JSON file.', 'error')
                        return redirect(request.url)
                    if not isinstance(data, list):
                        flash('JSON must be an array of objects.', 'error')
                        return redirect(request.url)
                    df = pd.DataFrame(data)
                else:
                    flash('Unsupported file type. Upload CSV, Excel, or JSON.', 'error')
                    return redirect(request.url)

                # Normalize column names
                df.columns = [c.strip().lower() for c in df.columns]

                # Validate required columns
                required_columns = {'question', 'option_a', 'option_b', 'correct_answer'}
                missing = required_columns - set(df.columns)
                if missing:
                    flash(f"Missing required columns: {', '.join(missing)}", 'error')
                    return redirect(request.url)

                # Limit for safety
                MAX_IMPORT = 20000
                if len(df) > MAX_IMPORT:
                    flash(f"File too large: maximum {MAX_IMPORT} questions allowed.", 'error')
                    return redirect(request.url)

                count = 0  # ← must be BEFORE the loop
                for idx, row in df.iterrows():
                    # Clean and normalize fields
                    q_text = str(row.get('question', '')).strip()
                    a = str(row.get('option_a', '')).strip()
                    b = str(row.get('option_b', '')).strip()
                    c = str(row.get('option_c', '')).strip() if 'option_c' in df.columns else None
                    d = str(row.get('option_d', '')).strip() if 'option_d' in df.columns else None
                    correct = str(row.get('correct_answer', '')).strip().upper()

                    # Normalize points
                    try:
                        points_val = float(row.get('points', 1.0))
                    except Exception:
                        points_val = 1.0

                    # Skip malformed rows
                    if not q_text or not a or not b or correct == "":
                        continue

                    # Normalize correct answer (should be A/B/C/D)
                    if correct not in ('A', 'B', 'C', 'D'):
                        correct = correct[0].upper() if correct else 'A'

                    # Convert blanks to None
                    def none_if_blank(x):
                        return x if x and x.strip() != "" else None

                    a, b, c, d = map(none_if_blank, [a, b, c, d])

                    # Create Question
                    new_q = Question(
                        exam_id=exam.id,
                        question_text=q_text,
                        option_a=a,
                        option_b=b,
                        option_c=c,
                        option_d=d,
                        correct_answer=correct,
                        points=points_val,
                        order_number=idx + 1,
                        enhanced=False
                    )

                    db.session.add(new_q)
                    count += 1

                db.session.commit()
                flash(f"✅ Successfully uploaded {count} question(s) to '{exam.title}'!", 'success')
                return redirect(url_for('view_exam', exam_id=exam.id))

            except Exception as e:
                db.session.rollback()
                import traceback
                current_app.logger.error(f"❌ Upload error: {traceback.format_exc()}")
                flash(f"Error processing file: {str(e)}", 'error')
                return redirect(request.url)

        # GET
        return render_template('faculty/upload_questions.html', exam=exam)



    # ========================================
    # REPLACE THE EXISTING view_exam ROUTE (around line 718-744)
    # WITH THIS ENHANCED VERSION
    # ========================================

    @app.route('/faculty/exam/<int:exam_id>')
    @login_required
    def view_exam(exam_id):
        """View exam details with enhanced access control and student-exam mapping."""
        
        # Security: Only faculty can view
        if current_user.role != 'faculty':
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))

        exam = Exam.query.get_or_404(exam_id)

        if exam.creator_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('faculty_dashboard'))

        # -------------------------------------------------
        # Basic exam info
        # -------------------------------------------------
        questions = Question.query.filter_by(exam_id=exam_id).order_by(Question.order_number).all()
        question_count = len(questions)

        student_exams = StudentExam.query.filter_by(exam_id=exam_id).all()
        total_attempts = len(student_exams)
        completed_attempts = len([se for se in student_exams if se.status == 'submitted'])

        # -------------------------------------------------
        # Load all students (for access modal)
        # -------------------------------------------------
        all_students = User.query.filter_by(role='student').order_by(User.full_name).all()

        # Fetch distinct batches
        batches = (
            db.session.query(User.batch)
            .filter(User.role == 'student', User.batch.isnot(None), User.batch != '')
            .distinct()
            .order_by(User.batch)
            .all()
        )
        all_batches = [b[0] for b in batches]

        # -------------------------------------------------
        # Build student → exams mapping
        # Show: "This student is selected in X exams"
        # -------------------------------------------------
        student_exam_map = {}  # { student_id: [exam_title, ...] }

        faculty_exams = Exam.query.filter_by(creator_id=current_user.id).all()

        for ex in faculty_exams:
            if not ex.allowed_students:
                continue
            
            allowed_ids = [
                int(x) for x in ex.allowed_students.split(',') if x.strip().isdigit()
            ]

            for sid in allowed_ids:
                student_exam_map.setdefault(sid, []).append(ex.title)

        # -------------------------------------------------
        # Render page
        # -------------------------------------------------
        return render_template(
            'faculty/view_exam.html',
            exam=exam,
            questions=questions,
            question_count=question_count,
            student_exams=student_exams,
            total_attempts=total_attempts,
            completed_attempts=completed_attempts,
            all_students=all_students,
            all_batches=all_batches,
            student_exam_map=student_exam_map   # ← NEW: passed to template
        )


    @app.route('/faculty/exam/<int:exam_id>/analytics')
    @login_required
    def exam_analytics(exam_id):
        """View exam analytics"""
        if current_user.role != 'faculty':
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))
        
        exam = Exam.query.get_or_404(exam_id)
        
        if exam.creator_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('faculty_dashboard'))
        
        student_exams = StudentExam.query.filter_by(exam_id=exam_id, status='submitted').all()
        
        total_attempts = len(student_exams)
        passed = len([se for se in student_exams if se.passed])
        failed = total_attempts - passed
        
        avg_score = sum([se.percentage for se in student_exams if se.percentage is not None]) / total_attempts if total_attempts > 0 else 0
        avg_time = sum([se.time_taken_minutes for se in student_exams if se.time_taken_minutes]) / total_attempts if total_attempts > 0 else 0
        
        questions = Question.query.filter_by(exam_id=exam_id).order_by(Question.order_number).all()
        question_stats = []
        
        for question in questions:
            # Total answers per question
            total_answers = StudentAnswer.query.join(StudentExam).filter(
                StudentAnswer.question_id == question.id,
                StudentExam.exam_id == exam_id,
                StudentExam.status == 'submitted'
            ).count()
            
            # Correct answers per question
            correct_answers = StudentAnswer.query.join(StudentExam).filter(
                StudentAnswer.question_id == question.id,
                StudentExam.exam_id == exam_id,
                StudentExam.status == 'submitted',
                StudentAnswer.is_correct == True
            ).count()
            
            accuracy = (correct_answers / total_answers * 100) if total_answers > 0 else 0
            
            question_stats.append({
                'question': question.question_text[:100] + '...' if len(question.question_text) > 100 else question.question_text,
                'total_answers': total_answers,
                'correct_answers': correct_answers,
                'accuracy': round(accuracy, 2),
                'difficulty': 'Easy' if accuracy > 70 else 'Medium' if accuracy > 40 else 'Hard'
            })
        
        flagged_exams = [se for se in student_exams if se.tab_switch_count > exam.max_tab_switches]
        
        return render_template(
            'faculty/analytics.html', 
            exam=exam,
            total_attempts=total_attempts,
            passed=passed,
            failed=failed,
            avg_score=round(avg_score, 2),
            avg_time=round(avg_time, 2),
            question_stats=question_stats,
            flagged_exams=flagged_exams,
            student_exams=student_exams
        )

    from fpdf import FPDF
    from flask import request, render_template, Response
    from datetime import datetime

    @app.route('/faculty/student_report', methods=['GET'])
    @login_required
    def faculty_student_report():
        """Display student report page with filters"""
        
        if current_user.role != 'faculty':
            flash('Access denied. Faculty only.', 'error')
            return redirect(url_for('student_dashboard'))
        
        # Get all batches
        batches = db.session.query(User.batch).filter(
            User.role == 'student',
            User.batch.isnot(None),
            User.batch != ''
        ).distinct().order_by(User.batch).all()
        all_batches = [b[0] for b in batches]
        
        # Get all exams created by this faculty
        exams = Exam.query.filter_by(creator_id=current_user.id).order_by(Exam.created_at.desc()).all()
        
        # Get filters from request
        selected_batch = request.args.get('batch', '')
        selected_exam_ids_str = request.args.getlist('exam_ids')
        selected_exam_ids = [int(id) for id in selected_exam_ids_str if id.isdigit()]
        
        report_data = None
        exam_titles = []
        summary = None
        
        if selected_batch and selected_exam_ids:
            # Get students in selected batch
            students = User.query.filter_by(
                role='student',
                batch=selected_batch
            ).order_by(User.prn_number).all()
            
            # Get exam details
            selected_exams = Exam.query.filter(Exam.id.in_(selected_exam_ids)).all()
            exam_titles = [exam.title for exam in selected_exams]
            
            # Get total possible marks for each exam
            exam_total_marks = {}
            for exam in selected_exams:
                total = sum(q.points for q in exam.questions)
                exam_total_marks[exam.id] = total
            
            # Build report data
            report_data = []
            total_students = len(students)
            appeared_count = 0
            passed_count = 0
            failed_count = 0
            absent_count = 0
            total_marks_sum = 0
            
            for student in students:
                student_row = {
                    'prn_number': student.prn_number,
                    'full_name': student.full_name,
                    'exam_marks': [],
                    'total_marks': 0,
                    'percentage': 0
                }
                
                student_appeared = False
                student_total_obtained = 0
                student_total_possible = 0
                
                # Get marks for each exam
                for exam in selected_exams:
                    student_exam = StudentExam.query.filter_by(
                        student_id=student.id,
                        exam_id=exam.id,
                        status='submitted'
                    ).first()
                    
                    if student_exam and student_exam.score is not None:
                        # Student attempted this exam
                        marks = student_exam.score
                        student_row['exam_marks'].append(f"{marks:.2f}")
                        student_total_obtained += marks
                        student_total_possible += exam_total_marks[exam.id]
                        student_appeared = True
                    else:
                        # Student didn't attempt
                        student_row['exam_marks'].append('Absent')
                        student_total_possible += exam_total_marks[exam.id]
                
                # Calculate totals and percentage
                student_row['total_marks'] = student_total_obtained
                
                if student_total_possible > 0:
                    student_row['percentage'] = (student_total_obtained / student_total_possible) * 100
                else:
                    student_row['percentage'] = 0
                
                report_data.append(student_row)
                
                # Update summary stats
                if student_appeared:
                    appeared_count += 1
                    total_marks_sum += student_row['total_marks']
                    
                    if student_row['percentage'] >= 50:
                        passed_count += 1
                    else:
                        failed_count += 1
                else:
                    absent_count += 1
            
            # Calculate summary
            summary = {
                'total_students': total_students,
                'appeared': appeared_count,
                'passed': passed_count,
                'failed': failed_count,
                'absent': absent_count,
                'average_marks': total_marks_sum / appeared_count if appeared_count > 0 else 0,
                'pass_percentage': (passed_count / appeared_count * 100) if appeared_count > 0 else 0
            }
        
        return render_template(
            'faculty/student_report.html',
            batches=all_batches,
            exams=exams,
            selected_batch=selected_batch,
            selected_exam_ids=selected_exam_ids,
            report_data=report_data,
            exam_titles=exam_titles,
            summary=summary
        )



    @app.route('/faculty/student_report/pdf', methods=['POST'])
    @login_required
    def faculty_student_report_pdf_post():
        """
        POST endpoint called by the frontend PDF modal.
        Accepts JSON payload, applies edits/filters/renames, returns base64-encoded PDF.
        """
        try:
            data = request.get_json(force=True)

            batch_name  = data.get('batch_name', '')
            exam_ids    = data.get('exam_ids', [])          # list of ints
            prn_order   = data.get('prn_order', [])         # filtered+sorted PRN list (strings)
            mark_edits  = data.get('mark_edits', {})        # {"si_mi": "value"}
            col_names   = data.get('col_names', [])         # renamed headers (list of str)
            col_notes   = data.get('col_notes', [])         # column notes  (list of str)

            # ── 1. Load exams ──────────────────────────────────────────────────────
            exams = Exam.query.filter(Exam.id.in_(exam_ids)).all()
            if not exams:
                return jsonify({'error': 'No exams found for the given IDs'}), 404

            exam_id_to_idx = {exam.id: idx for idx, exam in enumerate(exams)}

            # Use renamed titles if provided, else original titles
            if col_names and len(col_names) == len(exams):
                exam_titles = col_names
            else:
                exam_titles = [e.title for e in exams]

            # ── 2. Load students for this batch ────────────────────────────────────
            students = User.query.filter_by(batch=batch_name, role='student').all()
            if not students:
                return jsonify({'error': f'No students found for batch: {batch_name}'}), 404

            # Build a PRN → student map
            prn_to_student = {s.prn_number: s for s in students if s.prn_number}

            # ── 3. Build report_data (same structure your template expects) ─────────
            report_rows = []
            for idx, student in enumerate(students):
                # Collect marks per exam
                exam_marks = []
                total_marks = 0.0

                for exam_idx, exam in enumerate(exams):
                    student_exam = StudentExam.query.filter_by(
                        student_id=student.id,
                        exam_id=exam.id
                    ).first()

                    if student_exam and student_exam.submitted_at:
                        raw_mark = round(float(student_exam.score or 0), 2)
                    else:
                        raw_mark = None  # Absent

                    # Apply frontend mark edits (keyed as "si_mi")
                    edit_key = f"{idx}_{exam_idx}"
                    if edit_key in mark_edits:
                        edited = mark_edits[edit_key].strip()
                        raw_mark = None if edited.lower() == 'absent' else float(edited)

                    if raw_mark is None:
                        exam_marks.append('Absent')
                    else:
                        exam_marks.append(raw_mark)
                        total_marks += raw_mark

                # Compute percentage using sum of passing_score denominators
                # Use total_points from StudentExam or fall back to 100
                total_max = sum(
                    float(StudentExam.query.filter_by(
                        student_id=student.id, exam_id=e.id
                    ).with_entities(StudentExam.total_points).scalar() or 100)
                    for e in exams
                )
                # Simpler fallback: percentage based on marks only
                all_absent = all(m == 'Absent' for m in exam_marks)
                percentage = 0.0 if all_absent or total_max == 0 else round((total_marks / total_max) * 100, 2)

                report_rows.append({
                    'prn_number': student.prn_number or '—',
                    'full_name':  student.full_name  or student.username,
                    'exam_marks': exam_marks,
                    'total_marks': total_marks,
                    'percentage':  percentage,
                })

            # ── 4. Apply PRN filter/sort from frontend ─────────────────────────────
            if prn_order:
                prn_index = {prn: i for i, prn in enumerate(prn_order)}
                # Keep only rows whose PRN is in prn_order, in that order
                report_rows = sorted(
                    [r for r in report_rows if r['prn_number'] in prn_index],
                    key=lambda r: prn_index[r['prn_number']]
                )

            if not report_rows:
                return jsonify({'error': 'No data rows to include in the PDF'}), 400

            # ── 5. Compute summary ─────────────────────────────────────────────────
            appeared  = sum(1 for r in report_rows if r['total_marks'] > 0 or any(m != 'Absent' for m in r['exam_marks']))
            absent    = len(report_rows) - appeared
            passed    = sum(1 for r in report_rows if r['percentage'] >= 50)
            failed    = appeared - passed
            avg_marks = (sum(r['total_marks'] for r in report_rows if r['total_marks'] > 0) / appeared) if appeared else 0.0
            pass_pct  = (passed / appeared * 100) if appeared else 0.0

            summary_data = {
                'total_students': len(report_rows),
                'appeared':       appeared,
                'passed':         passed,
                'failed':         failed,
                'absent':         absent,
                'average_marks':  round(avg_marks, 2),
                'pass_percentage': round(pass_pct, 2),
            }

            # ── 6. Generate PDF ────────────────────────────────────────────────────
            pdf_buffer = generate_batch_report_pdf(
                batch_name   = batch_name,
                exam_titles  = exam_titles,
                report_data  = report_rows,
                summary_data = summary_data,
                col_notes    = col_notes if col_notes else None,
            )

            # ── 7. Return base64-encoded PDF ───────────────────────────────────────
            pdf_b64 = base64.b64encode(pdf_buffer.read()).decode('utf-8')
            filename = f"report_{batch_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

            return jsonify({
                'pdf_b64':  pdf_b64,
                'filename': filename,
            })

        except Exception as e:
            import traceback
            app.logger.error(f"PDF generation error: {traceback.format_exc()}")
            return jsonify({'error': str(e)}), 500

    @app.route('/faculty/student_report/pdf', methods=['POST'])
    @login_required
    def faculty_student_report_pdf_multi():
        """Generate PDF report — honours frontend filters, sort, edits, renamed columns."""
        import base64, traceback

        try:
            if current_user.role not in ('faculty', 'admin'):
                return jsonify({'error': 'Access denied'}), 403

            payload = request.get_json(force=True, silent=True) or {}

            batch_name   = payload.get('batch_name', '')
            exam_ids_raw = payload.get('exam_ids', [])
            try:
                exam_ids = [int(x) for x in exam_ids_raw if str(x).strip().isdigit() or isinstance(x, int)]
            except Exception:
                exam_ids = []

            if not batch_name or not exam_ids:
                return jsonify({'error': f'Invalid parameters: batch_name={repr(batch_name)}, exam_ids={exam_ids_raw}'}), 400

            # Faculty isolation
            if current_user.role == 'faculty':
                for eid in exam_ids:
                    ec = Exam.query.get(eid)
                    if ec and ec.creator_id != current_user.id:
                        return jsonify({'error': 'Access denied for exam id ' + str(eid)}), 403

            # ── Frontend state from payload ──────────────────────────────────────
            # Ordered list of PRN numbers (visible rows in browser sort/filter order)
            prn_order   = payload.get('prn_order', [])     # list[str]
            # Dict {"si_mi": "value"} of hand-edited marks
            mark_edits  = payload.get('mark_edits', {})    # dict
            # Renamed column headers (one per exam in order)
            col_names   = payload.get('col_names', [])     # list[str]
            # Column sub-notes
            col_notes   = payload.get('col_notes', [])     # list[str]

            # ── Fetch base data from DB ──────────────────────────────────────────
            students_q = User.query.filter_by(role='student', batch=batch_name).all()
            # Build a PRN → index map for quick lookup
            students_by_prn = {(s.prn_number or ''): s for s in students_q}

            exams = Exam.query.filter(Exam.id.in_(exam_ids)).all()
            # Preserve the order the frontend used (exam_ids order)
            exam_id_order = [int(x) for x in exam_ids_raw]
            exams_ordered = sorted(exams, key=lambda e: exam_id_order.index(e.id) if e.id in exam_id_order else 999)

            exam_total_marks = {exam.id: sum(q.points for q in exam.questions) for exam in exams_ordered}

            # Effective column titles (renamed if requested)
            effective_titles = []
            for i, exam in enumerate(exams_ordered):
                if col_names and i < len(col_names) and col_names[i].strip():
                    effective_titles.append(col_names[i].strip())
                else:
                    effective_titles.append(exam.title)

            # ── Build report rows ────────────────────────────────────────────────
            def build_row(student, row_index):
                row = {
                    'prn_number': student.prn_number or '',
                    'full_name':  student.full_name or student.username or '',
                    'exam_marks': [],
                    'total_marks': 0,
                    'percentage': 0,
                }
                total_obtained = 0
                total_possible = 0
                appeared = False

                for mi, exam in enumerate(exams_ordered):
                    edit_key = f'{row_index}_{mi}'
                    if edit_key in mark_edits:
                        raw_val = str(mark_edits[edit_key]).strip()
                    else:
                        se = StudentExam.query.filter_by(
                            student_id=student.id,
                            exam_id=exam.id,
                            status='submitted'
                        ).first()
                        raw_val = f'{se.score:.2f}' if (se and se.score is not None) else 'Absent'

                    row['exam_marks'].append(raw_val)
                    total_possible += exam_total_marks[exam.id]
                    try:
                        v = float(raw_val)
                        total_obtained += v
                        appeared = True
                    except ValueError:
                        pass  # Absent

                row['total_marks'] = total_obtained
                row['percentage']  = (total_obtained / total_possible * 100) if total_possible > 0 else 0
                row['_appeared']   = appeared
                return row

            # ── Determine row order ──────────────────────────────────────────────
            if prn_order:
                # Use browser's visible order (already filtered + sorted)
                ordered_students = []
                idx_map = {}  # prn -> original index in DB-ordered list
                for i, s in enumerate(User.query.filter_by(role='student', batch=batch_name).order_by(User.prn_number).all()):
                    idx_map[s.prn_number or ''] = i

                for prn in prn_order:
                    s = students_by_prn.get(prn)
                    if s:
                        ordered_students.append((idx_map.get(prn, 0), s))
            else:
                ordered_students = list(enumerate(
                    User.query.filter_by(role='student', batch=batch_name).order_by(User.prn_number).all()
                ))

            report_data     = []
            appeared_count  = passed_count = failed_count = absent_count = 0
            total_marks_sum = 0

            for orig_idx, student in ordered_students:
                row = build_row(student, orig_idx)
                report_data.append(row)
                if row['_appeared']:
                    appeared_count  += 1
                    total_marks_sum += row['total_marks']
                    if row['percentage'] >= 50:
                        passed_count += 1
                    else:
                        failed_count += 1
                else:
                    absent_count += 1

            total_students = len(report_data)

            summary_data = {
                'total_students':  total_students,
                'appeared':        appeared_count,
                'passed':          passed_count,
                'failed':          failed_count,
                'absent':          absent_count,
                'average_marks':   total_marks_sum / appeared_count if appeared_count > 0 else 0,
                'pass_percentage': (passed_count / appeared_count * 100) if appeared_count > 0 else 0,
            }

            # ── Generate PDF ─────────────────────────────────────────────────────
            from backend.services.pdf_generator import generate_batch_report_pdf
            pdf_buffer = generate_batch_report_pdf(
                batch_name=batch_name,
                exam_titles=effective_titles,
                report_data=report_data,
                summary_data=summary_data,
                col_notes=col_notes if col_notes else [],
            )

            pdf_bytes = pdf_buffer.read()
            encoded   = base64.b64encode(pdf_bytes).decode('utf-8')
            filename  = f"Student_Report_{batch_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

            return jsonify({'pdf_b64': encoded, 'filename': filename})

        except Exception as e:
            current_app.logger.exception('PDF generation error:')
            return jsonify({'error': str(e), 'detail': traceback.format_exc()}), 500

    @app.route('/faculty/student/<int:student_id>/profile')
    @login_required
    def faculty_student_profile(student_id):
        """View student profile with analytics (Faculty view) — faculty isolated"""
        if current_user.role not in ('faculty', 'admin'):
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))
        
        # Faculty isolation check
        if current_user.role == 'faculty' and not _faculty_can_access_student(student_id):
            flash('Access denied: this student is not in your exams.', 'error')
            return redirect(url_for('faculty_student_list'))
        
        student = User.query.get_or_404(student_id)
        
        if student.role != 'student':
            flash('Invalid student', 'error')
            return redirect(url_for('faculty_dashboard'))
        
        student_exams = StudentExam.query.filter_by(
            student_id=student_id,
            status='submitted'
        ).order_by(StudentExam.submitted_at.desc()).all()
        
        total_exams = len(student_exams)
        passed_exams = len([se for se in student_exams if se.passed])
        failed_exams = total_exams - passed_exams
        valid_scores = [se.percentage for se in student_exams if se.percentage is not None]
        avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0
        avg_time = sum([se.time_taken_minutes for se in student_exams if se.time_taken_minutes]) / total_exams if total_exams > 0 else 0
        
        recent_exams = student_exams[:10]
        chart_data = {
            'labels': [se.exam.title[:30] for se in reversed(recent_exams)],
            'scores': [se.percentage for se in reversed(recent_exams)],
            'dates': [se.submitted_at.strftime('%d/%m') for se in reversed(recent_exams)],
            'passed': [1 if se.passed else 0 for se in reversed(recent_exams)]
        }
        
        return render_template('faculty/student_profile.html',
                             student=student,
                             student_exams=student_exams,
                             total_exams=total_exams,
                             passed_exams=passed_exams,
                             failed_exams=failed_exams,
                             avg_score=avg_score,
                             avg_time=avg_time,
                             chart_data=chart_data)

    # ============= Student Routes =============

    # CORRECTED student_dashboard route
    # Replace lines 1552-1597 in routes.py with this:

    @app.route('/student/dashboard')
    @login_required
    def student_dashboard():
        """Enhanced Student Dashboard - Safe from None/Undefined values"""
        if current_user.role != 'student':
            flash('Access denied', 'error')
            return redirect(url_for('faculty_dashboard'))
        
        # Fetch student exams
        completed_exams = StudentExam.query.filter_by(
            student_id=current_user.id,
            status='submitted'
        ).order_by(StudentExam.submitted_at.desc()).all()
        
        in_progress = StudentExam.query.filter_by(
            student_id=current_user.id,
            status='in_progress'
        ).first()
        
        # Fetch all active exams
        available_exams = Exam.query.filter(Exam.is_active == True).all()
        
        # ✅ GET ALL ATTEMPTED EXAM IDs for this student
        attempted_exam_ids = set()
        all_student_attempts = StudentExam.query.filter_by(student_id=current_user.id).all()
        for attempt in all_student_attempts:
            attempted_exam_ids.add(attempt.exam_id)
        
        # ✅ ADD attempted_by_current_user FLAG to each exam
        for exam in available_exams:
            exam.attempted_by_current_user = exam.id in attempted_exam_ids
            exam.is_new = False
            exam.is_retake = False

            # 🔑 Determine if this exam is assigned to the current student
            if exam.allow_all_students:
                exam.is_assigned = True
            elif exam.allowed_students:
                allowed_ids = [s.strip() for s in exam.allowed_students.split(',') if s.strip()]
                exam.is_assigned = str(current_user.id) in allowed_ids
            else:
                exam.is_assigned = False

        # ✅ Filter valid percentages
        percentages = [se.percentage for se in completed_exams if se.percentage is not None]

        total_exams_taken = len(completed_exams)
        avg_score = (sum(percentages) / len(percentages)) if percentages else 0
        passed_count = len([se for se in completed_exams if se.passed])
        pass_rate = (passed_count / len(completed_exams) * 100) if completed_exams else 0
        best_score = max(percentages) if percentages else 0

        # ✅ Always define it
        max_percentage = best_score if best_score is not None else 0
        top_students = top_students if 'top_students' in locals() else []

        # ✅ Now pass clean, guaranteed serializable values
        return render_template(
            'student/dashboard.html',
            completed_exams=completed_exams or [],
            available_exams=available_exams or [],
            total_exams_taken=total_exams_taken or 0,
            avg_score=avg_score or 0,
            passed_count=passed_count or 0,
            pass_rate=pass_rate or 0,
            best_score=best_score or 0,
            max_percentage=(locals().get("max_percentage") or 0),
            top_students=top_students or [],
            in_progress=in_progress or None
        )

    @app.route("/start-exam/<int:exam_id>", methods=["POST", "GET"])
    @login_required
    def start_exam(exam_id):

        # -------------------------
        # 1. STUDENT ROLE CHECK
        # -------------------------
        if current_user.role != "student":
            flash("Only students can start exams.", "error")
            return redirect(url_for("faculty_dashboard"))

        exam = Exam.query.get_or_404(exam_id)

        # -------------------------
        # 2. EXAM ACTIVE CHECK
        # -------------------------
        if not exam.is_active:
            flash("This exam is not active.", "error")
            return redirect(url_for("student_dashboard"))

        # -------------------------
        # 3. EXAM ACCESS CONTROL
        # -------------------------
        # If faculty allowed only specific students
        if not exam.allow_all_students:
            allowed = exam.allowed_students or []  # list of IDs in string form
            if str(current_user.id) not in allowed:
                flash("You are not allowed to access this exam.", "error")
                return redirect(url_for("student_dashboard"))

        # -------------------------
        # 4. CHECK IF EXAM ALREADY STARTED
        # -------------------------
        existing_attempt = StudentExam.query.filter_by(
            student_id=current_user.id,
            exam_id=exam.id
        ).first()

        # If an attempt exists and is IN PROGRESS → resume
        if existing_attempt and existing_attempt.status == "in_progress":
            return redirect(url_for("take_exam", exam_id=exam_id))

        # If attempt exists and is already submitted → prevent restart
        if existing_attempt and existing_attempt.status == "submitted":
            flash("You have already completed this exam.", "error")
            return redirect(url_for("student_dashboard"))

        # -------------------------
        # 5. RANDOMIZE QUESTIONS
        # -------------------------
        questions = exam.questions[:]  # copy

        if exam.randomize_questions:
            random.shuffle(questions)

        # -------------------------
        # 6. CREATE NEW STUDENT EXAM ATTEMPT
        # -------------------------
        student_exam = StudentExam(
            student_id=current_user.id,
            exam_id=exam.id,
            started_at=datetime.utcnow(),  
            status="in_progress",
            time_taken_minutes=0,
            tab_switch_count=0,
            
        )
        db.session.add(student_exam)
        db.session.commit()

        # -------------------------
        # 7. LINK QUESTIONS → STORE ORDER SAFELY
        # -------------------------
        # Store question order as JSON list for consistent navigation
        student_exam.question_order = json.dumps([q.id for q in questions])
        db.session.commit()

        # -------------------------
        # 8. REDIRECT TO TAKE EXAM
        # -------------------------
        return redirect(url_for("take_exam", exam_id=exam_id))



    # ========================================
    # ADD THIS NEW ROUTE AFTER view_exam
    # ========================================

    @app.route('/faculty/exam/<int:exam_id>/update_access', methods=['POST'])
    @login_required
    def update_exam_access(exam_id):
        """Update exam access control settings with proper status handling."""
        
        # Access Check
        if current_user.role != 'faculty':
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))

        exam = Exam.query.get_or_404(exam_id)

        if exam.creator_id != current_user.id:
            flash("You don't have permission to modify this exam", 'error')
            return redirect(url_for('faculty_dashboard'))

        # Extract mode
        access_mode = request.form.get('access_mode')

        # ---------------------------------------------------------------------
        # MODE: STOPPED
        # ---------------------------------------------------------------------
        if access_mode == 'stopped':

            exam.is_active = False
            exam.allow_all_students = False
            exam.allowed_students = None
            exam.status = "inactive"        # 🔥 NEW: ensures badge shows INACTIVE

            # Log all active student attempts
            active_attempts = StudentExam.query.filter_by(
                exam_id=exam_id,
                status='in_progress'
            ).all()

            for attempt in active_attempts:
                db.session.add(ActivityLog(
                    student_exam_id=attempt.id,
                    activity_type='exam_disabled',
                    description='Exam access was stopped by faculty',
                    severity='high',
                    created_at=datetime.utcnow()
                ))

            db.session.commit()
            flash("⚠️ Exam access has been stopped for all students", 'warning')

        # ---------------------------------------------------------------------
        # MODE: ALLOW ALL STUDENTS
        # ---------------------------------------------------------------------
        elif access_mode == 'all':

            exam.is_active = True
            exam.allow_all_students = True
            exam.allowed_students = None
            exam.status = "active"          # 🔥 NEW

            db.session.commit()
            flash("✅ Exam is now open to all students", 'success')

        # ---------------------------------------------------------------------
        # MODE: SPECIFIC STUDENTS ONLY
        # ---------------------------------------------------------------------
        elif access_mode == 'specific':

            allowed_students = request.form.get('allowed_students', '')

            # must select students
            if not allowed_students.strip():
                flash("⚠️ Please select at least one student", 'warning')
                return redirect(url_for('view_exam', exam_id=exam_id))

            exam.is_active = True
            exam.allow_all_students = False
            exam.allowed_students = allowed_students
            exam.status = "active"          # 🔥 NEW

            # Count how many selected
            student_ids = [s.strip() for s in allowed_students.split(',') if s.strip()]
            count = len(student_ids)

            db.session.commit()
            flash(f"✅ Exam access granted to {count} selected student(s)", 'success')

        # ---------------------------------------------------------------------
        # INVALID MODE
        # ---------------------------------------------------------------------
        else:
            flash("Invalid access mode", 'error')

        return redirect(url_for('view_exam', exam_id=exam_id))


    # ============================================================
    # EXAM ACCESS KEY MANAGEMENT
    # ============================================================

    @app.route('/faculty/exam/<int:exam_id>/generate-key', methods=['POST'])
    @login_required
    def generate_exam_key(exam_id):
        """Faculty generates (or regenerates) a unique access key for an exam."""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        exam = Exam.query.get_or_404(exam_id)

        if current_user.role != 'admin' and exam.creator_id != current_user.id:
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

        # Generate a random 8-character alphanumeric key
        chars = string.ascii_uppercase + string.digits
        new_key = ''.join(random.choices(chars, k=8))
        exam.access_key = new_key
        db.session.commit()

        return jsonify({'success': True, 'key': new_key})

    @app.route('/faculty/exam/<int:exam_id>/clear-key', methods=['POST'])
    @login_required
    def clear_exam_key(exam_id):
        """Faculty removes the access key requirement for an exam."""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        exam = Exam.query.get_or_404(exam_id)

        if current_user.role != 'admin' and exam.creator_id != current_user.id:
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

        exam.access_key = None
        db.session.commit()
        return jsonify({'success': True})

    @app.route('/exam/<int:exam_id>/verify-key', methods=['POST'])
    @login_required
    def verify_exam_key(exam_id):
        """Student submits the access key for an exam. Stores approval in session."""
        if current_user.role != 'student':
            return jsonify({'success': False, 'error': 'Students only'}), 403

        exam = Exam.query.get_or_404(exam_id)

        # If no key is set on this exam, access is open
        if not exam.access_key:
            session[f'exam_key_verified_{exam_id}'] = True
            return jsonify({'success': True})

        if request.is_json:
            submitted_key = (request.json or {}).get('access_key', '')
        else:
            submitted_key = request.form.get('access_key', '')
        submitted_key = submitted_key.strip().upper()

        if submitted_key == exam.access_key.strip().upper():
            session[f'exam_key_verified_{exam_id}'] = True
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Invalid access key. Please check with your faculty.'})

    @app.route('/faculty/exam/<int:exam_id>/post-settings', methods=['POST'])
    @login_required
    def update_exam_post_settings(exam_id):
        """Faculty updates leaderboard visibility and post-exam redirect behaviour."""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        exam = Exam.query.get_or_404(exam_id)
        if current_user.role != 'admin' and exam.creator_id != current_user.id:
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

        data = request.get_json() or {}
        if 'show_leaderboard' in data:
            exam.show_leaderboard = bool(data['show_leaderboard'])
        if 'post_exam_redirect' in data and data['post_exam_redirect'] in ('results', 'dashboard'):
            exam.post_exam_redirect = data['post_exam_redirect']

        db.session.commit()
        return jsonify({'success': True})


    def calculate_student_score(student_exam_id):
        """
        Calculate and update the score for a completed student exam
        
        Args:
            student_exam_id: ID of the StudentExam record to score
        
        Returns:
            dict: Scoring results with score, total_points, percentage, passed
        """
        try:
            from models import StudentExam, StudentAnswer, Question
            
            # Get student exam
            student_exam = StudentExam.query.get(student_exam_id)
            if not student_exam:
                print(f"❌ StudentExam {student_exam_id} not found")
                return None
            
            # Get exam
            exam = student_exam.exam
            if not exam:
                print(f"❌ Exam not found for StudentExam {student_exam_id}")
                return None
            
            # Get all questions for this exam
            questions = Question.query.filter_by(exam_id=exam.id).all()
            
            if not questions:
                print(f"⚠️ No questions found for exam {exam.id}")
                student_exam.score = 0
                student_exam.total_points = 0
                student_exam.percentage = 0
                student_exam.passed = False
                db.session.commit()
                return {
                    'score': 0,
                    'total_points': 0,
                    'percentage': 0,
                    'passed': False
                }
            
            # Get student's answers
            student_answers = StudentAnswer.query.filter_by(
                student_exam_id=student_exam_id
            ).all()
            
            # Create answer lookup dict
            answer_dict = {ans.question_id: ans for ans in student_answers}
            
            # Calculate score
            earned_points = 0
            total_points = 0
            correct_count = 0
            
            for question in questions:
                points = question.points or 1.0
                total_points += points
                
                # Check if student answered this question
                student_answer = answer_dict.get(question.id)
                
                if student_answer and student_answer.selected_answer:
                    # Check if answer is correct
                    is_correct = (student_answer.selected_answer.upper() == 
                                question.correct_answer.upper())
                    
                    if is_correct:
                        earned_points += points
                        correct_count += 1
                        student_answer.is_correct = True
                        student_answer.points_earned = points
                    else:
                        student_answer.is_correct = False
                        student_answer.points_earned = 0
                else:
                    # No answer provided - create a record with 0 points
                    if not student_answer:
                        student_answer = StudentAnswer(
                            student_exam_id=student_exam_id,
                            question_id=question.id,
                            selected_answer="0",
                            is_correct=False,
                            points_earned=0
                        )
                        db.session.add(student_answer)
                    else:
                        student_answer.is_correct = False
                        student_answer.points_earned = 0
            
            # Calculate percentage
            percentage = (earned_points / total_points * 100) if total_points > 0 else 0
            
            # Determine if passed
            passing_score = exam.passing_score or 50.0
            passed = percentage >= passing_score
            
            # Update student exam record
            student_exam.score = round(earned_points, 2)
            student_exam.total_points = round(total_points, 2)
            student_exam.percentage = round(percentage, 2)
            student_exam.passed = passed
            student_exam.status = 'completed'
            student_exam.completed = True
            
            # Calculate time taken
            if student_exam.started_at and student_exam.submitted_at:
                time_taken = (student_exam.submitted_at - student_exam.started_at).total_seconds() / 60
                student_exam.time_taken_minutes = int(time_taken)
            
            db.session.commit()
            
            print(f"✅ Scored StudentExam {student_exam_id}:")
            print(f"   Score: {earned_points}/{total_points} ({percentage:.1f}%)")
            print(f"   Passed: {passed}")
            print(f"   Correct Answers: {correct_count}/{len(questions)}")
            
            return {
                'score': earned_points,
                'total_points': total_points,
                'percentage': percentage,
                'passed': passed,
                'correct_count': correct_count,
                'total_questions': len(questions)
            }
            
        except Exception as e:
            print(f"❌ Error calculating score for StudentExam {student_exam_id}: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            return None

    @app.route('/exam/<int:exam_id>/take', methods=['GET'])
    @login_required
    def take_exam(exam_id):
        """Student exam taking interface - tracks absolute start time with AI Proctoring"""
        if current_user.role != 'student':
            flash('Only students can take exams.', 'error')
            return redirect(url_for('index'))

        exam = Exam.query.get_or_404(exam_id)
        
        # Check if exam is active
        exam_status = getattr(exam, 'status', 'active')
        force_ended = getattr(exam, 'force_ended', False)
        
        if exam_status == 'ended' or force_ended:
            flash('This exam has been ended by the instructor.', 'error')
            return redirect(url_for('student_dashboard'))
        
        # Get or create student exam record
        student_exam = StudentExam.query.filter_by(
            student_id=current_user.id,
            exam_id=exam_id
        ).first()

        from datetime import datetime, timedelta
        
        if not student_exam:
            # Create new exam attempt
            student_exam = StudentExam(
                student_id=current_user.id,
                exam_id=exam_id,
                started_at=datetime.utcnow(),
                tab_switch_count=0,
                status="in_progress",
            )
            
            # Set proctoring defaults
            enable_proctoring = getattr(exam, 'enable_proctoring', True)
            student_exam.proctoring_enabled = enable_proctoring
            student_exam.calibration_completed = False
            student_exam.total_violations = 0
            student_exam.proctoring_status = 'active'
            
            db.session.add(student_exam)
            db.session.commit()
            
            # Calculate time remaining (new exam, so full duration)
            time_remaining = exam.duration_minutes or 60
            
        else:
            # Check if already submitted
            if student_exam.submitted_at:
                flash('You have already submitted this exam.', 'info')
                return redirect(url_for('student_dashboard'))
            
            # Calculate remaining time based on start time
            elapsed = (datetime.utcnow() - student_exam.started_at).total_seconds() / 60
            time_remaining = max(0, (exam.duration_minutes or 60) - elapsed)
            
            # Auto-submit if time expired
            if time_remaining <= 0:
                student_exam.submitted_at = datetime.utcnow()
                calculate_student_score(student_exam.id)
                db.session.commit()
                flash('Exam time expired. Your answers have been submitted.', 'warning')
                return redirect(url_for('student_dashboard'))

        # Get questions in persistent shuffled order for this student
        question_order_json = getattr(student_exam, 'question_order', None)
        if question_order_json:
            # Use stored order from start_exam or first visit
            try:
                q_ids = json.loads(question_order_json)
                q_map = {q.id: q for q in Question.query.filter_by(exam_id=exam_id).all()}
                questions = [q_map[qid] for qid in q_ids if qid in q_map]
            except Exception:
                questions = Question.query.filter_by(exam_id=exam_id).all()
        else:
            # First visit without start_exam — shuffle and persist
            import random as _random
            questions = Question.query.filter_by(exam_id=exam_id).all()
            _random.shuffle(questions)
            student_exam.question_order = json.dumps([q.id for q in questions])
            db.session.commit()
        
        # Always start with a clean slate — never pre-fill answers
        # (auto-save preserves answers server-side; the exam UI must be fresh each load)
        existing_answers = {}

        return render_template(

            'student/take_exam.html',
            exam=exam,
            questions=questions,
            student_exam=student_exam,
            time_remaining=time_remaining,
            existing_answers=existing_answers,
            needs_key=bool(exam.access_key and not session.get(f'exam_key_verified_{exam_id}'))
        )


    @app.route('/api/save-answer', methods=['POST'])
    @login_required
    def save_answer():

        data = request.get_json()
        student_exam_id = data.get("student_exam_id")
        answers = data.get("answers", {})

        if not student_exam_id:
            return jsonify({"error": "missing student_exam_id"}), 400

        for qid, selected in answers.items():
            qid = int(qid)

            record = StudentAnswer.query.filter_by(
                student_exam_id=student_exam_id,
                question_id=qid
            ).first()

            if record is None:
                # Create new answer row
                record = StudentAnswer(
                    student_exam_id=student_exam_id,
                    question_id=qid,
                    selected_answer=selected,
                    answered_at=datetime.utcnow()
                )
                db.session.add(record)
            else:
                # Update existing row
                record.selected_answer = selected
                record.answered_at = datetime.utcnow()

        db.session.commit()
        return jsonify({"status": "saved"})


    @app.route('/api/update-tabcount/<int:student_exam_id>', methods=['POST'])
    @login_required
    def api_update_tabcount(student_exam_id):
        """Update the number of tab switches."""
        data = request.get_json() or {}
        new_count = int(data.get('tab_switch_count', 0))

        student_exam = StudentExam.query.get_or_404(student_exam_id)
        if student_exam.student_id != current_user.id:
            return jsonify({"error": "Unauthorized"}), 403

        student_exam.tab_switch_count = new_count
        db.session.commit()

        return jsonify({"status": "updated", "tab_switch_count": new_count})
    # ================================
    # Log Activity
    # ================================
    @app.route('/api/log-activity/<int:student_exam_id>', methods=['POST'])
    @login_required
    def api_log_activity(student_exam_id):
        """Log tab switches, screenshot attempts, or other suspicious activities."""
        data = request.get_json() or {}
        activity_type = data.get('activity_type', 'unknown')
        description = data.get('description', '')
        severity = data.get('severity', 'low')

        student_exam = StudentExam.query.get_or_404(student_exam_id)
        if student_exam.student_id != current_user.id:
            return jsonify({"error": "Unauthorized"}), 403

        log = ActivityLog(
            student_exam_id=student_exam.id,
            activity_type=activity_type,
            description=description,
            severity=severity
        )
        db.session.add(log)
        db.session.commit()

        return jsonify({"status": "logged"})
    def assign_shuffle(student_exam):
        """Create a persistent shuffled question & option order for a new StudentExam."""
        import random, json

        exam = student_exam.exam
        questions = Question.query.filter_by(exam_id=exam.id).all()
        random.shuffle(questions)
        q_order = [q.id for q in questions]
        option_mapping = {}

        for q in questions:
            options = ['A', 'B', 'C', 'D']
            random.shuffle(options)
            option_mapping[str(q.id)] = options

        student_exam.question_order = json.dumps(q_order)
        student_exam.option_mapping = json.dumps(option_mapping)
        db.session.commit()

    @app.route("/api/check-exam-status/<int:student_exam_id>")
    @login_required
    def api_check_exam_status(student_exam_id):
        """Check if exam was force-ended by faculty"""
        try:
            # Use SQLAlchemy ORM instead of raw SQL
            student_exam = StudentExam.query.get(student_exam_id)
            
            if not student_exam:
                return jsonify({"error": "Not found"}), 404
            
            # Verify student access
            if student_exam.student_id != current_user.id:
                return jsonify({"error": "Unauthorized"}), 403
            
            # Get exam to check force_ended status
            exam = student_exam.exam
            
            # Check if exam was force-ended
            force_ended = getattr(exam, 'force_ended', False) or False
            force_ended_at = getattr(exam, 'force_ended_at', None)
            
            # Get updated end time if duration was extended
            updated_end_time = None
            if hasattr(exam, 'end_time') and exam.end_time:
                updated_end_time = exam.end_time.isoformat()
            elif student_exam.started_at and exam.duration_minutes:
                from datetime import timedelta
                end_time = student_exam.started_at + timedelta(minutes=exam.duration_minutes)
                updated_end_time = end_time.isoformat()
            
            return jsonify({
                "force_ended": force_ended,
                "force_ended_at": force_ended_at.isoformat() if force_ended_at else None,
                "updated_end_time": updated_end_time,
                "exam_status": getattr(exam, 'status', 'active')
            })
            
        except Exception as e:
            print(f"❌ Error checking exam status: {e}")
            return jsonify({"error": str(e)}), 500



    @app.route('/api/log-activity', methods=['POST'])
    @login_required
    def log_activity():
        """Log activity"""
        data = request.json
        student_exam_id = data.get('student_exam_id')
        activity_type = data.get('activity_type')
        description = data.get('description', '')
        severity = data.get('severity', 'low')
        
        student_exam = StudentExam.query.get(student_exam_id)
        
        if not student_exam or student_exam.student_id != current_user.id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        
        activity_log = ActivityLog(
            student_exam_id=student_exam_id,
            activity_type=activity_type,
            description=description,
            severity=severity
        )
        
        db.session.add(activity_log)
        
        if activity_type == 'tab_switch':
            student_exam.tab_switch_count += 1
        
        student_exam.suspicious_activity_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'tab_switch_count': student_exam.tab_switch_count,
            'max_allowed': student_exam.exam.max_tab_switches
        })

    @app.route('/submit_exam/<int:student_exam_id>', methods=['POST', 'GET'])
    @login_required
    def submit_exam(student_exam_id):
        """
        Finalize and grade a student's exam.
        This endpoint will:
        - treat missing answers as "0"
        - mark StudentAnswer.is_correct and points_earned
        - compute StudentExam.score, total_points, percentage, passed flag
        - mark status='submitted' and set submitted_at
        """
        student_exam = StudentExam.query.get_or_404(student_exam_id)

        # Security checks
        if current_user.role != 'student' or student_exam.student_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))

        if student_exam.status in ('submitted', 'force_ended', 'terminated'):
            # Already graded (e.g. faculty force-ended) — go straight to result
            # Make sure score is calculated if it's missing
            if student_exam.score is None:
                try:
                    calculate_student_score(student_exam_id)
                except Exception as _e:
                    print(f"⚠️ Late score calc failed: {_e}")
            flash('Exam submitted.', 'info')
            # Honour faculty's post-exam redirect even on re-entry
            _exam = student_exam.exam
            if _exam:
                _post = getattr(_exam, 'post_exam_redirect', 'results') or 'results'
                if _post == 'dashboard':
                    return redirect(url_for('student_dashboard'))
            return redirect(url_for('exam_result', student_exam_id=student_exam_id))

        exam = student_exam.exam
        if not exam:
            flash('Exam record missing or deleted', 'error')
            return redirect(url_for('student_dashboard'))

        # Load questions for this exam ONLY
        questions = Question.query.filter_by(exam_id=exam.id).all()
        total_questions = len(questions)

        # Build quick lookup for existing StudentAnswer rows
        existing_answers = {a.question_id: a for a in StudentAnswer.query.filter_by(student_exam_id=student_exam.id).all()}

        total_points = 0.0
        score = 0.0

        for q in questions:
            total_points += (q.points or 0.0)

            ans = existing_answers.get(q.id)
            if ans is None:
                # create unanswered answer with "0"
                ans = StudentAnswer(
                    student_exam_id=student_exam.id,
                    question_id=q.id,
                    selected_answer="0",
                    is_correct=False,
                    points_earned=0.0,
                    answered_at=datetime.utcnow()
                )
                db.session.add(ans)
            else:
                # normalize selected_answer to string
                sel = (ans.selected_answer or "").strip()
                if sel == "" or sel == "0":
                    ans.selected_answer = "0"
                    ans.is_correct = False
                    ans.points_earned = 0.0
                else:
                    # check correctness (compare to q.correct_answer)
                    if q.correct_answer and sel.upper() == q.correct_answer.upper():
                        ans.is_correct = True
                        ans.points_earned = q.points or 0.0
                    else:
                        ans.is_correct = False
                        ans.points_earned = 0.0

                # update answered_at if missing
                if not ans.answered_at:
                    ans.answered_at = datetime.utcnow()

            # sum score
            score += (ans.points_earned or 0.0)

        # Avoid division by zero
        percentage = (score / total_points * 100.0) if total_points > 0 else 0.0
        passed = (percentage >= (exam.passing_score or 0.0))

        # Update student_exam
        student_exam.score = score
        student_exam.total_points = total_points
        student_exam.percentage = round(percentage, 2)
        student_exam.passed = passed
        student_exam.submitted_at = datetime.utcnow()
        student_exam.status = 'submitted'
        student_exam.completed = True

        # time_taken_minutes: difference between submitted_at and started_at
        if student_exam.started_at:
            delta_min = (student_exam.submitted_at - student_exam.started_at).total_seconds() / 60.0
            student_exam.time_taken_minutes = int(round(delta_min))
        else:
            student_exam.time_taken_minutes = None

        db.session.commit()

        flash('✅ Exam submitted successfully.', 'success')

        # ── Honour faculty's post-exam redirect preference ───────────────
        post_redirect = getattr(exam, 'post_exam_redirect', 'results') or 'results'
        if post_redirect == 'dashboard':
            return redirect(url_for('student_dashboard'))
        return redirect(url_for('exam_result', student_exam_id=student_exam.id))

    @app.route('/student/exam/<int:student_exam_id>/result')
    @login_required
    def exam_result(student_exam_id):
        """View result"""
        if current_user.role != 'student':
            flash('Access denied', 'error')
            return redirect(url_for('faculty_dashboard'))
        
        student_exam = StudentExam.query.get_or_404(student_exam_id)
        
        if student_exam.student_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))
        
        if student_exam.status not in ('submitted', 'force_ended', 'terminated', 'completed'):
            flash('Exam not yet submitted', 'error')
            return redirect(url_for('take_exam', exam_id=student_exam.exam_id))

        # ── Enforce faculty's post-exam redirect preference ──────────────
        # If faculty set redirect to 'dashboard', students cannot view their
        # result page at all — even via direct URL — until faculty changes it.
        exam = student_exam.exam
        if exam:
            post_redirect = getattr(exam, 'post_exam_redirect', 'results') or 'results'
            if post_redirect == 'dashboard' and current_user.role == 'student':
                flash('Result viewing has been restricted by your faculty for this exam.', 'info')
                return redirect(url_for('student_dashboard'))
        
        answers_with_questions = []
        for answer in student_exam.answers:
            question = Question.query.get(answer.question_id)
            answers_with_questions.append({
                'question': question,
                'answer': answer
            })
        
        return render_template('student/result.html',
                             student_exam=student_exam,
                             exam=student_exam.exam,
                             answers_with_questions=answers_with_questions)

    @app.route('/student/<int:student_id>/profile')
    @login_required
    def student_profile(student_id):
        """View student profile (Public view)"""
        student = User.query.get_or_404(student_id)
        
        if student.role != 'student':
            flash('Invalid student', 'error')
            return redirect(url_for('index'))
        
        # Get all submitted exams for this student
        student_exams = StudentExam.query.filter_by(
            student_id=student.id,
            status='submitted'
        ).order_by(StudentExam.submitted_at.desc()).all()

        total_exams = len(student_exams)
        valid_scores = [se.percentage for se in student_exams if se.percentage is not None]
        avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0
        passed_exams = [se for se in student_exams if se.passed]
        pass_rate = (len(passed_exams) / total_exams * 100) if total_exams else 0
        best_score = max(valid_scores) if valid_scores else 0

        return render_template(
            'student/student_profile.html',
            student=student,
            student_exams=student_exams,
            total_exams=total_exams,
            avg_score=avg_score,
            pass_rate=pass_rate,
            best_score=best_score
        )


    @app.route('/faculty/students')
    @login_required
    def faculty_student_list():
        if current_user.role not in ('faculty', 'admin'):
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))

        q = request.args.get('q', '').strip()
        batch = request.args.get('batch')
        department = request.args.get('department')
        verified = request.args.get('verified')
        has_phone = request.args.get('has_phone')
        sort = request.args.get('sort')

        query = User.query.filter(User.role == 'student')

        # Faculty isolation: show students whose course matches faculty's department
        if current_user.role == 'faculty':
            faculty_course = _get_faculty_course()
            if faculty_course:
                query = query.filter(User.course == faculty_course)
            else:
                query = query.filter(db.false())  # Faculty has no course assigned

        if q:
            like = f"%{q}%"
            query = query.filter(
                db.or_(
                    User.full_name.ilike(like),
                    User.username.ilike(like),
                    User.email.ilike(like),
                    User.prn_number.ilike(like),
                    User.roll_id.ilike(like),
                    User.batch.ilike(like)
                )
            )

        if batch:
            query = query.filter(User.batch == batch)
        if department:
            query = query.filter(User.department == department)
        if verified == 'true':
            query = query.filter(User.is_verified == True)
        if verified == 'false':
            query = query.filter(User.is_verified == False)
        if has_phone == '1':
            query = query.filter(User.phone != None).filter(User.phone != '')

        if sort == 'name':
            query = query.order_by(User.full_name.asc())
        elif sort == 'batch':
            query = query.order_by(User.batch.asc())
        else:
            query = query.order_by(User.created_at.desc())

        students = query.all()

        batches = [b[0] for b in db.session.query(User.batch).distinct().all() if b[0]]
        departments = [d[0] for d in db.session.query(User.department).distinct().all() if d[0]]

        return render_template(
            'faculty/manage_students.html',
            students=students,
            batches=batches,
            departments=departments
        )

    @app.route('/faculty/import_students', methods=['POST'])
    @login_required
    def faculty_import_students():
        """
        Faculty imports students from CSV, Excel, or JSON files with proper number formatting
        Optionally assigns them to an exam and generates shuffled question/option orders
        """
        if current_user.role != 'faculty':
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))

        file = request.files.get('file')
        if not file or file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('faculty_student_list'))

        filename = secure_filename(file.filename)
        ext = filename.rsplit('.', 1)[-1].lower()

        # ✅ Allow JSON too
        if ext not in ('csv', 'xlsx', 'xls', 'json'):
            flash('Invalid file format. Please upload CSV, Excel, or JSON file.', 'error')
            return redirect(url_for('faculty_student_list'))

        print(f"[DEBUG] Received file: {file.filename}")

        try:
            # ✅ Step 1: Read file
            if ext == 'csv':
                try:
                    df = pd.read_csv(file, encoding='utf-8', dtype=str)
                except UnicodeDecodeError:
                    file.stream.seek(0)
                    df = pd.read_csv(file, encoding='utf-8-sig', dtype=str)
            elif ext in ('xlsx', 'xls'):
                df = pd.read_excel(file, dtype=str)
            elif ext == 'json':
                try:
                    data = json.load(file)
                except Exception as e:
                    flash(f'Invalid JSON file: {str(e)}', 'error')
                    return redirect(url_for('faculty_student_list'))

                if not isinstance(data, list):
                    flash('JSON must be an array of student objects.', 'error')
                    return redirect(url_for('faculty_student_list'))

                df = pd.DataFrame(data, dtype=str)

            print(f"[DEBUG] Columns: {df.columns.tolist()}")
            print(f"[DEBUG] Total rows: {len(df)}")

            if df.empty:
                flash('The file is empty or has no data rows.', 'error')
                return redirect(url_for('faculty_student_list'))

            # Normalize column names
            df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')

            added, skipped = 0, 0
            errors = []

            def clean_string(value):
                if pd.isna(value) or value == '':
                    return ''
                return str(value).strip()

            def validate_prn(prn):
                if not prn:
                    return True
                prn_clean = prn.replace('.', '').replace(' ', '')
                return prn_clean.isdigit() and len(prn_clean) == 12

            # ✅ Optional: attach students directly to a specific exam
            exam_id = request.args.get('exam_id', type=int)
            exam = Exam.query.get(exam_id) if exam_id else None

            # ✅ Process each row
            for index, row in df.iterrows():
                try:
                    email = clean_string(row.get('email')).lower()
                    if not email:
                        errors.append(f"Row {index + 2}: Missing required email")
                        skipped += 1
                        continue

                    existing = User.query.filter_by(email=email).first()
                    if existing:
                        print(f"[DEBUG] Skipped duplicate email: {email}")
                        skipped += 1
                        continue

                    prn_raw = clean_string(row.get('prn_number'))
                    prn_clean = prn_raw.replace('.', '').replace(' ', '') if prn_raw else ''

                    if prn_clean and not validate_prn(prn_raw):
                        errors.append(f"Row {index + 2}: PRN must be exactly 12 digits (got '{prn_raw}')")
                        skipped += 1
                        continue

                    if prn_clean:
                        existing_prn = User.query.filter_by(prn_number=prn_clean).first()
                        if existing_prn:
                            errors.append(f"Row {index + 2}: PRN {prn_clean} already exists")
                            skipped += 1
                            continue

                    password = clean_string(row.get('password')) or 'Student@123'
                    hashed_pw = generate_password_hash(password)

                    student_data = {
                        "username": clean_string(row.get('username')) or email.split('@')[0],
                        "email": email,
                        "full_name": clean_string(row.get('full_name')),
                        "prn_number": prn_clean,
                        "roll_id": clean_string(row.get('roll_id')),
                        "batch": clean_string(row.get('batch')),
                        "department": clean_string(row.get('department')),
                        "course": clean_string(row.get('course')) or (current_user.department or ''),
                        "phone": clean_string(row.get('phone')),
                        "gender": clean_string(row.get('gender')).capitalize(),
                        "password_hash": hashed_pw
                    }

                    is_verified = False
                    if 'is_verified' in row and str(row['is_verified']).lower() in ('true', '1', 'yes'):
                        is_verified = True

                    if not student_data["username"] or not student_data["email"]:
                        errors.append(f"Row {index + 2}: Missing username/email")
                        skipped += 1
                        continue

                    # ✅ Create the student
                    student = User()
                    student.set_as_student(student_data, verified=is_verified)
                    db.session.add(student)
                    db.session.commit()

                    # ✅ If exam provided, assign and shuffle
                    if exam:
                        student_exam = StudentExam(student_id=student.id, exam_id=exam.id)
                        db.session.add(student_exam)
                        db.session.commit()

                        # 🔀 Assign shuffled question & option order
                        assign_shuffle(student_exam)

                    added += 1

                    if added % 50 == 0:
                        db.session.commit()
                        print(f"[DEBUG] Interim commit after {added} records")

                except Exception as e:
                    traceback.print_exc()
                    error_msg = f"Row {index + 2}: {str(e)}"
                    print(f"[DEBUG] Error -> {error_msg}")
                    errors.append(error_msg)
                    skipped += 1
                    db.session.rollback()
                    continue

            db.session.commit()

            summary = f"✅ {added} added | ⚠️ {skipped} skipped"
            flash(summary, 'success')

            if errors:
                print("[DEBUG] --- ERRORS SUMMARY ---")
                for err in errors:
                    print(err)
                flash(f"{len(errors)} issue(s) encountered.", 'warning')

            print(f"[DEBUG] Import complete: {added} added, {skipped} skipped.")
            return redirect(url_for('faculty_student_list'))

        except Exception as e:
            db.session.rollback()
            traceback.print_exc()
            flash(f"Error processing file: {str(e)}", 'error')
            return redirect(url_for('faculty_student_list'))



    @app.route('/faculty/export_students')
    @login_required
    def faculty_export_students():
        """Export students to CSV"""
        if current_user.role not in ('faculty', 'admin'):
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))

        q = request.args.get('q', '').strip()
        batch = request.args.get('batch')
        department = request.args.get('department')
        verified = request.args.get('verified')
        has_phone = request.args.get('has_phone')
        sort_by = request.args.get('sort')
        selected_ids = request.args.getlist('ids')

        query = User.query.filter(User.role == 'student')

        # Faculty isolation: only export students connected to this faculty's exams
        if current_user.role == 'faculty':
            my_student_ids = _get_faculty_student_ids(current_user.id)
            if my_student_ids:
                query = query.filter(User.id.in_(my_student_ids))
            else:
                query = query.filter(db.false())

        if selected_ids:
            try:
                id_list = [int(id_str) for id_str in selected_ids if id_str.isdigit()]
                query = query.filter(User.id.in_(id_list))
            except ValueError:
                flash('Invalid student IDs provided', 'error')
                return redirect(url_for('faculty_student_list'))
        else:
            if q:
                like = f"%{q}%"
                query = query.filter(
                    db.or_(
                        User.full_name.ilike(like),
                        User.username.ilike(like),
                        User.email.ilike(like),
                        User.prn_number.ilike(like),
                        User.roll_id.ilike(like),
                        User.batch.ilike(like)
                    )
                )
            
            if batch:
                query = query.filter(User.batch == batch)
            if department:
                query = query.filter(User.department == department)
            if verified == 'true':
                query = query.filter(User.is_verified == True)
            elif verified == 'false':
                query = query.filter(User.is_verified == False)
            if has_phone == '1':
                query = query.filter(User.phone.isnot(None), User.phone != '')

        if sort_by == 'name':
            query = query.order_by(User.full_name.asc())
        elif sort_by == 'batch':
            query = query.order_by(User.batch.asc(), User.roll_id.asc())
        else:
            query = query.order_by(User.created_at.desc())

        students = query.all()

        if not students:
            flash('No students found to export', 'warning')
            return redirect(url_for('faculty_student_list'))

        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow([
            'ID', 'Username', 'Email', 'Full Name', 'PRN Number', 'Roll ID',
            'Batch', 'Department', 'Phone', 'Gender', 'Verified', 'Created Date',
            'Total Exams', 'Average Score'
        ])
        
        for student in students:
            student_exams = StudentExam.query.filter_by(
                student_id=student.id,
                status='submitted'
            ).all()
            
            total_exams = len(student_exams)
            avg_score = (sum(se.percentage for se in student_exams) / total_exams) if total_exams > 0 else 0
            
            writer.writerow([
                student.id,
                student.username,
                student.email,
                student.full_name or '',
                student.prn_number or '',
                student.roll_id or '',
                student.batch or '',
                student.department or '',
                student.phone or '',
                student.gender or '',
                'Yes' if student.is_verified else 'No',
                student.created_at.strftime('%Y-%m-%d %H:%M:%S') if student.created_at else '',
                total_exams,
                f"{avg_score:.2f}%" if total_exams > 0 else 'N/A'
            ])
        
        output.seek(0)
        
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        
        if selected_ids:
            filename = f"students_selected_{len(students)}_{timestamp}.csv"
        elif q or batch or department or verified:
            filename = f"students_filtered_{len(students)}_{timestamp}.csv"
        else:
            filename = f"students_all_{len(students)}_{timestamp}.csv"
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )

    @app.route('/faculty/download_template')
    @login_required
    def download_student_template():
        """Download CSV template"""
        if current_user.role != 'faculty':
            flash('Access denied', 'error')
            return redirect(url_for('student_dashboard'))
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow([
            'username', 'email', 'full_name', 'prn_number', 'roll_id',
            'batch', 'department', 'phone', 'gender', 'password', 'is_verified'
        ])
        
        writer.writerow([
            'johndoe', 'john.doe@university.edu', 'John Doe', '2508403250',
            'CS-2024-001', '2024', 'Computer Science', '+1234567890',
            'Male', 'Student@123', 'true'
        ])
        
        writer.writerow([
            'janesmith', 'jane.smith@university.edu', 'Jane Smith', '2508403251',
            'CS-2024-002', '2024', 'Computer Science', '+1234567891',
            'Female', 'Student@123', 'false'
        ])
        
        output.seek(0)
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                "Content-Disposition": "attachment; filename=student_import_template.csv",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )

    @app.route('/faculty/delete_student/<int:student_id>', methods=['POST'])
    @login_required
    def faculty_delete_student(student_id):
        """Delete single student — only if connected to this faculty's exams"""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Faculty isolation check
        if current_user.role == 'faculty' and not _faculty_can_access_student(student_id):
            return jsonify({'success': False, 'error': 'Access denied: student not in your exams'}), 403
        
        student = User.query.get(student_id)
        if not student:
            return jsonify({'success': False, 'error': 'Student not found'}), 404
        
        if student.role != 'student':
            return jsonify({'success': False, 'error': 'Can only delete students'}), 400
        
        try:
            # Delete related records first
            StudentExam.query.filter_by(student_id=student_id).delete()
            db.session.delete(student)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Student deleted successfully'})
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Delete failed: {str(e)}")
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/faculty/delete_students', methods=['POST'])
    @login_required
    def faculty_delete_students():
        """Delete multiple students — faculty isolated"""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        data = request.get_json() or {}
        ids = data.get('ids') or []
        
        if not ids:
            return jsonify({'success': False, 'error': 'No IDs provided'}), 400
        
        # Faculty isolation: filter to only students belonging to this faculty
        if current_user.role == 'faculty':
            my_student_ids = _get_faculty_student_ids(current_user.id)
            ids = [sid for sid in ids if sid in my_student_ids]
            if not ids:
                return jsonify({'success': False, 'error': 'No accessible students in provided IDs'}), 403
        
        try:
            # Delete related records first
            StudentExam.query.filter(StudentExam.student_id.in_(ids)).delete(synchronize_session=False)
            
            # Delete students
            deleted_count = User.query.filter(
                User.id.in_(ids),
                User.role == 'student'
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f'Successfully deleted {deleted_count} student(s)',
                'deleted_count': deleted_count
            })
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Bulk delete failed: {str(e)}")
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/student/exam/<int:student_exam_id>/download-pdf')
    @login_required
    def download_result_pdf(student_exam_id):
        """Download PDF - accessible by student and owning faculty/admin"""
        student_exam = StudentExam.query.get_or_404(student_exam_id)

        # Allow access for the student who took the exam or faculty who owns the exam
        if current_user.role == 'student':
            if student_exam.student_id != current_user.id:
                flash('Access denied', 'error')
                return redirect(url_for('student_dashboard'))
        elif current_user.role == 'faculty':
            # Faculty can only download PDFs for exams they created
            if not _faculty_owns_exam(student_exam.exam):
                flash('Access denied: not your exam', 'error')
                return redirect(url_for('faculty_dashboard'))
        elif current_user.role != 'admin':
            flash('Access denied', 'error')
            return redirect(url_for('index'))

        if student_exam.status != 'submitted':
            flash('Exam not yet submitted', 'error')
            if current_user.role == 'student':
                return redirect(url_for('take_exam', student_exam_id=student_exam_id))
            else:
                return redirect(url_for('faculty_dashboard'))

        # Enforce faculty's post-exam redirect preference
        exam = student_exam.exam
        if exam:
            post_redirect = getattr(exam, 'post_exam_redirect', 'results') or 'results'
            if post_redirect == 'dashboard' and current_user.role == 'student':
                flash('Result viewing has been restricted by your faculty for this exam.', 'info')
                return redirect(url_for('student_dashboard'))

        answers_with_questions = []
        for answer in student_exam.answers:
            question = Question.query.get(answer.question_id)
            answers_with_questions.append({
                'question': question,
                'answer': answer
            })
        
        # Get the actual student for the PDF (in case faculty is downloading)
        student = User.query.get(student_exam.student_id)
        
        pdf_buffer = generate_result_pdf(
            student_exam,
            student_exam.exam,
            student,  # Use the student who took the exam, not current_user
            answers_with_questions
        )
        
        filename = f"Result_{student.username}_{student_exam.exam.title.replace(' ', '_')}.pdf"
        
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )

    @app.route('/faculty/student/<int:student_id>/edit', methods=['GET', 'POST'])
    @login_required
    def faculty_edit_student(student_id):
        """Edit student details — faculty isolated"""
        if current_user.role not in ('faculty', 'admin'):
            flash('Access denied. Faculty only.', 'error')
            return redirect(url_for('faculty_dashboard'))

        # Faculty isolation check
        if current_user.role == 'faculty' and not _faculty_can_access_student(student_id):
            flash('Access denied: this student is not in your exams.', 'error')
            return redirect(url_for('faculty_student_list'))

        student = User.query.get_or_404(student_id)

        if request.method == 'POST':
            student.full_name = request.form.get('full_name')
            student.email = request.form.get('email')
            student.phone = request.form.get('phone')
            student.gender = request.form.get('gender')
            student.prn_number = request.form.get('prn_number')
            student.roll_id = request.form.get('roll_id')
            student.batch = request.form.get('batch')
            student.department = request.form.get('department')
            student.is_verified = request.form.get('is_verified') == 'true'

            db.session.commit()
            flash('Student information updated successfully!', 'success')
            return redirect(url_for('faculty_student_list'))

        return render_template('faculty/edit_student.html', student=student)

    @app.route('/global_leaderboard', methods=['GET'])
    @login_required
    def global_leaderboard():
        # ── Students: enforce per-exam leaderboard permission ────────────
        # If a specific exam_id is passed, check that exam's flag first.
        # If no exam_id, check whether ANY exam allows leaderboard access.
        # Students are blocked entirely when no accessible exam allows it.
        if current_user.role == 'student':
            exam_id_param = request.args.get('exam_id')
            if exam_id_param:
                # Per-exam check
                specific_exam = Exam.query.get(int(exam_id_param))
                if not specific_exam or not specific_exam.show_leaderboard:
                    flash('The faculty has disabled leaderboard access for this exam.', 'warning')
                    return redirect(url_for('student_dashboard'))
            else:
                # Global check: at least one exam must have leaderboard enabled
                allowed_count = Exam.query.filter(Exam.show_leaderboard == True).count()
                if allowed_count == 0:
                    flash('Leaderboard access has been disabled by your faculty.', 'warning')
                    return redirect(url_for('student_dashboard'))

        # Date filters (strings from query)
        start_date = request.args.get('start')
        end_date = request.args.get('end')

        # Parse to datetime objects (or None)
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") if end_date else None
        except Exception:
            start_dt = end_dt = None

        # Batch filter (passed from front-end)
        batch = request.args.get('batch')

        # Fetch exams within date range — FACULTY ISOLATED
        exam_query = Exam.query
        if current_user.role == 'faculty':
            exam_query = exam_query.filter(Exam.creator_id == current_user.id)
        elif current_user.role == 'student':
            # Students can ONLY see leaderboard for exams where faculty enabled it
            exam_query = exam_query.filter(Exam.show_leaderboard == True)
        if start_dt:
            exam_query = exam_query.filter(Exam.created_at >= start_dt)
        if end_dt:
            exam_query = exam_query.filter(Exam.created_at <= end_dt)

        exams = exam_query.all()
        exam_ids = [e.id for e in exams]

        # Calculate total possible marks
        exam_total_map = {}
        total_possible_marks = 0
        for ex in exams:
            tm = getattr(ex, "total_marks", None)
            if not tm:
                # sum question points fallback
                tm = db.session.query(func.coalesce(func.sum(Question.points), 0)).filter(Question.exam_id == ex.id).scalar() or 0
            exam_total_map[ex.id] = tm
            total_possible_marks += tm

        # Fetch students; apply batch filter if provided
        students_q = User.query.filter(User.role.in_(['student', 'faculty'])).order_by(User.full_name.asc())
        if batch:
            # assume User has attribute 'batch'
            students_q = students_q.filter(getattr(User, 'batch') == batch)
        students = students_q.all()

        leaderboard_data = []

        for student in students:
            # Student exams in selected range (only exams we fetched)
            student_exams = StudentExam.query.filter(
                StudentExam.student_id == student.id,
                StudentExam.exam_id.in_(exam_ids)
            ).all()

            exams_attempted = len(student_exams)

            # Total questions solved: count StudentAnswer rows joined to StudentExam and restricted by submitted_at if dates provided
            sa_query = db.session.query(StudentAnswer).join(StudentExam, StudentAnswer.student_exam_id == StudentExam.id).filter(
                StudentExam.student_id == student.id,
                StudentExam.exam_id.in_(exam_ids)
            )

            if start_dt and end_dt:
                sa_query = sa_query.filter(StudentExam.submitted_at.between(start_dt, end_dt))
            elif start_dt:
                sa_query = sa_query.filter(StudentExam.submitted_at >= start_dt)
            elif end_dt:
                sa_query = sa_query.filter(StudentExam.submitted_at <= end_dt)

            total_questions_attempted = sa_query.count()

            # Total marks obtained (use StudentExam.score if present else sum StudentAnswer.points_earned)
            total_marks_obtained = 0
            for se in student_exams:
                if se.score is not None:
                    try:
                        total_marks_obtained += float(se.score)
                    except Exception:
                        # ignore bad types, fallback to answers
                        pts = db.session.query(func.coalesce(func.sum(StudentAnswer.points_earned), 0)).filter(
                            StudentAnswer.student_exam_id == se.id
                        ).scalar() or 0
                        total_marks_obtained += pts
                else:
                    pts = db.session.query(func.coalesce(func.sum(StudentAnswer.points_earned), 0)).filter(
                        StudentAnswer.student_exam_id == se.id
                    ).scalar() or 0
                    total_marks_obtained += pts

            percentage = (total_marks_obtained / total_possible_marks * 100) if total_possible_marks else 0

            leaderboard_data.append({
                "name": student.full_name,
                "prn": student.prn_number,
                "exams_attempted": exams_attempted,
                "total_questions": total_questions_attempted,
                "marks_obtained": round(total_marks_obtained, 2),
                "possible_marks": total_possible_marks,
                "percentage": round(percentage, 2)
            })

        def apply_column_filters(data):
            # read relevant query params
            filters = {
                'prn_txt': request.args.get('col_1_txt'),
                'name_txt': request.args.get('col_2_txt'),
                'attempts_min': request.args.get('col_3_min'),
                'answered_min': request.args.get('col_4_min'),
                'marks_min': request.args.get('col_5_min'),
                'pct_min': request.args.get('col_7_min'),
            }

            def keep(item):
                # text filters (case-insensitive)
                if filters['prn_txt']:
                    if filters['prn_txt'].strip().lower() not in str(item.get('prn', '')).lower():
                        return False
                if filters['name_txt']:
                    if filters['name_txt'].strip().lower() not in str(item.get('name', '')).lower():
                        return False

                # numeric min filters
                try:
                    if filters['attempts_min']:
                        if int(item.get('exams_attempted', 0)) < int(filters['attempts_min']):
                            return False
                except ValueError:
                    pass
                try:
                    if filters['answered_min']:
                        if int(item.get('total_questions', 0)) < int(filters['answered_min']):
                            return False
                except ValueError:
                    pass
                try:
                    if filters['marks_min']:
                        if float(item.get('marks_obtained', 0)) < float(filters['marks_min']):
                            return False
                except ValueError:
                    pass
                try:
                    if filters['pct_min']:
                        if float(item.get('percentage', 0)) < float(filters['pct_min']):
                            return False
                except ValueError:
                    pass

                return True

            return [it for it in data if keep(it)]

        leaderboard_data = apply_column_filters(leaderboard_data)

        # Sort leaderboard (best first)
        leaderboard_data.sort(
            key=lambda x: (x["marks_obtained"], x["exams_attempted"], x["total_questions"]),
            reverse=True
        )

        # Compute "how behind"
        if leaderboard_data:
            top_marks = leaderboard_data[0]["marks_obtained"]
            top_attempt = leaderboard_data[0]["exams_attempted"]

            for item in leaderboard_data:
                item["behind_marks"] = round(top_marks - item["marks_obtained"], 2)
                item["behind_attempts"] = top_attempt - item["exams_attempted"]

        # Prepare batches list for template (unique sorted values from users)
        # If User doesn't have batch attribute, this will safely produce an empty list
        try:
            batches = sorted({ getattr(u, 'batch') for u in User.query.filter_by(role='student').all() if getattr(u, 'batch', None) })
        except Exception:
            batches = []

        return render_template(
            "leaderboard.html",
            leaderboard=leaderboard_data,
            start_date=start_date,
            end_date=end_date,
            batches=batches
        )
    @app.route('/api/check-exam-status/<int:student_exam_id>', methods=['GET'])
    @login_required
    def check_exam_status(student_exam_id):
        """
        Check if exam has been force-ended by faculty or time extended
        Students poll this endpoint every 5 seconds during exam
        """
        try:
            student_exam = StudentExam.query.get_or_404(student_exam_id)
            
            # Verify student owns this exam
            if student_exam.student_id != current_user.id:
                return jsonify({"error": "Unauthorized"}), 403
            
            exam = Exam.query.get(student_exam.exam_id)
            
            if not exam:
                return jsonify({"error": "Exam not found"}), 404
            
            response = {
                "force_ended": getattr(exam, 'force_ended', False),
                "exam_status": getattr(exam, 'status', 'active'),
                "updated_end_time": None,
                "current_time": datetime.utcnow().isoformat()
            }
            
            # Calculate expected end time based on start + duration
            if student_exam.started_at and exam.duration_minutes:
                from datetime import timedelta
                expected_end = student_exam.started_at + timedelta(minutes=exam.duration_minutes)
                response["updated_end_time"] = expected_end.isoformat()
                
                # Calculate remaining time in seconds
                now = datetime.utcnow()
                remaining = (expected_end - now).total_seconds()
                response["remaining_seconds"] = max(0, int(remaining))
            
            return jsonify(response)
            
        except Exception as e:
            print(f"❌ Error checking exam status: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    @socketio.on('join_exam')
    def handle_join_exam(data):
        try:
            exam_id = data.get('exam_id')
            student_exam_id = data.get('student_exam_id')

            if exam_id:
                room_name = f"exam_{exam_id}"
                join_room(room_name)
                print(f"Socket {request.sid} joined room {room_name}")

            if student_exam_id:
                student_room = f"student_exam_{student_exam_id}"
                join_room(student_room)
                print(f"Socket {request.sid} joined room {student_room}")

        except Exception as e:
            print("Error in join_exam:", e)


    @app.route('/faculty/exam/<int:exam_id>/force_end', methods=['POST'])
    @login_required
    def faculty_force_end_exam(exam_id):
        # ---------- ACCESS CONTROL ----------
        if current_user.role != 'faculty':
            flash("Access denied", "error")
            return redirect(url_for('student_dashboard'))

        exam = Exam.query.get_or_404(exam_id)

        if exam.creator_id != current_user.id:
            flash("Access denied", "error")
            return redirect(url_for('faculty_dashboard'))

        # ---------- FORCE-END FLAGS ----------
        exam.force_ended = True
        exam.is_active = False
        exam.status = "inactive"    # required for front-end exam block

        # ---------- CLOSE ALL ACTIVE ATTEMPTS ----------
        active_attempts = StudentExam.query.filter_by(
            exam_id=exam_id,
            status='in_progress'
        ).all()

        for attempt in active_attempts:
            attempt.status = "submitted"
            attempt.submitted_at = datetime.utcnow()

            # log every forced submission
            log = ActivityLog(
                student_exam_id=attempt.id,
                activity_type="exam_force_ended",
                description="Exam was force-ended by faculty",
                severity="high",
                created_at=datetime.utcnow()
            )
            db.session.add(log)

        # ---------- COMMIT CHANGES ----------
        db.session.commit()

        # ---------- CALCULATE SCORES FOR ALL FORCE-ENDED ATTEMPTS ----------
        for attempt in active_attempts:
            try:
                calculate_student_score(attempt.id)
            except Exception as _e:
                print(f"⚠️ Score calc failed for attempt {attempt.id}: {_e}")

        # ---------- REAL-TIME BROADCAST TO ALL STUDENTS ----------
        try:
            socketio.emit(
                'exam_force_ended',
                {
                    'exam_id': exam_id,
                    'force_ended': True,
                    'message': 'Faculty has force-ended the exam. Your exam will be submitted.'
                },
                room=f"exam_{exam_id}",
                namespace='/'
            )
            print(f"🔔 Emitted exam_force_ended for exam_{exam_id}")
        except Exception as e:
            print("⚠️ Socket emit error:", e)

        # ---------- RESPONSE ----------
        flash("⚠ Exam has been force-ended for all students.", "warning")
        return redirect(url_for('view_exam', exam_id=exam_id))


    @app.route('/faculty/exam/<int:exam_id>/force_end_student/<int:student_exam_id>', methods=['POST'])
    @login_required
    def faculty_force_end_student(exam_id, student_exam_id):
        if current_user.role != 'faculty':
            flash("Access denied", "error")
            return redirect(url_for('student_dashboard'))

        exam = Exam.query.get_or_404(exam_id)
        if exam.creator_id != current_user.id:
            flash("Access denied", "error")
            return redirect(url_for('faculty_dashboard'))

        attempt = StudentExam.query.get_or_404(student_exam_id)

        attempt.status = "submitted"
        attempt.submitted_at = datetime.utcnow()

        log = ActivityLog(
            student_exam_id=attempt.id,
            activity_type="student_force_ended",
            description="Faculty force-ended this student's exam.",
            severity="high",
            created_at=datetime.utcnow()
        )
        db.session.add(log)
        db.session.commit()

        # Calculate score immediately so result page has valid data
        try:
            calculate_student_score(attempt.id)
        except Exception as _e:
            print(f"⚠️ Score calc after force-end failed: {_e}")

        # REAL-TIME SOCKET ALERT — ONLY THIS STUDENT
        socketio.emit(
            "student_force_ended",
            {
                "student_exam_id": student_exam_id,
                "message": "Faculty has force-ended your exam."
            },
            room=f"student_exam_{student_exam_id}"
        )

        flash("Student exam force-ended.", "warning")
        return redirect(url_for('view_exam', exam_id=exam_id))



    @app.route('/faculty/extend-exam-time/<int:exam_id>', methods=['POST'])
    @login_required
    def faculty_extend_exam_time(exam_id):
        if current_user.role not in ['faculty', 'admin']:
            return jsonify({"error": "Unauthorized"}), 403
        
        try:
            exam = Exam.query.get_or_404(exam_id)
            
            # Faculty isolation: must own this exam
            if not _faculty_owns_exam(exam):
                flash('Access denied: not your exam', 'error')
                return redirect(url_for('faculty_dashboard'))
            
            extra_minutes = int(request.form.get('extra_minutes', 0))
            
            if extra_minutes <= 0:
                flash('❌ Invalid extension time. Must be at least 1 minute.', 'error')
                return redirect(url_for('faculty_dashboard'))  # ← CHANGED
            
            if extra_minutes > 120:
                flash('❌ Cannot extend by more than 120 minutes at once.', 'error')
                return redirect(url_for('faculty_dashboard'))  # ← CHANGED
            
            if getattr(exam, 'force_ended', False) or getattr(exam, 'status', 'active') == 'ended':
                flash('⚠️ Cannot extend time for an ended exam.', 'warning')
                return redirect(url_for('faculty_dashboard'))  # ← CHANGED
            
            old_duration = exam.duration_minutes or 60
            exam.duration_minutes = old_duration + extra_minutes
            db.session.commit()
            
            print(f"⏰ Exam '{exam.title}' extended from {old_duration} to {exam.duration_minutes} minutes")
            
            flash(
                f'✅ Exam time extended by {extra_minutes} minutes. '
                f'New duration: {exam.duration_minutes} minutes.',
                'success'
            )
            return redirect(url_for('faculty_dashboard'))  # ← CHANGED
            
        except ValueError:
            flash('❌ Invalid input. Please enter a valid number.', 'error')
            return redirect(url_for('faculty_dashboard'))
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error extending time: {e}")
            import traceback
            traceback.print_exc()
            flash(f'❌ Error extending time: {str(e)}', 'error')
            return redirect(url_for('faculty_dashboard'))    
        # ==========================================
    # OPTIONAL: Reactivate Exam Route
    # ==========================================

    @app.route('/faculty/reactivate-exam/<int:exam_id>', methods=['POST'])
    @login_required
    def faculty_reactivate_exam(exam_id):
        if current_user.role not in ['faculty', 'admin']:
            flash('❌ Unauthorized access.', 'error')
            return redirect(url_for('index'))
        
        try:
            exam = Exam.query.get_or_404(exam_id)
            
            # Faculty isolation: must own this exam
            if not _faculty_owns_exam(exam):
                flash('Access denied: not your exam', 'error')
                return redirect(url_for('faculty_dashboard'))
            
            exam.force_ended = False
            exam.status = 'active'
            exam.is_active = True
            db.session.commit()

            # Notify any connected clients that exam is active again
            socketio.emit('exam_reactivated', {
                'exam_id': exam_id,
                'message': 'Exam reactivated by faculty'
            }, room=f"exam_{exam_id}")

            flash(f'✅ Exam "{exam.title}" has been reactivated.', 'success')
            return redirect(url_for('faculty_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Error reactivating exam: {str(e)}', 'error')
            return redirect(url_for('faculty_dashboard'))

    @socketio.on('join_exam')
    def join_exam_room(data):
        exam_id = data.get('exam_id')
        student_exam_id = data.get('student_exam_id')

        if exam_id:
            join_room(f"exam_{exam_id}")
            print(f"Student joined room exam_{exam_id}")

        if student_exam_id:
            join_room(f"student_exam_{student_exam_id}")
            print(f"Student joined room student_exam_{student_exam_id}")


    @app.route('/faculty/restart-student/<int:student_exam_id>', methods=['POST'])
    @login_required
    def faculty_restart_student(student_exam_id):
        if current_user.role not in ['faculty', 'admin']:
            return jsonify({'error':'unauthorized'}), 403
        try:
            se = StudentExam.query.get_or_404(student_exam_id)
            
            # Faculty isolation: must own the exam
            if not _faculty_owns_exam(se.exam):
                return jsonify({'error': 'Access denied: not your exam'}), 403
            
            # create a fresh StudentExam rather than editing old one (safer)
            new_attempt = StudentExam(
                student_id = se.student_id,
                exam_id = se.exam_id,
                started_at = None,
                status = 'not_started',  # or allow logic you prefer
                tab_switch_count = 0
            )
            db.session.add(new_attempt)
            db.session.commit()

            # tell that specific student (if connected)
            socketio.emit('student_exam_restarted', {
                'old_student_exam_id': student_exam_id,
                'new_student_exam_id': new_attempt.id,
                'message': 'Your exam has been restarted by faculty'
            }, room=f"student_exam_{student_exam_id}")

            return jsonify({'success': True, 'new_student_exam_id': new_attempt.id})
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500


    # ==========================================
    # HELPER: Get Active Student Count
    # ==========================================

    def get_active_student_count(exam_id):
        """
        Get count of students currently taking the exam
        
        Args:
            exam_id: ID of the exam
            
        Returns:
            int: Number of active students
        """
        try:
            count = StudentExam.query.filter_by(
                exam_id=exam_id,
                submitted_at=None
            ).count()
            return count
        except Exception as e:
            print(f"Error getting active student count: {e}")
            return 0


    # ==========================================
    # API: Get Live Exam Statistics
    # ==========================================

    @app.route('/api/exam-stats/<int:exam_id>', methods=['GET'])
    @login_required
    def get_exam_stats(exam_id):
        """
        Get live statistics for an exam — faculty isolated
        """
        if current_user.role not in ['faculty', 'admin']:
            return jsonify({"error": "Unauthorized"}), 403
        
        try:
            exam = Exam.query.get_or_404(exam_id)
            
            # Faculty isolation: must own this exam
            if not _faculty_owns_exam(exam):
                return jsonify({"error": "Access denied: not your exam"}), 403
            
            # Get counts
            total_attempts = StudentExam.query.filter_by(exam_id=exam_id).count()
            active_attempts = StudentExam.query.filter_by(exam_id=exam_id, submitted_at=None).count()
            completed_attempts = StudentExam.query.filter_by(exam_id=exam_id).filter(
                StudentExam.submitted_at.isnot(None)
            ).count()
            
            # Get average score for completed attempts
            completed_exams = StudentExam.query.filter_by(exam_id=exam_id).filter(
                StudentExam.submitted_at.isnot(None)
            ).all()
            
            avg_score = 0
            if completed_exams:
                scores = [se.percentage for se in completed_exams if se.percentage is not None]
                if scores:
                    avg_score = round(sum(scores) / len(scores), 2)
            
            return jsonify({
                "exam_id": exam_id,
                "exam_title": exam.title,
                "status": getattr(exam, 'status', 'active'),
                "force_ended": getattr(exam, 'force_ended', False),
                "total_attempts": total_attempts,
                "active_attempts": active_attempts,
                "completed_attempts": completed_attempts,
                "average_score": avg_score,
                "timestamp": datetime.utcnow().isoformat()
            })
            
        except Exception as e:
            print(f"Error getting exam stats: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/faculty/force-end/<int:student_exam_id>", methods=["POST"])
    @login_required
    def force_end_exam(student_exam_id):
        """Force end a student's exam attempt — faculty isolated"""
        try:
            # Verify faculty access
            if current_user.role not in ['faculty', 'admin']:
                return jsonify({"error": "Unauthorized - Faculty only"}), 403
            
            # Get student exam
            student_exam = StudentExam.query.get(student_exam_id)
            
            if not student_exam:
                return jsonify({"error": "Student exam not found"}), 404
            
            # Faculty isolation: must own the exam
            if not _faculty_owns_exam(student_exam.exam):
                return jsonify({"error": "Access denied: not your exam"}), 403
            
            # Check if already submitted
            if student_exam.submitted_at:
                return jsonify({
                    "success": False,
                    "message": "Exam already submitted"
                }), 400
            
            # Force end the exam
            from datetime import datetime
            
            student_exam.force_ended = True
            student_exam.status = 'force_ended'
            student_exam.submitted_at = datetime.utcnow()
            
            # Also mark the exam as force-ended (affects all students)
            exam = student_exam.exam
            if exam:
                exam.force_ended = True
                if hasattr(exam, 'force_ended_at'):
                    exam.force_ended_at = datetime.utcnow()
            
            db.session.commit()
            
            print(f"✅ Faculty {current_user.username} force-ended exam for student_exam_id: {student_exam_id}")
            
            return jsonify({
                "success": True,
                "force_ended": True,
                "message": "Exam force-ended successfully"
            })
            
        except Exception as e:
            print(f"❌ Error force-ending exam: {e}")
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

# ==========================================
# COPY-PASTE THIS SECTION INTO YOUR ROUTES.PY
# Add this RIGHT AFTER the force_end_exam route (after line 3008)
# BEFORE adding this, DELETE lines 3010-3473
# ==========================================

    # ==========================================
    # AI PROCTORING ROUTES
    # ==========================================
    
    @app.route('/api/proctor/calibrate/<int:student_exam_id>', methods=['POST'])
    @login_required
    def proctor_calibrate(student_exam_id):
        """Calibrate baseline face position"""
        print(f"🎯 Calibration request for student_exam_id: {student_exam_id}")
        
        try:
            student_exam = StudentExam.query.get_or_404(student_exam_id)
            if student_exam.student_id != current_user.id:
                print("❌ Unauthorized access")
                return jsonify({"status": "error", "message": "Unauthorized"}), 403

            exam = student_exam.exam
            enable_proctoring = getattr(exam, 'enable_proctoring', True)
            
            if not enable_proctoring:
                return jsonify({"status": "ok", "message": "Proctoring disabled", "proctoring_enabled": False})

            data = request.get_json()
            frames_b64 = data.get("frames", [])
            
            print(f"📸 Received {len(frames_b64)} frames")

            if len(frames_b64) < 5:
                return jsonify({"status": "error", "message": "At least 5 frames required"}), 400

            frames = []
            for i, frame_b64 in enumerate(frames_b64):
                img = decode_base64_image(frame_b64)
                if img is not None:
                    frames.append(img)

            if len(frames) < 5:
                return jsonify({"status": "error", "message": "Failed to decode frames"}), 400

            print(f"✅ Decoded {len(frames)} frames")

            proctor_state, vision = get_proctor_instance(student_exam_id, exam)
            
            print("🔍 Performing calibration...")
            ok, info = vision.calibrate(frames)
            
            if not ok:
                print(f"❌ Calibration failed: {info}")
                return jsonify({"status": "error", "message": info}), 400

            calibration = ExamCalibration.query.filter_by(student_exam_id=student_exam_id).first()
            if not calibration:
                calibration = ExamCalibration(student_exam_id=student_exam_id)
                db.session.add(calibration)

            calibration.baseline_yaw = proctor_state.baseline_yaw
            calibration.baseline_pitch = proctor_state.baseline_pitch
            calibration.baseline_roll = proctor_state.baseline_roll
            calibration.calibration_frames = len(frames)

            student_exam.calibration_completed = True
            student_exam.proctoring_enabled = True
            
            db.session.commit()
            print("💾 Saved to database")

            return jsonify({
                "status": "ok",
                "message": "Calibration successful",
                "baseline": {
                    "yaw": float(proctor_state.baseline_yaw),
                    "pitch": float(proctor_state.baseline_pitch),
                    "roll": float(proctor_state.baseline_roll)
                }
            })

        except Exception as e:
            print(f"❌ Calibration error: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"status": "error", "message": str(e)}), 500


    @app.route('/api/proctor/analyze/<int:student_exam_id>', methods=['POST'])
    @login_required
    def proctor_analyze_frame(student_exam_id):
        """Analyze frame during exam"""
        try:
            student_exam = StudentExam.query.get_or_404(student_exam_id)
            if student_exam.student_id != current_user.id:
                return jsonify({"status": "error", "message": "Unauthorized"}), 403

            if student_exam.submitted_at:
                return jsonify({"status": "TERMINATED", "message": "Exam submitted"})

            exam = student_exam.exam
            enable_proctoring = getattr(exam, 'enable_proctoring', True)
            proctoring_enabled = getattr(student_exam, 'proctoring_enabled', True)
            
            if not enable_proctoring or not proctoring_enabled:
                return jsonify({"status": "NORMAL", "message": "Proctoring disabled"})

            calibration_completed = getattr(student_exam, 'calibration_completed', False)
            if not calibration_completed:
                return jsonify({"status": "ERROR", "message": "Not calibrated"}), 400

            data = request.get_json()
            frame_b64 = data.get("frame")
            
            if not frame_b64:
                return jsonify({"status": "ERROR", "message": "No frame"}), 400

            frame = decode_base64_image(frame_b64)
            if frame is None:
                return jsonify({"status": "ERROR", "message": "Decode failed"}), 400

            proctor_state, vision = get_proctor_instance(student_exam_id, exam)
            status, details = vision.check_frame(frame)

            if status in ("WARNING", "TERMINATE", "NO_FACE"):
                violation = ExamViolation(
                    student_exam_id=student_exam_id,
                    violation_type=status,
                    severity="high" if status == "TERMINATE" else "medium",
                    message=details.get("message", ""),
                    yaw=details.get("yaw"),
                    pitch=details.get("pitch"),
                    roll=details.get("roll"),
                    deviation_yaw=details.get("dyaw"),
                    deviation_pitch=details.get("dpitch"),
                    deviation_roll=details.get("droll"),
                    faces_detected=details.get("faces_detected", 0)
                )
                db.session.add(violation)

                current_violations = getattr(student_exam, 'total_violations', 0) or 0
                student_exam.total_violations = current_violations + 1
                
                # Keep proctoring_status at 'warning' level for faculty review
                if student_exam.total_violations >= 10:
                    student_exam.proctoring_status = "warning"
                
                db.session.commit()

            return jsonify({
                "status": status,
                "warning_count": proctor_state.warning_count,
                "total_violations": student_exam.total_violations or 0,
                "message": details.get("message", ""),
                "should_terminate": False,   # Auto-submit is disabled; exam always continues
                "debug": {
                    "yaw": details.get("yaw"),
                    "pitch": details.get("pitch"),
                    "roll": details.get("roll")
                }
            })

        except Exception as e:
            print(f"❌ Analysis error: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"status": "ERROR", "message": str(e)}), 500


    @app.route('/api/proctor/status/<int:student_exam_id>', methods=['GET'])
    @login_required
    def proctor_get_status(student_exam_id):
        """Get proctoring status"""
        try:
            student_exam = StudentExam.query.get_or_404(student_exam_id)
            if student_exam.student_id != current_user.id:
                # For faculty viewing, allow access
                if current_user.role not in ['faculty', 'admin']:
                    return jsonify({"error": "Unauthorized"}), 403

            print(f"🔍 Getting violations for student_exam_id: {student_exam_id}")
            
            violations = ExamViolation.query.filter_by(
                student_exam_id=student_exam_id
            ).order_by(ExamViolation.timestamp.desc()).all()  # Remove .limit(10) for now
            
            print(f"📊 Found {len(violations)} violations in database")
            
            for v in violations:
                print(f"  - {v.violation_type}: {v.message} at {v.timestamp}")

            return jsonify({
                "calibration_completed": getattr(student_exam, 'calibration_completed', False),
                "total_violations": getattr(student_exam, 'total_violations', 0) or 0,
                "proctoring_status": getattr(student_exam, 'proctoring_status', 'active'),
                "is_invalidated": getattr(student_exam, 'is_invalidated', False),
                "faculty_review_note": getattr(student_exam, 'faculty_review_note', None),
                "recent_violations": [{
                    "id": v.id,
                    "type": v.violation_type,
                    "message": v.message,
                    "severity": v.severity,
                    "timestamp": v.timestamp.isoformat(),
                    "evidence_path": v.evidence_path if hasattr(v, 'evidence_path') else None,
                    "evidence_url": url_for('static', filename=v.evidence_path.replace('frontend/static/', '').replace('static/', '')) if (hasattr(v, 'evidence_path') and v.evidence_path) else None
                } for v in violations]
            })

        except Exception as e:
            print(f"❌ Error getting violations: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500


    # ═══════════════════════════════════════════════════════════════════════
    # FACULTY REVIEW APIs — Accept/Reject exam, manage violation evidence
    # ═══════════════════════════════════════════════════════════════════════

    @app.route('/api/faculty/review-exam/<int:student_exam_id>', methods=['POST'])
    @login_required
    def faculty_review_exam(student_exam_id):
        """Faculty accepts or invalidates a student's exam attempt."""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({"error": "Access denied"}), 403

        data = request.get_json() or {}
        action = data.get('action')  # 'accept' or 'invalidate'
        note = data.get('note', '')

        student_exam = StudentExam.query.get_or_404(student_exam_id)

        if action == 'invalidate':
            student_exam.is_invalidated = True
            student_exam.passed = False
            student_exam.faculty_review_note = note or 'Invalidated by faculty due to proctoring violations'
            student_exam.reviewed_by = current_user.id
            student_exam.reviewed_at = datetime.utcnow()
            db.session.commit()
            return jsonify({"success": True, "message": "Exam invalidated. Student marked as failed."})

        elif action == 'accept':
            student_exam.is_invalidated = False
            student_exam.faculty_review_note = note or 'Reviewed and accepted by faculty'
            student_exam.reviewed_by = current_user.id
            student_exam.reviewed_at = datetime.utcnow()
            db.session.commit()
            return jsonify({"success": True, "message": "Exam accepted. Result stands as-is."})

        return jsonify({"error": "Invalid action. Use 'accept' or 'invalidate'."}), 400


    @app.route('/api/faculty/delete-violation/<int:violation_id>', methods=['DELETE'])
    @login_required
    def faculty_delete_violation(violation_id):
        """Delete a single violation record and its evidence image."""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({"error": "Access denied"}), 403

        violation = ExamViolation.query.get_or_404(violation_id)

        # Delete evidence file if it exists
        if hasattr(violation, 'evidence_path') and violation.evidence_path:
            try:
                if os.path.exists(violation.evidence_path):
                    os.remove(violation.evidence_path)
                    print(f"🗑️ Deleted evidence: {violation.evidence_path}")
            except Exception as e:
                print(f"⚠️ Could not delete evidence file: {e}")

        db.session.delete(violation)

        # Update total_violations count
        student_exam = StudentExam.query.get(violation.student_exam_id)
        if student_exam and student_exam.total_violations:
            student_exam.total_violations = max(0, student_exam.total_violations - 1)

        db.session.commit()
        return jsonify({"success": True, "message": "Violation deleted."})


    @app.route('/api/faculty/delete-evidence/<int:violation_id>', methods=['DELETE'])
    @login_required
    def faculty_delete_evidence(violation_id):
        """Delete only the evidence image, keep the violation record."""
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({"error": "Access denied"}), 403

        violation = ExamViolation.query.get_or_404(violation_id)

        if hasattr(violation, 'evidence_path') and violation.evidence_path:
            try:
                if os.path.exists(violation.evidence_path):
                    os.remove(violation.evidence_path)
            except Exception:
                pass
            violation.evidence_path = None
            db.session.commit()
            return jsonify({"success": True, "message": "Evidence image deleted."})

        return jsonify({"success": True, "message": "No evidence to delete."})


    @app.route('/api/faculty/publish-results/<int:exam_id>', methods=['POST'])
    @login_required
    def faculty_publish_results(exam_id):
        """
        Publish exam results — finalizes all grades and cleans up:
        1. Deletes all violation evidence images for this exam
        2. Clears proctor cache/instances
        3. Marks exam as published
        """
        if current_user.role not in ('faculty', 'admin'):
            return jsonify({"error": "Access denied"}), 403

        exam = Exam.query.get_or_404(exam_id)
        if exam.creator_id != current_user.id and current_user.role != 'admin':
            return jsonify({"error": "Not your exam"}), 403

        import shutil

        # Get all student exams for this exam
        student_exams = StudentExam.query.filter_by(exam_id=exam_id).all()
        deleted_images = 0
        deleted_dirs = set()

        for se in student_exams:
            # Delete evidence images from violations
            violations = ExamViolation.query.filter_by(student_exam_id=se.id).all()
            for v in violations:
                if hasattr(v, 'evidence_path') and v.evidence_path:
                    try:
                        if os.path.exists(v.evidence_path):
                            os.remove(v.evidence_path)
                            deleted_images += 1
                        # Track parent directories for cleanup
                        parent = os.path.dirname(v.evidence_path)
                        if parent:
                            deleted_dirs.add(parent)
                    except Exception as e:
                        print(f"⚠️ Could not delete {v.evidence_path}: {e}")
                    v.evidence_path = None

            # Clean up proctor instance cache
            if se.id in PROCTOR_INSTANCES:
                try:
                    del PROCTOR_INSTANCES[se.id]
                except:
                    pass
            _heartbeat_registry.pop(se.id, None)
            _last_frame_processed.pop(se.id, None)

        # Remove empty violation directories
        for d in deleted_dirs:
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    # Also try removing parent (exam-level dir)
                    parent = os.path.dirname(d)
                    if os.path.isdir(parent) and not os.listdir(parent):
                        os.rmdir(parent)
            except:
                pass

        # Mark exam as published
        exam.results_published = True
        exam.results_published_at = datetime.utcnow()
        exam.results_published_by = current_user.id
        db.session.commit()

        print(f"📢 Results published for exam '{exam.title}' — {deleted_images} evidence images deleted")

        return jsonify({
            "success": True,
            "message": f"Results published! {deleted_images} evidence images cleaned up.",
            "deleted_images": deleted_images,
            "student_count": len(student_exams)
        })


    @app.route('/change-password', methods=['GET', 'POST'])
    @login_required
    def change_password():
        """Change password page - forced for first-time students, optional for faculty"""

        from datetime import datetime
        
        # Check if this is a forced change (student first login)
        is_forced = (current_user.role == 'student' and not getattr(current_user, 'password_changed', False))
        
        if request.method == 'POST':
            old_password = request.form.get('old_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            # Validation
            if not old_password or not new_password or not confirm_password:
                flash('❌ All fields are required!', 'error')
                return redirect(url_for('change_password'))
            
            # Check old password - FIXED: use password_hash instead of password
            if not check_password_hash(current_user.password_hash, old_password):
                flash('❌ Current password is incorrect!', 'error')
                return redirect(url_for('change_password'))
            
            # Check new passwords match
            if new_password != confirm_password:
                flash('❌ New passwords do not match!', 'error')
                return redirect(url_for('change_password'))
            
            # Check password length
            if len(new_password) < 6:
                flash('❌ New password must be at least 6 characters long!', 'error')
                return redirect(url_for('change_password'))
            
            # Check new password is different from old - FIXED: use password_hash
            if check_password_hash(current_user.password_hash, new_password):
                flash('❌ New password must be different from current password!', 'error')
                return redirect(url_for('change_password'))
            
            # Update password - FIXED: use password_hash
            try:
                current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
                current_user.password_changed = True
                current_user.password_changed_at = datetime.utcnow()
                db.session.commit()
                
                flash('✅ Password changed successfully!', 'success')
                
                # Redirect based on role
                if current_user.role == 'student':
                    return redirect(url_for('student_dashboard'))
                elif current_user.role == 'faculty':
                    return redirect(url_for('faculty_dashboard'))
                else:
                    return redirect(url_for('admin_dashboard'))
                    
            except Exception as e:
                db.session.rollback()
                print(f"❌ Error changing password: {e}")
                import traceback
                traceback.print_exc()
                flash(f'❌ Error changing password: {str(e)}', 'error')
                return redirect(url_for('change_password'))
        
        return render_template('change_password.html', is_forced=is_forced)


    @app.route('/check-password-status')
    @login_required
    def check_password_status():
        """API endpoint to check if user needs to change password"""
        needs_change = (current_user.role == 'student' and not current_user.password_changed)
        
        return jsonify({
            'needs_change': needs_change,
            'role': current_user.role,
            'password_changed': current_user.password_changed
        })

    @app.route('/exam/<int:exam_id>/log_activity', methods=['POST'])
    def log_activity_fix(exam_id):
        return jsonify({"success": True})

        
    @app.route('/faculty/change-student-password/<int:student_id>', methods=['POST'])
    @login_required
    def faculty_change_student_password(student_id):
        """Faculty can reset a student's password — faculty isolated"""
        from datetime import datetime
        
        # Authorization check
        if current_user.role not in ['faculty', 'admin']:
            return jsonify({"success": False, "message": "Unauthorized"}), 403
        
        # Faculty isolation check
        if current_user.role == 'faculty' and not _faculty_can_access_student(student_id):
            return jsonify({"success": False, "message": "Access denied: student not in your exams"}), 403
        
        try:
            # Get student
            student = User.query.get_or_404(student_id)
            
            # Verify it's a student
            if student.role != 'student':
                return jsonify({
                    "success": False,
                    "message": "Can only change passwords for students"
                }), 400
            
            # Get new password from form
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            # Validation
            if not new_password or not confirm_password:
                return jsonify({
                    "success": False,
                    "message": "Both password fields are required"
                }), 400
            
            if new_password != confirm_password:
                return jsonify({
                    "success": False,
                    "message": "Passwords do not match"
                }), 400
            
            if len(new_password) < 6:
                return jsonify({
                    "success": False,
                    "message": "Password must be at least 6 characters long"
                }), 400
            
            # Update password
            student.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
            student.password_changed = False  # Force student to change on next login
            student.password_changed_at = None
            db.session.commit()
            
            # Log activity
            print(f"✅ Faculty {current_user.username} reset password for student {student.username}")
            
            return jsonify({
                "success": True,
                "message": f"Password reset successfully for {student.full_name or student.username}. Student will be required to change it on next login."
            })
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error changing student password: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({
                "success": False,
                "message": f"Error: {str(e)}"
            }), 500


    print("✅ All enhanced routes registered!")