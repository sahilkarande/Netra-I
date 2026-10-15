"""
Building and validating an Exam from faculty form input.

Why this is a service
─────────────────────
Single-create and batch-create both need the same rules — the same defaults, the
same validation, the same access-key generation. Keeping that in one place is
what stops the two paths drifting apart, which is how settings quietly stop
working (the create form was posting ``exam_mode`` that nothing read, and
promising an access key that was never generated).

Everything a faculty can set is declared once in :data:`FIELD_SPEC`, so adding a
setting later means touching one list rather than hunting through a route.
"""

import json
import secrets
import string
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from backend.models import Exam

ACCESS_KEY_LENGTH = 8
# No O/0/I/1 — these get read aloud in a hall and mistyped.
ACCESS_KEY_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

MIN_DURATION = 1
MAX_DURATION = 600            # 10 hours
MAX_TITLE = 200

SENSITIVITY_PRESETS = {
    'low':    {'no_face_timeout_sec': 8, 'warning_cooldown_sec': 8, 'deviation_min_duration': 8},
    'medium': {'no_face_timeout_sec': 5, 'warning_cooldown_sec': 5, 'deviation_min_duration': 5},
    'high':   {'no_face_timeout_sec': 2, 'warning_cooldown_sec': 2, 'deviation_min_duration': 2},
}


def generate_access_key(length: int = ACCESS_KEY_LENGTH) -> str:
    """A short key a faculty can read out without ambiguity."""
    return ''.join(secrets.choice(ACCESS_KEY_ALPHABET) for _ in range(length))


def parse_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _as_int(value, fallback):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return fallback


def _as_float(value, fallback):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return fallback


