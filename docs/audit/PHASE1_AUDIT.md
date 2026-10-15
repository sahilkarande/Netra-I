# Netra-I — Phase 1 Audit: Baseline, Security, Reliability

Goal: reliable for a tech fest, then suitable for production, with **offline exams** as a first-class
requirement.

---

## 1. Baseline

| Item | Value |
|---|---|
| Commit audited | `0b57ae5` "directory rewiring" (branch `main`) |
| Working tree | clean — no uncommitted changes before or after the audit |
| Python | 3.12.3 (`venv/`); dependencies require ≥ 3.11 |
| Run (dev) | `python app.py` → <http://localhost:8800> |
| Run (LAN) | `bash scripts/generate_lan_cert.sh` once, then `python app.py --ssl` |
| Tests | `venv/bin/python -m pytest` (config in `pytest.ini`, suite in `tests/`) |
| Test result | **48 passed, 0 failed**, 1 third-party deprecation warning (`openvino.runtime`) |

| Module | Tests | Result |
|---|---|---|
| `tests/test_pages.py` | 12 | all pass |
| `tests/test_security.py` | 22 | all pass |
| `tests/test_exam_runtime.py` | 14 | all pass |

**How findings were confirmed.** Each finding below is backed by either a code reference or a
probe. Probes were temporary pytest files run against the throwaway test database and deleted
afterwards; schema checks ran against a *copy* of `instance/exam_platform.db`. No application
file was changed during the audit.

**Baseline rule.** Everything in §3–§5 exists at `0b57ae5`. Problems introduced by later work go
in §7, never here, so regressions stay distinguishable from inherited defects.

---

## 2. Summary

| ID | Priority | Area | Finding |
|---|---|---|---|
| P0-1 | P0 | Authorization | A student whose department matches the exam creator's can act as faculty on that exam — read the access key, extend time, delete the exam |
| P0-2 | P0 | Deployment | `python app.py` exposes Werkzeug's interactive debugger on `0.0.0.0` by default |
| P0-3 | P0 | Sessions | 365-day "remember me" on every password login, so the next person at a shared lab PC is logged in as the previous user |
| P0-4 | P0 | Accounts / identity | Default first-login passwords + sequential PRNs + one-shot face enrolment let anyone claim a not-yet-activated student's identity |
| P0-5 | P0 | Offline | Exam page loads Socket.IO, face-api.js, models, Bootstrap and fonts from the internet; offline, no camera exam can start |
| P0-6 | P0 | Exam data loss | An attempt pre-created at import shows "Continue" on the dashboard and opens with **0 seconds**, so it auto-submits instantly |
| P0-7 | P0 | Data integrity | Deleting students or faculty orphans rows or fails with a 500; orphans crash the leaderboard for everyone |
| P1-1 | P1 | CSRF | No CSRF tokens; form-encoded state changes accepted |
| P1-2 | P1 | Exam lifecycle | Abandoned attempts are never scored or closed |
| P1-3 | P1 | Exam lifecycle | Force-end, inactive exams and closed windows do not stop students who had not started |
| P1-4 | P1 | Timer | Time extensions never reach open exam pages; the client auto-submits at the old deadline |
| P1-5 | P1 | Answer persistence | Failed saves are silent, nothing is backed up locally, and submit proceeds even if the last save failed |
| P1-6 | P1 | Incomplete | Faculty student-report PDF is a stub; the client expects JSON it never receives |
| P1-7 | P1 | Incomplete | "Enhance questions with AI" checkbox does nothing (and conflicts with offline-first) |
| P1-8 | P1 | Validation | Question upload accepts invalid answer keys, defaults missing keys to "A", stores missing options as `"None"` |
| P1-9 | P1 | Data exposure | Any faculty can list, export, edit, reset and delete every student in every department |
| P1-10 | P1 | Integrity | No uniqueness on attempts or answers; concurrent requests can duplicate them |
| P1-11 | P1 | Schema / backups | No migration tool, FK enforcement off, no backup procedure |
| P1-12 | P1 | Error handling | Raw exception text (including SQL) returned to users |
| P2-1 … P2-14 | P2 | Various | See §5 |

