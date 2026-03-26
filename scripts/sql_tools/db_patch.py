import sqlite3

DB_NAME = "exam_platform.db"

print("📌 Applying DB patch...")

conn = sqlite3.connect(DB_NAME)
cur = conn.cursor()

# Enable foreign keys
cur.execute("PRAGMA foreign_keys = ON;")

# ------------------------
# Add missing columns
# ------------------------
def add_column_if_missing(table, column, ddl):
    cur.execute(f"PRAGMA table_info({table});")
    cols = [c[1] for c in cur.fetchall()]
    if column not in cols:
        print(f"➕ Adding column {column} to {table}")
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl};")
    else:
        print(f"✔ Column {column} already exists in {table}")

# Add allow_all_students (default TRUE)
add_column_if_missing("exam", "allow_all_students", "BOOLEAN DEFAULT 1")

# Add allowed_students (stores JSON list)
add_column_if_missing("exam", "allowed_students", "TEXT DEFAULT NULL")

conn.commit()
conn.close()

print("🎉 Patch applied successfully!")
