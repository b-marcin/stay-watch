from datetime import date

from stay_watch.plan import candidate_stays
from tests.support import make_search


def keys(search):
    return [stay.key for stay in candidate_stays(search)]


def test_two_nights_including_saturday_are_friday_and_saturday_checkins():
    search = make_search(window_start=date(2026, 12, 1), window_end=date(2027, 1, 1))
    assert keys(search) == [
        "2026-12-04_2026-12-06",
        "2026-12-05_2026-12-07",
        "2026-12-11_2026-12-13",
        "2026-12-12_2026-12-14",
        "2026-12-18_2026-12-20",
        "2026-12-19_2026-12-21",
        "2026-12-25_2026-12-27",
        "2026-12-26_2026-12-28",
    ]


def test_three_nights_including_saturday_are_thu_fri_sat_checkins():
    search = make_search(
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 15),
        nights=3,
    )
    assert keys(search) == [
        "2026-12-03_2026-12-06",
        "2026-12-04_2026-12-07",
        "2026-12-05_2026-12-08",
        "2026-12-10_2026-12-13",
        "2026-12-11_2026-12-14",
        "2026-12-12_2026-12-15",
    ]


def test_window_edges_drop_stays_that_start_too_early_or_end_too_late():
    late_start = make_search(window_start=date(2026, 12, 4), window_end=date(2026, 12, 15), nights=3)
    assert "2026-12-03_2026-12-06" not in keys(late_start)
    assert keys(late_start)[0] == "2026-12-04_2026-12-07"

    early_end = make_search(window_start=date(2026, 12, 1), window_end=date(2026, 12, 14), nights=3)
    found = keys(early_end)
    assert "2026-12-12_2026-12-15" not in found
    assert found[-1] == "2026-12-11_2026-12-14"

    only_friday = make_search(window_start=date(2026, 12, 4), window_end=date(2026, 12, 6))
    assert keys(only_friday) == ["2026-12-04_2026-12-06"]


def test_empty_must_include_keeps_every_check_in():
    search = make_search(
        window_start=date(2026, 12, 4),
        window_end=date(2026, 12, 7),
        nights=1,
        must_include=frozenset(),
    )
    assert keys(search) == [
        "2026-12-04_2026-12-05",
        "2026-12-05_2026-12-06",
        "2026-12-06_2026-12-07",
    ]
