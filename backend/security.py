"""
CSRF protection for every state-changing request.

A per-session random token is rendered into each page (<meta name="csrf-token">)
and must come back on every POST/PUT/PATCH/DELETE, either as the
``X-CSRFToken`` header (fetch) or a ``csrf_token`` form field (HTML forms).
``base.html`` adds both automatically, so individual pages need no changes.

A cross-site page can make a logged-in browser *send* a request, but it cannot
read this token, so the forged request is refused. Socket.IO is exempt: its
connections are already restricted to the same origin.
"""

import hmac
import secrets

from flask import jsonify, request, session, flash, redirect, url_for

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})
SESSION_KEY = '_csrf_token'
HEADER = 'X-CSRFToken'
FIELD = 'csrf_token'


def get_csrf_token() -> str:
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def _submitted_token():
    token = request.headers.get(HEADER)
    if token:
        return token
    if request.mimetype in ('application/x-www-form-urlencoded', 'multipart/form-data'):
        return request.form.get(FIELD)
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data.get(FIELD)
    return None


def init_csrf(app):
    app.config.setdefault('CSRF_ENABLED', True)
    app.jinja_env.globals['csrf_token'] = get_csrf_token

    @app.before_request
    def csrf_protect():
        if not app.config.get('CSRF_ENABLED', True):
            return None
        if request.method in SAFE_METHODS or request.path.startswith('/socket.io'):
            return None

        expected = session.get(SESSION_KEY)
        submitted = _submitted_token()
        if expected and submitted and hmac.compare_digest(str(expected), str(submitted)):
            return None

        app.logger.warning('CSRF check failed for %s %s', request.method, request.path)
        wants_json = (request.is_json or request.path.startswith('/api/')
                      or request.accept_mimetypes.best == 'application/json'
                      or request.headers.get('X-Requested-With') == 'XMLHttpRequest')
        message = 'Your session expired or the request could not be verified. Please reload the page and try again.'
        if wants_json:
            return jsonify({'success': False, 'error': message, 'csrf': True}), 400
        flash(message, 'error')
        return redirect(request.referrer or url_for('auth.index'))