---

## 3. P0 — critical security or data-loss issues

### P0-1 Students can perform faculty actions on exams in their department

- **Where:** `backend/routes/faculty.py` → `_faculty_can_access_exam()`, `_get_faculty_exams()`.
  Affected routes (they have no separate role check): `delete_exam`, `upload_questions`,
  `exam_analytics`, `faculty_force_end_exam`, `faculty_force_end_student`, `generate_exam_key`,
  `clear_exam_key`, `update_exam_access`, `faculty_extend_exam_time`.
- **Evidence:** the helper returns `True` when
  `current_user.course or current_user.department` equals the creator's, without checking
  `current_user.role`. Probe — a student with `department='DBDA'`, exam created by a DBDA
  faculty:
  `POST generate_key → 200 {'key': 'S887JK'}` · `GET analytics → 200` ·
  `POST extend_time 60 → duration 30→90` · `POST delete → exam deleted`.
- **Exposure today:** 0 of 95 live students have a department/course set, so it is latent; one
  import with a department column, or an admin edit, makes it live.
- **Impact:** a student could read the access key, view classmates' results, extend or end the
  exam, or delete it with all attempts.
- **Fix:** both helpers return nothing/False unless `current_user.role in ('faculty', 'admin')`.
  Add a `@staff_required` decorator and apply it to every faculty route as defence in depth.
- **Tests:** parametrized test — student with a matching department gets 403/redirect on each of
  the nine routes, and the exam, key and duration are unchanged.

### P0-2 Werkzeug interactive debugger exposed on the network

- **Where:** `app.py` main block — `debug = os.getenv('FLASK_DEBUG', 'True') == 'True'` when not
  `--ssl`, `host='0.0.0.0'`, `allow_unsafe_werkzeug=True`.
- **Evidence:** Flask-SocketIO (`flask_socketio/__init__.py` ~l.613–637) wraps the app in
  `DebuggedApplication(..., evalex=True)` whenever `app.debug` is set and the async mode is
  eventlet.
- **Impact:** anyone on the LAN who triggers an unhandled error sees full tracebacks with source.
  The in-browser Python console is one PIN away from remote code execution on the exam server.
  `python app.py` is the documented quick-start command.
- **Fix:** default `FLASK_DEBUG` to false; when debug is on, bind `127.0.0.1` only; drop
  `allow_unsafe_werkzeug`; print a loud warning if debug is requested with a non-loopback host.
- **Tests:** unit-test a `resolve_run_options(argv, env)` helper — the default gives
  `debug=False`; debug forces `host='127.0.0.1'`.

### P0-3 Year-long "remember me" on shared machines

- **Where:** `backend/routes/auth.py:217` `login_user(user, remember=True)`; no `REMEMBER_COOKIE_*`
  settings in `app.py`.
- **Evidence:** Flask-Login defaults are `COOKIE_DURATION = 365 days`, `COOKIE_SAMESITE = None`,
  `COOKIE_SECURE = False` (`flask_login/config.py`). `session_protection='strong'` only resets
  when IP or user agent changes, and they don't on the same lab PC.
- **Impact:** a student or faculty member who closes the browser without logging out stays logged
  in on that PC for a year. The next exam batch at that seat inherits the account, including
  faculty or admin. The cookie also lacks `SameSite`, which weakens CSRF defences (P1-1).
- **Fix:** `remember=False` for all logins (or an explicit opt-in box hidden for students); set
  `REMEMBER_COOKIE_DURATION` short, `REMEMBER_COOKIE_SAMESITE='Lax'`, `REMEMBER_COOKIE_HTTPONLY`,
  and `REMEMBER_COOKIE_SECURE` under SSL; make sessions permanent with the existing 1-hour
  lifetime; log students out after submit.
