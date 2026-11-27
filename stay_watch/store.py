from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from stay_watch.config import WEEKDAY_NAMES
from stay_watch.model import (
    Catalog,
    PriceAlert,
    Search,
    StayResult,
    TrackedTrip,
)
from stay_watch.alerts import qualifies
from stay_watch.rank import per_night
from stay_watch.render import describe, display_name


class StoreError(Exception):
    pass


def term_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    """Each term is a stem. It matches the word it starts, plus that word's ending.

    Words in a multi-word term may be split by any whitespace, including NBSP.
    """
    parts: list[str] = []
    cleaned = sorted({term.strip() for term in terms if term and term.strip()}, key=len, reverse=True)
    for term in cleaned:
        words = [word for word in re.split(r"\s+", term) if word]
        if not words:
            continue
        body = r"\s+".join(re.escape(word) + r"[^\W\d_]*" for word in words)
        parts.append(r"(?<![\w])" + body)
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


def redact(value: object, terms: Sequence[str]) -> object:
    pattern = term_pattern(terms)
    if pattern is None:
        return value
    return _redact(value, pattern)


def _redact(value: object, pattern: re.Pattern[str]) -> object:
    if isinstance(value, str):
        return pattern.sub("the hotel", value)
    if isinstance(value, list):
        return [_redact(item, pattern) for item in value]
    if isinstance(value, dict):
        return {_redact(key, pattern): _redact(item, pattern) for key, item in value.items()}
    return value


def history_group(
    adults: int,
    codes: tuple[str, ...],
    offer_filter: tuple[str, ...] = (),
    room_filter: tuple[str, ...] = (),
) -> str:
    # Filters sit in the key so two searches with the same party and codes
    # but different room lists do not overwrite one price series.
    return (
        f"adults={adults};codes={','.join(codes)};"
        f"offers={','.join(offer_filter)};rooms={','.join(room_filter)}"
    )


def default_state() -> dict:
    return {
        "alerts_sent": {},
        "alert_baselines": {},
        "tracked": {},
        "consecutive_failures": 0,
    }


def alert_baseline_key(search: Search) -> str:
    context = [
        search.id,
        search.window_start.isoformat(),
        search.window_end.isoformat(),
        search.nights,
        sorted(search.must_include),
        search.adults,
        search.codes,
        search.offer_filter,
        search.room_filter,
        _alert_context(search.alert),
    ]
    return "0x" + hashlib.sha256(json.dumps(context).encode("utf-8")).hexdigest()


def prune_alert_baselines(baselines: dict, today: date, searches: Sequence[Search] | None) -> dict:
    if not isinstance(baselines, dict):
        return {}
    known = None if searches is None else {alert_baseline_key(search) for search in searches}
    kept: dict = {}
    for context, prices in baselines.items():
        if (known is not None and context not in known) or not isinstance(prices, dict):
            continue
        current = {}
        for stay_key, price in prices.items():
            check_in = check_in_of(str(stay_key))
            if check_in is not None and check_in >= today and (price is None or type(price) is int):
                current[stay_key] = price
        if current:
            kept[context] = current
    return kept


def check_in_of(stay_key: str) -> date | None:
    try:
        return date.fromisoformat(stay_key.split("_", 1)[0])
    except ValueError:
        return None


def flatten_alerts_sent(sent: object) -> dict:
    """Collapse a rule-bucket map to one price per stay, the lowest one stored."""
    if not isinstance(sent, dict):
        return {}
    flat: dict = {}
    for search_id, bucket in sent.items():
        if not isinstance(bucket, dict):
            continue
        stays: dict[str, int] = {}
        if any(isinstance(value, dict) for value in bucket.values()):
            for rule_bucket in bucket.values():
                if not isinstance(rule_bucket, dict):
                    continue
                for stay_key, price in rule_bucket.items():
                    _keep_lower(stays, stay_key, price)
        else:
            for stay_key, price in bucket.items():
                _keep_lower(stays, stay_key, price)
        if stays:
            flat[str(search_id)] = stays
    return flat


def _keep_lower(stays: dict[str, int], stay_key: object, price: object) -> None:
    if isinstance(price, bool) or not isinstance(price, int):
        return
    key = str(stay_key)
    current = stays.get(key)
    if current is None or price < current:
        stays[key] = price


def _alert_context(alert: PriceAlert | None) -> list | None:
    if alert is None:
        return None
    return [alert.under, alert.again_cheaper_by, alert.recent_low, alert.discounted, alert.drop_pct]


def prune_alerts_sent(sent: dict, today: date, searches: Sequence[Search] | None) -> dict:
    """Drop past stays, searches that are gone, and searches that no longer email."""
    sent = flatten_alerts_sent(sent)
    known: dict[str, Search] | None = None
    if searches is not None:
        known = {search.id: search for search in searches}
    kept: dict = {}
    for search_id, stays in sent.items():
        if known is not None and (search_id not in known or known[search_id].alert is None):
            continue
        if not isinstance(stays, dict):
            continue
        kept_stays = {}
        for stay_key, price in stays.items():
            check_in = check_in_of(str(stay_key))
            if check_in is not None and check_in < today:
                continue
            kept_stays[stay_key] = price
        if kept_stays:
            kept[search_id] = kept_stays
    return kept


