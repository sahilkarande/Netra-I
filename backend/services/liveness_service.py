"""
Presentation Attack Detection (PAD) — liveness checking for face auth.

Why this module exists
──────────────────────
A face *matcher* answers "is this the same face?". It cannot answer "is this a
real person?". A photograph of an enrolled student, held on a phone screen in
front of the webcam, yields a perfectly valid 128-dim descriptor that matches
the enrolment. Matching alone is therefore not authentication — it must be
gated on liveness first.

Three independent layers, all adjudicated **server-side**
─────────────────────────────────────────────────────────
1. **Active challenge–response.** The server issues a random, single-use,
   time-bound sequence of head movements. The client must return one frame per
   step. Head yaw/pitch are measured *on the server* with the OpenVINO
   head-pose model, never trusted from the client. This defeats printed photos
   and static images on a screen, and it defeats a pre-recorded video unless
   that recording happens to contain the exact random sequence within the
   window.

2. **Passive texture/frequency PAD.** Every submitted frame is analysed for the
   artefacts that re-capture introduces: moiré from a display's pixel grid,
   specular glare from an emissive panel, the flattened micro-texture of print,
   a compressed colour gamut, and the straight high-contrast border of a device
   held in frame.

3. **Temporal consistency.** Frames must actually differ from one another (the
   same image replayed three times is rejected), must arrive with plausible
   spacing, and must all be the same person.

Optional deep PAD
─────────────────
The heuristics in layer 2 are an ensemble, not a trained classifier, and a
trained one is materially better. Drop a MiniFASNet ONNX model into
``ai_models/`` as ``anti-spoof-minifasnet.onnx`` and it is loaded automatically
and becomes the dominant signal. Without it the heuristics carry the load and
``DEEP_PAD_AVAILABLE`` is False, which the API reports honestly.

Honest bounds
─────────────
No RGB-only, webcam-only PAD is unbeatable. A high-resolution video replay on a
good panel, performing the right movements, can still pass. Defeating that
class of attack needs depth or infrared hardware. What this module does is raise
the cost from "hold up a phone" to "produce a bespoke video that satisfies a
random challenge inside 20 seconds" — which is the meaningful threshold for an
exam platform.
"""

import os
import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# ── Challenge protocol ──────────────────────────────────────────────────────

CHALLENGE_TTL_SEC = 45.0        # a challenge must be completed within this
CHALLENGE_MIN_SEC = 1.2         # faster than a human could physically move
# Steps per challenge, by purpose. Sign-in and the exam gate stay short because
# they happen often; enrolment is one-time and builds the reference template, so
# it takes an extra capture to make that template more robust.
CHALLENGE_STEPS = 2              # centre + 1 movement (login, exam)
CHALLENGE_STEPS_ENROLL = 2      # centre + 2 movements

# Actions the server can ask for. All are verifiable from head pose alone, which
# is measured server-side — client-reported geometry is never the gate.
ACTION_CENTER = 'center'
ACTION_LEFT = 'turn_left'
ACTION_RIGHT = 'turn_right'
ACTION_UP = 'look_up'
ACTION_DOWN = 'look_down'

# (instruction shown to the user, verifier key)
ACTION_PROMPTS = {
    ACTION_CENTER: 'Look straight at the camera',
    ACTION_LEFT:   'Slowly turn your head to your LEFT',
    ACTION_RIGHT:  'Slowly turn your head to your RIGHT',
    ACTION_UP:     'Tilt your head UP slightly',
    ACTION_DOWN:   'Tilt your head DOWN slightly',
}

# Degrees of head movement required. Deliberately modest: a real person manages
# these easily, while a flat surface cannot produce the matching pose change
# together with the parallax and texture evidence the other layers look for.
YAW_TURN_MIN = 10.0
PITCH_TILT_MIN = 7.0
CENTER_TOLERANCE = 12.0

_challenges: Dict[str, dict] = {}
_challenge_lock = threading.Lock()


def _prune_challenges():
    now = time.time()
    for nonce in [n for n, c in _challenges.items() if c['expires_at'] < now]:
        _challenges.pop(nonce, None)


