from __future__ import annotations

from datetime import date, timedelta

from stay_watch.model import Search, Stay


def candidate_stays(search: Search, today: date | None = None) -> list[Stay]:
    """Check-ins from the window start through window_end minus nights.

    When today is set, check-ins before that Warsaw date are left out. A
    check-in on today is kept. The booking feed rejects anything earlier.
    window_end is the latest check-out. An empty must_include keeps every
    check-in still in range.
    """
    if search.nights < 1:
        return []
    last_check_in = search.window_end - timedelta(days=search.nights)
    start = search.window_start
    if today is not None and today > start:
        start = today
    stays: list[Stay] = []
    day = start
    while day <= last_check_in:
        stay = Stay(day, day + timedelta(days=search.nights))
        if all(stay.covers_weekday(weekday) for weekday in search.must_include):
            stays.append(stay)
        day += timedelta(days=1)
    return stays
