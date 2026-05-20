import eventlet
eventlet.monkey_patch()

from app import create_app

app = create_app()

if __name__ == "__main__":
    from backend.routes import socketio
    socketio.run(app, debug=False, log_output=True)
