from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stay_watch.model import (
    AlertRule,
    Below,
    BelowRecentLowest,
    Discounted,
    DropPct,
    NotifyMode,
    PerNightBelow,
    Search,
    Stay,
    TrackedTrip,
    tracked_id,
)

WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "pon": 0,
    "tue": 1,
    "tuesday": 1,
    "wt": 1,
    "wed": 2,
    "wednesday": 2,
    "sr": 2,
    "thu": 3,
    "thursday": 3,
    "czw": 3,
    "fri": 4,
    "friday": 4,
    "pt": 4,
    "sat": 5,
    "saturday": 5,
    "sob": 5,
    "sun": 6,
    "sunday": 6,
    "nd": 6,
}

WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

CONFIG_WEEKDAYS = {name: index for index, name in enumerate(WEEKDAY_NAMES)}

_SEARCH_ID = re.compile(r"[a-z0-9-]{1,40}")

BUILTIN_DEFAULTS = {
    "adults": 2,
    "nights": 2,
    "must_include": ["sat"],
    "top": 3,
    "codes": [],
    "offers": [],
    "rooms": [],
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Secrets:
    slug: str
    terms: tuple[str, ...]
    smtp_user: str | None
    smtp_password: str | None
    alert_to: str | None
    smtp_host: str
    smtp_port: int
    dry_run: bool


@dataclass(frozen=True)
class WatchConfig:
    searches: tuple[Search, ...]
    tracked: tuple[TrackedTrip, ...]
    digest_weekday: int | None


def parse_weekday_token(token: str) -> int:
    key = token.strip().lower()
    if key not in WEEKDAYS:
        raise ConfigError(f"unknown weekday {token!r} (use mon tue wed thu fri sat sun, or pon wt sr czw pt sob nd)")
    return WEEKDAYS[key]


def load_secrets(env: dict[str, str] | None = None) -> Secrets:
    source = os.environ if env is None else env
    slug = (source.get("HOTEL_SLUG") or "").strip()
    if not slug:
        raise ConfigError("HOTEL_SLUG is not set")
    terms: list[str] = []
    seen: set[str] = set()
    raw_terms = source.get("HOTEL_NAME_TERMS") or ""
    for piece in [*raw_terms.split(","), slug]:
        item = piece.strip()
        if not item:
            continue
        folded = item.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        terms.append(item)
    terms.sort(key=len, reverse=True)
    user = (source.get("SMTP_USER") or "").strip() or None
    password = source.get("SMTP_PASSWORD")
    if password is not None and password == "":
        password = None
    alert_to = (source.get("ALERT_TO") or "").strip() or user
    host = (source.get("SMTP_HOST") or "smtp.gmail.com").strip() or "smtp.gmail.com"
    port_raw = (source.get("SMTP_PORT") or "465").strip() or "465"
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ConfigError(f"SMTP_PORT is not a number: {port_raw}") from exc
    return Secrets(
        slug=slug,
        terms=tuple(terms),
        smtp_user=user,
        smtp_password=password,
        alert_to=alert_to,
        smtp_host=host,
        smtp_port=port,
        dry_run=(source.get("DRY_RUN") or "").strip() == "1",
    )


def load_watch(path: Path) -> WatchConfig:
    if not path.is_file():
        raise ConfigError(f"missing config file {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name}: not valid JSON") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: must be an object")
    if not isinstance(data.get("version"), int) or isinstance(data.get("version"), bool) or data.get("version") != 1:
        _fail("version", "must be 1")
    defaults = _object(data.get("defaults", {}), "defaults")
    digest = _object(data.get("digest", {}), "digest")
    searches = _list(data.get("searches", []), "searches")
    tracked = _list(data.get("tracked", []), "tracked")
    parsed_defaults = _parse_defaults(defaults)
    parsed_searches = tuple(
        _parse_search(item, parsed_defaults, f"searches[{index}]") for index, item in enumerate(searches)
    )
    seen: set[str] = set()
    for index, search in enumerate(parsed_searches):
        if search.id in seen:
            _fail(f"searches[{index}].id", "already used")
        seen.add(search.id)
    parsed_tracked = tuple(_parse_tracked(item, f"tracked[{index}]") for index, item in enumerate(tracked))
    seen_trips: set[str] = set()
    for index, trip in enumerate(parsed_tracked):
        if trip.id in seen_trips:
            _fail(f"tracked[{index}].id", "already used")
        seen_trips.add(trip.id)
    return WatchConfig(parsed_searches, parsed_tracked, _parse_digest(digest))


def _parse_defaults(raw: dict) -> dict:
    nights = _positive_int(raw["nights"], "defaults.nights") if "nights" in raw else BUILTIN_DEFAULTS["nights"]
    adults = _positive_int(raw["adults"], "defaults.adults") if "adults" in raw else BUILTIN_DEFAULTS["adults"]
    top = _parse_top(raw["top"], "defaults.top") if "top" in raw else BUILTIN_DEFAULTS["top"]
    must = _parse_must_include(raw["must_include"], "defaults.must_include") if "must_include" in raw else _parse_must_include(BUILTIN_DEFAULTS["must_include"], "defaults.must_include")
    codes = tuple(_string_list(raw["codes"], "defaults.codes")) if "codes" in raw else ()
    offers = tuple(_string_list(raw["offers"], "defaults.offers")) if "offers" in raw else ()
    rooms = tuple(_string_list(raw["rooms"], "defaults.rooms")) if "rooms" in raw else ()
    return {
        "nights": nights,
        "adults": adults,
        "top": top,
        "must_include": must,
        "codes": codes,
        "offers": offers,
        "rooms": rooms,
    }


def _parse_search(item: object, defaults: dict, path: str) -> Search:
    raw = _object(item, path)
    search_id = _search_id(raw.get("id"), f"{path}.id")
    name = _search_name(raw, f"{path}.name")
    start = _parse_date(raw.get("from"), f"{path}.from")
    end = _parse_date(raw.get("to"), f"{path}.to")
    if start >= end:
        _fail(f"{path}.to", "must be after from")
    nights = _positive_int(raw["nights"], f"{path}.nights") if "nights" in raw else defaults["nights"]
    adults = _positive_int(raw["adults"], f"{path}.adults") if "adults" in raw else defaults["adults"]
    top = _parse_top(raw["top"], f"{path}.top") if "top" in raw else defaults["top"]
    codes = tuple(_string_list(raw["codes"], f"{path}.codes")) if "codes" in raw else defaults["codes"]
    offers = tuple(_string_list(raw["offers"], f"{path}.offers")) if "offers" in raw else defaults["offers"]
    rooms = tuple(_string_list(raw["rooms"], f"{path}.rooms")) if "rooms" in raw else defaults["rooms"]
    must = _parse_must_include(raw["must_include"], f"{path}.must_include") if "must_include" in raw else defaults["must_include"]
    alerts_raw = raw.get("alerts", [])
    alerts = tuple(_parse_alert(entry, f"{path}.alerts[{index}]") for index, entry in enumerate(_list(alerts_raw, f"{path}.alerts")))
    return Search(
        id=search_id,
        name=name,
        window_start=start,
        window_end=end,
        nights=nights,
        must_include=must,
        adults=adults,
        top=top,
        codes=codes,
        offer_filter=offers,
        room_filter=rooms,
        alerts=alerts,
    )


def _parse_tracked(item: object, path: str) -> TrackedTrip:
    raw = _object(item, path)
    check_in = _parse_date(raw.get("check_in"), f"{path}.check_in")
    check_out = _parse_date(raw.get("check_out"), f"{path}.check_out")
    if check_in >= check_out:
        _fail(f"{path}.check_out", "must be after check_in")
    if "adults" not in raw:
        _fail(f"{path}.adults", "missing")
    adults = _positive_int(raw.get("adults"), f"{path}.adults")
    codes = tuple(_string_list(raw["codes"], f"{path}.codes")) if "codes" in raw else ()
    notify = _notify(raw.get("notify"), f"{path}.notify")
    below = None
    if "below" in raw and raw["below"] is not None:
        below = _pln(raw["below"], f"{path}.below")
    added = _parse_date(raw.get("added"), f"{path}.added")
    expected = tracked_id(check_in, check_out, adults, codes)
    if "id" in raw and raw["id"] != expected:
        _fail(f"{path}.id", f"expected {expected}")
    return TrackedTrip(
        id=expected,
        stay=Stay(check_in, check_out),
        adults=adults,
        codes=codes,
        notify=notify,
        below=below,
        added=added,
    )


def _parse_alert(entry: object, path: str) -> AlertRule:
    raw = _object(entry, path)
    kind = raw.get("kind")
    if "kind" not in raw:
        _fail(f"{path}.kind", "missing")
    if kind == "below":
        return Below(_pln(raw.get("amount"), f"{path}.amount"))
    if kind == "per_night_below":
        return PerNightBelow(_pln(raw.get("amount"), f"{path}.amount"))
    if kind == "drop_pct":
        value = raw.get("pct")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            _fail(f"{path}.pct", "must be a whole number from 1 to 100")
        return DropPct(value)
    if kind == "below_recent_lowest":
        return BelowRecentLowest()
    if kind == "discounted":
        return Discounted()
    _fail(f"{path}.kind", f"unknown alert {kind!r}")


def _parse_digest(raw: dict) -> int | None:
    if "weekday" not in raw:
        return None
    value = raw["weekday"]
    if value is None or (isinstance(value, str) and value.strip().lower() == "off"):
        return None
    if not isinstance(value, str):
        _fail("digest.weekday", 'must be mon tue wed thu fri sat sun or "off"')
    key = value.strip().lower()
    if key not in CONFIG_WEEKDAYS:
        _fail("digest.weekday", 'must be mon tue wed thu fri sat sun or "off"')
    return CONFIG_WEEKDAYS[key]


def _parse_must_include(value: object, path: str) -> frozenset[int]:
    items = _list(value, path)
    days: set[int] = set()
    for index, item in enumerate(items):
        if not isinstance(item, str) or item.strip().lower() not in CONFIG_WEEKDAYS:
            _fail(f"{path}[{index}]", "unknown weekday")
        days.add(CONFIG_WEEKDAYS[item.strip().lower()])
    return frozenset(days)


def _search_name(raw: dict, path: str) -> str | None:
    if "name" not in raw or raw["name"] is None:
        return None
    value = raw["name"]
    if not isinstance(value, str):
        _fail(path, "must be a string or null")
    stripped = value.strip()
    if not stripped:
        _fail(path, "must be a string or null")
    return stripped


def _search_id(value: object, path: str) -> str:
    if not isinstance(value, str) or _SEARCH_ID.fullmatch(value) is None:
        _fail(path, "must match [a-z0-9-]{1,40}")
    return value


def _notify(value: object, path: str) -> NotifyMode:
    if value not in ("any", "drop"):
        _fail(path, 'must be "any" or "drop"')
    return value


def _parse_date(value: object, path: str) -> date:
    if not isinstance(value, str):
        _fail(path, "not a date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(path, "not a date")


def _parse_top(value: object, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(path, "must be a positive number or null")
    return value


def _pln(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(path, "must be whole PLN")
    return value * 100


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(path, "must be a whole number of 1 or more")
    return value


def _string_list(value: object, path: str) -> list[str]:
    items = _list(value, path)
    texts: list[str] = []
    for index, entry in enumerate(items):
        if not isinstance(entry, str) or not entry.strip():
            _fail(f"{path}[{index}]", "must be text")
        texts.append(entry.strip())
    return texts


def _object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _list(value: object, path: str) -> list:
    if not isinstance(value, list):
        _fail(path, "must be a list")
    return value


def _fail(path: str, message: str) -> None:
    raise ConfigError(f"{path}: {message}")