- **Tests:** a login response sets no `remember_token`; the session cookie has `HttpOnly` and
  `SameSite=Lax`; a fresh client without the session cookie is anonymous.

### P0-4 Account and identity takeover before first login

- **Where:** `faculty.py:364` (import default `'Student@123'`), `faculty.py:850` (reset to
  `'123456789'`), `auth.py` login by PRN, `face_service.enroll_face` (enrolment is one-shot and
  locked).
- **Evidence:** the import default applies to every row without a password column; reset always
  uses the same constant. The batch file in `data/Students A25` has **77 consecutive PRNs**.
  The live database has **4 students who have not completed first login**. On first login the
  account holder sets the password and registers the face, and the face then cannot be changed
  without staff.
- **Impact:** anyone knowing the default can log in as an unactivated student by PRN, set the
  password, and **register their own face**. They then pass the identity gate as that student,
  and the real student cannot sit camera exams.
- **Fix:** generate a random temporary password per student at import and on reset; return it
  once to the faculty (on screen and in a downloadable CSV) and store only the hash. Optionally
  require faculty-supervised face enrolment (enrol only from a faculty-unlocked session).
- **Tests:** import without a password column gives distinct passwords, none equal to
  `Student@123`; reset returns a new random password and invalidates the old one; the default
  constant cannot log in.

### P0-5 Exams cannot start without internet access

- **Where:** `student/take_exam.html:663` (`https://cdn.socket.io/4.6.0/socket.io.min.js`),
  face-api.js and models in `take_exam.html` / `static/js/face_liveness.js`
  (`cdn.jsdelivr.net`, `justadudewhohacks.github.io`), `base.html` (Bootstrap, Bootstrap Icons,
  Google Fonts, Chart.js), `upload_questions.html` (PapaParse, SheetJS), `verify_otp.html`.
- **Evidence:** offline, `io` is undefined, `initSocket()` fails, and `sendCalibFrame()` retries
  forever while waiting for `S.socket`. The identity gate cannot load face-api. An identical
  Socket.IO v4.6.0 client already ships at `frontend/static/js/socket.io.min.js` but is unused.
- **Impact:** at an offline venue, no camera-proctored exam can begin, and every page is unstyled.
- **Fix:** vendor every third-party asset under `frontend/static/vendor/` (socket.io, face-api.js
  plus its three model sets, Bootstrap CSS/JS, Bootstrap Icons with fonts, Chart.js, PapaParse,
  SheetJS, self-hosted fonts or a system font stack). Point templates at `url_for('static', …)`.
- **Tests:** an automated test that fails if any template or static JS references an external
  `http(s)://` host (allow-list the footer contact link); a manual rehearsal with the WAN unplugged.

### P0-6 Imported attempts open with zero time

- **Where:** `student.py:54` — `in_progress = StudentExam…filter_by(completed=False).first()`;
  `dashboard.html:300` links "Continue" straight to `exam.take_exam`; `faculty.import_students`
  pre-creates `StudentExam` rows (`started_at` defaults to import time).
- **Evidence:** probe — attempt imported a day earlier: the dashboard shows "Exam in Progress";
  `GET /exam/<id>/take` → 200 with `totalSec: 0`. The `start_exam` fix that resets the clock is
  bypassed by this link.
- **Impact:** a student who follows the dashboard prompt is auto-submitted with nothing answered.
- **Fix:** `in_progress` only counts `status == 'started'`; `take_exam` redirects any attempt not in
  `started` to the dashboard with a "Start" action. Better: stop pre-creating attempts on import
  and record assignment instead (`allowed_students` or an association table).
- **Tests:** an imported attempt is not shown as in progress; `GET take` for it redirects;
  `POST start` then gives full time.

### P0-7 Deleting users corrupts data or fails

- **Where:** `faculty.delete_student`, `faculty.delete_students`, `admin.delete_faculty`;
  `backend/database.py` (no `PRAGMA foreign_keys=ON`).
