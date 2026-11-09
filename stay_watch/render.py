from __future__ import annotations

from datetime import date

from stay_watch.model import (
    AlertHit,
    AlertRule,
    Below,
    BelowRecentLowest,
    Catalog,
    Discounted,
    DropPct,
    PerNightBelow,
    Search,
    Stay,
    StayResult,
)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def derived_window_name(start: date, end: date, scan_year: int) -> str:
    """Date span used when a search has no name of its own.

    Years show when the window crosses a year, or when it is not in the scan year.
    """
    show_year = start.year != end.year or start.year != scan_year

    def side(day: date) -> str:
        text = f"{day.day} {_MONTHS[day.month - 1]}"
        if show_year:
            return f"{text} {day.year}"
        return text

    return f"{side(start)} – {side(end)}"


def display_name(search: Search, on: date) -> str:
    if search.name:
        return search.name
    return derived_window_name(search.window_start, search.window_end, on.year)


def format_pln(grosze: int) -> str:
    sign = "-" if grosze < 0 else ""
    whole, rest = divmod(abs(grosze), 100)
    grouped = f"{whole:,}".replace(",", " ")
    if rest == 0:
        return f"{sign}{grouped} zł"
    return f"{sign}{grouped},{rest:02d} zł"


def format_stay(stay: Stay) -> str:
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

    def one(day) -> str:
        return f"{weekdays[day.weekday()]} {day.day} {months[day.month - 1]}"

    return f"{one(stay.check_in)} → {one(stay.check_out)}"


def rule_label(rule: AlertRule) -> str:
    match rule:
        case Below(amount):
            return f"under {format_pln(amount)}"
        case PerNightBelow(amount):
            return f"under {format_pln(amount)} a night"
        case DropPct(pct):
            return f"down at least {pct}%"
        case BelowRecentLowest():
            return "under the 30-day lowest"
        case Discounted():
            return "discounted"


def describe(result: StayResult, catalog: Catalog) -> tuple[str, str, str, str]:
    quote = result.best
    if quote is None:
        return ("", "", "", "")
    offer = catalog.offers.get(quote.offer_id)
    room = catalog.rooms.get(quote.room_id) or f"Room {quote.room_id}"
    if offer is None:
        return (f"Offer {quote.offer_id}", room, "other", "no free cancellation")
    return (offer.name, room, offer.meal_plan_label, offer.cancel_for(quote.profile_id))


def render_table(results: list[StayResult], catalog: Catalog, top: int | None) -> str:
    if top is None:
        visible = results
    else:
        visible = [result for result in results if result.best is not None][:top]
    if not visible:
        return "No stays in this window.\n"
    lines = ["Stay                     Nights  Total        A night    Room / meal / offer"]
    for result in visible:
        when = format_stay(result.stay)
        if result.best is None:
            lines.append(f"{when:<24} {result.stay.nights:<7} sold out")
            continue
        offer, room, meal, cancel = describe(result, catalog)
        total = format_pln(result.best.price)
        each = format_pln(result.best.price // result.stay.nights)
        lines.append(f"{when:<24} {result.stay.nights:<7} {total:<12} {each:<10} {room}; {meal}; {offer}; {cancel}")
    return "\n".join(lines) + "\n"


def alert_subject(hits: list[AlertHit]) -> str:
    groups = group_hits(hits)
    count = len(groups)
    noun = "stay" if count == 1 else "stays"
    lead = groups[0][0]
    when = format_stay(lead.stay_result.stay)
    price = format_pln(lead.stay_result.best.price)
    belows = [hit.rule.amount for hit in hits if isinstance(hit.rule, Below)]
    if belows and len(belows) == len(hits) and all(amount == belows[0] for amount in belows):
        return f"{count} {noun} under {format_pln(belows[0])}: {when} {price}"
    label = "stay alert" if count == 1 else "stay alerts"
    return f"{count} {label}: {when} {price}"


def group_hits(hits: list[AlertHit]) -> list[list[AlertHit]]:
    groups: list[list[AlertHit]] = []
    index: dict[tuple[str, str], list[AlertHit]] = {}
    for hit in hits:
        key = (hit.search_id, hit.stay_result.stay.key)
        if key not in index:
            index[key] = []
            groups.append(index[key])
        index[key].append(hit)
    groups.sort(key=lambda group: (group[0].stay_result.best.price, group[0].stay_result.stay.check_in))
    return groups
