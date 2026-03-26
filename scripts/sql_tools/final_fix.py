"""
=============================================================
🌟 UPDATED FINAL DATABASE REBUILD (CDAC SECURE PROCTORED EXAM PLATFORM)
=============================================================

Includes:
✔ question_order TEXT
✔ option_mapping TEXT
✔ allow_access BOOLEAN (faculty can disable entire exam)
✔ allowed_students TEXT (JSON list of PRNs allowed)
✔ force_ended BOOLEAN (faculty can force-end exam)
✔ status VARCHAR (exam status: active, ended, archived)
✔ AI PROCTORING: enable_proctoring, max_violations
✔ AI PROCTORING: calibration_completed, total_violations, proctoring_status
✔ AI PROCTORING TABLES: exam_calibration, exam_violation
✔ PASSWORD MANAGEMENT: password_changed, password_changed_at (NEW)
"""

import os
import sqlite3
from werkzeug.security import generate_password_hash
from datetime import datetime

DB_NAME = "exam_platform.db"

print("\n" + "="*90)
print("🧹 INITIALIZING UPDATED DATABASE REBUILD — SECURE PROCTORED EXAM PLATFORM + AI PROCTORING + PASSWORD MANAGEMENT")
print("="*90 + "\n")

# ---------------------------------------------------------------------
# 1️⃣ Delete old database files safely
# ---------------------------------------------------------------------
for file in [DB_NAME, f"{DB_NAME}-journal", f"{DB_NAME}-wal", f"{DB_NAME}-shm"]:
    if os.path.exists(file):
        try:
            os.remove(file)
            print(f"✅ Deleted old database file: {file}")
        except Exception as e:
            print(f"⚠️ Could not delete {file}: {e}")

# ---------------------------------------------------------------------
# 2️⃣ Create new database and tables
# ---------------------------------------------------------------------
print("\n🚀 Creating fresh database schema...\n")
conn = sqlite3.connect(DB_NAME)

# Enable foreign keys
conn.execute("PRAGMA foreign_keys = ON;")
conn.execute("PRAGMA encoding = 'UTF-8';")
cursor = conn.cursor()

# ======================
# USERS TABLE (with Password Management)
# ======================
cursor.execute("""
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('student','faculty','admin')),
    full_name TEXT,
    phone TEXT,
    prn_number TEXT UNIQUE DEFAULT NULL,
    roll_id TEXT,
    batch TEXT,
    department TEXT,
    gender TEXT,
    employee_id TEXT UNIQUE DEFAULT NULL,
    is_verified BOOLEAN DEFAULT 0,
    otp TEXT,
    otp_created_at DATETIME,
    
    -- NEW: PASSWORD MANAGEMENT
    password_changed BOOLEAN DEFAULT 0,
    password_changed_at DATETIME DEFAULT NULL,
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
""")

# ======================
# EXAM TABLE (with AI Proctoring)
# ======================
cursor.execute("""
CREATE TABLE exam (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    duration_minutes INTEGER NOT NULL,
    passing_score FLOAT DEFAULT 50.0,
    creator_id INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1,
    start_time DATETIME,
    end_time DATETIME,

    -- Security Settings
    allow_tab_switch BOOLEAN DEFAULT 0,
    max_tab_switches INTEGER DEFAULT 3,
    randomize_questions BOOLEAN DEFAULT 1,
    show_results_immediately BOOLEAN DEFAULT 1,

    -- ACCESS CONTROL
    allow_access BOOLEAN DEFAULT 1,
    allowed_students TEXT DEFAULT NULL,

    -- FORCE END CONTROL
    force_ended BOOLEAN DEFAULT 0,
    force_ended_at DATETIME DEFAULT NULL,
    status TEXT DEFAULT 'active',

    -- AI PROCTORING SETTINGS
    enable_proctoring BOOLEAN DEFAULT 1,
    max_violations INTEGER DEFAULT 6,

    FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE CASCADE
);
""")

