"""One attempt, one device (audit P2-11).

The first browser that opens an attempt's paper claims it: a random token goes
into that browser's session and onto the attempt. Requests from any other
session are refused, so a student cannot hand a running exam to a friend on a
second computer, or sit it on two machines at once.

A crashed laptop must not lock a student out, though. If the device holding
the attempt has gone quiet for DEVICE_STALE_SECONDS (no autosave, no status
poll), a new device may take over. That switch is allowed but recorded as a
violation, so the invigilator sees it in the review.

Last-seen times are kept in memory: they change every few seconds per student
and writing them to SQLite would cost a write per poll. A restart forgets them,
which simply means a device switch right after a restart is allowed (and still
recorded).
"""
import os
import secrets
import time

from flask import session

from backend.database import db

DEVICE_STALE_SECONDS = int(os.getenv('DEVICE_STALE_SECONDS', '60'))

_last_seen = {}     # student_exam_id -> unix time of the holding device's last request

# Outcomes of claim()
BOUND = 'bound'           # first device to open the attempt
SAME = 'same'             # the device that already holds it (a refresh)
SWITCHED = 'switched'     # holder went quiet; this device took over (recorded)
BLOCKED = 'blocked'       # holder is still active; refused (recorded)


def _key(attempt_id):
    return f'attempt_device_{attempt_id}'


def touch(attempt):
    _last_seen[attempt.id] = time.time()


def forget(attempt_id):
    _last_seen.pop(attempt_id, None)


def seconds_since_seen(attempt):
    seen = _last_seen.get(attempt.id)
    return None if seen is None else time.time() - seen


def is_current(attempt) -> bool:
    """Is this request coming from the device that holds the attempt?

    An attempt nobody has claimed yet (started before this check existed, or
    never opened) is not bound to anything, so any of its owner's sessions may
    use it.
    """
    if not attempt.device_token:
        return True
    return secrets.compare_digest(session.get(_key(attempt.id)) or '', attempt.device_token)


def _bind(attempt):
    token = secrets.token_urlsafe(24)
    attempt.device_token = token
    session[_key(attempt.id)] = token
    touch(attempt)


def claim(attempt) -> str:
    """Called when the paper is opened. Commits the attempt."""
    from backend.services.proctor_service import log_proctor_event

    if attempt.device_token and is_current(attempt):
        touch(attempt)
        return SAME

    if not attempt.device_token:
        _bind(attempt)
        db.session.commit()
        return BOUND

    quiet = seconds_since_seen(attempt)
    if quiet is not None and quiet < DEVICE_STALE_SECONDS:
        log_proctor_event(
            attempt.id, 'second_device', 'high',
            'The exam was opened on a second device while the first was still in use. '
            'The second device was refused.')
        db.session.commit()
        return BLOCKED

    _bind(attempt)
    attempt.device_switches = (attempt.device_switches or 0) + 1
    log_proctor_event(
        attempt.id, 'device_switch', 'medium',
        'The exam moved to another device after the first one stopped responding'
        + (f' for {int(quiet)} s.' if quiet is not None else ' (server had restarted).'))
    db.session.commit()
    return SWITCHED
