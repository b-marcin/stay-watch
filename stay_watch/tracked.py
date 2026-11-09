from __future__ import annotations

import copy
from dataclasses import dataclass

from stay_watch.model import NotifyMode, StayResult, TrackedTrip


@dataclass(frozen=True)
class TrackedNotice:
    trip: TrackedTrip
    reasons: tuple[str, ...]
    previous_price: int | None
    result: StayResult


def evaluate_tracked(
    trip: TrackedTrip,
    result: StayResult,
    entry: dict | None,
) -> tuple[TrackedNotice | None, dict]:
    """Decide whether this scan emails about one tracked trip.

    The first priced scan says "Now tracking" once. After that, ``any`` emails
    on every change and ``drop`` emails only when the price is under the last
    emailed price. A sold-out stay becoming available emails in both modes.
    ``below`` uses the same watermark as a search alert: it sends when the
    price is under the threshold and under the last below-email, and a rise
    leaves that remembered price in place.
    """
    state = copy.deepcopy(entry) if isinstance(entry, dict) else {}
    price = None if result.best is None else result.best.price
    first = not state.get("introduced")
    has_previous = "last_seen_price" in state
    previous = state.get("last_seen_price") if has_previous else None
    reasons: list[str] = []

    if first:
        state["introduced"] = True
        state["first_price"] = price
        if price is not None:
            reasons.append("now_tracking")
    else:
        reasons.extend(
            _change_reasons(
                trip.notify,
                price,
                previous,
                state.get("last_notified_price"),
                has_previous,
            )
        )

    if price is not None and trip.below is not None and price < trip.below:
        watermark = state.get("below_sent")
        if not isinstance(watermark, int) or price < watermark:
            state["below_sent"] = price
            if "now_tracking" not in reasons:
                reasons.append("below")

    if reasons and price is not None:
        state["last_notified_price"] = price
    state["last_seen_price"] = price
    if not reasons:
        return None, state
    shown_previous = None if "now_tracking" in reasons else previous
    return TrackedNotice(trip, tuple(reasons), shown_previous, result), state


def _change_reasons(
    notify: NotifyMode,
    price: int | None,
    previous: int | None,
    last_notified: object,
    has_previous: bool,
) -> list[str]:
    if not has_previous:
        return []
    if previous is None and price is not None:
        return ["available"]
    if price is None and previous is not None:
        return ["sold_out"] if notify == "any" else []
    if price is None or previous is None or price == previous:
        return []
    if notify == "any":
        return ["change"]
    if not isinstance(last_notified, int) or price < last_notified:
        return ["change"]
    return []