# ======================
# QUESTION TABLE
# ======================
cursor.execute("""
CREATE TABLE question (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    option_a TEXT NOT NULL,
    option_b TEXT NOT NULL,
    option_c TEXT,
    option_d TEXT,
    correct_answer TEXT NOT NULL,
    points FLOAT DEFAULT 1.0,
    order_number INTEGER,
    original_question TEXT,
    enhanced BOOLEAN DEFAULT 0,
    FOREIGN KEY (exam_id) REFERENCES exam(id) ON DELETE CASCADE
);
""")

# ======================
# STUDENT_EXAM TABLE (with AI Proctoring)
# ======================
cursor.execute("""
CREATE TABLE student_exam (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    exam_id INTEGER NOT NULL,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    submitted_at DATETIME,
    score FLOAT,
    total_points FLOAT,
    percentage FLOAT,
    passed BOOLEAN,
    tab_switch_count INTEGER DEFAULT 0,
    suspicious_activity_count INTEGER DEFAULT 0,
    time_taken_minutes INTEGER,
    status TEXT DEFAULT 'in_progress',
    completed BOOLEAN DEFAULT 0,

    -- Supports randomization + option shuffling
    question_order TEXT,
    option_mapping TEXT,

    -- Track if exam was force-ended
    force_ended BOOLEAN DEFAULT 0,

    -- AI PROCTORING FIELDS
    proctoring_enabled BOOLEAN DEFAULT 1,
    calibration_completed BOOLEAN DEFAULT 0,
    total_violations INTEGER DEFAULT 0,
    proctoring_status TEXT DEFAULT 'active',

    -- 🔥 NEW (required by your routes.py)
    no_face_count INTEGER DEFAULT 0,
    multiple_faces_count INTEGER DEFAULT 0,


    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (exam_id) REFERENCES exam(id) ON DELETE CASCADE
);
""")

# ======================
# STUDENT_ANSWER TABLE
# ======================
cursor.execute("""
CREATE TABLE student_answer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_exam_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    selected_answer TEXT,
    is_correct BOOLEAN,
    points_earned FLOAT DEFAULT 0.0,
    answered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_exam_id) REFERENCES student_exam(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES question(id) ON DELETE CASCADE
);
""")

# ======================
# ACTIVITY LOG TABLE
# ======================
cursor.execute("""
CREATE TABLE activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_exam_id INTEGER NOT NULL,
    activity_type TEXT NOT NULL,
    description TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    severity TEXT DEFAULT 'low',
    FOREIGN KEY (student_exam_id) REFERENCES student_exam(id) ON DELETE CASCADE
);
""")

# ======================
# EXAM CALIBRATION TABLE
# Stores baseline face position data for each student exam
# ======================
cursor.execute("""
CREATE TABLE exam_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_exam_id INTEGER UNIQUE NOT NULL,
    baseline_yaw FLOAT NOT NULL,
    baseline_pitch FLOAT NOT NULL,
    baseline_roll FLOAT NOT NULL,
    calibrated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    calibration_frames INTEGER DEFAULT 0,
    FOREIGN KEY (student_exam_id) REFERENCES student_exam(id) ON DELETE CASCADE
);
""")

# ======================
# EXAM VIOLATION TABLE
# Tracks all AI proctoring violations detected during exam
# ======================
cursor.execute("""
CREATE TABLE exam_violation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_exam_id INTEGER NOT NULL,
    violation_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    yaw FLOAT,
    pitch FLOAT,
    roll FLOAT,
    deviation_yaw FLOAT,
    deviation_pitch FLOAT,
    deviation_roll FLOAT,
    faces_detected INTEGER,
    FOREIGN KEY (student_exam_id) REFERENCES student_exam(id) ON DELETE CASCADE
);
""")

conn.commit()
print("✅ All tables created successfully.\n")