def prune_tracked_state(tracked_state: dict, today: date, trips: Sequence[TrackedTrip] | None) -> dict:
    """Drop trips removed from the config, and trips whose check-in has passed."""
    if not isinstance(tracked_state, dict):
        return {}
    known = None if trips is None else {trip.id for trip in trips}
    kept: dict = {}
    for trip_id, entry in tracked_state.items():
        if not isinstance(entry, dict):
            continue
        if known is not None and trip_id not in known:
            continue
        check_in = check_in_of(str(trip_id))
        if check_in is not None and check_in < today:
            continue
        kept[trip_id] = entry
    return kept


def update_history(
    history: dict,
    group: str,
    observations: list[tuple[str, date, int | None, str | None, str | None]],
    timestamp: str,
    today: date,
) -> dict:
    history = copy.deepcopy(history)
    if not isinstance(history, dict):
        history = {}
    bucket = history.get(group)
    if not isinstance(bucket, dict):
        bucket = {}
    for stay_key, check_in, price, offer_name, room_name in observations:
        if check_in < today:
            continue
        rows = list(bucket.get(stay_key) or [])
        if not rows or rows[-1][1] != price:
            rows.append([timestamp, price, offer_name, room_name])
        bucket[stay_key] = rows
    for stay_key in list(bucket):
        check_in = check_in_of(stay_key)
        if check_in is not None and check_in < today:
            del bucket[stay_key]
    if bucket:
        history[group] = bucket
    else:
        history.pop(group, None)
    return history


def last_recorded_price(rows: list) -> int | None:
    if not rows:
        return None
    price = rows[-1][1]
    return price if isinstance(price, int) else None


def lowest_seen(rows: list, current: int | None) -> int | None:
    prices = [row[1] for row in rows if isinstance(row, list) and len(row) > 1 and isinstance(row[1], int)]
    if isinstance(current, int):
        prices.append(current)
    return min(prices) if prices else None


def trend_baseline(rows: list, current: int | None) -> int | None:
    numeric = [row[1] for row in rows if isinstance(row, list) and len(row) > 1 and isinstance(row[1], int)]
    if current is None or not numeric:
        return None
    if numeric[-1] != current:
        return numeric[-1]
    if len(numeric) >= 2:
        return numeric[-2]
    return None


def alert_payload(alert: PriceAlert | None) -> dict | None:
    if alert is None:
        return None
    return {
        "under": alert.under // 100,
        "again_cheaper_by": alert.again_cheaper_by // 100,
        "require": {
            "recent_low": alert.recent_low,
            "discounted": alert.discounted,
            "drop_pct": alert.drop_pct,
        },
    }


def describe_quote(catalog: Catalog, result: StayResult) -> dict:
    quote = result.best
    if quote is None:
        return {
            "price": None,
            "per_night": None,
            "recent_lowest": None,
            "original": None,
            "rooms_left": None,
            "offer_name": None,
            "room_name": None,
            "meal_plan": None,
            "cancel": None,
        }
    offer_name, room_name, meal, cancel = describe(result, catalog)
    return {
        "price": quote.price,
        "per_night": per_night(quote.price, result.stay.nights),
        "recent_lowest": quote.recent_lowest,
        "original": quote.original,
        "rooms_left": quote.rooms_left,
        "offer_name": offer_name,
        "room_name": room_name,
        "meal_plan": meal,
        "cancel": cancel,
    }


def build_latest(
    searches: list[tuple[Search, list[StayResult]]],
    catalog: Catalog,
    history: dict,
    scanned_at: datetime,
    tracked: list[tuple[TrackedTrip, StayResult]] | None = None,
    tracked_state: dict | None = None,
    *,
    config_revision: str | None = None,
) -> dict:
    payload_searches = []
    for search, results in searches:
        group = history_group(search.adults, search.codes, search.offer_filter, search.room_filter)
        bucket = history.get(group) if isinstance(history.get(group), dict) else {}
        stays = []
        for result in results:
            rows = bucket.get(result.stay.key) or []
            current = None if result.best is None else result.best.price
            previous = last_recorded_price(rows)
            stays.append(
                {
                    "key": result.stay.key,
                    "check_in": result.stay.check_in.isoformat(),
                    "check_out": result.stay.check_out.isoformat(),
                    "nights": result.stay.nights,
                    "sold_out": result.best is None,
                    "offer_count": result.offer_count,
                    "previous_price": trend_baseline(rows, current),
                    "alert_match": bool(search.alert is not None and qualifies(search.alert, result, previous)),
                    **describe_quote(catalog, result),
                }
            )
        payload_searches.append(
            {
                "id": search.id,
                "name": display_name(search, scanned_at.date()),
                "custom_name": search.name is not None,
                "window_start": search.window_start.isoformat(),
                "window_end": search.window_end.isoformat(),
                "nights": search.nights,
                "must_include": [WEEKDAY_NAMES[day] for day in sorted(search.must_include)],
                "adults": search.adults,
                "top": search.top,
                "codes": list(search.codes),
                "history_key": group,
                "alert": alert_payload(search.alert),
                "stays": stays,
            }
        )
    return {
        "scanned_at": scanned_at.isoformat(timespec="seconds"),
        "config_revision": config_revision,
        "searches": payload_searches,
        "tracked": [_tracked_payload(item, catalog, history, tracked_state or {}) for item in tracked or []],
    }


