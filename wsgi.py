import eventlet
eventlet.monkey_patch()

from app import create_app

app = create_app()

# Close attempts whose time ran out unattended (see exam_service)
from backend.socket_io import socketio as _socketio  # noqa: E402
from backend.services.exam_service import start_attempt_sweeper  # noqa: E402
start_attempt_sweeper(app, _socketio)

if __name__ == "__main__":
    from backend.socket_io import socketio
    socketio.run(app, debug=False, log_output=True)
