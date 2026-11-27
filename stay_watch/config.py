from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stay_watch.model import (
    PriceAlert,
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
    revision: str | None = None


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
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name}: not valid JSON") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: must be an object")
    version = data.get("version")
    if type(version) is int and version == 1:
        raise ConfigError("config.json is version 1; this scanner reads version 2")
    if type(version) is not int or version != 2:
        _fail("version", "must be 2")
    defaults = _object(data.get("defaults", {}), "defaults")
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
    revision = hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw, usedforsecurity=False).hexdigest()
    return WatchConfig(parsed_searches, parsed_tracked, revision)


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
    if "alerts" in raw:
        _fail(f"{path}.alerts", "removed; use alert")
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
        alert=_parse_price_alert(raw.get("alert"), f"{path}.alert"),
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
    added = _parse_date(raw.get("added"), f"{path}.added")
    expected = tracked_id(check_in, check_out, adults, codes)
    if "id" in raw and raw["id"] != expected:
        _fail(f"{path}.id", f"expected {expected}")
    return TrackedTrip(
        id=expected,
        stay=Stay(check_in, check_out),
        adults=adults,
        codes=codes,
        added=added,
    )


def _parse_price_alert(value: object, path: str) -> PriceAlert | None:
    if value is None:
        return None
    raw = _object(value, path)
    if "under" not in raw:
        _fail(f"{path}.under", "missing")
    under = _pln(raw.get("under"), f"{path}.under")
    if "again_cheaper_by" not in raw or raw["again_cheaper_by"] is None:
        margin = 0
    else:
        margin = _whole_pln(raw["again_cheaper_by"], f"{path}.again_cheaper_by", minimum=0)
    recent_low = False
    discounted = False
    drop_pct = None
    if "require" in raw and raw["require"] is not None:
        require = _object(raw["require"], f"{path}.require")
        if "recent_low" in require:
            recent_low = _bool(require["recent_low"], f"{path}.require.recent_low")
        if "discounted" in require:
            discounted = _bool(require["discounted"], f"{path}.require.discounted")
        if "drop_pct" in require and require["drop_pct"] is not None:
            drop_pct = _percent(require["drop_pct"], f"{path}.require.drop_pct")
    return PriceAlert(under, margin, recent_low, discounted, drop_pct)


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
    return _whole_pln(value, path, minimum=1)


def _whole_pln(value: object, path: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(path, "must be whole PLN")
    return value * 100


def _percent(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        _fail(path, "must be a whole number from 1 to 100")
    return value


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, "must be true or false")
    return value


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

