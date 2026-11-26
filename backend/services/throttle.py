"""Counters with a time window, stored in the database (audit P2-4).

Used for every brute-force limit: password sign-in, face sign-in, exam access
keys, and the password-hint / face-availability lookups. Keeping them in the
database means restarting the server does not reset a lockout, and an expired
window is simply overwritten the next time the key is hit.
"""
from datetime import datetime, timedelta
from typing import Tuple

from backend.database import db
from backend.models import LoginThrottle

DEFAULT_LIMIT = 5
DEFAULT_MINUTES = 10


def _live_row(key, minutes):
    row = db.session.get(LoginThrottle, key)
    if row and datetime.utcnow() - row.window_start > timedelta(minutes=minutes):
        return row, False
    return row, bool(row)


def is_locked_out(key: str, limit: int = DEFAULT_LIMIT,
                  minutes: int = DEFAULT_MINUTES) -> Tuple[bool, int]:
    """(locked, minutes_remaining) — locked once `limit` hits fall in the window."""
    row, live = _live_row(key, minutes)
    if not live or row.count < limit:
        return False, 0
    elapsed = datetime.utcnow() - row.window_start
    remaining = minutes - int(elapsed.total_seconds() // 60)
    return True, max(1, remaining)


def record(key: str, minutes: int = DEFAULT_MINUTES) -> int:
    """Count one hit against `key`; returns the count in the current window."""
    row, live = _live_row(key, minutes)
    now = datetime.utcnow()
    if row is None:
        row = LoginThrottle(key=key[:200], count=1, window_start=now)
        db.session.add(row)
    elif live:
        row.count += 1
    else:
        row.count, row.window_start = 1, now
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()       # a racing request inserted the same key; theirs counts
    return row.count


def clear(key: str):
    row = db.session.get(LoginThrottle, key)
    if row:
        db.session.delete(row)
        db.session.commit()


def purge_expired(older_than_minutes: int = 24 * 60) -> int:
    """Drop rows whose window ended long ago. Returns how many were removed."""
    cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)
    n = LoginThrottle.query.filter(LoginThrottle.window_start < cutoff).delete()
    db.session.commit()
    return n
