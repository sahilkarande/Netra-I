"""
Face enrolment and matching for the optional face-login flow.

Design notes
────────────
* **Vectors, not images.** The browser runs face-api.js and sends only the
  128-dimension float32 descriptor. 512 bytes per sample; a 3-sample enrolment
  is 1.5 KB, so 300 students cost ~450 KB — against ~15 MB if we stored JPEGs.
  The descriptor is also the only thing matching needs, so storing a picture
  would be strictly more data for strictly less relevance.

* **Matching happens here, on the server.** If the browser decided whether a
  face matched, anyone could POST ``{"match": true}`` and walk in. The client is
  never trusted with the verdict — it only supplies the probe vector.

* **A face vector cannot be rotated.** Unlike a password, a leaked descriptor is
  compromised forever. Face login is therefore a *convenience* factor that sits
  beside password login, and it is deliberately narrow: the caller must name who
  they claim to be, so this verifies an identity rather than searching for one.

* **Liveness is mandatory.** Matching runs only after
  :mod:`backend.services.liveness_service` has cleared the frame sequence as a
  live person. A descriptor on its own is never accepted, because a photo of an
  enrolled student produces a valid matching descriptor.

* **Enrolment is locked once set.** A student may enrol once; only faculty or an
  admin can reset it, and every change is written to ``face_audit_log``.
"""

import base64
import struct
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import cv2
import numpy as np

from backend.database import db
from backend.models import FaceAuditLog, FaceEnrollment, FaceLoginAttempt, User
from backend.services import liveness_service as LV

# ── Tunables ────────────────────────────────────────────────────────────────
DESCRIPTOR_DIM = 128

# face-api.js convention: < 0.6 is "probably the same person". That is tuned for
# photo tagging, where a false match is harmless. For authentication we want the
# opposite bias, so the gate is tighter — a rejected login costs one retry, a
# false accept lets the wrong person sit an exam.
MATCH_THRESHOLD = 0.52

# Identity is judged on the CENTERED frame. The turned-head frames exist to
# prove liveness, and a 128-dim descriptor degrades sharply off-frontal, so
# holding them to the frontal threshold would fail legitimate users. They get a
# looser bar whose only job is "still the same person, nobody swapped in".
POSE_MATCH_THRESHOLD = 0.68

MIN_ENROLL_SAMPLES = 2
MAX_ENROLL_SAMPLES = 5

# Samples within an enrolment must be of one person; a wildly different vector
# means the enroller swapped seats midway (or is enrolling two people).
ENROLL_CONSISTENCY_MAX = 0.55

# Same-person check ACROSS poses within one challenge — necessarily looser than
# the frontal-to-frontal enrolment check above.
SEQUENCE_CONSISTENCY_MAX = 0.75

# Brute-force limiting. In-process, like the rest of this app's runtime state.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 10
_failed_attempts = {}          # identifier -> [count, first_failure_datetime]


# ── Packing ─────────────────────────────────────────────────────────────────

def pack_descriptors(samples: List[List[float]]) -> bytes:
    """Pack N descriptors into little-endian float32 bytes."""
    out = bytearray()
    for sample in samples:
        if len(sample) != DESCRIPTOR_DIM:
            raise ValueError(f"descriptor must have {DESCRIPTOR_DIM} values, got {len(sample)}")
        out.extend(struct.pack(f'<{DESCRIPTOR_DIM}f', *(float(v) for v in sample)))
    return bytes(out)


def unpack_descriptors(blob: bytes) -> List[List[float]]:
    """Reverse of pack_descriptors."""
    stride = DESCRIPTOR_DIM * 4
    return [
        list(struct.unpack_from(f'<{DESCRIPTOR_DIM}f', blob, offset))
        for offset in range(0, len(blob) - stride + 1, stride)
    ]


# ── Distance ────────────────────────────────────────────────────────────────

def euclidean_distance(a: List[float], b: List[float]) -> float:
    """Same metric face-api.js uses, so client-side and server-side agree."""
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def best_distance(probe: List[float], stored: List[List[float]]) -> Optional[float]:
    """Closest match across all enrolled samples."""
    if not stored:
        return None
    return min(euclidean_distance(probe, sample) for sample in stored)


# ── Validation ──────────────────────────────────────────────────────────────

