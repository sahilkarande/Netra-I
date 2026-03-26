# proctor_vision/openvino_vision.py
# AI Proctoring Engine v4.0 — Faculty-Controlled Detection Strength
# CHANGES FROM v3: Always calibrates, per-type cooldowns, evidence capture,
# sensitivity-based thresholds, proper error logging, no silent failures.
PROCTOR_ENGINE_VERSION = "4.0"

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict, Any
from enum import Enum

import cv2
import numpy as np

try:
    from openvino.runtime import Core
except ImportError:
    Core = None
    print("⚠️ OpenVINO not available — running in fallback mode")


# ═══════════════════════════════════════════════════════════════════════
# ENUMS & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Simple weights — only used for logging, NOT for rapid termination
CHEATING_WEIGHTS: Dict[str, float] = {
    "no_face": 0.3,
    "multiple_faces": 0.5,
    "looking_away": 0.2,
    "mobile_phone": 0.5,
    "book": 0.3,
    "voice_detected": 0.2,
}


# ═══════════════════════════════════════════════════════════════════════
# PROCTOR STATE — Simplified
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ProctorState:
    session_id: str
    max_warnings: int = 25  # Very generous — students shouldn't feel scared
    # Evidence capture
    evidence_dir: str = "frontend/static/violations"
    evidence_path: Optional[str] = None

    # baseline head pose
    baseline_yaw: Optional[float] = None
    baseline_pitch: Optional[float] = None
    baseline_roll: Optional[float] = None

    # for center tracking
    baseline_cx: Optional[float] = None
    baseline_cy: Optional[float] = None
    baseline_w: Optional[float] = None
    baseline_h: Optional[float] = None

    warning_count: int = 0
    last_face_time: float = field(default_factory=time.time)
    terminated: bool = False

    # ── LONG cooldowns to prevent spam ──
    last_warning_time: float = field(default_factory=lambda: 0.0)
    deviation_start_time: Optional[float] = None

    # Frame counter
    frame_count: int = 0


# ═══════════════════════════════════════════════════════════════════════
# MAIN PROCTOR CLASS — SIMPLIFIED
# ═══════════════════════════════════════════════════════════════════════

