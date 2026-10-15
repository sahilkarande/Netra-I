#!/usr/bin/env python3
"""
Back up the Netra-I SQLite database — safe to run while the server is live.

Uses SQLite's online backup API, so the copy is consistent even mid-exam
(unlike copying the .db file, which can miss data still in the -wal file).
Each backup is integrity-checked; old ones are pruned.

Usage:
    python scripts/backup_db.py                 # → backup/exam_platform_YYYYmmdd_HHMMSS.db
    python scripts/backup_db.py --keep 50       # keep the newest 50 (default 20)
    python scripts/backup_db.py --dest /mnt/usb # another folder (e.g. a USB stick)

Run it before every exam and on a schedule during one, e.g. every 10 minutes:
    */10 * * * * cd /path/to/Netra-I && venv/bin/python scripts/backup_db.py

Restore: stop the server, copy a backup over instance/exam_platform.db
(and delete instance/exam_platform.db-wal / -shm if present), start again.
"""

import argparse
import glob
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_db_path(database_url=None):
    """The SQLite file the app uses (DATABASE_URL, default instance/exam_platform.db)."""
    url = database_url if database_url is not None else os.getenv('DATABASE_URL', 'sqlite:///exam_platform.db')
    if not url.startswith('sqlite:///'):
        raise SystemExit('Only SQLite databases are handled by this script; use your '
                         "database server's own backup tool for DATABASE_URL=" + url.split(':')[0])
    path = url[len('sqlite:///'):]
    if not os.path.isabs(path):
        # Flask-SQLAlchemy resolves relative SQLite paths inside instance/
        path = os.path.join(ROOT, 'instance', path)
    return path


def backup(src_path, dest_dir, keep=20, now=None):
    if not os.path.exists(src_path):
        raise FileNotFoundError(f'Database not found: {src_path}')
    os.makedirs(dest_dir, exist_ok=True)

    stamp = (now or datetime.now()).strftime('%Y%m%d_%H%M%S')
    base = os.path.splitext(os.path.basename(src_path))[0]
    dest_path = os.path.join(dest_dir, f'{base}_{stamp}.db')

    src = sqlite3.connect(f'file:{src_path}?mode=ro', uri=True)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
        # A backup must be one self-contained file: no -wal/-shm companions
        dst.execute('PRAGMA journal_mode=DELETE')
        result = dst.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        dst.close()
        src.close()
    if result != 'ok':
        os.remove(dest_path)
        raise RuntimeError(f'Backup failed integrity check: {result}')

    backups = sorted(glob.glob(os.path.join(dest_dir, f'{base}_*.db')))
    for old in backups[:-keep] if keep > 0 else []:
        os.remove(old)
    return dest_path


def main(argv=None):
    parser = argparse.ArgumentParser(description='Back up the Netra-I database.')
    parser.add_argument('--dest', default=os.path.join(ROOT, 'backup'), help='destination folder')
    parser.add_argument('--keep', type=int, default=20, help='how many backups to keep (0 = all)')
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, '.env'))
    except ImportError:
        pass

    path = backup(resolve_db_path(), args.dest, keep=args.keep)
    size_kb = os.path.getsize(path) // 1024
    print(f'Backup written: {path} ({size_kb} KB, integrity ok)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