# ---------------------------------------------------------------------
# 3️⃣ Insert default users with password status
# ---------------------------------------------------------------------
print("👤 Creating admin + demo users...")

now = datetime.utcnow().isoformat()

# Users: (username, email, password, role, is_verified, employee_id, prn_number, full_name, department, password_changed)
users = [
    ("admin", "admin@example.com", "admin123", "admin", 1, "ADMIN001", None, "System Admin", "IT", 1),
    ("faculty_demo", "faculty@cdac.in", "faculty123", "faculty", 1, "FAC1001", None, "Demo Faculty", "IT", 1),
    ("student_demo1", "stud1@cdac.in", "student123", "student", 1, None, "250840325001", "Demo Student 1", "IT", 0),
    ("student_demo2", "stud2@cdac.in", "student123", "student", 1, None, "250840325002", "Demo Student 2", "IT", 0),
    ("student_demo3", "stud3@cdac.in", "student123", "student", 1, None, "250840325003", "Demo Student 3", "IT", 0)
]

for u in users:
    cursor.execute("""
    INSERT INTO users (
        username, email, password_hash, role, is_verified,
        employee_id, prn_number, full_name, department, 
        password_changed, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (u[0], u[1], generate_password_hash(u[2]), u[3], u[4],
          u[5], u[6], u[7], u[8], u[9], now))

conn.commit()
print("✅ Default users added")
print(f"   ✅ Admin & Faculty: password_changed = True (can change anytime)")
print(f"   ✅ Students: password_changed = False (must change on first login)\n")

# ---------------------------------------------------------------------
# 4️⃣ Create sample exam with AI proctoring enabled
# ---------------------------------------------------------------------
print("📝 Creating sample exam...")

cursor.execute("""
INSERT INTO exam (
    title, description, duration_minutes, passing_score, 
    creator_id, is_active, enable_proctoring, max_violations, 
    status, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    "Sample Python Basics Exam",
    "Test your Python programming knowledge",
    30,
    60.0,
    1,  # Created by admin
    1,  # Active
    1,  # Proctoring enabled
    6,  # Max 6 violations
    'active',
    now
))

exam_id = cursor.lastrowid

# Add sample questions
questions = [
    ("What is Python?", "A snake", "A programming language", "A coffee type", "A game", "B", 1.0),
    ("What does print() do?", "Calculates", "Displays output", "Saves file", "Deletes data", "B", 1.0),
    ("Which keyword starts a function?", "start", "def", "function", "begin", "B", 1.0),
    ("What is a variable?", "A constant", "A storage location", "A function", "A loop", "B", 1.0),
    ("What does len() return?", "Length", "Width", "Height", "Depth", "A", 1.0)
]

