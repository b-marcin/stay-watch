from __future__ import annotations

import copy
from collections.abc import Mapping

from stay_watch.model import AlertHit, PriceAlert, Search, StayResult


def qualifies(alert: PriceAlert, result: StayResult, previous_price: int | None) -> bool:
    quote = result.best
    if quote is None:
        return False
    price = quote.price
    if price >= alert.under:
        return False
    if alert.recent_low and (quote.recent_lowest is None or price >= quote.recent_lowest):
        return False
    if alert.discounted and quote.original is None:
        return False
    if alert.drop_pct is not None:
        if previous_price is None or previous_price <= 0:
            return False
        if (previous_price - price) * 100 < alert.drop_pct * previous_price:
            return False
    return True


def evaluate(
    search: Search,
    results: list[StayResult],
    previous_prices: Mapping[str, int | None],
    sent: dict,
) -> tuple[list[AlertHit], dict]:
    """Return hits and the next alerts_sent map.

    A hit is sent only the first time, or when the price is at least
    ``again_cheaper_by`` below the last emailed price. A rise keeps that
    remembered price.
    """
    sent = copy.deepcopy(sent)
    alert = search.alert
    if alert is None:
        return [], sent
    bucket = sent.get(search.id)
    if not isinstance(bucket, dict):
        bucket = {}
    hits: list[AlertHit] = []
    changed = False
    for result in results:
        if result.best is None or not qualifies(alert, result, previous_prices.get(result.stay.key)):
            continue
        last = bucket.get(result.stay.key)
        if isinstance(last, bool) or not isinstance(last, int):
            last = None
        price = result.best.price
        if last is not None and not (price < last and last - price >= alert.again_cheaper_by):
            continue
        hits.append(AlertHit(search.id, result, previous_prices.get(result.stay.key), last))
        bucket[result.stay.key] = price
        changed = True
    if changed:
        sent[search.id] = bucket
    return hits, sent
