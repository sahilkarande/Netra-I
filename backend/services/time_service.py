"""
The exam hall's local time vs. the UTC the database stores.

Every datetime in the database is naive UTC (``datetime.utcnow()``). People,
though, type and read times in the hall's local time: a faculty member who
sets an exam to open at 10:00 means 10:00 on the wall clock. This module is the
one place that converts between the two.

The zone comes from the ``APP_TIMEZONE`` setting (an IANA name, default
``Asia/Kolkata``). It is resolved from the operating system's time-zone data,
which needs no network; if that data is missing (some minimal containers), a
built-in table of fixed offsets is used instead.
"""

import logging
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional

from flask import current_app, has_app_context

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = 'Asia/Kolkata'

# Fallback when the OS has no tz database. Zones without daylight saving only.
_FIXED_OFFSETS = {
    'Asia/Kolkata': timedelta(hours=5, minutes=30),
    'Asia/Calcutta': timedelta(hours=5, minutes=30),
    'UTC': timedelta(0),
    'Etc/UTC': timedelta(0),
}

_cache = {}


def resolve_zone(name: Optional[str]) -> tzinfo:
    """tzinfo for an IANA name; UTC (with a warning) if it cannot be found."""
    name = (name or DEFAULT_TIMEZONE).strip()
    if name in _cache:
        return _cache[name]
    zone = None
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(name)
    except Exception:
        if name in _FIXED_OFFSETS:
            zone = timezone(_FIXED_OFFSETS[name], name)
        else:
            log.warning('Unknown APP_TIMEZONE %r; showing and reading times as UTC', name)
            zone = timezone.utc
    _cache[name] = zone
    return zone


def app_zone() -> tzinfo:
    name = current_app.config.get('APP_TIMEZONE') if has_app_context() else None
    return resolve_zone(name)


def local_to_utc(value: Optional[datetime]) -> Optional[datetime]:
    """A wall-clock time in the hall's zone -> naive UTC for the database."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=app_zone())
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_local(value: Optional[datetime]) -> Optional[datetime]:
    """Naive UTC from the database -> naive wall-clock time in the hall's zone."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(app_zone()).replace(tzinfo=None)


def localtime(value: Optional[datetime], fmt: str = '%d %b %Y, %H:%M') -> str:
    """Jinja filter: ``{{ exam.start_time|localtime('%d %b, %H:%M') }}``."""
    local = utc_to_local(value)
    return local.strftime(fmt) if local else ''


def init_app(app) -> None:
    import os
    app.config.setdefault('APP_TIMEZONE', os.getenv('APP_TIMEZONE', DEFAULT_TIMEZONE))
    app.jinja_env.filters['localtime'] = localtime
    resolve_zone(app.config['APP_TIMEZONE'])     # warn at start-up, not mid-exam