for i, q in enumerate(questions, 1):
    cursor.execute("""
    INSERT INTO question (
        exam_id, question_text, option_a, option_b, 
        option_c, option_d, correct_answer, points, order_number
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (exam_id, q[0], q[1], q[2], q[3], q[4], q[5], q[6], i))

conn.commit()
print(f"✅ Sample exam created with {len(questions)} questions\n")

# ---------------------------------------------------------------------
# 5️⃣ Verification
# ---------------------------------------------------------------------
print("🔍 Verifying schema...")

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("📌 Tables created:", [t[0] for t in tables])

# Verify users table columns
print("\n🔍 Verifying USERS table columns...")
cursor.execute("PRAGMA table_info(users)")
user_columns = [row[1] for row in cursor.fetchall()]
print("📌 Users table columns:", user_columns)

if 'password_changed' in user_columns and 'password_changed_at' in user_columns:
    print("✅ Password management columns added to users table!")
    
    # Show password status of created users
    cursor.execute("SELECT username, role, password_changed FROM users")
    users_status = cursor.fetchall()
    print("\n📊 User Password Status:")
    for username, role, pwd_changed in users_status:
        status = "✅ Changed" if pwd_changed else "⏳ Must Change"
        print(f"   - {username} ({role}): {status}")
else:
    print("⚠️ Warning: Password management columns missing in users table")

# Verify exam table columns
print("\n🔍 Verifying EXAM table columns...")
cursor.execute("PRAGMA table_info(exam)")
exam_columns = [row[1] for row in cursor.fetchall()]
print("📌 Exam table columns:", exam_columns)

if 'enable_proctoring' in exam_columns and 'max_violations' in exam_columns:
    print("✅ AI Proctoring columns added to exam table!")
else:
    print("⚠️ Warning: AI Proctoring columns missing in exam table")

# Verify student_exam table columns
print("\n🔍 Verifying STUDENT_EXAM table columns...")
cursor.execute("PRAGMA table_info(student_exam)")
student_exam_columns = [row[1] for row in cursor.fetchall()]
print("📌 Student exam table columns:", student_exam_columns)

if 'proctoring_enabled' in student_exam_columns and 'calibration_completed' in student_exam_columns:
    print("✅ AI Proctoring columns added to student_exam table!")
else:
    print("⚠️ Warning: AI Proctoring columns missing in student_exam table")

# Verify new tables
table_names = [t[0] for t in tables]
if 'exam_calibration' in table_names and 'exam_violation' in table_names:
    print("✅ AI Proctoring tables (exam_calibration, exam_violation) created!")
else:
    print("⚠️ Warning: AI Proctoring tables missing")

# Show calibration table structure
print("\n🔍 Verifying EXAM_CALIBRATION table...")
cursor.execute("PRAGMA table_info(exam_calibration)")
calibration_columns = [row[1] for row in cursor.fetchall()]
print("📌 Exam calibration columns:", calibration_columns)

# Show violation table structure
print("\n🔍 Verifying EXAM_VIOLATION table...")
cursor.execute("PRAGMA table_info(exam_violation)")
violation_columns = [row[1] for row in cursor.fetchall()]
print("📌 Exam violation columns:", violation_columns)

# Show created exam
cursor.execute("SELECT id, title, enable_proctoring, max_violations, status FROM exam")
exams = cursor.fetchall()
print("\n📊 Created Exams:")
for exam in exams:
    print(f"   - ID: {exam[0]}, Title: {exam[1]}, Proctoring: {exam[2]}, Max Violations: {exam[3]}, Status: {exam[4]}")

conn.close()

print("\n" + "="*90)
print("🎓 UPDATED DATABASE REBUILD COMPLETE — READY TO USE")
print("="*90)
print("\n🚀 Features Available:")
print("   ✅ Force-end exam capability")
print("   ✅ Exam status tracking (active/ended/archived)")
print("   ✅ Student exam force-end tracking")
print("   ✅ AI Face Detection Proctoring")
print("   ✅ Baseline calibration storage")
print("   ✅ Real-time violation tracking")
print("   ✅ Auto-submit on violation threshold")
print("   ✅ First-time password change for students")
print("   ✅ Optional password change for faculty/admin")
print("="*90)
print("\n💡 Next Steps:")
print("   1. Copy proctor_vision/ folder to project root")
print("   2. Copy models/ folder (OpenVINO model files)")
print("   3. Add password change routes to routes.py")
print("   4. Create change_password.html template")
print("   5. Update login route to check password_changed")
print("   6. Update routes.py with proctoring routes")
print("   7. Replace exam.js with exam_enhanced.js")
print("   8. Add proctor_styles.css to static/css/")
print("   9. Start the application!")
print("="*90)
print("\n📋 Default Credentials:")
print("   Admin: admin / admin123 (password already changed)")
print("   Faculty: faculty_demo / faculty123 (password already changed)")
print("   Student1: student_demo1 / student123 (MUST change password on first login)")
print("   Student2: student_demo2 / student123 (MUST change password on first login)")
print("   Student3: student_demo3 / student123 (MUST change password on first login)")
print("="*90 + "\n")