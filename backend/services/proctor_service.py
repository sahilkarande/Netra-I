import os
import time
import threading
import json
import re
import cv2
import numpy as np
import base64
import traceback
from datetime import datetime
from typing import Dict

from backend.database import db
from backend.models import (
    User, Exam, StudentExam, ExamViolation, ActivityLog
)
from backend.services.proctor_vision.openvino_vision import (
    ProctorState,
    OpenVINOProctor
)

# ═══════════════════════════════════════════════════════════════════════
# SHARED RESOURCES FOR SCALABILITY
# ═══════════════════════════════════════════════════════════════════════

# Global proctoring instances (one per student_exam_id)
PROCTOR_INSTANCES = {}
_proctor_lock = threading.Lock()

# Heartbeat tracking
_heartbeat_registry: Dict = {}  # student_exam_id -> last_heartbeat_time
_last_frame_processed: Dict = {} # student_exam_id -> last frame time
HEARTBEAT_TIMEOUT = 30 

# Shared face detector
_FACE_CASCADE = None
try:
    _FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    if _FACE_CASCADE.empty():
        print("⚠️ WARNING: Face cascade classifier failed to load")
        _FACE_CASCADE = None
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
    """Safely extract proctor settings from an Exam object."""
    DEFAULTS = {
        'camera_enabled': True,
        'face_detection': True,
        'head_pose': True,
        'object_detection': True,
        'voice_detection': True,
        'tab_switch': True,
        'max_warnings': 25,
        'max_tab_switches': 5,
        'sensitivity': 'medium',
        'no_face_timeout_sec': 30,
        'warning_cooldown_sec': 30,
        'deviation_min_duration': 7,
    }
    
    settings = {}
    if hasattr(exam, 'get_proctor_settings') and callable(getattr(exam, 'get_proctor_settings', None)):
        try:
            settings = exam.get_proctor_settings() or {}
        except Exception:
            pass
    
    if not settings and hasattr(exam, 'proctor_settings'):
        raw = getattr(exam, 'proctor_settings', None)
        if raw and isinstance(raw, str) and raw.strip() and raw.strip() != '{}':
            try:
                settings = json.loads(raw)
            except:
                pass
    
    return {**DEFAULTS, **settings}


def get_proctor_instance(student_exam_id: int, exam):
    """Get or create a ProctorState instance (thread-safe)"""
    with _proctor_lock:
        if student_exam_id not in PROCTOR_INSTANCES:
            feature_flags = safe_get_proctor_settings(exam)
            max_warnings = feature_flags.get('max_warnings', 25)
            
            student_exam = StudentExam.query.get(student_exam_id)
            student = User.query.get(student_exam.student_id) if student_exam else None
            
            exam_title_safe = re.sub(r'[^a-zA-Z0-9_\- ]', '', exam.title or 'exam').strip().replace(' ', '_')
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
            PROCTOR_INSTANCES[student_exam_id] = (proctor_state, vision)
        return PROCTOR_INSTANCES[student_exam_id]


def cleanup_proctor_instance(student_exam_id: int):
    """Remove proctor instance when exam ends to free memory."""
    with _proctor_lock:
        if student_exam_id in PROCTOR_INSTANCES:
            del PROCTOR_INSTANCES[student_exam_id]
    _heartbeat_registry.pop(student_exam_id, None)
    _last_frame_processed.pop(student_exam_id, None)


def log_proctor_event(student_exam_id: int, event_type: str, severity: str,
                      message: str, cheating_score: float = 0.0,
                      evidence_path: str = None):
    """Centralized event logging for proctoring violations."""
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
            pass

        db.session.commit()
    except Exception as e:
        print(f"⚠️ Event logging error: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
