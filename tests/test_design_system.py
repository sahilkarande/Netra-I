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
from tests.conftest import PASSWORD, login

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
    'frontend/templates/layouts/auth.html',
    'frontend/templates/login.html',
    'frontend/templates/register.html',
    'frontend/templates/verify_otp.html',
    'frontend/templates/change_password.html',
    'frontend/static/js/pages/login.js',
    'frontend/static/js/pages/auth_forms.js',
    'frontend/static/js/pages/verify_otp.js',
    'frontend/static/js/pages/change_password.js',
    'frontend/templates/layouts/exam.html',
    'frontend/templates/partials/theme_menu.html',
    'frontend/templates/student/exam_key.html',
    'frontend/templates/student/take_exam.html',
    'frontend/static/css/ds/exam.css',
    'frontend/static/js/pages/exam_key.js',
    'frontend/templates/student/dashboard.html',
    'frontend/static/js/pages/student_dashboard.js',
    'frontend/templates/student/result.html',
    'frontend/templates/student/student_profile.html',
    'frontend/static/js/pages/result.js',
    'frontend/static/js/pages/student_profile.js',
    'frontend/templates/faculty/view_exam.html',
    'frontend/static/js/pages/view_exam.js',
    'frontend/templates/faculty/analytics.html',
    'frontend/static/js/pages/analytics.js',
    'frontend/templates/faculty/create_exam.html',
    'frontend/static/js/pages/create_exam.js',
    'frontend/templates/faculty/upload_questions.html',
    'frontend/static/js/pages/upload_questions.js',
    'frontend/templates/faculty/split_questions.html',
    'frontend/static/js/pages/split_questions.js',
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


# ── Sign-in flow ────────────────────────────────────────────────────────────

def _assert_auth_shell(html):
    assert 'css/ds/tokens.css' in html and 'nx-auth__rail' in html
    assert 'css/style.css' not in html and 'bootstrap.min.css' not in html
    assert 'css/auth.css' not in html
    assert '<meta name="csrf-token"' in html


def test_login_page(app):
    html = app.test_client().get('/login').data.decode()
    _assert_auth_shell(html)
    assert 'name="username"' in html and 'name="password"' in html
    assert 'Remember me' not in html                          # logins are never remembered
    assert 'id="capsWarning"' in html and 'id="showHintLink"' in html
    assert 'data-face-login-url="/face/login"' in html
    assert 'js/face_liveness.js' in html


def test_register_page_matches_what_the_server_does(app):
    html = app.test_client().get('/register').data.decode()
    _assert_auth_shell(html)
    assert 'name="role"' not in html and 'employee_id' not in html   # students only
    for name in ('full_name', 'username', 'email', 'prn_number', 'roll_id', 'batch', 'phone', 'password'):
        assert f'name="{name}"' in html, name


def test_register_saves_batch_and_phone(app, monkeypatch):
    monkeypatch.setattr('backend.routes.auth.send_otp_email', lambda *a, **k: True)
    app.test_client().post('/register', data={
        'username': 'newbie', 'email': 'n@example.com', 'password': 'secret123',
        'full_name': 'New Bie', 'batch': 'DBDA-A26', 'phone': '9876543210'})
    with app.app_context():
        u = User.query.filter_by(username='newbie').one()
        assert (u.batch, u.phone) == ('DBDA-A26', '9876543210')


