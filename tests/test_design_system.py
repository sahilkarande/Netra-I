"""Design-system guardrails and the redesigned faculty dashboard.

- every text colour pairing in tokens.css meets WCAG AA in both themes
- the two dark-theme blocks in tokens.css stay identical
- redesigned files use tokens, never literal colours
- the dashboard renders on the new shell, with its data and states
"""

import os
import re
from datetime import datetime, timedelta

import pytest

from backend.database import db
from backend.models import Exam, StudentExam, User
from tests.conftest import login

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DS = os.path.join(ROOT, 'frontend', 'static', 'css', 'ds')
TOKENS = os.path.join(DS, 'tokens.css')

# Files that belong to the design system or have been redesigned on it
REDESIGNED = [
    'frontend/templates/layouts/app.html',
    'frontend/templates/partials/sidebar.html',
    'frontend/templates/partials/topbar_end.html',
    'frontend/templates/partials/toasts.html',
    'frontend/templates/partials/theme_init.html',
    'frontend/templates/components/ui.html',
    'frontend/templates/faculty/dashboard.html',
    'frontend/static/css/ds/base.css',
    'frontend/static/css/ds/layout.css',
    'frontend/static/css/ds/components.css',
    'frontend/static/js/ds/app.js',
    'frontend/static/js/pages/faculty_dashboard.js',
]


# ── Tokens ──────────────────────────────────────────────────────────────────

def _blocks():
    css = open(TOKENS).read()
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    light = css[css.index(':root {'):css.index('@media (prefers-color-scheme: dark)')]
    media = css[css.index('@media (prefers-color-scheme: dark)'):css.index(':root[data-theme="dark"]')]
    forced = css[css.index(':root[data-theme="dark"]'):]
    return light, media, forced


def _decls(block):
    return dict(re.findall(r'(--[\w-]+)\s*:\s*([^;]+);', block))


def _resolve(name, scope, primitives, depth=0):
    value = scope.get(name, primitives.get(name, '')).strip()
    m = re.fullmatch(r'var\((--[\w-]+)\)', value)
    if m and depth < 5:
        return _resolve(m.group(1), scope, primitives, depth + 1)
    return value


def _luminance(hex_colour):
    h = hex_colour.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


TEXT_PAIRS = [
    ('--text', '--bg-surface'), ('--text', '--bg-canvas'), ('--text', '--bg-raised'),
    ('--text-muted', '--bg-surface'), ('--text-muted', '--bg-canvas'),
    ('--text-subtle', '--bg-surface'), ('--text-subtle', '--bg-canvas'), ('--text-subtle', '--bg-subtle'),
    ('--accent', '--bg-surface'), ('--text-on-accent', '--accent'),
    ('--success', '--bg-surface'), ('--warning', '--bg-surface'), ('--danger', '--bg-surface'),
]


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('fg,bg', TEXT_PAIRS)
def test_text_tokens_meet_wcag_aa(theme, fg, bg):
    light, _media, forced = _blocks()
    primitives = _decls(light)
    scope = dict(primitives)
    if theme == 'dark':
        scope.update(_decls(forced))
    a, b = _resolve(fg, scope, primitives), _resolve(bg, scope, primitives)
    assert re.fullmatch(r'#[0-9A-Fa-f]{6}', a) and re.fullmatch(r'#[0-9A-Fa-f]{6}', b), (fg, a, bg, b)
    ratio = _contrast(a, b)
    assert ratio >= 4.5, f'{theme}: {fg} {a} on {bg} {b} is {ratio:.2f}:1'


def test_dark_blocks_are_identical():
    """The device-preference and forced dark blocks must never drift apart."""
    _light, media, forced = _blocks()
    assert _decls(media) == _decls(forced)


def test_every_semantic_token_has_a_light_value():
    light, _media, forced = _blocks()
    missing = set(_decls(forced)) - set(_decls(light))
    assert not missing, f'dark-only tokens: {sorted(missing)}'


# ── Literal colours ─────────────────────────────────────────────────────────

HEX = re.compile(r'(?<![\w&])#[0-9A-Fa-f]{3,8}\b')
FUNC = re.compile(r'\b(?:rgba?|hsla?)\(')


@pytest.mark.parametrize('rel', REDESIGNED)
def test_redesigned_files_use_tokens_not_colours(rel):
    text = open(os.path.join(ROOT, rel), encoding='utf-8').read()
    text = re.sub(r'/\*.*?\*/|{#.*?#}', '', text, flags=re.S)
    found = HEX.findall(text) + FUNC.findall(text)
    assert not found, f'{rel} hard-codes colours: {found[:5]}'


# ── Dashboard ───────────────────────────────────────────────────────────────