def validate_descriptor(value) -> Tuple[bool, str, Optional[List[float]]]:
    """Reject anything that is not a plausible face-api.js descriptor.

    This is the trust boundary: the payload arrives from a browser, so it is
    checked for shape and magnitude before it reaches the matcher.
    """
    if not isinstance(value, (list, tuple)):
        return False, 'Descriptor must be an array of numbers.', None

    if len(value) != DESCRIPTOR_DIM:
        return False, f'Descriptor must have exactly {DESCRIPTOR_DIM} values.', None

    try:
        vec = [float(v) for v in value]
    except (TypeError, ValueError):
        return False, 'Descriptor contains non-numeric values.', None

    if any(v != v or v in (float('inf'), float('-inf')) for v in vec):   # NaN / inf
        return False, 'Descriptor contains invalid numbers.', None

    # A real face-api.js descriptor is roughly unit-norm; anything far off is
    # fabricated or corrupt. An all-zero vector would otherwise sit suspiciously
    # close to everything.
    norm = sum(v * v for v in vec) ** 0.5
    if not (0.5 <= norm <= 2.0):
        return False, 'Descriptor does not look like a face vector.', None

    return True, '', vec


def check_enroll_consistency(samples: List[List[float]]) -> Tuple[bool, str]:
    """All enrolment samples should be the same person."""
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            if euclidean_distance(samples[i], samples[j]) > ENROLL_CONSISTENCY_MAX:
                return False, ('The captured samples do not look like the same person. '
                               'Please stay in frame and try again.')
    return True, ''


# ── Rate limiting ───────────────────────────────────────────────────────────

