from __future__ import annotations

from stay_watch.model import Catalog, Quote, Search, Stay, StayResult


def per_night(price: int, nights: int) -> int:
    return price // nights


def _matches(name: str, filters: tuple[str, ...]) -> bool:
    if not filters:
        return True
    haystack = name.casefold()
    return any(item.casefold() in haystack for item in filters)


def select_quotes(
    quotes: list[Quote],
    catalog: Catalog,
    offer_filter: tuple[str, ...],
    room_filter: tuple[str, ...],
) -> list[Quote]:
    chosen: list[Quote] = []
    for quote in quotes:
        offer = catalog.offers.get(quote.offer_id)
        offer_name = offer.name if offer else ""
        room_name = catalog.rooms.get(quote.room_id, "")
        if _matches(offer_name, offer_filter) and _matches(room_name, room_filter):
            chosen.append(quote)
    return chosen


def cheapest(quotes: list[Quote]) -> Quote | None:
    if not quotes:
        return None
    return min(quotes, key=lambda quote: (quote.price, quote.offer_id, quote.room_id))


def rank_search(
    search: Search,
    stays: list[Stay],
    quotes_by_key: dict[str, list[Quote]],
    catalog: Catalog,
) -> list[StayResult]:
    results: list[StayResult] = []
    for stay in stays:
        matched = select_quotes(
            quotes_by_key.get(stay.key, []),
            catalog,
            search.offer_filter,
            search.room_filter,
        )
        offer_ids = {quote.offer_id for quote in matched}
        results.append(StayResult(stay, cheapest(matched), len(offer_ids)))
    available = [result for result in results if result.best is not None]
    sold_out = [result for result in results if result.best is None]
    available.sort(key=lambda result: (result.best.price, result.stay.check_in, result.stay.check_out))
    sold_out.sort(key=lambda result: (result.stay.check_in, result.stay.check_out))
    return available + sold_out


def priced_results(results: list[StayResult], top: int | None) -> list[StayResult]:
    priced = [result for result in results if result.best is not None]
    if top is None:
        return priced
    return priced[:top]