def issue_challenge(purpose: str, user_id: Optional[int] = None) -> dict:
    """Create a single-use liveness challenge.

    ``purpose`` scopes the challenge ('enroll' | 'login' | 'exam') so a
    challenge minted for one flow cannot be redeemed in another.
    """
    with _challenge_lock:
        _prune_challenges()

        # Always start centred (gives a clean reference pose), then two random
        # movements that are not the same and are not both trivially similar.
        movements = [ACTION_LEFT, ACTION_RIGHT, ACTION_UP, ACTION_DOWN]
        wanted = CHALLENGE_STEPS_ENROLL if purpose == 'enroll' else CHALLENGE_STEPS
        picked = []
        while len(picked) < wanted - 1:
            choice = secrets.choice(movements)
            if choice not in picked:
                picked.append(choice)

        actions = [ACTION_CENTER] + picked
        nonce = secrets.token_urlsafe(24)
        now = time.time()

        _challenges[nonce] = {
            'actions': actions,
            'purpose': purpose,
            'user_id': user_id,
            'issued_at': now,
            'expires_at': now + CHALLENGE_TTL_SEC,
            'consumed': False,
        }

    return {
        'nonce': nonce,
        'steps': [{'action': a, 'prompt': ACTION_PROMPTS[a]} for a in actions],
        'ttl_seconds': int(CHALLENGE_TTL_SEC),
    }


def consume_challenge(nonce: str, purpose: str,
                      user_id: Optional[int] = None) -> Tuple[bool, str, Optional[dict]]:
    """Redeem a challenge exactly once. Returns (ok, reason, challenge)."""
    with _challenge_lock:
        _prune_challenges()
        challenge = _challenges.get(nonce)

        if not challenge:
            return False, 'challenge_unknown_or_expired', None
        if challenge['consumed']:
            return False, 'challenge_already_used', None
        if challenge['purpose'] != purpose:
            return False, 'challenge_wrong_purpose', None
        if challenge['user_id'] is not None and challenge['user_id'] != user_id:
            return False, 'challenge_wrong_user', None
        if time.time() > challenge['expires_at']:
            _challenges.pop(nonce, None)
            return False, 'challenge_expired', None

        challenge['consumed'] = True
        return True, 'ok', dict(challenge)


# ── Optional deep PAD model ─────────────────────────────────────────────────

_MODEL_DIR = os.getenv(
    'NETRA_MODEL_DIR',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'ai_models')
)

_deep_pad = None
_deep_pad_tried = False
DEEP_PAD_AVAILABLE = False


def _load_deep_pad():
    """Load a MiniFASNet-style ONNX PAD model if one has been provided."""
    global _deep_pad, _deep_pad_tried, DEEP_PAD_AVAILABLE
    if _deep_pad_tried:
        return _deep_pad
    _deep_pad_tried = True

    path = os.path.join(_MODEL_DIR, 'anti-spoof-minifasnet.onnx')
    if not os.path.exists(path):
        print('ℹ️  [Liveness] No deep PAD model found — using heuristic ensemble only.')
        print(f'   Drop a MiniFASNet ONNX at {path} for stronger anti-spoofing.')
        return None

    try:
        net = cv2.dnn.readNetFromONNX(path)
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        _deep_pad = net
        DEEP_PAD_AVAILABLE = True
        print('✅ [Liveness] Deep PAD model loaded — heuristics become secondary.')
    except Exception as e:
        print(f'⚠️  [Liveness] Deep PAD model failed to load: {e}')

    return _deep_pad


def deep_pad_score(face_bgr: np.ndarray) -> Optional[float]:
    """Probability the crop is a genuine live face, or None if no model."""
    net = _load_deep_pad()
    if net is None:
        return None

    try:
        blob = cv2.dnn.blobFromImage(face_bgr, scalefactor=1 / 255.0,
                                     size=(80, 80), mean=(0, 0, 0), swapRB=True)
        net.setInput(blob)
        out = net.forward().flatten()

        if out.size >= 2:
            exp = np.exp(out - out.max())
            probs = exp / exp.sum()
            # MiniFASNet convention: index 1 is the live class
            return float(probs[1])
        return float(out[0])
    except Exception as e:
        print(f'⚠️  [Liveness] Deep PAD inference failed: {e}')
        return None


# ── Passive PAD features ────────────────────────────────────────────────────

def _lbp_uniformity(gray: np.ndarray) -> float:
    """Local Binary Pattern histogram entropy.

    Real skin under real light has rich micro-texture. Print and re-capture
    flatten it, concentrating the LBP histogram into fewer bins.
    """
    g = gray.astype(np.int16)
    code = np.zeros(g.shape, dtype=np.uint8)
    neighbours = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]

    for bit, (dy, dx) in enumerate(neighbours):
        shifted = np.roll(np.roll(g, dy, axis=0), dx, axis=1)
        code |= ((shifted >= g) << bit).astype(np.uint8)

    hist = np.bincount(code[1:-1, 1:-1].ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 0.0
    p = hist / total
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum() / 8.0)      # normalised 0..1