def is_locked_out(identifier: str) -> Tuple[bool, int]:
    """Return (locked, minutes_remaining)."""
    entry = _failed_attempts.get(identifier)
    if not entry:
        return False, 0

    count, first = entry
    elapsed = datetime.utcnow() - first

    if elapsed > timedelta(minutes=LOCKOUT_MINUTES):
        _failed_attempts.pop(identifier, None)
        return False, 0

    if count >= MAX_FAILED_ATTEMPTS:
        remaining = LOCKOUT_MINUTES - int(elapsed.total_seconds() // 60)
        return True, max(1, remaining)

    return False, 0


def record_failure(identifier: str):
    entry = _failed_attempts.get(identifier)
    if entry and datetime.utcnow() - entry[1] <= timedelta(minutes=LOCKOUT_MINUTES):
        entry[0] += 1
    else:
        _failed_attempts[identifier] = [1, datetime.utcnow()]


def clear_failures(identifier: str):
    _failed_attempts.pop(identifier, None)


# ── Audit ───────────────────────────────────────────────────────────────────

def log_attempt(identifier, user_id=None, success=False, distance=None,
                reason=None, ip_address=None):
    """Record every face-login attempt. Failures matter more than successes."""
    try:
        db.session.add(FaceLoginAttempt(
            user_id=user_id,
            identifier=(identifier or '')[:100],
            success=success,
            distance=distance,
            reason=(reason or '')[:100] or None,
            ip_address=(ip_address or '')[:45] or None,
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"⚠️ Could not log face login attempt: {e}")


# ── Frame decoding ──────────────────────────────────────────────────────────

MAX_FRAME_BYTES = 400 * 1024        # a 480x360 JPEG is ~30 KB; this is generous


def decode_frame(data_url_or_b64: str):
    """Decode a client-supplied JPEG into BGR. Returns None on anything odd."""
    if not isinstance(data_url_or_b64, str) or not data_url_or_b64:
        return None
    try:
        payload = data_url_or_b64.split(',', 1)[1] if ',' in data_url_or_b64 else data_url_or_b64
        raw = base64.b64decode(payload, validate=False)
        if not raw or len(raw) > MAX_FRAME_BYTES:
            return None
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.shape[0] < 80 or frame.shape[1] < 80:
            return None
        return frame
    except Exception:
        return None


def _vision():
    """A shared vision engine for face boxes and head pose.

    Reuses the same OpenVINO models the proctoring pipeline compiles, so this
    adds no model-loading cost.
    """
    from backend.services.proctor_vision.openvino_vision import OpenVINOProctor, ProctorState
    global _VISION
    try:
        return _VISION
    except NameError:
        pass
    _VISION = OpenVINOProctor(
        ProctorState(session_id='face-auth'),
        feature_flags={'camera_enabled': True, 'face_detection': True,
                       'head_pose': True, 'sensitivity': 'medium'},
    )
    return _VISION


# ── The gate: liveness, then identity ──────────────────────────────────────

def process_liveness_sequence(steps, challenge) -> Tuple[bool, str, dict]:
    """Validate a completed challenge and prove the subject is live.

    ``steps`` is the client's submission: one entry per challenge action, each
    with a JPEG frame, the face-api.js descriptor computed for it, and a
    timestamp. Everything that decides the outcome is recomputed here — the
    client's own geometry is only ever used as a hint.

    Returns (live, reason, detail) where detail carries the descriptors that
    passed, for the caller to match or enrol.
    """
    expected = challenge['actions']

    if not isinstance(steps, list) or len(steps) != len(expected):
        return False, 'wrong_step_count', {}

    vision = _vision()
    if vision.hp_compiled is None:
        # Without head pose there is no challenge-response layer, and the
        # passive heuristics alone are not enough to authenticate. Fail closed.
        return False, 'liveness_unavailable_no_head_pose_model', {}

    per_frame, descriptors, grays, timestamps = [], [], [], []
    ref_yaw = ref_pitch = None

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, f'step_{index}_malformed', {}

        action = step.get('action')
        if action != expected[index]:
            return False, f'step_{index}_wrong_action', {}

        frame = decode_frame(step.get('frame'))
        if frame is None:
            return False, f'step_{index}_bad_frame', {}

        ok, message, descriptor = validate_descriptor(step.get('descriptor'))
        if not ok:
            return False, f'step_{index}_bad_descriptor', {'message': message}

        # Server-side face box — the client does not get to say where the face is
        box = vision._detect_face(frame)
        if box is None:
            return False, f'step_{index}_no_face_detected', {}

        x1, y1, x2, y2 = [int(v) for v in box]
        crop = frame[max(0, y1):y2, max(0, x1):x2]
        if crop.size == 0:
            return False, f'step_{index}_empty_crop', {}

        # Server-side head pose — this is what verifies the challenge
        try:
            yaw, pitch, _roll = vision._estimate_head_pose(crop)
        except Exception:
            return False, f'step_{index}_head_pose_failed', {}

        if action == LV.ACTION_CENTER:
            ref_yaw, ref_pitch = yaw, pitch

        if ref_yaw is None:
            return False, 'no_center_reference', {}

        moved, why = LV.verify_action(action, yaw, pitch, ref_yaw, ref_pitch)
        if not moved:
            return False, f'step_{index}_action_not_performed:{why}', {}

        pad = LV.analyse_frame_pad(frame, (x1, y1, x2, y2))
        pad['action'] = action
        pad['is_center'] = (action == LV.ACTION_CENTER)
        pad['yaw'] = round(float(yaw), 1)
        pad['pitch'] = round(float(pitch), 1)
        per_frame.append(pad)

        descriptors.append(descriptor)
        # Temporal comparison uses the face crop only — hashing the whole frame
        # lets a static background swamp the head movement we are looking for.
        grays.append(cv2.cvtColor(cv2.resize(crop, (96, 96)), cv2.COLOR_BGR2GRAY))
        try:
            timestamps.append(float(step.get('t') or 0.0) / 1000.0)
        except (TypeError, ValueError):
            timestamps.append(0.0)

    # The same person must be present throughout the whole sequence. Compared at
    # the cross-pose bar, not the frontal one.
    for i in range(len(descriptors)):
        for j in range(i + 1, len(descriptors)):
            if euclidean_distance(descriptors[i], descriptors[j]) > SEQUENCE_CONSISTENCY_MAX:
                return False, 'person_changed_mid_sequence', {'per_frame': per_frame}

    # Not one still image sent repeatedly
    temporal_ok, temporal_reason, temporal_detail = LV.check_temporal(grays, timestamps)
    if not temporal_ok:
        return False, f'temporal:{temporal_reason}', {
            'per_frame': per_frame, 'temporal': temporal_detail}

    # Passive presentation-attack verdict
    live, verdict_reason, verdict_detail = LV.liveness_verdict(per_frame)

    detail = {
        'per_frame': per_frame,
        'temporal': temporal_detail,
        'verdict': verdict_detail,
        'descriptors': descriptors,
        # index of the frontal capture — the one identity is judged on
        'center_index': next((i for i, f in enumerate(per_frame)
                              if f.get('is_center')), 0),
    }

    if not live:
        return False, verdict_reason, detail

    return True, 'live', detail


# ── Public API ──────────────────────────────────────────────────────────────


def audit(subject_id, action, actor=None, reason=None, sample_count=None,
          liveness_score=None, ip_address=None):
    """Permanent record of a face-template change."""
    try:
        db.session.add(FaceAuditLog(
            subject_user_id=subject_id,
            actor_user_id=getattr(actor, 'id', None),
            actor_role=getattr(actor, 'role', None),
            action=action,
            reason=(reason or None),
            sample_count=sample_count,
            liveness_score=liveness_score,
            ip_address=(ip_address or '')[:45] or None,
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Could not write face audit log: {e}")


def enrollment_locked(user: User) -> bool:
    """True when this user already has a locked enrolment they cannot replace."""
    record = FaceEnrollment.query.filter_by(user_id=user.id).first()
    return bool(record and record.descriptors and record.is_locked)


def enroll_face(user: User, samples: List[List[float]], actor=None,
                liveness_score=None, liveness_method=None,
                ip_address=None, force=False) -> Tuple[bool, str]:
    """Store a user's enrolled face. Returns (ok, message).

    Enrolment is one-shot. A locked record can only be replaced with
    ``force=True``, which the routes permit exclusively for faculty/admin.
    """
    record = FaceEnrollment.query.filter_by(user_id=user.id).first()

    if record and record.descriptors and record.is_locked and not force:
        return False, ('Your face is already registered and cannot be changed. '
                       'Ask your faculty or an administrator if it needs to be reset.')

    if len(samples) < MIN_ENROLL_SAMPLES:
        return False, f'Need at least {MIN_ENROLL_SAMPLES} captures to enrol a face.'

    samples = samples[:MAX_ENROLL_SAMPLES]

    ok, message = check_enroll_consistency(samples)
    if not ok:
        return False, message

    try:
        if record is None:
            record = FaceEnrollment(user_id=user.id)
            db.session.add(record)

        record.descriptors = pack_descriptors(samples)
        record.sample_count = len(samples)
        record.is_active = True
        record.is_locked = True                  # locked the moment it is set
        record.fail_count = 0
        record.enrolled_by = getattr(actor, 'id', None) or user.id
        record.liveness_score = liveness_score
        record.liveness_method = liveness_method
        if force:
            record.reset_by = getattr(actor, 'id', None)
            record.reset_at = datetime.utcnow()
        db.session.commit()

        audit(user.id, 'reset' if force else 'enrolled', actor=actor,
              sample_count=len(samples), liveness_score=liveness_score,
              ip_address=ip_address,
              reason=('faculty/admin re-enrolment' if force
                      else 'self-enrolment after liveness check'))

        return True, f'Face registered from {len(samples)} live captures.'
    except Exception as e:
        db.session.rollback()
        return False, f'Could not save face enrolment: {e}'


def reset_enrollment(subject: User, actor: User, reason: str = None,
                     ip_address: str = None) -> Tuple[bool, str]:
    """Clear a student's face so they can enrol again. Staff only.

    The caller performs the role check; this records who authorised it.
    """
    try:
        record = FaceEnrollment.query.filter_by(user_id=subject.id).first()
        if not record:
            return False, 'That student has no registered face.'

        db.session.delete(record)
        db.session.commit()

        audit(subject.id, 'reset_cleared', actor=actor, reason=reason,
              ip_address=ip_address)
        return True, (f"Face registration cleared for "
                      f"{subject.full_name or subject.username}. They will be asked "
                      "to register again on next sign-in.")
    except Exception as e:
        db.session.rollback()
        return False, f'Could not reset face registration: {e}'


def verify_face(user: User, probe: List[float],
                threshold: float = None) -> Tuple[bool, Optional[float], str]:
    """Match a probe descriptor against a user's enrolment.

    Returns (matched, distance, reason).
    """
    record = FaceEnrollment.query.filter_by(user_id=user.id, is_active=True).first()
    if record is None or not record.descriptors:
        return False, None, 'not_enrolled'

    stored = unpack_descriptors(record.descriptors)
    distance = best_distance(probe, stored)
    if distance is None:
        return False, None, 'no_samples'

    matched = distance <= (MATCH_THRESHOLD if threshold is None else threshold)

    try:
        if matched:
            record.success_count = (record.success_count or 0) + 1
            record.last_used_at = datetime.utcnow()
        else:
            record.fail_count = (record.fail_count or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()

    return matched, distance, 'match' if matched else 'distance_above_threshold'


def delete_enrollment(user: User, actor=None, ip_address=None) -> Tuple[bool, str]:
    """Remove face data. Refused for a locked enrolment unless staff ask.

    A student must not be able to delete and re-register their own face — that
    would reopen exactly the substitution hole the lock exists to close.
    """
    record = FaceEnrollment.query.filter_by(user_id=user.id).first()
    if not record:
        return True, 'No face data to delete.'

    is_staff = getattr(actor, 'role', None) in ('faculty', 'admin')
    if record.is_locked and not is_staff:
        return False, ('Your face registration is locked. Ask your faculty or an '
                       'administrator to reset it.')

    try:
        db.session.delete(record)
        db.session.commit()
        audit(user.id, 'deleted', actor=actor, ip_address=ip_address)
        return True, 'Face data deleted.'
    except Exception:
        db.session.rollback()
        return False, 'Could not delete face data.'


def enrollment_info(user: User) -> dict:
    record = FaceEnrollment.query.filter_by(user_id=user.id).first()
    if not record:
        return {'enrolled': False}
    return {
        'enrolled': bool(record.is_active),
        'locked': bool(record.is_locked),
        'liveness_score': record.liveness_score,
        'sample_count': record.sample_count,
        'bytes_stored': len(record.descriptors or b''),
        'created_at': record.created_at.isoformat() if record.created_at else None,
        'last_used_at': record.last_used_at.isoformat() if record.last_used_at else None,
    }
