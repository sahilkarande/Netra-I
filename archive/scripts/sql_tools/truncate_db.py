import sqlite3

DB_PATH = 'exam_platform.db'

print("\n" + "="*70)
print("🧹 TRUNCATING ALL TABLES IN DATABASE")
print("="*70 + "\n")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Disable foreign key constraints temporarily
cursor.execute("PRAGMA foreign_keys = OFF;")

# List of all tables to clear (adjust if needed)
tables = [
    "answer",
    "activity_log",
    "student_exam",
    "question",
    "exam",
    "user"
]

# Truncate (delete all rows) and reset autoincrement counters
for table in tables:
    print(f"🗑️  Clearing table: {table} ...", end="")
    cursor.execute(f"DELETE FROM {table};")
    cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}';")  # Reset AUTOINCREMENT
    print(" ✅")

# Re-enable foreign key constraints
cursor.execute("PRAGMA foreign_keys = ON;")
conn.commit()

print("\n" + "="*70)
print("✅ All tables truncated successfully!")
print("="*70 + "\n")

# Verify the database is now empty
for table in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table};")
    count = cursor.fetchone()[0]
    print(f"📊 {table}: {count} rows")

conn.close()
print("\n🎉 Database is clean and ready to use again!")
print("="*70)
