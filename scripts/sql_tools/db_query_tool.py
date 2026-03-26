"""
SQLite Database Query Tool for Exam Platform
--------------------------------------------
Use this script to interactively run SQL queries
on your existing exam_platform.db database.

Usage:
    python db_query_tool.py
"""

import sqlite3
import csv
import os
from tabulate import tabulate

DB_PATH = "exam_platform.db"


def connect_db():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found: {DB_PATH}")
        exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    print(f"✅ Connected to database: {DB_PATH}")
    return conn


def run_query(conn, query):
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        if query.strip().lower().startswith("select"):
            rows = cursor.fetchall()
            if rows:
                headers = rows[0].keys()
                table = [[row[h] for h in headers] for row in rows]
                print(tabulate(table, headers=headers, tablefmt="grid"))
                print(f"\n🔹 {len(rows)} row(s) returned.")
            else:
                print("⚠️ No rows found.")
        else:
            conn.commit()
            print(f"✅ Query executed successfully ({cursor.rowcount} row(s) affected).")
    except Exception as e:
        print(f"❌ Error executing query: {e}")


def export_to_csv(conn, query, filename):
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        if not rows:
            print("⚠️ No data to export.")
            return
        headers = rows[0].keys()
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([row[h] for h in headers])
        print(f"✅ Results exported to {filename}")
    except Exception as e:
        print(f"❌ Error exporting data: {e}")


def main():
    conn = connect_db()
    print("\n💡 Type SQL commands to query the database.")
    print("   Example: SELECT * FROM users LIMIT 5;")
    print("   Type 'export <filename>.csv <SQL query>' to save to CSV.")
    print("   Type 'exit' or 'quit' to close.\n")

    while True:
        try:
            query = input("SQL> ").strip()
            if not query:
                continue
            if query.lower() in {"exit", "quit"}:
                break

            if query.lower().startswith("export "):
                parts = query.split(" ", 2)
                if len(parts) < 3:
                    print("⚠️ Usage: export <filename>.csv <SQL query>")
                    continue
                filename, sql_query = parts[1], parts[2]
                export_to_csv(conn, sql_query, filename)
                continue

            run_query(conn, query)
        except KeyboardInterrupt:
            print("\n⏹️  Interrupted.")
            break
        except EOFError:
            print("\n👋 Exiting...")
            break

    conn.close()
    print("🔒 Connection closed.")


if __name__ == "__main__":
    try:
        from tabulate import tabulate
    except ImportError:
        print("⚠️ 'tabulate' not found. Installing...")
        os.system("pip install tabulate")
        from tabulate import tabulate

    main()