- **Evidence (probes):**
  - Bulk delete (`Query.delete`, which bypasses the ORM) removes the user and leaves the attempt
    orphaned. `/global_leaderboard` then raises `AttributeError: 'NoneType' object has no
    attribute 'full_name'`: **500 for every viewer**.
  - Single delete of a student with an attempt → 500 with `NOT NULL constraint failed:
    student_exam.student_id`, and the SQL text is returned in the JSON.
  - Admin deleting a faculty member who owns exams → unhandled `IntegrityError` (500).
  - The live database has no orphans yet (checked on a copy); FK enforcement is off.
- **Impact:** one bulk delete can take down the leaderboard and corrupt reporting; deletes that
  fail leave staff unable to clean up.
- **Fix:** route all deletion through a service. Students: soft-delete (`is_active=False`,
  excluded from logins and lists), or an explicit cascade of answers, violations, logs,
  calibration, attempts, face data and evidence files in one transaction. Faculty: refuse while
  they own exams, or require reassignment. Enable `PRAGMA foreign_keys=ON`. Make leaderboards and
  reports tolerate missing users. Never return `str(e)`.
- **Tests:** delete a student with attempts → success, no orphans, leaderboard 200; bulk delete
  likewise; delete a faculty member with exams → clear 409 message; a schema test asserts FKs are on.

---

## 4. P1 — important bugs and incomplete core functionality

### P1-1 No CSRF protection
- **Where:** app-wide; no Flask-WTF or token anywhere (`grep csrf` → none). Many endpoints accept
  `request.form` (e.g. `set_results_visibility`, `review-exam`, `create-faculty`, `force_end`,
  `update_access`, `change-student-password`, `delete_student`).
- **Current mitigation:** the session cookie is `SameSite=Lax`, and starting and submitting exams
  are POST-only. The remember cookie (P0-3) has no `SameSite`, so browsers that don't default to
  Lax send it on cross-site POSTs, and Flask-Login re-authenticates from it.
- **Impact:** a malicious page could make a logged-in faculty member's browser publish results,
  reset passwords or end exams.
- **Fix:** `CSRFProtect(app)`; a `<meta name="csrf-token">` in `base.html`; one `fetch` wrapper
  that adds `X-CSRFToken`; hidden inputs in forms; Socket.IO is unaffected.
- **Tests:** a state-changing POST without a token → 400; with a token → success. Run every
  existing test with CSRF enabled (the fixture supplies the token).

### P1-2 Abandoned attempts are never finalised
- **Where:** no server-side deadline enforcement; scoring only happens in `submit_exam`,
  force-end and `force_end_student`. `HEARTBEAT_TIMEOUT` is unused.
- **Evidence:** probe — an attempt started 5 h earlier on a 30-min exam stays `status='started'`,
  `score=None`, after visiting the dashboard and polling status.
- **Impact:** students who close the browser or lose power get no result; reports show them as
  absent or in progress forever.
- **Fix:** an `expire_overdue_attempts()` service (deadline + grace → score and mark submitted)
  called lazily from the dashboard, faculty exam views, the status poll and `start_exam`, plus an
  eventlet background task every 60 s.
- **Tests:** an overdue attempt is scored on the next dashboard or faculty view, its saved answers
  count, and a non-overdue attempt is untouched.

### P1-3 Closing an exam does not stop students who had not started
- **Where:** `exam.take_exam` checks only `is_assigned` and `completed`; force-end scores only
  `status='started'`.
- **Evidence:** probe — after force-end, the imported attempt stays `in_progress` and
  `GET /exam/<id>/take` → 200 (paper rendered).
- **Fix:** in `take_exam`, attempts not in `started` must pass `_exam_closed_reason()`; force-end
  marks never-started attempts `absent` (new status) or deletes the pre-created rows.
- **Tests:** after force-end, inactivation or window close, `take` redirects for non-started
  attempts.

