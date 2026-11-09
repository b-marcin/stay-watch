from datetime import date

from stay_watch.model import Quote, Search, Stay


def stay(start: str, end: str) -> Stay:
    return Stay(date.fromisoformat(start), date.fromisoformat(end))


def make_search(**overrides) -> Search:
    values = dict(
        id="s",
        name="S",
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 31),
        nights=2,
        must_include=frozenset({5}),
        adults=2,
        top=3,
        codes=(),
        offer_filter=(),
        room_filter=(),
        alerts=(),
    )
    values.update(overrides)
    return Search(**values)


def make_quote(price: int, stay_value: Stay | None = None, **overrides) -> Quote:
    values = dict(
        offer_id=1,
        room_id=1,
        recent_lowest=None,
        original=None,
        rooms_left=5,
        profile_id=1,
    )
    values.update(overrides)
    return Quote(stay=stay_value or stay("2026-12-04", "2026-12-06"), price=price, **values)
