import csv
import sqlite3
from werkzeug.security import generate_password_hash
from datetime import datetime
import os

DB_PATH = "exam_platform.db"
CSV_PATH = "students.csv"

# --- Check Files ---
if not os.path.exists(DB_PATH):
    print(f"❌ Database not found: {DB_PATH}")
    exit(1)
if not os.path.exists(CSV_PATH):
    print(f"❌ CSV file not found: {CSV_PATH}")
    exit(1)

# --- Connect to database ---
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# --- Read CSV ---
with open(CSV_PATH, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    students = list(reader)

print(f"📋 Found {len(students)} students in CSV file")

# --- Insert Students ---
inserted, skipped = 0, 0
for s in students:
    try:
        username = s.get("username") or s.get("Username") or s.get("user")
        email = s.get("email") or s.get("Email")
        prn = s.get("prn_number") or s.get("PRN") or s.get("prn")
        full_name = s.get("full_name") or s.get("name") or s.get("Full Name")
        roll_id = s.get("roll_id") or s.get("Roll") or None
        batch = s.get("batch") or s.get("Batch") or None
        phone = s.get("phone") or s.get("Phone") or None
        department = s.get("department") or s.get("Department") or None

        # Skip invalid rows
        if not username or not email:
            print(f"⚠️ Skipping incomplete record: {s}")
            skipped += 1
            continue

        # Check duplicates
        cursor.execute("SELECT id FROM user WHERE email=? OR prn_number=?", (email, prn))
        if cursor.fetchone():
            print(f"⏩ Skipping existing student: {email}")
            skipped += 1
            continue

        # Create hashed password
        password_hash = generate_password_hash("student123")
        now = datetime.utcnow().isoformat()

        # Insert
        cursor.execute("""
            INSERT INTO user (username, email, password_hash, role, is_verified,
                              prn_number, roll_id, batch, department, full_name, phone, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (username, email, password_hash, "student", 1,
              prn, roll_id, batch, department, full_name, phone, now))

        inserted += 1

    except Exception as e:
        print(f"❌ Error adding student {s}: {e}")
        skipped += 1

# --- Commit and Close ---
conn.commit()
conn.close()

print("\n" + "="*60)
print(f"✅ Students added successfully!")
print(f"   ➕ Inserted: {inserted}")
print(f"   ➖ Skipped: {skipped}")
print("="*60)
print("\n📋 Default password for all students: student123")
print("⚠️  Advise students to change it after first login.")