### P1-4 Time extensions do not reach open exam pages
- **Where:** `take_exam.html` `pollStatus()` reads only `force_ended`/`exam_status`; the timer
  `S.secs` is set once from `CFG.totalSec`. `api_check_exam_status` already returns
  `updated_end_time`.
- **Impact:** after faculty add 10 minutes, open pages still auto-submit at the original deadline.
  Only a page refresh picks up the change.
- **Fix:** the status API returns `remaining_seconds` computed on the server; the client resyncs
  `S.secs` on every poll (this also corrects client clock drift); `extend_time` emits
  `time_extended` to the exam room.
- **Tests:** API — after `extend_time`, `remaining_seconds` grows by the extension. Manual — an
  open page's timer jumps.

### P1-5 Answer persistence is fragile
- **Where:** `take_exam.html` `pushAnswers()` / `doSubmit()`.
- **Evidence:** non-2xx responses are ignored (no error shown); network errors show "Save failed —
  retrying", but there is no retry loop or backoff. Nothing is stored in `localStorage`.
  `doSubmit()` awaits a failed push and submits anyway. `submit_exam` takes no answers payload.
- **Impact:** a Wi-Fi drop near the end loses the last answers silently. A browser crash loses
  everything since the last successful save.
- **Fix:** keep a per-attempt journal in `localStorage`, restored on load (server answers win when
  newer); retry with backoff; show a persistent "N answers not saved" banner; make `submit_exam`
  accept the final answers map and save it in the same transaction as scoring.
- **Tests:** server — submit with an answers payload persists them before scoring and rejects
  questions from other exams. Manual — offline toggle in DevTools.

### P1-6 Faculty student-report PDF is a stub
- **Where:** `faculty.py` `faculty_student_report_pdf()` returns `"PDF Report generation
  triggered"`; `student_report.html:1512` expects JSON `{pdf_b64, filename}`;
  `pdf_generator.generate_batch_report_pdf()` exists and is imported but never called. The route
  has no role check.
- **Impact:** the button always shows "Network error while generating PDF".
- **Fix:** staff-only; validate `exam_ids` through `_faculty_can_access_exam`; rebuild report rows
  from the database (apply `mark_edits` only as display overrides and label them); call
  `generate_batch_report_pdf`; return `{pdf_b64, filename}`, or switch the client to a binary
  download.
- **Tests:** faculty gets a valid PDF (`%PDF` header); a student gets 403; an exam from another
  department is excluded.

### P1-7 AI question-enhancement checkbox is a no-op
- **Where:** `upload_questions.html:82–85` (`enhance_with_ai`); no backend code reads it, and no
  Anthropic client exists.
- **Impact:** faculty think questions were improved when they were not. A cloud dependency would
  also contradict the offline goal.
- **Fix (Phase 1):** remove the checkbox. Later, if wanted: behind a config flag, run as an
  explicit preview-and-accept step, never silently on upload.
- **Tests:** the upload page doesn't render the control unless the feature flag is on.

### P1-8 Question upload accepts broken questions
- **Where:** `faculty.upload_questions` — `ans = …or 'A'`, `option_c=str(c)`, `option_d=str(d)`.
- **Evidence:** probe — `correct_answer = 'E'` is stored as-is (the question becomes impossible to
  answer correctly). A missing key silently becomes `'A'`. A missing option C/D is stored as the
  string `"None"` (`"nan"` from Excel) and shown to students. A bad `points` value rolls back the
  whole file with a raw error.
- **Fix:** reuse `question_splitter.parse_questions()` (already normalises columns and handles
  NaN) in `upload_questions` and `preview_questions`. Reject rows whose key is not A–D or points
  to an empty option, and report row numbers; store empty options as `NULL` and hide them in the UI.
- **Tests:** rows with an invalid or missing key are rejected with row numbers; two-option
  questions store `NULL` for C/D; the exam page renders two options.

### P1-9 Faculty can manage every student in every department
- **Where:** `faculty_students`, `export_students`, `faculty_student_profile`,
  `faculty_edit_student`, `faculty_change_student_password`, `faculty_reset_student_face`,
  `faculty_student_face_history`, `delete_student(s)` — role-checked, not scoped.