class OpenVINOProctor:
    """
    Simplified AI Proctoring Engine v3.0 — Faculty Driven

    Only runs checks that the faculty has enabled.
    Generous cooldowns between warnings (15 seconds minimum).
    No cheating score system — just simple warning count.
    """

    def __init__(
        self,
        state: ProctorState,
        model_dir: str = "models",
        device: str = "CPU",
        feature_flags: Optional[Dict[str, Any]] = None,
    ):
        self.state = state
        self.model_dir = model_dir
        self.device = device

        # ── Feature flags (from faculty's exam settings) ──
        defaults = {
            'camera_enabled': True,       # Master switch for camera
            'face_detection': True,       # Detect if face is present
            'head_pose': False,           # Track head orientation (OFF by default)
            'object_detection': False,    # Detect phone/book (OFF by default)
            'voice_detection': False,     # Audio monitoring (OFF by default)
            'tab_switch': True,           # Tab switch monitoring
            'max_warnings': 25,            # Very generous — students are human
            'no_face_timeout_sec': 30,     # 30 seconds before no-face warning
            'warning_cooldown_sec': 30,    # 30 seconds between ANY warnings
            'deviation_min_duration': 7,   # Must look away 7 seconds continuously
            'sensitivity': 'medium',      # Default to balanced detection
        }
        self.feature_flags = {**defaults, **(feature_flags or {})}
        print(f"🚀 [OpenVINO Proctor v{PROCTOR_ENGINE_VERSION}] Initialized")
        print(f"🔧 [OpenVINO] Feature flags: camera={self.feature_flags.get('camera_enabled')}, "
              f"head_pose={self.feature_flags.get('head_pose')}, "
              f"object_det={self.feature_flags.get('object_detection')}, "
              f"voice={self.feature_flags.get('voice_detection')}")

        # ── Load OpenVINO Core ──
        if Core is not None:
            self.core = Core()
        else:
            self.core = None

        # ── Only load models that are needed ──
        self._face_cascade = None
        self.fd_compiled = None
        self.hp_compiled = None
        self.obj_net = None
        self.obj_model_type = None

        if self.feature_flags.get('camera_enabled') and self.feature_flags.get('face_detection'):
            self._load_face_detection_model(model_dir, device)

        if self.feature_flags.get('camera_enabled') and self.feature_flags.get('head_pose'):
            self._load_head_pose_model(model_dir, device)

        if self.feature_flags.get('camera_enabled') and self.feature_flags.get('object_detection'):
            self._load_object_detection_model(model_dir, device)

        # ── Thresholds — controlled by faculty via 'sensitivity' setting ──
        # 'high' = STRICT — catches everything fast, minimal tolerance
        # 'medium' = BALANCED — reasonable for most exams
        # 'low' = RELAXED — very forgiving, only obvious violations
        sensitivity = self.feature_flags.get('sensitivity', 'medium')

        if sensitivity == 'high':
            # STRICT MODE — catches even small movements quickly
            self.yaw_threshold = 12.0       # ~20° head turn triggers
            self.pitch_threshold = 12.0     # ~15° look up/down triggers
            self.face_conf = 0.1           # Detect faces at low confidence
            self.object_conf_threshold = 0.1  # Catch objects at low confidence
        elif sensitivity == 'low':
            # EASY MODE — very forgiving, students can move naturally
            self.yaw_threshold = 50.0       # Must turn head ~50° to trigger
            self.pitch_threshold = 45.0
            self.face_conf = 0.5            # Only clear faces
            self.object_conf_threshold = 0.7   # Only clear objects
        else:
            # MEDIUM — balanced defaults
            self.yaw_threshold = 30.0       # ~30° head turn
            self.pitch_threshold = 25.0
            self.face_conf = 0.35
            self.object_conf_threshold = 0.55

        # Timing thresholds — from faculty settings (set by sensitivity in form JS)
        self.no_face_timeout = float(self.feature_flags.get('no_face_timeout_sec', 15))
        self.warning_cooldown = float(self.feature_flags.get('warning_cooldown_sec', 15))
        self.deviation_min_duration = float(self.feature_flags.get('deviation_min_duration', 5))

        # Apply max warnings from faculty setting
        self.state.max_warnings = int(self.feature_flags.get('max_warnings', 25))

        print(f"🎛️ [Proctor] Sensitivity={sensitivity}")
        print(f"   yaw={self.yaw_threshold}° pitch={self.pitch_threshold}° face_conf={self.face_conf}")
        print(f"   no_face_timeout={self.no_face_timeout}s cooldown={self.warning_cooldown}s deviation_min={self.deviation_min_duration}s")
        print(f"   max_warnings={self.state.max_warnings} obj_conf={self.object_conf_threshold}")

        # Evidence capture directory
        os.makedirs(self.state.evidence_dir, exist_ok=True)

    # ═══════════════════════════════════════════════════════════════════
    # MODEL LOADING (with graceful fallbacks)
    # ═══════════════════════════════════════════════════════════════════

    def _load_face_detection_model(self, model_dir: str, device: str):
        self.fd_compiled = None
        self.fd_input = None
        self.fd_output = None
        self.fd_h = self.fd_w = 0

        try:
            fd_model_path = f"{model_dir}/face-detection-adas-0001.xml"
            if self.core and os.path.exists(fd_model_path):
                fd_model = self.core.read_model(fd_model_path)
                self.fd_compiled = self.core.compile_model(fd_model, device)
                self.fd_input = self.fd_compiled.input(0)
                self.fd_output = self.fd_compiled.output(0)
                _, _, self.fd_h, self.fd_w = self.fd_input.shape
                print("✅ Face detection model loaded")
            else:
                print("⚠️ Face detection model not found — using Haar cascade fallback")
        except Exception as e:
            print(f"⚠️ Face detection model load failed: {e}")

        # Haar cascade fallback
        self._face_cascade = None
        if self.fd_compiled is None:
            try:
                self._face_cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
                )
                if self._face_cascade.empty():
                    self._face_cascade = None
                else:
                    print("✅ Haar cascade fallback loaded")
            except Exception:
                pass

    def _load_head_pose_model(self, model_dir: str, device: str):
        self.hp_compiled = None
        self.hp_input = None
        self.hp_h = self.hp_w = 0

        try:
            hp_model_path = f"{model_dir}/head-pose-estimation-adas-0001.xml"
            if self.core and os.path.exists(hp_model_path):
                hp_model = self.core.read_model(hp_model_path)
                self.hp_compiled = self.core.compile_model(hp_model, device)
                self.hp_input = self.hp_compiled.input(0)
                _, _, self.hp_h, self.hp_w = self.hp_input.shape
                print("✅ Head pose model loaded")
        except Exception as e:
            print(f"⚠️ Head pose model load failed: {e}")

    def _load_object_detection_model(self, model_dir: str, device: str):
        self.obj_net = None
        self.obj_model_type = None

        try:
            yolo_cfg = f"{model_dir}/yolov4-tiny.cfg"
            yolo_weights = f"{model_dir}/yolov4-tiny.weights"
            if os.path.exists(yolo_cfg) and os.path.exists(yolo_weights):
                self.obj_net = cv2.dnn.readNetFromDarknet(yolo_cfg, yolo_weights)
                self.obj_net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                self.obj_net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                self.obj_model_type = "yolo"
                self._yolo_output_layers = self.obj_net.getUnconnectedOutLayersNames()
                print("✅ YOLO object detection model loaded")
                return

            ssd_proto = f"{model_dir}/MobileNetSSD_deploy.prototxt"
            ssd_model = f"{model_dir}/MobileNetSSD_deploy.caffemodel"
            if os.path.exists(ssd_proto) and os.path.exists(ssd_model):
                self.obj_net = cv2.dnn.readNetFromCaffe(ssd_proto, ssd_model)
                self.obj_model_type = "ssd"
                print("✅ MobileNet-SSD object detection model loaded")
                return

            print("⚠️ No object detection model found — object detection disabled")
        except Exception as e:
            print(f"⚠️ Object detection model load failed: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # DETECTION HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def _detect_faces(self, frame_bgr, conf_thresh=None):
        """Detect all faces in frame. Returns list of (x1,y1,x2,y2) boxes."""
        if conf_thresh is None:
            # Faculty-controlled strength: low=0.5, medium=0.4, high=0.3
            strength = self.feature_flags.get('sensitivity', 'low')
            conf_thresh = {'high': 0.3, 'medium': 0.4, 'low': 0.5}.get(strength, 0.5)

        h, w, _ = frame_bgr.shape

        if self.fd_compiled is not None:
            try:
                img = cv2.resize(frame_bgr, (self.fd_w, self.fd_h))
                img = img.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
                result = self.fd_compiled([img])[self.fd_output]
                boxes = []
                for det in result[0, 0]:
                    conf = float(det[2])
                    if conf < conf_thresh:
                        continue
                    xmin = max(0, int(det[3] * w))
                    ymin = max(0, int(det[4] * h))
                    xmax = min(w - 1, int(det[5] * w))
                    ymax = min(h - 1, int(det[6] * h))
                    bw, bh = xmax - xmin, ymax - ymin
                    if bw < 20 or bh < 20:  # Minimum face size
                        continue
                    boxes.append((xmin, ymin, xmax, ymax))
                if self.state.frame_count % 20 == 0:
                    print(f"👁️ [FaceDet] {len(boxes)} face(s) found (conf>={conf_thresh})")
                return boxes
            except Exception as e:
                print(f"⚠️ OpenVINO face detection error: {e}")

        # Haar cascade fallback — tuned for multiple faces
        if self._face_cascade is not None:
            try:
                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                gray = cv2.equalizeHist(gray)  # Improve contrast
                # minNeighbors=3 catches more faces (was 5, too strict)
                faces = self._face_cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=3, minSize=(30, 30)
                )
                boxes = [(x, y, x + fw, y + fh) for (x, y, fw, fh) in faces]
                if self.state.frame_count % 20 == 0:
                    print(f"👁️ [Haar] {len(boxes)} face(s) found")
                return boxes
            except Exception as e:
                print(f"⚠️ Haar cascade error: {e}")

        return []

    def _detect_face(self, frame_bgr):
        boxes = self._detect_faces(frame_bgr)
        if not boxes:
            return None
        return max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))

    def _estimate_head_pose(self, face_bgr):
        """Estimate yaw, pitch, roll from face crop. Raises on failure."""
        if self.hp_compiled is None:
            raise RuntimeError("Head pose model not loaded")

        if face_bgr.size == 0 or face_bgr.shape[0] < 10 or face_bgr.shape[1] < 10:
            raise ValueError(f"Face crop too small: {face_bgr.shape}")

        img = cv2.resize(face_bgr, (self.hp_w, self.hp_h))
        img = img.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
        outputs = self.hp_compiled([img])

        yaw = pitch = roll = None
        for out in self.hp_compiled.outputs:
            name = out.get_any_name()
            val = float(outputs[out].flatten()[0])
            if "angle_y" in name:
                yaw = val
            elif "angle_p" in name:
                pitch = val
            elif "angle_r" in name:
                roll = val

        if yaw is None or pitch is None or roll is None:
            vals = [float(outputs[o].flatten()[0]) for o in self.hp_compiled.outputs]
            if len(vals) >= 3:
                yaw, pitch, roll = vals[:3]
            else:
                raise ValueError(f"Head pose model returned {len(vals)} outputs, need 3")

        if self.state.frame_count % 10 == 0:
            print(f"🔄 [HeadPose] yaw={yaw:.1f}° pitch={pitch:.1f}° roll={roll:.1f}° "
                  f"(baseline: y={self.state.baseline_yaw:.1f} p={self.state.baseline_pitch:.1f})")

        return yaw, pitch, roll

    def _detect_objects(self, frame_bgr):
        """Detect prohibited objects (phone, book, laptop). Returns list of dicts."""
        if self.obj_net is None:
            return []

        h, w = frame_bgr.shape[:2]
        detected = []

        # Faculty-controlled confidence threshold
        strength = self.feature_flags.get('sensitivity', 'low')
        conf_thresh = {'high': 0.25, 'medium': 0.35, 'low': 0.45}.get(strength, 0.45)

        # COCO class IDs for YOLO
        YOLO_PROHIBITED = {
            67: "cell phone", 73: "book", 63: "laptop",
            66: "keyboard", 64: "mouse",  # Additional items
        }

        # MobileNet-SSD doesn't have phone class — skip SSD for object det
        # SSD class 7 is "car" not "phone". Only YOLO is reliable for phone detection.

        try:
            if self.obj_model_type == "yolo":
                blob = cv2.dnn.blobFromImage(frame_bgr, 1/255.0, (416, 416), swapRB=True, crop=False)
                self.obj_net.setInput(blob)
                outs = self.obj_net.forward(self._yolo_output_layers)
                for out in outs:
                    for detection in out:
                        scores = detection[5:]
                        class_id = int(np.argmax(scores))
                        confidence = float(scores[class_id])
                        if confidence < conf_thresh:
                            continue
                        if class_id in YOLO_PROHIBITED:
                            label = YOLO_PROHIBITED[class_id]
                            print(f"📱 [ObjDet] {label} detected! conf={confidence:.2f} (thresh={conf_thresh})")
                            detected.append({
                                "label": label,
                                "confidence": confidence,
                                "class_id": class_id,
                            })

            elif self.obj_model_type == "ssd":
                blob = cv2.dnn.blobFromImage(cv2.resize(frame_bgr, (300, 300)), 0.007843, (300, 300), 127.5)
                self.obj_net.setInput(blob)
                detections = self.obj_net.forward()
                # MobileNet-SSD PASCAL VOC classes (no phone class!)
                # But class 20 = tvmonitor which could be a tablet/screen
                SSD_PROHIBITED = {20: "screen/tablet"}
                for i in range(detections.shape[2]):
                    conf = float(detections[0, 0, i, 2])
                    if conf < conf_thresh:
                        continue
                    class_id = int(detections[0, 0, i, 1])
                    if class_id in SSD_PROHIBITED:
                        label = SSD_PROHIBITED[class_id]
                        print(f"📱 [ObjDet-SSD] {label} detected! conf={conf:.2f}")
                        detected.append({"label": label, "confidence": conf})

            if self.state.frame_count % 20 == 0 and not detected:
                print(f"📱 [ObjDet] No prohibited objects found (thresh={conf_thresh}, model={self.obj_model_type})")

        except Exception as e:
            print(f"⚠️ Object detection error: {e}")

        return detected

    # ═══════════════════════════════════════════════════════════════════
    # EVIDENCE CAPTURE — Save frame snapshots for violation review
    # ═══════════════════════════════════════════════════════════════════

    def _capture_evidence(self, frame: np.ndarray, violation_type: str) -> Optional[str]:
        """Save frame as evidence for a violation. Returns file path."""
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            uid = uuid.uuid4().hex[:6]
            filename = f"{self.state.session_id}_{violation_type}_{timestamp}_{uid}.jpg"
            filepath = os.path.join(self.state.evidence_dir, filename)

            # Draw violation label and timestamp on copy of frame
            annotated = frame.copy()
            cv2.putText(
                annotated, f"VIOLATION: {violation_type}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2,
            )
            cv2.putText(
                annotated, time.strftime("%Y-%m-%d %H:%M:%S"),
                (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1,
            )

            cv2.imwrite(filepath, annotated)
            print(f"📸 Evidence saved: {filepath}")
            return filepath
        except Exception as e:
            print(f"⚠️ Evidence capture failed: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # CORE: Per-type cooldowns — different violations don't block each other
    # ═══════════════════════════════════════════════════════════════════

    def _can_warn(self):
        """Check if enough time has passed since last warning (global)."""
        now = time.time()
        # Global minimum gap: based on sensitivity setting
        if now - self.state.last_warning_time < self.warning_cooldown:
            return False
        return True

    def _can_warn_type(self, violation_type):
        """Check per-type cooldown — so a face warning doesn't block object detection."""
        now = time.time()
        # Global minimum: based on sensitivity
        if now - self.state.last_warning_time < self.warning_cooldown:
            return False
        # Per-type cooldown: same as global cooldown
        if not hasattr(self.state, '_type_cooldowns'):
            self.state._type_cooldowns = {}
        last_time = self.state._type_cooldowns.get(violation_type, 0)
        if now - last_time < self.warning_cooldown:
            return False
        return True

    def _issue_warning(self, reason, violation_type="general", frame=None):
        """Issue a single warning. Respects per-type cooldown. Captures evidence image."""
        if not self._can_warn():
            return "NORMAL", {"message": "Cooldown active", "faces_detected": 1}

        now = time.time()
        self.state.last_warning_time = now
        # Record per-type cooldown
        if not hasattr(self.state, '_type_cooldowns'):
            self.state._type_cooldowns = {}
        self.state._type_cooldowns[violation_type] = now
        self.state.warning_count += 1

        # Capture evidence screenshot if frame is available
        evidence_path = None
        if frame is not None:
            evidence_path = self._capture_evidence(frame, violation_type)

        details = {
            "message": reason,
            "severity": "low",  # Keep severity low — don't scare students
            "violation_type": violation_type,
            "warning_count": self.state.warning_count,
            "max_warnings": self.state.max_warnings,
            "faces_detected": 1,
            "evidence_path": evidence_path,
        }

        # Check if we hit the limit
        if self.state.warning_count >= self.state.max_warnings:
            self.state.terminated = True
            details["severity"] = "medium"
            details["message"] = f"Your exam has been auto-submitted. Please contact your instructor if you have any concerns."
            return "TERMINATE", details

        details["message"] = f"Gentle reminder ({self.state.warning_count}/{self.state.max_warnings}): {reason}"
        return "WARNING", details

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API: CALIBRATE
    # ═══════════════════════════════════════════════════════════════════

    def calibrate(self, frames):
        """Calibrate baseline from frames. ALWAYS succeeds — uses defaults if needed."""
        yaw_list, pitch_list, roll_list = [], [], []
        centers_x, centers_y, widths, heights = [], [], [], []
        face_found = False

        for frame in frames:
            try:
                box = self._detect_face(frame)
                if box is None:
                    continue
                face_found = True
                x1, y1, x2, y2 = box
                face = frame[y1:y2, x1:x2]
                if face.size == 0:
                    continue

                centers_x.append((x1 + x2) / 2.0)
                centers_y.append((y1 + y2) / 2.0)
                widths.append(x2 - x1)
                heights.append(y2 - y1)

                try:
                    yaw, pitch, roll = self._estimate_head_pose(face)
                    yaw_list.append(yaw)
                    pitch_list.append(pitch)
                    roll_list.append(roll)
                except Exception as e:
                    print(f"⚠️ [Calibration] Head pose failed on frame: {e}")
                    # Still count as good frame for face detection
            except Exception as e:
                print(f"⚠️ [Calibration] Frame processing error: {e}")
                continue

        # Set head pose baseline (use measured values or defaults)
        if yaw_list:
            self.state.baseline_yaw = float(np.mean(yaw_list))
            self.state.baseline_pitch = float(np.mean(pitch_list))
            self.state.baseline_roll = float(np.mean(roll_list))
            print(f"✅ [Calibration] Head pose baseline: yaw={self.state.baseline_yaw:.1f} pitch={self.state.baseline_pitch:.1f}")
        else:
            # Default baseline — assume looking straight at camera
            self.state.baseline_yaw = 0.0
            self.state.baseline_pitch = 0.0
            self.state.baseline_roll = 0.0
            print(f"⚠️ [Calibration] Using default head pose baseline (0,0,0)")

        # Set face position baseline
        if centers_x:
            self.state.baseline_cx = float(np.mean(centers_x))
            self.state.baseline_cy = float(np.mean(centers_y))
            self.state.baseline_w = float(np.mean(widths))
            self.state.baseline_h = float(np.mean(heights))
        else:
            self.state.baseline_cx = 160.0
            self.state.baseline_cy = 120.0
            self.state.baseline_w = 100.0
            self.state.baseline_h = 100.0

        # Reset state
        self.state.warning_count = 0
        self.state.terminated = False
        self.state.last_face_time = time.time()
        self.state.deviation_start_time = None
        self.state.last_warning_time = 0.0
        self.state.frame_count = 0

        msg = f"Calibration successful (faces={len(centers_x)}, headpose={'yes' if yaw_list else 'default'})"
        print(f"✅ [Calibration] {msg}")
        return True, msg

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API: CHECK FRAME — SIMPLIFIED
    # ═══════════════════════════════════════════════════════════════════

    def check_frame(self, frame):
        """
        Check a single frame. Runs ALL enabled checks (not just the first one).
        Uses per-violation-type cooldowns so one type doesn't block others.
        Returns (status, details) where status is:
          NORMAL / WARNING / TERMINATE / NO_FACE / ERROR
        """
        now = time.time()
        self.state.frame_count += 1

        if self.state.terminated:
            return "TERMINATE", {"message": "Exam already terminated."}

        # If not calibrated, set defaults so face/object detection still works
        if self.state.baseline_yaw is None:
            self.state.baseline_yaw = 0.0
            self.state.baseline_pitch = 0.0
            self.state.baseline_roll = 0.0
            print("⚠️ [check_frame] No calibration — using default baseline (0,0,0)")

        # Collect all violations found in this frame
        violations_found = []
        faces_detected = 0
        face_crop = None

        try:
            # ── Check 1: Face Detection (if enabled) ──
            if self.feature_flags.get('face_detection', True):
                boxes = self._detect_faces(frame)

                if not boxes:
                    # Only warn after timeout
                    if now - self.state.last_face_time > self.no_face_timeout:
                        violations_found.append({
                            "reason": "No face detected — please face the camera",
                            "type": "no_face",
                        })
                    else:
                        # Brief no-face, don't run other checks
                        return "NO_FACE", {
                            "message": "Face temporarily not visible",
                            "faces_detected": 0,
                        }
                else:
                    self.state.last_face_time = now
                    faces_detected = len(boxes)

                    # Multiple faces
                    if faces_detected > 1:
                        violations_found.append({
                            "reason": f"Multiple faces detected ({faces_detected})",
                            "type": "multiple_faces",
                        })

                    # Get largest face for further analysis
                    box = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                    x1, y1, x2, y2 = box
                    face_crop = frame[y1:y2, x1:x2]
                    if face_crop.size == 0:
                        face_crop = None
            else:
                return "NORMAL", {"message": "Camera monitoring disabled", "faces_detected": 0}

            # ── Check 2: Object Detection (if enabled) — run EVERY frame ──
            if self.feature_flags.get('object_detection', False):
                objects = self._detect_objects(frame)
                for obj in objects:
                    label = obj["label"]
                    if label == "person":
                        continue
                    violations_found.append({
                        "reason": f"{label.title()} detected — please remove it",
                        "type": label.replace(" ", "_"),
                    })
                    break  # One object warning per frame is enough

            # ── Check 3: Head Pose (if enabled and we have a face) ──
            if self.feature_flags.get('head_pose', False) and face_crop is not None:
                try:
                    yaw, pitch, roll = self._estimate_head_pose(face_crop)
                    dyaw = abs(yaw - self.state.baseline_yaw)
                    dpitch = abs(pitch - self.state.baseline_pitch)

                    # Grace period in first 2 minutes
                    grace = 1.3 if self.state.frame_count < 25 else 1.0
                    eff_yaw = self.yaw_threshold * grace
                    eff_pitch = self.pitch_threshold * grace

                    deviated = dyaw > eff_yaw or dpitch > eff_pitch

                    if deviated:
                        if self.state.deviation_start_time is None:
                            self.state.deviation_start_time = now
                        elif now - self.state.deviation_start_time >= self.deviation_min_duration:
                            self.state.deviation_start_time = None
                            violations_found.append({
                                "reason": "Please face the screen",
                                "type": "looking_away",
                            })
                    else:
                        self.state.deviation_start_time = None
                        # Adapt baseline
                        alpha = 0.05 if self.state.frame_count < 25 else 0.02
                        self.state.baseline_yaw = (1 - alpha) * self.state.baseline_yaw + alpha * yaw
                        self.state.baseline_pitch = (1 - alpha) * self.state.baseline_pitch + alpha * pitch
                        self.state.baseline_roll = (1 - alpha) * self.state.baseline_roll + alpha * roll
                except Exception as e:
                    print(f"⚠️ Head pose estimation error: {e}")

            # ── Process violations: issue the FIRST one that passes its per-type cooldown ──
            if violations_found:
                for v in violations_found:
                    if self._can_warn_type(v["type"]):
                        return self._issue_warning(
                            v["reason"],
                            violation_type=v["type"],
                            frame=frame
                        )
                # All violations are on cooldown — return normal
                return "NORMAL", {"message": "Cooldown active", "faces_detected": faces_detected}

            # ── ALL GOOD ──
            return "NORMAL", {
                "message": "OK",
                "faces_detected": faces_detected or 1,
            }

        except Exception as e:
            print(f"⚠️ check_frame error: {e}")
            return "ERROR", {"message": f"Processing error: {str(e)}"}

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API: GET STATUS
    # ═══════════════════════════════════════════════════════════════════

    def get_status(self):
        return {
            "session_id": self.state.session_id,
            "terminated": self.state.terminated,
            "warning_count": self.state.warning_count,
            "max_warnings": self.state.max_warnings,
            "total_violations": self.state.warning_count,
            "frame_count": self.state.frame_count,
            "features": {
                "camera": self.feature_flags.get('camera_enabled', True),
                "face_detection": self.feature_flags.get('face_detection', True),
                "head_pose": self.feature_flags.get('head_pose', False),
                "object_detection": self.feature_flags.get('object_detection', False),
                "voice_detection": self.feature_flags.get('voice_detection', False),
            }
        }

    def get_violation_log(self):
        return {
            "warning_count": self.state.warning_count,
            "max_warnings": self.state.max_warnings,
            "terminated": self.state.terminated,
            "features": {
                "camera": self.feature_flags.get('camera_enabled', True),
                "face_detection": self.feature_flags.get('face_detection', True),
                "head_pose": self.feature_flags.get('head_pose', False),
                "object_detection": self.feature_flags.get('object_detection', False),
                "voice_detection": self.feature_flags.get('voice_detection', False),
            }
        }