from __future__ import annotations

import copy
import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from stay_watch.config import WEEKDAY_NAMES
from stay_watch.model import AlertRule, Below, BelowRecentLowest, Catalog, Discounted, DropPct, PerNightBelow, Search, StayResult
from stay_watch.alerts import rule_id, rule_matches
from stay_watch.rank import per_night


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
        "consecutive_failures": 0,
        "last_digest_date": None,
    }


def check_in_of(stay_key: str) -> date | None:
    try:
        return date.fromisoformat(stay_key.split("_", 1)[0])
    except ValueError:
        return None


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


def prune_history(history: dict, today: date) -> dict:
    history = copy.deepcopy(history)
    if not isinstance(history, dict):
        return {}
    for group in list(history):
        bucket = history.get(group)
        if not isinstance(bucket, dict):
            history.pop(group, None)
            continue
        for stay_key in list(bucket):
            check_in = check_in_of(stay_key)
            if check_in is not None and check_in < today:
                del bucket[stay_key]
        if not bucket:
            del history[group]
    return history


def last_recorded_price(rows: list) -> int | None:
    if not rows:
        return None
    price = rows[-1][1]
    return price if isinstance(price, int) else None


def trend_baseline(rows: list, current: int | None) -> int | None:
    numeric = [row[1] for row in rows if isinstance(row, list) and len(row) > 1 and isinstance(row[1], int)]
    if current is None or not numeric:
        return None
    if numeric[-1] != current:
        return numeric[-1]
    if len(numeric) >= 2:
        return numeric[-2]
    return None


def rule_payload(rule: AlertRule) -> dict:
    match rule:
        case Below(amount):
            return {"kind": "below", "amount": amount}
        case PerNightBelow(amount):
            return {"kind": "per_night_below", "amount": amount}
        case DropPct(pct):
            return {"kind": "drop_pct", "pct": pct}
        case BelowRecentLowest():
            return {"kind": "below_recent_lowest"}
        case Discounted():
            return {"kind": "discounted"}


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
    offer = catalog.offers.get(quote.offer_id)
    room_name = catalog.rooms.get(quote.room_id) or f"Room {quote.room_id}"
    if offer is None:
        offer_name = f"Offer {quote.offer_id}"
        meal = "other"
        cancel = "no free cancellation"
    else:
        offer_name = offer.name
        meal = offer.meal_plan_label
        cancel = offer.cancel_for(quote.profile_id)
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
            matched = [
                rule_id(rule)
                for rule in search.alerts
                if rule_matches(rule, result, previous)
            ]
            stays.append(
                {
                    "key": result.stay.key,
                    "check_in": result.stay.check_in.isoformat(),
                    "check_out": result.stay.check_out.isoformat(),
                    "nights": result.stay.nights,
                    "sold_out": result.best is None,
                    "offer_count": result.offer_count,
                    "previous_price": trend_baseline(rows, current),
                    "matched_rules": matched,
                    **describe_quote(catalog, result),
                }
            )
        payload_searches.append(
            {
                "id": search.id,
                "name": search.name,
                "window_start": search.window_start.isoformat(),
                "window_end": search.window_end.isoformat(),
                "nights": search.nights,
                "must_include": [WEEKDAY_NAMES[day] for day in sorted(search.must_include)],
                "adults": search.adults,
                "top": search.top,
                "codes": list(search.codes),
                "history_key": group,
                "alerts": [rule_payload(rule) for rule in search.alerts],
                "stays": stays,
            }
        )
    return {
        "scanned_at": scanned_at.isoformat(timespec="seconds"),
        "searches": payload_searches,
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
        if not isinstance(state.get("alerts_sent"), dict):
            state["alerts_sent"] = {}
        return state

    def save_latest(self, payload: dict) -> None:
        self._write("latest.json", payload)

    def save_history(self, payload: dict) -> None:
        self._write("history.json", payload)

    def save_state(self, payload: dict) -> None:
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