- **Impact:** PII (email, phone, PRN, batch) and account control across departments. Combined
  with P0-4, any faculty can reset any student to a known password.
- **Fix:** record the owning department (or importing faculty) on student rows; scope lists and
  actions to it; admin keeps global access.
- **Tests:** a faculty member from another department gets 404 on profile, edit, reset, delete and
  export, and lists exclude those students.

### P1-10 Duplicate attempts and answers are possible
- **Where:** `models.py` — no `UniqueConstraint(student_id, exam_id)` on `student_exam`, none on
  `(student_exam_id, question_id)` on `student_answer`; `start_exam` and `save_answer` do
  check-then-insert.
- **Impact:** a double-click on Start, or overlapping autosaves, can create two rows. Answers then
  split across attempts and scoring picks one arbitrarily.
- **Fix:** add both unique constraints (with a dedupe migration) and handle `IntegrityError` by
  re-reading.
- **Tests:** two concurrent starts give one attempt; repeated saves give one answer row per question.

### P1-11 No migrations, no FK enforcement, no backups
- **Where:** `app.ensure_schema_columns()` (additive `ALTER TABLE` only); `backend/database.py`
  (WAL, no `foreign_keys`); no backup tooling. The live database also carries three legacy `exam`
  columns (`is_question_bank`, `parent_exam_id`, `set_label`); they are nullable, so harmless.
- **Impact:** schema changes beyond adding a column (constraints in P1-10, FK fixes in P0-7)
  cannot be applied safely; a corrupted or deleted SQLite file means total loss.
- **Fix:** an Alembic baseline matching the current schema, with `LATE_COLUMNS` retired into it;
  `scripts/backup_db.py` using SQLite's online backup API with timestamped copies, run before
  every exam and on a schedule; a documented restore procedure.
- **Tests:** migration upgrade on a copy of the live database succeeds and matches the models;
  backup and restore round-trip.

### P1-12 Internal error text shown to users
- **Where:** e.g. `delete_student`/`delete_students` (`"error": str(e)`), `import_students`,
  `upload_questions`, `create_exam`, `batch_create_exams`, `change_password` flash `str(e)`.
- **Evidence:** the P0-7 probe returned a full SQL statement to the browser.
- **Fix:** log with `current_app.logger.exception`; return generic messages; add a 500 error
  handler with a friendly page.
- **Tests:** forced failure → generic message, no `SQL`/`Traceback` text in the response.

---

## 5. P2 — can wait for later phases

