import os

from flask_socketio import SocketIO


def _allowed_origins():
    """Origins allowed to open a Socket.IO connection.

    Unset means same-origin only. A wildcard would let any website open a socket
    with a logged-in student's cookies, so extra origins must be listed
    explicitly (comma-separated) in SOCKETIO_CORS_ORIGINS.
    """
    raw = os.getenv('SOCKETIO_CORS_ORIGINS', '').strip()
    if not raw:
        return None
    return [o.strip() for o in raw.split(',') if o.strip()]


# Initialize Socket.IO instance
socketio = SocketIO(
    cors_allowed_origins=_allowed_origins(),
    async_mode='eventlet',
    engineio_logger=False,
    logger=False,
    ping_timeout=60,
    ping_interval=25,
    # Proctoring frames are ~15 KB; a 10 MB ceiling only invited memory abuse
    max_http_buffer_size=1_000_000
)
