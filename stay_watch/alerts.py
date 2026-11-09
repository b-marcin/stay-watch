from __future__ import annotations

import copy
from collections.abc import Mapping

from stay_watch.model import (
    AlertHit,
    AlertRule,
    Below,
    BelowRecentLowest,
    Discounted,
    DropPct,
    PerNightBelow,
    Search,
    StayResult,
)


def rule_id(rule: AlertRule) -> str:
    match rule:
        case Below(amount):
            return f"below:{amount}"
        case PerNightBelow(amount):
            return f"per_night_below:{amount}"
        case DropPct(pct):
            return f"drop_pct:{pct}"
        case BelowRecentLowest():
            return "below_recent_lowest"
        case Discounted():
            return "discounted"


def rule_matches(rule: AlertRule, result: StayResult, previous_price: int | None) -> bool:
    quote = result.best
    if quote is None:
        return False
    match rule:
        case Below(amount):
            return quote.price < amount
        case PerNightBelow(amount):
            return quote.price < amount * result.stay.nights
        case DropPct(pct):
            if previous_price is None or previous_price <= 0:
                return False
            return (previous_price - quote.price) * 100 >= pct * previous_price
        case BelowRecentLowest():
            return quote.recent_lowest is not None and quote.price < quote.recent_lowest
        case Discounted():
            return quote.original is not None


def evaluate(
    search: Search,
    results: list[StayResult],
    previous_prices: Mapping[str, int | None],
    sent: dict,
) -> tuple[list[AlertHit], dict]:
    """Return hits and the next alerts_sent map.

    A hit is sent only the first time, or when the price is lower than the
    last price we sent. A rise keeps that remembered price: the stay is
    re-armed, and the next price below it sends again.
    """
    sent = copy.deepcopy(sent)
    bucket = sent.setdefault(search.id, {})
    hits: list[AlertHit] = []
    for result in results:
        previous = previous_prices.get(result.stay.key)
        for rule in search.alerts:
            if not rule_matches(rule, result, previous):
                continue
            prices = bucket.setdefault(rule_id(rule), {})
            last = prices.get(result.stay.key)
            price = result.best.price
            if last is None or price < last:
                hits.append(AlertHit(search.id, rule, result, previous))
                prices[result.stay.key] = price
    return hits, sent
