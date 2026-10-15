# Netra-I — Secure Proctored Exam Platform

Netra-I runs multiple-choice exams on a college's own network with an AI invigilator built in.
Students take the exam in a normal browser. A webcam check flags an empty seat, a second person,
or a head turned away for too long, and faculty review each flag before marks are published.

It is a Flask + Socket.IO application with OpenVINO computer vision, designed to run on one
machine on a campus LAN, with no cloud services and no per-student cost.

| Document | Read it for |
|---|---|
| **README.md** (this file) | What it does, how to install and run it, where things live |
| [docs/README_OVERVIEW.md](docs/README_OVERVIEW.md) | A plain-language explanation for faculty, students and administrators |
| [docs/README_TECHNICAL.md](docs/README_TECHNICAL.md) | Architecture, data model, every route, the proctoring pipeline, security model, scaling |
| [docs/LAN_DEPLOYMENT_GUIDE.md](docs/LAN_DEPLOYMENT_GUIDE.md) | Running an exam over campus Wi-Fi/LAN with HTTPS |

---

## Features

### Three roles

| Role | What they do |
|---|---|
| **Admin** | Creates faculty accounts, sees every exam and student, has a SQL console |
| **Faculty** | Creates exams, uploads questions, imports students, watches live, reviews flags, publishes results |
| **Student** | Passes an equipment and identity check, sits the exam, sees results once published |

### Exams
- Create one exam or a batch at once; questions from **CSV, Excel or JSON**
- **Question splitter** turns one question bank into several non-overlapping papers with balanced marks
- Per-student question and option shuffling, stored so a page refresh gives the same paper
- Optional **access key** that the teacher reads out in the hall; the paper is not sent to the browser until it is entered
- Exam window (start/end time), active/inactive switch, per-student or class-wide assignment
- Server-side timer anchored to the start time, so refreshing never adds time
- Answers auto-save; late answers are refused once time is up or the paper is submitted
- Live control: extend time, end the exam for everyone, or end it for one student (pushed to the browser instantly)

### Proctoring
- **Webcam checks on the server** (OpenVINO): no face, more than one face, head turned away for a sustained period, optional phone/book detection
- Per-student **calibration**, so a naturally tilted head is not penalised, plus a baseline that adapts as the student settles
- Gentle, rate-limited warnings ("Gentle reminder 3/25") with per-type cooldowns, and auto-submit at the faculty-set limit
- A photo is saved **only at a flagged moment**; there is no video recording
- Browser guards: tab-switch counting, full-screen enforcement, copy/paste and context-menu blocking, screen-recording interception, external-monitor and virtual-camera detection
- **Identity:** students register their face once, with a liveness challenge (random head movements checked server-side) that rejects photos and screens. Camera-proctored exams verify the student against that registration before the paper opens and re-check during the exam.

### Results
- Instant scoring on submit
- Faculty review screen: violation timeline with evidence photos, then **accept** or **invalidate** an attempt with a written reason
- Separate switches for **publishing results** and **opening the leaderboard** (the leaderboard needs results published first)
- PDF result per student, batch report, CSV export, question-level analytics

### Security
- Passwords hashed with PBKDF2; imported students get a **random** temporary password (handed out from a one-time credentials sheet) and must set their own on first login
- Throttling on password login, OTP codes, exam access keys and face sign-in (5 failures → 10-minute lockout)
- CSRF tokens on every state-changing request
- No long-lived login cookies: sessions end with the browser or after 60 idle minutes, which matters on shared lab PCs
- Every faculty route is staff-only; faculty act only on their department's exams and students
- Evidence photos are only served to staff who can access that exam
- Socket.IO connections are same-origin only, and every event is checked against the logged-in student
- Random per-installation session key when `SECRET_KEY` is not set

---

## Quick start

**Requirements:** Python 3.11+ (3.12 recommended) and a webcam for testing the exam page. Linux, macOS and Windows all work.

```bash
git clone <repo-url> Netra-I
cd Netra-I
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # optional; every setting has a default
```

**AI models.** The OpenVINO model files are not in git. Put these four files in `ai_models/`:

```
face-detection-adas-0001.xml / .bin
head-pose-estimation-adas-0001.xml / .bin
```

They are available from the [Open Model Zoo](https://github.com/openvinotoolkit/open_model_zoo).
Without them, face detection falls back to an OpenCV Haar cascade and head-pose checks are off.
The server prints a banner saying which detectors are degraded.

**Create the first admin and start the server:**

```bash
python scripts/create_admin.py --username admin --email admin@example.com --password '<choose one>'
python app.py
```

Open <http://localhost:8800>. The database (`instance/exam_platform.db`) is created on first start.

Cameras only work on `localhost` or over HTTPS. To run an exam for other machines on the network:

```bash
bash scripts/generate_lan_cert.sh   # once: writes certs/cert.pem and certs/key.pem
python app.py --ssl                 # or: bash scripts/start_lan_exam.sh
```

See [docs/LAN_DEPLOYMENT_GUIDE.md](docs/LAN_DEPLOYMENT_GUIDE.md) for the student-side browser steps.

> **Works with no internet.** Every library, font and face-recognition model is served from
> `frontend/static/vendor/`, so an exam hall network needs no outside connection.
> `tests/test_offline.py` fails if anything starts loading from the internet again.

---

## Typical workflow

1. **Admin** creates faculty accounts (*Manage Faculty*).
2. **Faculty** imports students from CSV/Excel (*Manage Students → Import*; a template is downloadable there). Students without a password in the file get a random temporary one: download the credentials sheet from the banner that appears after the import (it is offered once) and hand each student their line. On first login they set their own password and register their face.
3. **Faculty** creates an exam, uploads questions (see [docs/samples/](docs/samples/)), picks proctoring strictness, and notes the generated access key.
4. **Students** start the exam from their dashboard, enter the key, pass the equipment and identity check, and answer.
5. **Faculty** watches live, then reviews flagged attempts on the exam's *Analytics* page and accepts or invalidates them.
6. **Faculty** publishes results; students then see scores, breakdowns and PDFs.

Question file columns: `question, option_a, option_b, option_c, option_d, correct_answer, points`
(`correct_answer` is A–D; `points` defaults to 1).

---

## Configuration

All settings come from environment variables (or `.env`). See [.env.example](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | random, stored in `instance/.secret_key` | Signs session cookies |
| `DATABASE_URL` | `sqlite:///exam_platform.db` (in `instance/`) | Any SQLAlchemy URL; use PostgreSQL for large cohorts |
| `PORT` | `8800` | HTTP port |
| `FLASK_DEBUG` | `false` | Werkzeug debugger for local development only; when on, the server listens on 127.0.0.1 and other machines cannot connect. Never on with `--ssl` |
| `HOST` | `0.0.0.0` | Interface to listen on (ignored in debug mode) |
| `SESSION_IDLE_MINUTES` | `60` | Sign out a session idle this long |
| `USE_SSL` | `false` | Mark session cookies HTTPS-only. Set to `true` when running behind an HTTPS proxy (nginx); `--ssl` sets this automatically |
| `MAX_CONTENT_LENGTH` | 16 MB | Upload limit |
| `SOCKETIO_CORS_ORIGINS` | *(same-origin only)* | Extra allowed Socket.IO origins, comma-separated |
| `NETRA_FRAME_INTERVAL_SEC` | `4.0` | Seconds between inspected webcam frames per student |
| `NETRA_MODEL_DIR` | `ai_models/` | Location of model files |
| `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `FROM_EMAIL` | — | OTP email for self-registration. Without them the OTP is printed to the console |

Per-exam settings (duration, pass mark, shuffling, access key, camera/head-pose/object/voice/tab
checks, warning limit, sensitivity low/medium/high) are chosen on the *Create Exam* page.

---

## Project structure

```
Netra-I/
├── app.py                    # Application factory, start-up checks, CLI entry (python app.py [--ssl])
├── wsgi.py                   # Entry point for gunicorn
├── requirements.txt
├── pytest.ini
├── Dockerfile · docker-compose.yml · .dockerignore
├── .env.example              # Every configuration variable, documented
│
├── backend/
│   ├── database.py           # SQLAlchemy + Flask-Login instances
│   ├── models.py             # All tables (users, exams, attempts, violations, face data, audit)
│   ├── socket_io.py          # Socket.IO instance and origin policy
│   ├── security.py           # CSRF protection
│   ├── routes/               # One blueprint per area
│   │   ├── auth.py           #   login, registration/OTP, password change, face sign-in
│   │   ├── student.py        #   dashboard, results, PDF, leaderboard, profile
│   │   ├── exam.py           #   exam runtime + Socket.IO proctoring handlers
│   │   ├── faculty.py        #   exams, students, results, live control, review, evidence
│   │   └── admin.py          #   faculty accounts, all students/exams, SQL console
│   ├── services/             # Logic with no HTTP in it
│   │   ├── exam_builder.py       # validation + defaults shared by single and batch create
│   │   ├── exam_service.py       # scoring and per-student shuffle
│   │   ├── question_splitter.py  # one bank → several papers
│   │   ├── proctor_service.py    # per-attempt vision instances, violation logging
│   │   ├── proctor_vision/openvino_vision.py   # the computer-vision engine
│   │   ├── account_service.py    # temp passwords, faculty scoping of students, safe deletion
│   │   ├── face_service.py       # face enrolment, matching, lockout, audit
│   │   ├── liveness_service.py   # liveness challenges and anti-spoof checks
│   │   └── pdf_generator.py      # result and batch PDFs
│   └── utils/email_utils.py  # OTP email (console fallback)
│
├── frontend/
│   ├── templates/            # Jinja2 pages: base.html, auth pages, admin/, faculty/, student/
│   └── static/
│       ├── css/  js/  images/
│       ├── vendor/           # every third-party library, font and face model (offline)
│       └── violations/       # evidence photos, written at runtime (gitignored, staff-only)
│
├── ai_models/                # OpenVINO model files (not in git)
├── tests/                    # pytest suite (128 tests)
├── scripts/                  # create_admin, backup_db, repair_proctor_settings, LAN cert + launcher
├── deploy/                   # nginx reverse-proxy template + installer
├── docs/                     # Overview, technical reference, LAN guide, diagrams, samples
├── archive/                  # Old code no longer used, kept for reference (see archive/README.md)
│
├── certs/                    # TLS certificate + key (gitignored)
├── instance/                 # SQLite database + generated secret key (gitignored)
├── data/                     # Local question banks and student lists (gitignored)
└── backup/                   # Local database backups (gitignored)
```

---

## Testing

```bash
pip install pytest
python -m pytest
```

The suite runs against a throwaway SQLite database. Besides pages, results visibility,
registration/OTP and Socket.IO authorisation, `tests/test_phase1_hardening.py` holds one regression
test per finding of the [Phase 1 audit](docs/audit/PHASE1_AUDIT.md) — CSRF, sessions, role and
department scoping, temporary passwords, deletion, deadlines, timer sync, answer durability,
upload validation, backups — and `tests/test_offline.py` guards the no-internet requirement.
The camera and face-check screens need a real browser and webcam, so do a manual run before
exam day.

---

## Deployment

| Setup | How |
|---|---|
| Single machine, LAN | `bash scripts/start_lan_exam.sh` (HTTPS from `certs/`) |
| Behind nginx | `bash deploy/deploy_nginx.sh`, then run the app **without** `--ssl` on port 8800, with `USE_SSL=true` |
| Docker | `docker compose up --build` (gunicorn + eventlet, one worker) |

Before a real exam:

- Set `SECRET_KEY` explicitly if you run more than one process or restore from backup.
- Keep `instance/` and `frontend/static/violations/` on persistent storage.
- Back up the database before the exam and every few minutes during it:
  `python scripts/backup_db.py` (safe while the server runs; `--dest` for a USB stick).
- Run one full rehearsal exam on the actual hardware and network.

Known limits: one server process handles roughly **250 concurrent camera-proctored students**
(measured). Evidence photos are deleted when results are published, but are not otherwise
cleaned up. See the technical reference for the scaling analysis and next steps.

---

## License

Provided as-is for educational use.
