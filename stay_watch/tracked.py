from __future__ import annotations

from stay_watch.model import StayResult


def remember_tracked(result: StayResult, entry: dict | None) -> dict:
    """Keep the prices the dashboard shows. Tracked trips never send email."""
    price = None if result.best is None else result.best.price
    if isinstance(entry, dict) and "first_price" in entry:
        first = entry["first_price"]
    else:
        first = price
    return {"first_price": first, "last_seen_price": price}
