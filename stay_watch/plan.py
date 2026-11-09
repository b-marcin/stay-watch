from __future__ import annotations

from datetime import timedelta

from stay_watch.model import Search, Stay


def candidate_stays(search: Search) -> list[Stay]:
    """Check-ins from window_start through window_end minus nights.

    window_end is the latest check-out, so the last check-in is that many
    nights earlier. An empty must_include keeps every check-in in range.
    """
    if search.nights < 1:
        return []
    last_check_in = search.window_end - timedelta(days=search.nights)
    stays: list[Stay] = []
    day = search.window_start
    while day <= last_check_in:
        stay = Stay(day, day + timedelta(days=search.nights))
        if all(stay.covers_weekday(weekday) for weekday in search.must_include):
            stays.append(stay)
        day += timedelta(days=1)
    return stays