def test_verify_otp_page(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['verify_email'] = 'n@example.com'
    html = client.get('/verify-otp').data.decode()
    _assert_auth_shell(html)
    assert 'n@example.com' in html and 'For user' not in html
    assert html.count('aria-label="Digit') == 6
    assert 'name="otp"' in html


def _first_login_student(app):
    with app.app_context():
        u = User.query.filter_by(username='stud2').one()
        u.password_changed = False
        db.session.commit()
    client = app.test_client()
    login(client, 'stud2')
    return client


def test_first_login_shows_onboarding_steps(app, seed):
    client = _first_login_student(app)
    html = client.get('/change-password').data.decode()
    _assert_auth_shell(html)
    assert 'Set your password' in html and 'Temporary password' in html
    assert 'aria-current="step"' in html and 'nx-steps' in html
    assert 'id="faceCard"' not in html                          # face comes in step 2


def _change(client):
    return client.post('/change-password', data={
        'old_password': PASSWORD, 'new_password': 'Fresh#2026',
        'confirm_password': 'Fresh#2026', 'password_hint': 'fest'})


def test_student_without_face_goes_to_face_step(app, seed):
    client = _first_login_student(app)
    resp = _change(client)
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/change-password?step=face')
    html = client.get('/change-password?step=face').data.decode()
    assert 'Register your face' in html and 'id="faceCard"' in html
    assert 'passwordChangeForm' not in html


def test_student_with_face_goes_to_dashboard(app, seed):
    from backend.models import FaceEnrollment
    with app.app_context():
        db.session.add(FaceEnrollment(user_id=seed['s2'], descriptors=b'\0' * 512,
                                      sample_count=1, is_locked=True))
        db.session.commit()
    client = _first_login_student(app)
    resp = _change(client)
    assert resp.status_code == 302 and not resp.headers['Location'].endswith('step=face')


def test_staff_change_password_page(app, seed):
    client = app.test_client()
    login(client, 'faca')
    html = client.get('/change-password').data.decode()
    assert 'Change password' in html and 'nx-steps' not in html
    assert 'optional' in html                                  # hint optional for staff
    resp = client.post('/change-password', data={
        'old_password': PASSWORD, 'new_password': 'Fresh#2026',
        'confirm_password': 'Fresh#2026', 'password_hint': ''})
    assert resp.status_code == 302 and 'step=face' not in resp.headers['Location']


# ── Taking an exam ──────────────────────────────────────────────────────────

def _exam_page(app, seed, as_user, key=None):
    if key:
        with app.app_context():
            Exam.query.get(seed['exam']).access_key = key
            db.session.commit()
    client = as_user('stud2')
    client.post(f"/start-exam/{seed['exam']}")
    return client, client.get(f"/exam/{seed['exam']}/take").data.decode()


def _assert_exam_shell(html):
    assert 'css/ds/tokens.css' in html and 'css/ds/exam.css' in html
    assert 'css/style.css' not in html and 'bootstrap.min.css' not in html
    assert '<meta name="csrf-token"' in html
    assert 'nx-sidebar' not in html                              # no navigation away mid-exam
    assert '/logout' not in html                                 # no sign-out during the exam
    assert 'data-nx-theme="dark"' in html                        # theme can still be changed


def test_exam_page_uses_the_exam_shell(app, seed, as_user):
    _, html = _exam_page(app, seed, as_user)
    _assert_exam_shell(html)
    assert 'nx-exam exam-mode' in html
    assert 'js/face_liveness.js' in html and 'NETRA_FACE_MODEL_URL' in html
    assert 'vendor/socket.io/socket.io.min.js' in html


def test_exam_page_keeps_the_runtime_hooks(app, seed, as_user):
    """IDs the proctoring, timer and autosave script depend on."""
    _, html = _exam_page(app, seed, as_user)
    for el in ('syscheck-overlay', 'scProceedBtn', 'modal-instr', 'exam-ui', 'exam-topbar',
               'timer-box', 'tab-counter', 'qNavGrid', 'qs0', 'opts0', 'cam-widget',
               'monitor-video', 'calib-video', 'featureBadges', 'modal-submit', 'fs-overlay',
               'sc-rec-blocker', 'exam-rec-cover', 'toast-wrap', 'as-pill'):
        assert f'id="{el}"' in html, el
    assert 'id="key-overlay"' not in html                         # dead in-page key overlay removed


def test_exam_runtime_renders_untrusted_text_as_text(app, seed, as_user):
    _, html = _exam_page(app, seed, as_user)
    assert "el.querySelector('.ti-msg').textContent = msg" in html      # toasts
    assert 'el.lastChild.textContent = msg' in html                      # activity log
    assert 'span.textContent = label' in html                            # device names
    assert "el.querySelector('b').textContent = msg" in html             # force-end banner


def test_exam_key_page(app, seed, as_user):
    _, html = _exam_page(app, seed, as_user, key='ABCD2345')
    _assert_exam_shell(html)
    assert 'id="keyForm"' in html and 'name="access_key"' in html
    assert f'data-verify-url="/exam/{seed["exam"]}/verify-key"' in html
    assert 'js/pages/exam_key.js' in html
    assert 'id="qs0"' not in html


# ── Student dashboard ───────────────────────────────────────────────────────

from backend.services.student_dashboard_service import exam_state  # noqa: E402


def _student_home(as_user, username='stud1'):
    resp = as_user(username).get('/student/dashboard')
    assert resp.status_code == 200
    return resp.data.decode()


def _set_exam(app, exam_id, **fields):
    with app.app_context():
        exam = Exam.query.get(exam_id)
        for k, v in fields.items():
            setattr(exam, k, v)
        db.session.commit()


def test_student_dashboard_uses_design_system_shell(app, seed, as_user):
    html = _student_home(as_user)
    assert 'css/ds/tokens.css' in html and 'css/style.css' not in html
    assert 'student_dashboard.css' not in html and 'chart.umd.js' not in html
    assert re.search(r'href="/student/dashboard"\s+aria-current="page"', html)
    assert f'href="/student/{seed["s1"]}/profile"' in html
    assert '/faculty/dashboard' not in html                      # no staff navigation


def test_submitted_exam_cannot_be_retaken(app, seed, as_user):
    html = _student_home(as_user)                                # stud1 submitted; results hidden
    assert 'Retake' not in html
    assert f'action="/start-exam/{seed["exam"]}"' not in html
    assert 'result on hold' in html.lower()
    assert f'/student/exam/{seed["attempt"]}/result' not in html  # score stays locked


def test_released_result_links_to_result_and_pdf(app, seed, as_user):
    _set_exam(app, seed['exam'], results_published=True)
    html = _student_home(as_user)
    assert f'/student/exam/{seed["attempt"]}/result' in html
    assert f'/student/exam/{seed["attempt"]}/download-pdf' in html
    assert '50%' in html and 'nx-trend' in html


def test_open_exam_offers_start(app, seed, as_user):
    html = _student_home(as_user, 'stud2')
    assert f'action="/start-exam/{seed["exam"]}"' in html
    assert 'name="csrf_token"' in html


def test_upcoming_exam_has_no_start(app, seed, as_user):
    _set_exam(app, seed['exam'], start_time=datetime.utcnow() + timedelta(days=1))
    html = _student_home(as_user, 'stud2')
    assert f'action="/start-exam/{seed["exam"]}"' not in html
    assert 'Upcoming' in html and 'Opens ' in html


def test_in_progress_exam_shows_continue(app, seed, as_user):
    client = as_user('stud2')
    client.post(f'/start-exam/{seed["exam"]}')
    html = client.get('/student/dashboard').data.decode()
    assert 'is in progress' in html
    assert f'href="/exam/{seed["exam"]}/take"' in html


def test_unassigned_exams_are_not_listed(app, seed, as_user):
    _set_exam(app, seed['exam'], allow_all_students=False, allowed_students=str(seed['s1']))
    html = _student_home(as_user, 'stud2')
    assert 'SQL Basics' not in html
    assert 'No exams yet' in html


def test_face_reminder_until_registered(app, seed, as_user, monkeypatch):
    assert 'Register your face' in _student_home(as_user)
    assert 'change-password?step=face' in _student_home(as_user)
    from backend.services import face_service
    monkeypatch.setattr(face_service, 'enrollment_locked', lambda user: True)
    assert 'Register your face' not in _student_home(as_user)


def test_exam_state():
    class E:  # minimal stand-ins
        is_active, force_ended, start_time, end_time = True, False, None, None

    class A:
        completed, status, is_invalidated, result_visible_to_student = False, 'started', False, False

    now = datetime.utcnow()
    assert exam_state(E(), None, now) == 'open'
    assert exam_state(E(), A(), now) == 'in_progress'
    done = A(); done.completed = True; done.status = 'submitted'
    assert exam_state(E(), done, now) == 'awaiting'
    done.result_visible_to_student = True
    assert exam_state(E(), done, now) == 'released'
    done.is_invalidated = True
    assert exam_state(E(), done, now) == 'invalidated'
    later = E(); later.start_time = now + timedelta(hours=1)
    assert exam_state(later, None, now) == 'upcoming'
    over = E(); over.end_time = now - timedelta(hours=1)
    assert exam_state(over, None, now) == 'closed'


# ── Result page and profile ─────────────────────────────────────────────────

from backend.models import Question, StudentAnswer  # noqa: E402
from backend.services.text_format import format_question  # noqa: E402


def _released_with_answers(app, seed):
    """stud1's attempt: Q1 answered correctly, Q2 skipped; results released."""
    with app.app_context():
        Exam.query.get(seed['exam']).results_published = True
        db.session.add(StudentAnswer(student_exam_id=seed['attempt'], question_id=seed['q1'],
                                     selected_answer='A', is_correct=True))
        db.session.commit()


def test_result_page_reviews_every_question(app, seed, as_user):
    _released_with_answers(app, seed)
    html = as_user('stud1').get(f"/student/exam/{seed['attempt']}/result").data.decode()
    assert 'css/ds/tokens.css' in html and 'css/style.css' not in html
    assert html.count('class="nx-review__item ') == 2                # skipped Q2 is listed too
    assert 'data-status="correct"' in html and 'data-status="skipped"' in html
    assert 'Your answer' in html and 'Correct answer' in html
    assert f'/student/exam/{seed["attempt"]}/download-pdf' in html
    assert 'Math.random' not in html


def test_result_page_still_locked_until_released(app, seed, as_user):
    resp = as_user('stud1').get(f"/student/exam/{seed['attempt']}/result")
    assert resp.status_code == 302


def test_question_text_is_escaped_and_formats_code():
    html = str(format_question('Run `ls`<script>x</script>\\n```py\nprint(1)\n```'))
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<code>ls</code>' in html
    assert '<pre class="nx-code"><code>print(1)</code></pre>' in html


def test_result_page_renders_question_text_safely(app, seed, as_user):
    _released_with_answers(app, seed)
    with app.app_context():
        Question.query.get(seed['q1']).question_text = '<img src=x onerror=alert(1)>'
        db.session.commit()
    html = as_user('stud1').get(f"/student/exam/{seed['attempt']}/result").data.decode()
    assert '<img src=x' not in html and '&lt;img src=x' in html


def test_own_profile_hides_unreleased_scores(app, seed, as_user):
    html = as_user('stud1').get(f"/student/{seed['s1']}/profile").data.decode()
    assert 'css/ds/tokens.css' in html
    assert 'On hold' in html and '50%' not in html
    assert 'Reset password' not in html and 'student_profile.js' not in html
    assert re.search(r'href="/student/\d+/profile"\s+aria-current="page"', html)


def test_students_cannot_open_other_profiles(app, seed, as_user):
    resp = as_user('stud2').get(f"/student/{seed['s1']}/profile")
    assert resp.status_code == 302 and f"/student/{seed['s1']}" not in resp.headers['Location']


def test_staff_are_sent_to_the_scoped_profile(app, seed, as_user):
    resp = as_user('faca').get(f"/student/{seed['s1']}/profile")
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith(f"/faculty/student/{seed['s1']}/profile")


def test_staff_profile_shows_scores_and_reset(app, seed, as_user):
    html = as_user('admin1').get(f"/faculty/student/{seed['s1']}/profile").data.decode()
    assert '50%' in html and 'not released' in html
    assert f'data-reset-url="/faculty/change-student-password/{seed["s1"]}"' in html
    assert 'student_profile.js' in html and 'confirm(' not in html


# ── Exam page (faculty) ─────────────────────────────────────────────────────

def _exam_html(as_user, seed, who='faca'):
    resp = as_user(who).get(f"/faculty/exam/{seed['exam']}")
    assert resp.status_code == 200
    return resp.data.decode()


def test_exam_page_on_design_system(app, seed, as_user):
    html = _exam_html(as_user, seed)
    assert 'css/ds/tokens.css' in html and 'css/style.css' not in html
    assert 'js/pages/view_exam.js' in html
    assert 'confirm(' not in html and 'alert(' not in html and 'onclick=' not in html
    for path in (f"/faculty/exam/{seed['exam']}/update_access", f"/faculty/exam/{seed['exam']}/extend_time",
                 f"/faculty/exam/{seed['exam']}/force_end", f"/faculty/exam/{seed['exam']}/generate_key",
                 f"/api/faculty/publish-results/{seed['exam']}"):
        assert path in html, path
    assert '<dialog class="nx-dialog' in html and 'name="allowed_students"' in html


def test_exam_page_lists_attempts_with_actions(app, seed, as_user):
    client = as_user('stud2')
    client.post(f"/start-exam/{seed['exam']}")
    html = _exam_html(as_user, seed)
    assert 'In progress' in html and 'Submitted' in html
    assert 'data-nx-confirm="End now?"' in html                 # two-step end for the live attempt
    assert f'/student/exam/{seed["attempt"]}/result' in html     # finished attempt links to its result


def test_exam_page_warns_when_there_are_no_questions(app, seed, as_user):
    with app.app_context():
        Question.query.filter_by(exam_id=seed['exam']).delete()
        db.session.commit()
    assert 'This exam has no questions yet' in _exam_html(as_user, seed)


def test_picker_hides_other_departments_exam_titles(app, seed, as_user):
    """facb (another department) has an open exam with stud2 on it; faca must not see its title."""
    with app.app_context():
        other = Exam(title='Secret DAC Exam', duration_minutes=30, creator_id=seed['fac_b'], is_active=True, status='active')
        db.session.add(other); db.session.commit()
        db.session.add(StudentExam(student_id=seed['s2'], exam_id=other.id, status='started'))
        db.session.commit()
    assert 'Secret DAC Exam' not in _exam_html(as_user, seed)


def test_confirm_helper_replaces_window_confirm():
    js = open(os.path.join(ROOT, 'frontend/static/js/ds/app.js')).read()
    assert 'nx.confirmPress' in js and 'window.confirm' not in js.replace('instead of window.confirm', '')


# ── Analytics ───────────────────────────────────────────────────────────────

def _analytics(as_user, seed):
    resp = as_user('faca').get(f"/faculty/exam/{seed['exam']}/analytics")
    assert resp.status_code == 200
    return resp.data.decode()


def test_analytics_on_design_system(app, seed, as_user):
    html = _analytics(as_user, seed)
    assert 'css/ds/tokens.css' in html and 'css/style.css' not in html
    assert 'chart.umd.js' not in html and 'js/pages/analytics.js' in html
    assert 'confirm(' not in html and 'alert(' not in html and 'onclick=' not in html
    assert 'class="nx-bars"' in html                               # score chart drawn server-side
    assert '<dialog class="nx-dialog' in html


def test_analytics_numbers(app, seed, as_user):
    html = _analytics(as_user, seed)                               # one submitted attempt, 50%, passed
    assert '1 / 1' in html                                         # submitted of attempts
    assert '100<small>%</small>' in html                           # pass rate over submitted attempts


def test_analytics_flags_attempts_for_review(app, seed, as_user):
    with app.app_context():
        StudentExam.query.get(seed['attempt']).total_violations = 5
        db.session.commit()
    html = _analytics(as_user, seed)
    assert '1 flagged attempt to review' in html
    assert f'data-review-open="{seed["attempt"]}"' in html
    assert 'data-review="1"' in html


def test_analytics_question_accuracy_counts_skips(app, seed, as_user):
    with app.app_context():
        db.session.add(StudentAnswer(student_exam_id=seed['attempt'], question_id=seed['q1'],
                                     selected_answer='A', is_correct=True))
        db.session.commit()
    html = _analytics(as_user, seed)
    assert html.count('class="nx-qstats__row"') == 2
    assert '1 skipped' in html                                     # Q2 was not answered


# ── New exam ────────────────────────────────────────────────────────────────

def test_create_exam_page_on_design_system(app, seed, as_user):
    html = as_user('faca').get('/faculty/exam/create').data.decode()
    assert 'css/ds/tokens.css' in html and 'create_exam.css' not in html
    assert 'js/pages/create_exam.js' in html
    assert 'prompt(' not in html and 'onclick=' not in html
    for name in ('title', 'duration_minutes', 'passing_score', 'start_time', 'end_time', 'exam_mode',
                 'max_tab_switches', 'proctor_settings_json', 'post_exam_redirect'):
        assert f'name="{name}"' in html, name
    # every server-read checkbox is followed by a hidden "false"
    for name in ('require_access_key', 'randomize_questions', 'show_leaderboard', 'is_active'):
        assert f'<input type="hidden" name="{name}" value="false">' in html, name


def test_unticked_options_are_saved_as_off(app, seed, as_user):
    """An unticked box used to send nothing, which the server read as 'on'."""
    client = as_user('faca')
    resp = client.post('/faculty/exam/create', data={
        'title': 'Unticked', 'duration_minutes': '30', 'passing_score': '40',
        'require_access_key': 'false', 'randomize_questions': 'false',
        'show_leaderboard': 'false', 'is_active': 'false'})
    assert resp.status_code == 302
    with app.app_context():
        exam = Exam.query.filter_by(title='Unticked').one()
        assert not exam.access_key and not exam.randomize_questions
        assert not exam.show_leaderboard and not exam.is_active


def test_ticked_options_still_win(app, seed, as_user):
    client = as_user('faca')
    from werkzeug.datastructures import MultiDict
    client.post('/faculty/exam/create', data=MultiDict([
        ('title', 'Ticked'), ('duration_minutes', '30'), ('passing_score', '40'),
        ('require_access_key', 'true'), ('require_access_key', 'false'),
        ('is_active', 'true'), ('is_active', 'false')]))
    with app.app_context():
        exam = Exam.query.filter_by(title='Ticked').one()
        assert exam.access_key and exam.is_active


def test_create_exam_error_keeps_what_was_typed(app, seed, as_user):
    resp = as_user('faca').post('/faculty/exam/create', data={'title': 'Kept title', 'duration_minutes': '0'})
    assert resp.status_code == 400
    assert 'value="Kept title"' in resp.data.decode()


# ── Add questions ───────────────────────────────────────────────────────────

import io  # noqa: E402


def test_upload_page_on_design_system(app, seed, as_user):
    html = as_user('faca').get(f"/faculty/exam/{seed['exam']}/upload").data.decode()
    assert 'css/ds/tokens.css' in html and 'upload_questions.css' not in html
    assert 'papaparse' not in html and 'alert(' not in html and 'onclick=' not in html
    assert 'samples/questions_sample.csv' in html and 'name="file"' in html


def test_preview_uses_the_upload_rules(app, seed, as_user):
    client = as_user('faca')
    bad = b'question,option_a,option_b,correct_answer\nOK?,a,b,A\nBroken,a,b,Z\n'
    data = client.post(f"/faculty/exam/{seed['exam']}/preview_questions",
                       data={'file': (io.BytesIO(bad), 'q.csv')}, content_type='multipart/form-data').get_json()
    assert data['success'] is False and data['error_count'] == 1 and 'Row 3' in data['errors'][0]

    sample = open(os.path.join(ROOT, 'frontend/static/samples/questions_sample.csv'), 'rb').read()
    data = client.post(f"/faculty/exam/{seed['exam']}/preview_questions",
                       data={'file': (io.BytesIO(sample), 'sample.csv')}, content_type='multipart/form-data').get_json()
    assert data['success'] is True and data['total'] == 3 and data['total_points'] == 4


def test_sample_file_uploads(app, seed, as_user):
    sample = open(os.path.join(ROOT, 'frontend/static/samples/questions_sample.csv'), 'rb').read()
    resp = as_user('faca').post(f"/faculty/exam/{seed['exam']}/upload",
                                data={'file': (io.BytesIO(sample), 'sample.csv')}, content_type='multipart/form-data')
    assert resp.status_code == 302
    with app.app_context():
        assert Question.query.filter_by(exam_id=seed['exam']).count() == 5


def test_splitter_page_on_design_system(app, seed, as_user):
    html = as_user('faca').get('/faculty/split-questions').data.decode()
    assert 'css/ds/tokens.css' in html and 'js/pages/split_questions.js' in html
    assert 'data-preview-url="/faculty/split-questions/preview"' in html
    assert 'data-download-url="/faculty/split-questions/download"' in html
    assert re.search(r'href="/faculty/split-questions"\s+aria-current="page"', html)