def _tracked_payload(
    item: tuple[TrackedTrip, StayResult],
    catalog: Catalog,
    history: dict,
    tracked_state: dict,
) -> dict:
    trip, result = item
    group = history_group(trip.adults, trip.codes)
    bucket = history.get(group) if isinstance(history.get(group), dict) else {}
    rows = bucket.get(result.stay.key) or []
    current = None if result.best is None else result.best.price
    entry = tracked_state.get(trip.id) if isinstance(tracked_state.get(trip.id), dict) else {}
    quote = describe_quote(catalog, result)
    return {
        "id": trip.id,
        "key": result.stay.key,
        "check_in": result.stay.check_in.isoformat(),
        "check_out": result.stay.check_out.isoformat(),
        "nights": result.stay.nights,
        "adults": trip.adults,
        "codes": list(trip.codes),
        "added": trip.added.isoformat(),
        "history_key": group,
        "sold_out": result.best is None,
        "offer_count": result.offer_count,
        "price": quote["price"],
        "per_night": quote["per_night"],
        "recent_lowest": quote["recent_lowest"],
        "original": quote["original"],
        "rooms_left": quote["rooms_left"],
        "offer_name": quote["offer_name"],
        "room_name": quote["room_name"],
        "meal_plan": quote["meal_plan"],
        "cancel": quote["cancel"],
        "previous_price": trend_baseline(rows, current),
        "first_price": entry.get("first_price"),
        "lowest_seen": lowest_seen(rows, current),
    }


def had_availability(latest: dict | None) -> bool:
    if not isinstance(latest, dict):
        return False
    for search in latest.get("searches") or []:
        if not isinstance(search, dict):
            continue
        for stay in search.get("stays") or []:
            if isinstance(stay, dict) and isinstance(stay.get("price"), int):
                return True
    for trip in latest.get("tracked") or []:
        if isinstance(trip, dict) and isinstance(trip.get("price"), int):
            return True
    return False


class Store:
    def __init__(self, docs: Path, terms: Sequence[str]):
        self.docs = Path(docs)
        self.data = self.docs / "data"
        self.terms = tuple(terms)

    def load_latest(self) -> dict | None:
        return self._read("latest.json")

    def load_history(self) -> dict:
        loaded = self._read("history.json")
        return loaded if isinstance(loaded, dict) else {}

    def load_state(self) -> dict:
        loaded = self._read("state.json")
        if not isinstance(loaded, dict):
            return default_state()
        state = default_state()
        state.update(loaded)
        state["alerts_sent"] = flatten_alerts_sent(state.get("alerts_sent"))
        state.pop("last_digest_date", None)
        if not isinstance(state.get("alert_baselines"), dict):
            state["alert_baselines"] = {}
        if not isinstance(state.get("tracked"), dict):
            state["tracked"] = {}
        return state

    def save_latest(self, payload: dict) -> None:
        self._write("latest.json", payload)

    def save_history(self, payload: dict) -> None:
        self._write("history.json", payload)

    def save_state(
        self,
        payload: dict,
        *,
        today: date | None = None,
        searches: Sequence[Search] | None = None,
        tracked: Sequence[TrackedTrip] | None = None,
    ) -> None:
        if today is not None:
            payload = copy.deepcopy(payload)
            if isinstance(payload.get("alerts_sent"), dict):
                payload["alerts_sent"] = prune_alerts_sent(payload["alerts_sent"], today, searches)
            if isinstance(payload.get("alert_baselines"), dict):
                payload["alert_baselines"] = prune_alert_baselines(payload["alert_baselines"], today, searches)
            if isinstance(payload.get("tracked"), dict) or tracked is not None:
                raw = payload.get("tracked")
                payload["tracked"] = prune_tracked_state(raw if isinstance(raw, dict) else {}, today, tracked)
        self._write("state.json", payload)

    def _read(self, name: str) -> dict | None:
        path = self.data / name
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(f"docs/data/{name} is not valid JSON") from exc

    def _write(self, name: str, payload: dict) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        clean = redact(payload, self.terms)
        text = json.dumps(clean, ensure_ascii=False, indent=2) + "\n"
        (self.data / name).write_text(text, encoding="utf-8")
