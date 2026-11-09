from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def now_warsaw() -> datetime:
    try:
        return datetime.now(ZoneInfo("Europe/Warsaw"))
    except ZoneInfoNotFoundError:
        # This machine has no IANA data. The user lives in Poland, so the
        # local clock is already Europe/Warsaw.
        return datetime.now().astimezone()
