"""
REPAIR + TRUNCATE DATABASE SCRIPT
Fixes missing columns (question_order, option_mapping)
and resets all tables cleanly for development/testing.
"""

import sqlite3

DB_PATH = 'exam_platform.db'

print("\n" + "="*80)
print("🧹 DATABASE REPAIR & TRUNCATE TOOL")
print("="*80 + "\n")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# 1️⃣ Disable foreign key constraints
cursor.execute("PRAGMA foreign_keys = OFF;")

# 2️⃣ Check if columns exist, if not — add them
cursor.execute("PRAGMA table_info(student_exam)")
columns = [col[1] for col in cursor.fetchall()]

added = []
if "question_order" not in columns:
    cursor.execute("ALTER TABLE student_exam ADD COLUMN question_order TEXT;")
    added.append("question_order")

if "option_mapping" not in columns:
    cursor.execute("ALTER TABLE student_exam ADD COLUMN option_mapping TEXT;")
    added.append("option_mapping")

if added:
    print(f"✅ Added missing columns: {', '.join(added)}")
else:
    print("✅ All required columns already exist.")

# 3️⃣ Truncate all data
tables = ["answer", "activity_log", "student_exam", "question", "exam", "users"]

for table in tables:
    print(f"🗑️  Clearing table: {table} ...", end="")
    cursor.execute(f"DELETE FROM {table};")
    cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}';")  # Reset AUTOINCREMENT
    print(" ✅")

# 4️⃣ Re-enable foreign keys
cursor.execute("PRAGMA foreign_keys = ON;")
conn.commit()

# 5️⃣ Verification
print("\n" + "="*80)
print("📋 VERIFICATION RESULTS")
print("="*80)

for table in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table};")
    count = cursor.fetchone()[0]
    print(f"📊 {table}: {count} rows")

cursor.execute("PRAGMA table_info(student_exam)")
print("\n🧩 student_exam Columns:")
for col in cursor.fetchall():
    print("  -", col[1])

conn.close()

print("\n✅ Database repair complete! Schema is up-to-date.")
print("="*80)