| ID | Where | Problem | Fix | Tests |
|---|---|---|---|---|
| P2-1 | `app.py` headers | No `X-Frame-Options`/CSP `frame-ancestors`, `X-Content-Type-Options`, `Referrer-Policy` | Add headers; a CSP once assets are vendored (P0-5) | header assertions |
| P2-2 | `socket_io.py` | `max_http_buffer_size=10 MB` per message; frames are ~15 KB | Lower to ~1 MB | oversized frame dropped |
| P2-3 | `proctor_service` | Proctor state (warning counts, cooldowns) lives in memory, so a restart resets counts while the DB total persists | Seed the engine's count from `total_violations` | restart simulation |
| P2-4 | `face_service` | Lockouts are in-process; restart clears them | Persist failures (table) or accept and document | — |
| P2-5 | socket handlers | Exceptions are only `print`ed; no structured logging anywhere | Logging config, rotating file log, request IDs | — |
| P2-6 | `auth.logout` | Logout is GET (cross-site logout) | POST with CSRF | — |
| P2-7 | `student.global_leaderboard` | Students see names and PRNs from every published exam, all departments | Scope to the student's own exams/batch; hide PRNs | scoping test |
| P2-8 | `auth.password_hint`, `face/available` | Anyone can read any student's hint by PRN; `face/available` reveals account existence once enrolment is universal | Remove the hint endpoint (faculty reset instead) or rate-limit; always return `available: true` | — |
| P2-9 | `models.py` | No indexes on hot FK columns (`student_exam.student_id/exam_id`, `student_answer.student_exam_id`, `exam_violation.student_exam_id`) | Add in the Alembic migration | — |
| P2-10 | `publish-results`, `delete_exam` | Publishing deletes evidence irreversibly; exam delete is a hard delete | Retain evidence N days or archive; soft-delete exams | — |
| P2-11 | sessions | One account can take the same attempt from two devices at once | Bind the attempt to the first socket/session; flag a second | — |
| P2-12 | `pdf_generator.py` | Question and name text interpolated into ReportLab markup unescaped (`<class 'list'>` didn't crash, but tags are parsed) | `xml.sax.saxutils.escape` on all user text | PDF text contains the literal |
| P2-13 | `auth.login` | `roll_id` is non-unique but accepted as a login identifier | Drop it from login, or scope by batch | — |
| P2-14 | scale | Inference runs on the single eventlet hub (~250-student ceiling) | Process pool, then Redis + workers (see technical reference §14) | load test |

Also noted: `email_utils.send_result_notification` is never called; `take_exam.html` (2.7 k
lines) and `style.css` (16.7 k lines) are monoliths that slow every UI fix.

---

## 6. Checked and found sound

| Area | What was verified |
|---|---|
| SQL injection | All queries go through the ORM with bound parameters. Raw SQL appears only in `ensure_schema_columns` (constant identifiers) and the admin-only SQL console (intended) |
| Answer submission | Owner-only, refused after submit or deadline + 120 s, question IDs restricted to the exam, POST-only submit |
| Exam access | Students only; assignment, window, active and force-end checked at start; access key gates the paper server-side, throttled |
| Socket.IO | Same-origin by default; every handler checks attempt ownership; rooms for force-end |
| Evidence | `/static/violations/*` blocked for non-staff; scoped evidence route with path containment |
| Registration | Role forced to student; CSPRNG OTP with expiry and lockout |
| Login brute force | 5 failures → 10 min per identifier (in-process — see P2-4) |
| Results | Results, PDF, leaderboard and own profile gated on publication and invalidation |
| XSS | Jinja autoescape; exam options escaped by `formatText`; analytics and SQL console escape output |
| Schema drift | Live database columns match the models (3 nullable legacy columns only) |

---

## 7. Changes after baseline

Record every issue introduced or discovered **after** `0b57ae5` here, with the commit that
introduced it. Do not edit §3–§5 except to mark items fixed.

| Date | Commit | Issue | Status |
|---|---|---|---|
| — | — | *(none yet)* | — |

### Fix tracking

Branch `fix/phase1-hardening`. Tests live in `tests/test_phase1_hardening.py` (named
`test_<id>_…`) and `tests/test_offline.py`.

| ID | Status | Commit | Regression tests |
|---|---|---|---|
| P0-1 | Fixed | `78cf211` | `test_p0_1_*` — 9 routes × same-department student, helpers, every faculty route decorated |
| P0-2 | Fixed | `78cf211` | `test_p0_2_*` — default not debug, debug binds loopback, never with `--ssl` |
| P0-3 | Fixed | `78cf211` | `test_p0_3_*` — no remember cookie, session cookie attributes, idle timeout, logout |
| P0-4 | Fixed | `6ba044c` | `test_p0_4_*` — random distinct import passwords, one-time sheet bound to session, reset |
| P0-5 | Fixed | `dbca43b` | `tests/test_offline.py` — no external hosts, vendor files exist, models complete, served |
| P0-6 | Fixed | `78cf211` | `test_p0_6_*` — imported attempt not "in progress", `take` needs Start, full time |
| P0-7 | Fixed | `6ba044c` | `test_p0_7_*` — delete with attempts, bulk delete + leaderboard, faculty with exams, FK pragma |
| P1-1 | Fixed | `c2e2987` | `test_p1_1_*` — login and state changes need the token, wrong token, pages carry hook |
| P1-2 | Fixed | `0dcbd40` | `test_p1_2_*` — dashboard / faculty view / status / sweep close overdue attempts |
| P1-3 | Fixed | `78cf211` | `test_p1_3_*` |
| P1-4 | Fixed | `0dcbd40` | `test_p1_4_*` — `remaining_seconds`, extension pushed over Socket.IO |
| P1-5 | Fixed | `0dcbd40` | `test_p1_5_*` — submit carries answers, ignored after time-up, cleared answers |
| P1-6 | Fixed | `c2e2987` | `test_p1_6_*` |
| P1-7 | Fixed (removed) | `c2e2987` | `test_p1_7_*` |
| P1-8 | Fixed | `c2e2987` | `test_p1_8_*` — five invalid row types, empty options, batch create |
| P1-9 | Fixed | `6ba044c` | `test_p1_9_*` — six actions out of scope → 404, lists/exports scoped |
| P1-10 | Fixed | `6ba044c` | `test_p1_10_*` — unique attempt/answer, constraints added to an old database |
| P1-11 | Partly fixed | `6ba044c`, `c2e2987` | `test_p1_11_*` — backups; FK enforcement in P0-7. **Alembic not introduced** (see below) |
| P1-12 | Fixed | `78cf211` | `test_p1_12_*` |
| P2-1 | Fixed | `c2e2987` | `test_p2_1_*` |
| P2-2 | Fixed | `c2e2987` | `test_p2_2_*` |
| P2-3 | Fixed | `c2e2987` | `test_p2_3_*` |
| P2-9 | Fixed | `6ba044c` | covered by `test_p1_10_indexes_added_to_existing_database` |
| P2-12 | Fixed | `c2e2987` | `test_p2_12_*` |
| P2-4, 5, 6, 7, 8, 10, 11, 13, 14 | Open | — | Phase 2 |

**Test suite:** 48 at baseline → **128 passing** after these fixes.

**Not done, deliberately:**
- *Alembic* (P1-11). Schema changes in this round were additive and are applied at start-up by
  `ensure_schema_columns()` / `ensure_schema_indexes()`, which were tested against an old-style
  database. Alembic is still recommended before running more than one environment.
- *Faculty-supervised face enrolment* (optional part of P0-4). Random temporary passwords remove
  the takeover path; supervised enrolment would add defence in depth.

**Behaviour changes to know about:**
- `python app.py` no longer runs in debug mode; set `FLASK_DEBUG=true` for local debugging (it then
  listens on 127.0.0.1 only).
- Logins no longer survive closing the browser, and idle sessions end after
  `SESSION_IDLE_MINUTES` (default 60).
- Students imported without a password column get random passwords — download the credentials
  sheet from the banner on *Manage Students* right after importing; it is offered once.
- Faculty see students created by their department, students who sat their exams, and older
  accounts with no recorded owner. Admin still sees everyone.
- A question file with any invalid row is rejected as a whole, with row numbers in the message.
- A faculty member who still owns exams cannot be deleted.

---

## 8. Suggested Phase 2 order

1. **Same-day, small, high value:** P0-1 (role check), P0-2 (debug default), P0-3 (remember
   cookie), P0-6 (in-progress filter + take redirect), P1-12 (generic errors).
2. **Before any tech-fest rehearsal:** P0-5 (vendor assets, offline test), P0-4 (random temp
   passwords), P1-5 (answer journal + submit payload), P1-4 (timer resync), P1-2/P1-3 (deadline
   enforcement).
3. **Before real data at scale:** P1-11 (Alembic + backups), then P0-7 and P1-10 (constraints and
   cascades), P1-1 (CSRF), P1-9 (student scoping).
4. **Feature completion:** P1-6 (report PDF), P1-7 (remove the AI checkbox), P1-8 (upload
   validation).
5. **Later:** the P2 items.

Every fix lands with its test in the same commit, and the full suite stays green.
