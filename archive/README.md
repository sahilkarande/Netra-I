# archive/

Code that is **no longer used by the application**, kept for reference only.
Nothing here is imported, served or run by Netra-I. Do not run these scripts against a
real database.

| Path | What it was | Why it is archived |
|---|---|---|
| `scripts/sql_tools/` | Ad-hoc SQLite scripts (query, patch, rebuild, truncate, insert students) | Point at `./exam_platform.db` instead of `instance/`, use old table names (`user`, `answer`), and several wipe or rebuild the database. Replaced by the admin SQL console and the faculty student import. |
| `backend_utils/create_db.py` | Stand-alone schema creator with its own model definitions | Defines an outdated schema; the app now creates tables itself on start-up. |
| `backend_utils/delete_all_questions.py` | Was `backend/utils/view_users.py` | Despite its old name, it **deletes every question**. Renamed so the name says what it does. |
| `frontend/js/exam.js` | Old exam-page script | `student/take_exam.html` carries its own runtime inline. |
| `frontend/js/theme.js` | Old theme toggle | `base.html` implements the theme toggle inline. |
| `frontend/images/CDAC_Logo_org.png` | Logo | Not referenced by any template. |
| `frontend/templates/import_students.html` | Stand-alone import page | No route renders it; import lives on the Manage Students page. |

To restore a file, move it back to its original location (shown in `git log --follow`).
