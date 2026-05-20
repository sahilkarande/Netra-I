from flask_socketio import SocketIO

# Initialize Socket.IO instance
socketio = SocketIO(
    cors_allowed_origins="*", 
    async_mode='eventlet',
    engineio_logger=False,
    logger=False,
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=10000000
)