def _as_datetime(value) -> Optional[datetime]:
    """Read an ``<input type="datetime-local">`` value."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ── Proctoring ──────────────────────────────────────────────────────────────

def build_proctor_settings(form: Dict) -> dict:
    """Assemble the proctoring block.

    The form posts the whole block as JSON (``proctor_settings_json``). Flat
    field names are still honoured so a simpler caller — a script, a test, a
    future API — does not have to construct JSON.
    """
    settings = dict(Exam.PROCTOR_DEFAULTS)

    raw = form.get('proctor_settings_json')
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                settings.update(parsed)
        except (ValueError, TypeError):
            pass

    for key in ('camera_enabled', 'face_detection', 'head_pose',
                'object_detection', 'voice_detection', 'tab_switch'):
        if key in form:
            settings[key] = parse_bool(form.get(key))

    # Camera off means every camera-derived detector is off too, whatever the
    # client sent — otherwise the exam page advertises checks it cannot run.
    if not settings.get('camera_enabled', True):
        settings['face_detection'] = False
        settings['head_pose'] = False
        settings['object_detection'] = False

    sensitivity = str(settings.get('sensitivity') or 'medium').lower()
    if sensitivity not in SENSITIVITY_PRESETS:
        sensitivity = 'medium'
    settings['sensitivity'] = sensitivity
    for key, value in SENSITIVITY_PRESETS[sensitivity].items():
        settings.setdefault(key, value)

    settings['max_warnings'] = max(1, _as_int(
        form.get('max_warnings', settings.get('max_warnings')), 25))
    settings['max_tab_switches'] = max(0, _as_int(
        form.get('max_tab_switches', settings.get('max_tab_switches')), 5))

    return settings


# ── Validation ──────────────────────────────────────────────────────────────

def validate(form: Dict) -> Tuple[List[str], dict]:
    """Check one exam's input. Returns (errors, cleaned values).

    Errors are collected rather than raised on the first problem, so a faculty
    sees everything wrong with the form at once instead of fixing it one field
    per round trip.
    """
    errors: List[str] = []

    title = (form.get('title') or '').strip()
    if not title:
        errors.append('Exam title is required.')
    elif len(title) > MAX_TITLE:
        errors.append(f'Exam title must be {MAX_TITLE} characters or fewer.')

    duration = _as_int(form.get('duration_minutes'), None)
    if duration is None:
        errors.append('Duration must be a whole number of minutes.')
    elif not (MIN_DURATION <= duration <= MAX_DURATION):
        errors.append(f'Duration must be between {MIN_DURATION} and {MAX_DURATION} minutes.')

    passing = _as_float(form.get('passing_score'), None)
    if passing is None:
        errors.append('Passing score must be a number.')
    elif not (0 <= passing <= 100):
        errors.append('Passing score must be between 0 and 100.')

    mode = (form.get('exam_mode') or 'classroom').strip().lower()
    if mode not in ('classroom', 'home'):
        mode = 'classroom'

    redirect_to = (form.get('post_exam_redirect') or 'results').strip().lower()
    if redirect_to not in ('results', 'dashboard'):
        redirect_to = 'results'

    starts_at = _as_datetime(form.get('start_time'))
    ends_at = _as_datetime(form.get('end_time'))
    if starts_at and ends_at and ends_at <= starts_at:
        errors.append('The exam window must end after it starts.')

    cleaned = {
        'title': title,
        'description': (form.get('description') or '').strip() or None,
        'duration_minutes': duration,
        'passing_score': passing,
        'exam_mode': mode,
        'post_exam_redirect': redirect_to,
        'start_time': starts_at,
        'end_time': ends_at,
        'randomize_questions': parse_bool(form.get('randomize_questions'), True),
        'show_results_immediately': parse_bool(form.get('show_results_immediately'), True),
        'is_active': parse_bool(form.get('is_active'), True),
        'allow_all_students': parse_bool(form.get('allow_all_students'), True),
        'require_access_key': parse_bool(form.get('require_access_key'), True),
        'show_leaderboard': parse_bool(
            form.get('show_leaderboard', form.get('show_leaderboard_check')), True),
    }

    return errors, cleaned


# ── Construction ────────────────────────────────────────────────────────────

def build_exam(form: Dict, creator, defaults_from_user: bool = True) -> Tuple[List[str], Optional[Exam]]:
    """Validate and construct (but do not persist) an Exam."""
    errors, data = validate(form)
    if errors:
        return errors, None

    proctor = build_proctor_settings(form)

    show_leaderboard = data['show_leaderboard']
    results_published = False
    if defaults_from_user:
        if 'show_leaderboard' not in form and 'show_leaderboard_check' not in form:
            show_leaderboard = bool(getattr(creator, 'default_show_leaderboard', True))
        results_published = bool(getattr(creator, 'default_results_published', False))

    exam = Exam(
        title=data['title'],
        description=data['description'],
        duration_minutes=data['duration_minutes'],
        passing_score=data['passing_score'],
        creator_id=creator.id,
        exam_mode=data['exam_mode'],
        is_active=data['is_active'],
        start_time=data['start_time'],
        end_time=data['end_time'],
        allow_all_students=data['allow_all_students'],
        randomize_questions=data['randomize_questions'],
        show_results_immediately=data['show_results_immediately'],
        show_leaderboard=show_leaderboard,
        results_published=results_published,
        post_exam_redirect=data['post_exam_redirect'],
        enable_proctoring=bool(proctor.get('camera_enabled', True)),
        max_violations=proctor.get('max_warnings', 25),
        max_tab_switches=proctor.get('max_tab_switches', 5),
        allow_tab_switch=not proctor.get('tab_switch', True),
        proctor_settings=json.dumps(proctor),
        # Promised by the UI, and previously never generated.
        access_key=generate_access_key() if data['require_access_key'] else None,
    )

    return [], exam


def summarise(exam: Exam) -> dict:
    """Compact description of what was created, for flash messages and JSON."""
    try:
        proctor = exam.get_proctor_settings()
    except Exception:
        proctor = {}

    active = [name for key, name in (
        ('camera_enabled', 'Camera'),
        ('head_pose', 'Head pose'),
        ('object_detection', 'Objects'),
        ('voice_detection', 'Voice'),
        ('tab_switch', 'Tab switch'),
    ) if proctor.get(key)]

    return {
        'id': exam.id,
        'title': exam.title,
        'duration_minutes': exam.duration_minutes,
        'passing_score': exam.passing_score,
        'exam_mode': exam.exam_mode,
        'access_key': exam.access_key,
        'proctoring': active,
        'randomize_questions': bool(exam.randomize_questions),
    }
