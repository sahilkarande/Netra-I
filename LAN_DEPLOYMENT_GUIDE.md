# ProctoGuard LAN Deployment Guide
## Camera Access & ~300 Concurrent Student Support

---

## Problem: "Camera access is denied" Error

### Root Cause
Browsers **block `navigator.mediaDevices.getUserMedia()`** (camera access) on pages loaded from non-secure origins. Specifically:

| Origin | Camera Works? | Why |
|--------|--------------|-----|
| `http://localhost:8800` | ✅ Yes | `localhost` is a "secure context" |
| `http://127.0.0.1:8800` | ✅ Yes | Loopback is a "secure context" |
| `http://192.168.1.100:8800` | ❌ **NO** | HTTP over LAN is NOT a secure context |
| `https://192.168.1.100:8800` | ✅ Yes | HTTPS is always a secure context |

**When students connect via the LAN IP over HTTP, the browser silently blocks camera access entirely.**

---

## Solution: Quick Start

### Step 1: Generate SSL Certificate (One-time)
```bash
cd /path/to/ProctoGuard
bash scripts/generate_lan_cert.sh
```
This creates `cert.pem` and `key.pem` with SAN entries for localhost AND your LAN IP.

### Step 2: Start Server with HTTPS
```bash
python app.py --ssl
```
Or use the convenience launcher:
```bash
bash scripts/start_lan_exam.sh
```

### Step 3: Student Browser Setup
Since the certificate is self-signed, students need to accept it **once**:

1. Open Chrome/Edge browser
2. Navigate to `https://<SERVER_IP>:8800`
3. Click **"Advanced" → "Proceed to site (unsafe)"**
4. Camera permissions will now work normally

> **Tip**: For Chrome, students can also enable `chrome://flags/#allow-insecure-localhost` to bypass certificate warnings.

---

## What Was Changed

### 1. Frontend (`exam.js`) — Camera Permission Handling
- **Secure context detection**: Checks `window.isSecureContext` before calling `getUserMedia()` and shows a clear error message if HTTPS is needed
- **Permissions API pre-check**: Uses `navigator.permissions.query({ name: 'camera' })` to detect if camera is already denied
- **Detailed error messages**: Each `getUserMedia()` error type (`NotAllowedError`, `NotFoundError`, `NotReadableError`, `OverconstrainedError`, `SecurityError`) gets a specific, student-friendly message with steps to fix
- **Auto-retry with exponential backoff**: Camera access retries 3 times (1s, 2s, 4s delays) before asking for manual intervention
- **Fallback constraints**: If the camera doesn't support the requested resolution, automatically retries with `{ video: true }` minimal constraints

### 2. Frontend (`take_exam.html`) — Template Fixes
- **Removed duplicate `initExam()` call**: The old template called `initExam()` twice (once in DOMContentLoaded and once in a separate script tag), wasting resources
- **Early HTTPS warning**: Students see an immediate warning if the page isn't loaded over HTTPS on a non-localhost origin
- **Added `examId` to config**: The exam ID was missing from the `initExam()` config, preventing Socket.IO room joining

### 3. Backend (`app.py`) — HTTPS & Headers
- **`--ssl` CLI flag**: `python app.py --ssl` enables HTTPS using `cert.pem`/`key.pem`
- **`Permissions-Policy` header**: Tells the browser that camera and microphone are allowed for this origin
- **`Feature-Policy` header**: Legacy version of Permissions-Policy for older browsers
- **Auto LAN IP detection**: Server startup shows the correct HTTPS URL students should use
- **Secure session cookies**: `SESSION_COOKIE_SECURE` is automatically set when SSL is enabled

### 4. Backend (`routes.py`) — Scalability Fixes
- **Shared face cascade classifier**: Previously, `cv2.CascadeClassifier()` was loaded from disk on **every single frame** for every student. With 300 students × 1 frame/3 seconds = 100 disk I/O operations per second! 🔥 Now loaded **once** at module startup and shared across all threads
- **Thread-safe proctor instances**: Added `threading.Lock()` to `PROCTOR_INSTANCES` dict access
- **Proctor cleanup function**: `cleanup_proctor_instance()` frees memory when exams end

### 5. New Scripts
- **`scripts/generate_lan_cert.sh`**: Generates self-signed SSL cert with SAN entries for all detected LAN IPs
- **`scripts/start_lan_exam.sh`**: One-command launcher that generates certs if needed and starts the server

---

## Architecture for ~300 Concurrent Students

### Current Architecture (Sufficient for 300)
```
Students (300)  →  HTTPS  →  Flask + Socket.IO (threading mode)
   ↕                              ↕
getUserMedia()              Face Detection (OpenCV)
 (browser)                  CascadeClassifier (shared, loaded once)
   ↓                              ↕
 frame capture              Violations → DB (SQLite)
 every 3 seconds
```

### Performance Characteristics (300 students)
| Metric | Value |
|--------|-------|
| Frame rate per student | 1 frame / 3 seconds |
| Total frames/second | ~100 |
| Frame size | ~5-15 KB (JPEG, 160×120) |
| Network bandwidth | ~1-1.5 MB/s total |
| Face detection time | ~5-10ms per frame |
| Memory per proctor instance | ~1-2 MB |
| Total server memory | ~600 MB - 1 GB |

### If You Need More Scale (500+ students)

1. **Switch to eventlet/gevent async mode**:
   ```bash
   pip install eventlet
   ```
   Then in `routes.py`, change:
   ```python
   socketio = SocketIO(cors_allowed_origins="*", async_mode='eventlet')
   ```

2. **Use Gunicorn with eventlet workers**:
   ```bash
   pip install gunicorn eventlet
   gunicorn --worker-class eventlet -w 1 --certfile=cert.pem --keyfile=key.pem -b 0.0.0.0:8800 "app:create_app()"
   ```

3. **Switch from SQLite to PostgreSQL** for concurrent write handling:
   ```
   DATABASE_URL=postgresql://user:pass@localhost/proctoguard
   ```

4. **Use Nginx as reverse proxy** for SSL termination:
   ```nginx
   server {
       listen 443 ssl;
       ssl_certificate /path/to/cert.pem;
       ssl_certificate_key /path/to/key.pem;
       
       location / {
           proxy_pass http://127.0.0.1:8800;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
       }
   }
   ```

---

## Troubleshooting

### Camera still not working after HTTPS?
1. **Check the certificate covers the IP**: Run `openssl x509 -in cert.pem -text -noout | grep -A1 "Subject Alternative Name"` — your LAN IP should be listed
2. **Regenerate certificate**: If IP changed, run `bash scripts/generate_lan_cert.sh` again
3. **Chrome flag**: Navigate to `chrome://flags/#unsafely-treat-insecure-origin-as-secure` and add `http://YOUR_LAN_IP:8800`

### Students can't connect at all?
1. **Firewall**: Ensure port 8800 is open: `sudo ufw allow 8800`
2. **Same network**: Students must be on the same LAN/WiFi network
3. **IP changed**: The server's IP may have changed — check with `hostname -I`

### Server crashes under load?
1. **Increase file descriptors**: `ulimit -n 65536`
2. **Check SQLite lock contention**: Switch to PostgreSQL for 300+ concurrent writes
3. **Monitor memory**: The face cascade is now shared, but each proctor instance still uses memory