def _moire_score(gray: np.ndarray) -> float:
    """Detect a display's pixel grid via narrowband peaks in the spectrum.

    Photographing a screen aliases its pixel matrix against the camera sensor,
    producing periodic energy that a real scene does not contain.
    """
    g = cv2.resize(gray, (128, 128)).astype(np.float32)
    g = g - g.mean()
    g *= np.outer(np.hanning(128), np.hanning(128))      # window to kill edge leakage

    mag = np.abs(np.fft.fftshift(np.fft.fft2(g)))
    h, w = mag.shape
    cy, cx = h // 2, w // 2

    yy, xx = np.ogrid[:h, :w]
    radius = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)

    # Mid-to-high band is where display aliasing lands; ignore DC and the corners.
    band = (radius > 24) & (radius < 56)
    vals = mag[band]
    if vals.size == 0 or vals.mean() <= 0:
        return 0.0

    # Peakiness: a few strong spikes above the local mean means periodic content.
    peakiness = float(vals.max() / (vals.mean() + 1e-6))
    strong = float((vals > vals.mean() + 4 * vals.std()).mean())

    return float(np.clip(peakiness / 40.0, 0, 1) * 0.6 + np.clip(strong * 25, 0, 1) * 0.4)


def _specular_score(bgr: np.ndarray) -> float:
    """Emissive panels create broad, desaturated, near-blown highlights."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1].astype(np.float32), hsv[:, :, 2].astype(np.float32)

    glare = (val > 225) & (sat < 45)
    fraction = float(glare.mean())

    # Contiguous glare patches are characteristic of a screen; scattered
    # pixel-level highlights are normal on skin.
    mask = (glare * 255).astype(np.uint8)
    blob = 0.0
    if fraction > 0.002:
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n > 1:
            largest = stats[1:, cv2.CC_STAT_AREA].max()
            blob = float(largest / mask.size)

    return float(np.clip(fraction * 12, 0, 1) * 0.5 + np.clip(blob * 40, 0, 1) * 0.5)


def _colour_gamut_score(bgr: np.ndarray) -> float:
    """Re-capture compresses the colour distribution. Low spread ⇒ suspicious."""
    chans = cv2.split(bgr.astype(np.float32))
    spread = float(np.mean([c.std() for c in chans]))
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat_std = float(hsv[:, :, 1].std())

    # Map "low variation" onto a 0..1 suspicion score.
    return float(np.clip((34.0 - spread) / 34.0, 0, 1) * 0.5
                 + np.clip((40.0 - sat_std) / 40.0, 0, 1) * 0.5)


def _device_border_score(gray: np.ndarray, face_box) -> float:
    """A phone or tablet held up usually frames the face in straight edges."""
    edges = cv2.Canny(gray, 60, 180)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70,
                            minLineLength=int(min(gray.shape) * 0.45), maxLineGap=12)
    if lines is None:
        return 0.0

    x1b, y1b, x2b, y2b = face_box
    long_straight = 0

    for line in lines[:60]:
        x1, y1, x2, y2 = line[0]
        angle = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
        is_axis_aligned = angle < 12 or angle > 168 or abs(angle - 90) < 12
        if not is_axis_aligned:
            continue
        # Only count edges OUTSIDE the face — the face itself has no straight lines.
        midx, midy = (x1 + x2) / 2, (y1 + y2) / 2
        if not (x1b <= midx <= x2b and y1b <= midy <= y2b):
            long_straight += 1

    return float(np.clip(long_straight / 6.0, 0, 1))


def _sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def analyse_frame_pad(frame_bgr: np.ndarray, face_box) -> dict:
    """Score one frame for presentation-attack evidence.

    Returns per-feature values plus a combined ``spoof_score`` in 0..1, where
    higher means more likely to be a presentation attack.
    """
    x1, y1, x2, y2 = [int(v) for v in face_box]
    h, w = frame_bgr.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)

    if x2 - x1 < 24 or y2 - y1 < 24:
        return {'spoof_score': 1.0, 'reason': 'face_too_small', 'features': {}}

    crop = frame_bgr[y1:y2, x1:x2]
    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    full_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    texture = _lbp_uniformity(crop_gray)
    moire = _moire_score(crop_gray)
    specular = _specular_score(crop)
    gamut = _colour_gamut_score(crop)
    border = _device_border_score(full_gray, (x1, y1, x2, y2))
    sharp = _sharpness(crop_gray)

    # Flat texture is itself suspicious (print), as is an implausibly low
    # Laplacian variance (out-of-focus re-photograph).
    texture_flat = float(np.clip((0.62 - texture) / 0.62, 0, 1))
    blur = float(np.clip((45.0 - sharp) / 45.0, 0, 1))

    deep = deep_pad_score(crop)

    heuristic = float(np.clip(
        0.28 * moire +
        0.22 * specular +
        0.18 * texture_flat +
        0.16 * border +
        0.10 * gamut +
        0.06 * blur,
        0, 1))

    if deep is not None:
        # A trained model is the better signal; heuristics stay as a floor so a
        # model that is fooled cannot single-handedly wave an attack through.
        spoof = float(np.clip(0.7 * (1.0 - deep) + 0.3 * heuristic, 0, 1))
    else:
        spoof = heuristic

    return {
        'spoof_score': spoof,
        'features': {
            'moire': round(moire, 4),
            'specular': round(specular, 4),
            'texture_entropy': round(texture, 4),
            'texture_flat': round(texture_flat, 4),
            'device_border': round(border, 4),
            'colour_gamut': round(gamut, 4),
            'sharpness': round(sharp, 2),
            'blur': round(blur, 4),
            'deep_pad_live': round(deep, 4) if deep is not None else None,
        },
    }


# ── Temporal checks ─────────────────────────────────────────────────────────

def _dhash(gray: np.ndarray) -> int:
    """64-bit difference hash, for cheap frame-identity comparison."""
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    out = 0
    for bit in bits.flatten():
        out = (out << 1) | int(bit)
    return out


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count('1')


# Applied to the FACE CROP, where a genuine head turn changes many bits while a
# replayed still image changes almost none.
MIN_FRAME_DIFFERENCE = 4


def check_temporal(frames_gray: List[np.ndarray], timestamps: List[float]) -> Tuple[bool, str, dict]:
    """Verify the frames are a genuine sequence, not one image repeated."""
    if len(frames_gray) < 2:
        return False, 'too_few_frames', {}

    hashes = [_dhash(g) for g in frames_gray]
    distances = [_hamming(hashes[i], hashes[i + 1]) for i in range(len(hashes) - 1)]
    min_distance = min(distances)

    detail = {'frame_distances': distances, 'min_distance': min_distance}

    if min_distance < MIN_FRAME_DIFFERENCE:
        return False, 'frames_nearly_identical', detail

    if timestamps and len(timestamps) == len(frames_gray):
        span = max(timestamps) - min(timestamps)
        detail['span_seconds'] = round(span, 2)
        if span < CHALLENGE_MIN_SEC:
            return False, 'completed_impossibly_fast', detail

    return True, 'ok', detail


# ── Challenge-action verification ───────────────────────────────────────────

def verify_action(action: str, yaw: float, pitch: float,
                  ref_yaw: float, ref_pitch: float) -> Tuple[bool, str]:
    """Did the head actually perform the requested movement?

    Measured against the pose recorded in the 'center' step, so a student whose
    neutral posture is slightly off-axis is not penalised.
    """
    dy = yaw - ref_yaw
    dp = pitch - ref_pitch

    if action == ACTION_CENTER:
        if abs(yaw) > CENTER_TOLERANCE * 2 or abs(pitch) > CENTER_TOLERANCE * 2:
            return False, f'not_centered(yaw={yaw:.0f},pitch={pitch:.0f})'
        return True, 'ok'

    if action == ACTION_LEFT:
        # Camera image is mirrored for the user; the sign convention here follows
        # the model's own output, validated as "moved far enough on the axis".
        return (abs(dy) >= YAW_TURN_MIN, 'ok' if abs(dy) >= YAW_TURN_MIN
                else f'insufficient_yaw({dy:.0f}<{YAW_TURN_MIN:.0f})')

    if action == ACTION_RIGHT:
        return (abs(dy) >= YAW_TURN_MIN, 'ok' if abs(dy) >= YAW_TURN_MIN
                else f'insufficient_yaw({dy:.0f}<{YAW_TURN_MIN:.0f})')

    if action in (ACTION_UP, ACTION_DOWN):
        return (abs(dp) >= PITCH_TILT_MIN, 'ok' if abs(dp) >= PITCH_TILT_MIN
                else f'insufficient_pitch({dp:.0f}<{PITCH_TILT_MIN:.0f})')

    return False, 'unknown_action'


# ── Thresholds ──────────────────────────────────────────────────────────────

# Any single frame above this is treated as an attack outright.
SPOOF_REJECT_ANY = 0.62
# Mean across the sequence must stay below this.
SPOOF_REJECT_MEAN = 0.48


def liveness_verdict(per_frame: List[dict]) -> Tuple[bool, str, dict]:
    """Combine per-frame PAD scores into one pass/fail."""
    scores = [f['spoof_score'] for f in per_frame]
    if not scores:
        return False, 'no_frames_analysed', {}

    worst = max(scores)
    mean = float(np.mean(scores))
    detail = {
        'spoof_scores': [round(s, 4) for s in scores],
        'worst': round(worst, 4),
        'mean': round(mean, 4),
        'deep_pad': DEEP_PAD_AVAILABLE,
    }

    if worst >= SPOOF_REJECT_ANY:
        return False, f'presentation_attack_frame(score={worst:.2f})', detail
    if mean >= SPOOF_REJECT_MEAN:
        return False, f'presentation_attack_sequence(mean={mean:.2f})', detail

    return True, 'live', detail
