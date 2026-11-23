"""Logging for the whole app (audit P2-5).

- One format everywhere: time, level, request reference, logger, message.
- Console (stderr) plus a rotating file, by default instance/logs/netra.log,
  so a problem on exam day can be read back afterwards.
- Every HTTP request gets a short reference ID. It is sent back in the
  X-Request-ID header and shown on the error page, so "it said error ab12cd34"
  leads straight to the matching log lines. Socket.IO events are tagged with
  the socket's session id instead.
- Requests slower than SLOW_REQUEST_MS are logged as warnings.

Settings (environment):
  LOG_LEVEL        INFO (default), DEBUG, WARNING ...
  LOG_DIR          folder for netra.log (default: <instance>/logs)
  LOG_TO_FILE      false to log to the console only
  LOG_MAX_MB       size of one file before it rotates (default 10)
  LOG_BACKUPS      rotated files kept (default 5)
  SLOW_REQUEST_MS  default 2000
"""
import logging
import os
import secrets
import time
from logging.handlers import RotatingFileHandler

from flask import g, has_request_context, request

FORMAT = '%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s'
_HANDLER_TAG = '_netra_handler'


class RequestIdFilter(logging.Filter):
    """Adds record.request_id: the HTTP request's reference, a socket id, or '-'."""

    def filter(self, record):
        rid = '-'
        if has_request_context():
            rid = g.get('request_id')
            if not rid:
                sid = getattr(request, 'sid', None)       # set by Flask-SocketIO
                rid = f'sio:{sid[:8]}' if sid else '-'
        record.request_id = rid
        return True


def _env_bool(name, default=True):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ('0', 'false', 'no', 'off', '')


def log_file_path(app):
    folder = os.getenv('LOG_DIR') or os.path.join(app.instance_path, 'logs')
    return os.path.join(folder, 'netra.log')


def configure(app):
    """Install handlers on the root logger once, then hook request IDs into app."""
    root = logging.getLogger()
    level = getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
    root.setLevel(level)

    if not any(getattr(h, _HANDLER_TAG, False) for h in root.handlers):
        formatter = logging.Formatter(FORMAT)
        rid_filter = RequestIdFilter()

        console = logging.StreamHandler()
        handlers = [console]

        if _env_bool('LOG_TO_FILE'):
            path = log_file_path(app)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                handlers.append(RotatingFileHandler(
                    path,
                    maxBytes=int(float(os.getenv('LOG_MAX_MB', '10')) * 1024 * 1024),
                    backupCount=int(os.getenv('LOG_BACKUPS', '5')),
                    encoding='utf-8'))
            except OSError as exc:
                logging.getLogger(__name__).warning('Cannot write log file %s: %s', path, exc)

        for h in handlers:
            h.setFormatter(formatter)
            h.addFilter(rid_filter)
            setattr(h, _HANDLER_TAG, True)
            root.addHandler(h)

        # Flask's own default handler would print every app.logger line twice
        from flask.logging import default_handler
        app.logger.removeHandler(default_handler)

    # Third-party chatter that drowns out the app at INFO
    for noisy in ('engineio', 'socketio', 'urllib3', 'PIL'):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    slow_ms = int(os.getenv('SLOW_REQUEST_MS', '2000'))
    slow_log = logging.getLogger('netra.slow')

    @app.before_request
    def _assign_request_id():
        g.request_id = secrets.token_hex(4)
        g.request_started = time.perf_counter()

    @app.after_request
    def _report_request(response):
        rid = g.get('request_id')
        if rid:
            response.headers['X-Request-ID'] = rid
            started = g.get('request_started')
            if started is not None:
                ms = (time.perf_counter() - started) * 1000
                if ms >= slow_ms:
                    slow_log.warning('Slow request: %s %s took %d ms (status %s)',
                                     request.method, request.path, ms, response.status_code)
        return response
