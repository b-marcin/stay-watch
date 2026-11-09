from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Windows CPython often ships without the IANA database. Europe/Warsaw follows
# the EU rule: UTC+1, and UTC+2 from the last Sunday of March 01:00 UTC until
# the last Sunday of October 01:00 UTC.
_zone: ZoneInfo | None | bool = False


def warsaw_zone() -> ZoneInfo | None:
    global _zone
    if _zone is False:
        try:
            _zone = ZoneInfo("Europe/Warsaw")
        except ZoneInfoNotFoundError:
            _zone = None
    return _zone if isinstance(_zone, ZoneInfo) else None


def now_warsaw() -> datetime:
    raw = os.environ.get("STAY_WATCH_NOW")
    if raw:
        return in_warsaw(datetime.fromisoformat(raw))
    zone = warsaw_zone()
    if zone is not None:
        return datetime.now(zone)
    return in_warsaw(datetime.now(timezone.utc))


def in_warsaw(moment: datetime) -> datetime:
    zone = warsaw_zone()
    if zone is not None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=zone)
        return moment.astimezone(zone)
    if moment.tzinfo is None:
        offset = _offset_for_wall(moment)
        return moment.replace(tzinfo=timezone(offset))
    utc = moment.astimezone(timezone.utc)
    return utc.astimezone(timezone(_offset_for_utc(utc)))


def _last_sunday(year: int, month: int) -> datetime:
    if month == 12:
        cursor = datetime(year + 1, 1, 1)
    else:
        cursor = datetime(year, month + 1, 1)
    cursor -= timedelta(days=1)
    cursor -= timedelta(days=(cursor.weekday() + 1) % 7)
    return cursor


def _offset_for_utc(moment: datetime) -> timedelta:
    utc = moment.astimezone(timezone.utc)
    start = _last_sunday(utc.year, 3).replace(hour=1, tzinfo=timezone.utc)
    end = _last_sunday(utc.year, 10).replace(hour=1, tzinfo=timezone.utc)
    if start <= utc < end:
        return timedelta(hours=2)
    return timedelta(hours=1)


def _offset_for_wall(naive: datetime) -> timedelta:
    for hours in (1, 2):
        offset = timedelta(hours=hours)
        guess = naive.replace(tzinfo=timezone(offset))
        if _offset_for_utc(guess) == offset:
            return offset
    return timedelta(hours=1)