def _dashboard(app, username='faca'):
    client = app.test_client()
    login(client, username)
    resp = client.get('/faculty/dashboard')
    assert resp.status_code == 200
    return resp.data.decode()


def test_dashboard_uses_design_system_shell_only(app, seed):
    html = _dashboard(app)
    assert 'css/ds/tokens.css' in html and 'css/ds/components.css' in html
    assert 'css/style.css' not in html and 'bootstrap.min.css' not in html
    assert '<html lang="en">' in html                          # theme follows the device by default
    assert html.index("localStorage.getItem('netra-theme')") < html.index('css/ds/tokens.css')
    assert '<meta name="csrf-token"' in html


def test_sidebar_navigation(app, seed):
    html = _dashboard(app)
    for path in ('/faculty/dashboard', '/faculty/exam/create', '/faculty/students',
                 '/faculty/student_report', '/global_leaderboard', '/faculty/split-questions'):
        assert f'href="{path}"' in html, path
    assert re.search(r'href="/faculty/dashboard"\s+aria-current="page"', html)
    assert '/admin/sql_console' not in html                     # faculty: no admin section

    admin = _dashboard(app, 'admin1')
    assert 'href="/admin/sql_console"' in admin


def test_dashboard_shows_exam_rows_and_summary(app, seed):
    html = _dashboard(app)
    assert 'SQL Basics' in html
    assert 'id="examSearch"' in html and 'data-exam-view="grid"' in html
    assert 'id="resultsToggle"' in html and 'id="leaderboardToggle"' in html
    assert f"/faculty/exam/{seed['exam']}/delete" in html
    assert 'Score distribution' in html


def test_attention_items_for_live_flagged_and_unpublished(app, seed):
    with app.app_context():
        db.session.add(StudentExam(student_id=seed['s2'], exam_id=seed['exam'],
                                   status='started', started_at=datetime.utcnow()))
        se = db.session.get(StudentExam, seed['attempt'])
        se.total_violations = 4
        db.session.commit()
    html = _dashboard(app)
    assert 'Live: SQL Basics' in html
    assert '1 attempt flagged for review' in html
    assert 'Results not published' in html


def test_reviewed_attempts_are_not_flagged(app, seed):
    with app.app_context():
        se = db.session.get(StudentExam, seed['attempt'])
        se.total_violations = 4
        se.reviewed_at = datetime.utcnow()
        e = db.session.get(Exam, seed['exam'])
        e.results_published = True
        db.session.commit()
    html = _dashboard(app)
    assert 'flagged for review' not in html
    assert 'nx-attention' not in html                           # nothing needs attention


def test_dashboard_empty_state(app, seed):
    html = _dashboard(app, 'facb')                              # owns no exams
    assert 'No exams yet' in html
    assert 'No scores yet' in html


def test_students_cannot_open_dashboard(app, seed):
    client = app.test_client()
    login(client, 'stud1')
    assert client.get('/faculty/dashboard').status_code == 302


# ── dashboard_service ───────────────────────────────────────────────────────

class _E:
    def __init__(self, **kw):
        self.is_active = kw.get('is_active', True)
        self.force_ended = kw.get('force_ended', False)
        self.start_time = kw.get('start_time')
        self.end_time = kw.get('end_time')


@pytest.mark.parametrize('exam,in_progress,expected', [
    (_E(), 0, 'open'),
    (_E(), 2, 'live'),
    (_E(is_active=False), 2, 'inactive'),
    (_E(force_ended=True), 2, 'closed'),
    (_E(end_time=datetime(2020, 1, 1)), 0, 'closed'),
    (_E(start_time=datetime(2999, 1, 1)), 0, 'scheduled'),
])
def test_exam_status(exam, in_progress, expected):
    from backend.services.dashboard_service import exam_status
    assert exam_status(exam, in_progress) == expected


def test_score_distribution_counts_every_submission(app, seed):
    from backend.services.dashboard_service import score_distribution
    with app.app_context():
        for i, pct in enumerate((0, 20, 20.5, 61, 100)):
            u = User(username=f'd{i}', email=f'd{i}@x', password_hash='x', role='student')
            db.session.add(u)
            db.session.flush()
            db.session.add(StudentExam(student_id=u.id, exam_id=seed['exam'], status='submitted',
                                       completed=True, percentage=pct))
        db.session.commit()
        bands = score_distribution([seed['exam']])
    counts = {b['label']: b['count'] for b in bands}
    # seeded attempt is 50%; plus 0, 20 -> 0–20; 20.5 -> 21–40; 61 -> 61–80; 100 -> 81–100
    assert counts == {'0–20': 2, '21–40': 1, '41–60': 1, '61–80': 1, '81–100': 1}
