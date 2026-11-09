from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from stay_watch.model import (
    AlertRule,
    Below,
    BelowRecentLowest,
    Discounted,
    DropPct,
    PerNightBelow,
    Search,
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

TOP_LEVEL_KEYS = {"defaults", "digest", "search"}
DEFAULT_KEYS = {"adults", "nights", "must_include", "top", "codes", "offers", "rooms"}
DIGEST_KEYS = {"weekday"}
SEARCH_KEYS = {
    "id",
    "name",
    "from",
    "to",
    "adults",
    "nights",
    "must_include",
    "top",
    "codes",
    "offers",
    "rooms",
    "alert",
}
ALERT_KEYS = {"below", "per_night_below", "drop_pct", "below_recent_lowest", "discounted"}


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
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        lineno = getattr(exc, "lineno", None)
        where = f"{path.name}:{lineno}" if lineno else path.name
        raise ConfigError(f"{where}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: config must be a table")
    unknown = [key for key in data if key not in TOP_LEVEL_KEYS]
    if unknown:
        raise ConfigError(f"{path.name}:{_line(text, unknown[0])}: unknown section {unknown[0]!r}")
    defaults = data.get("defaults") or {}
    digest = data.get("digest") or {}
    searches = data.get("search") or []
    if not isinstance(defaults, dict):
        raise ConfigError(f"{path.name}: [defaults] must be a table")
    if not isinstance(digest, dict):
        raise ConfigError(f"{path.name}: [digest] must be a table")
    if not isinstance(searches, list):
        raise ConfigError(f"{path.name}: [[search]] entries must be tables")
    _reject_unknown(defaults, DEFAULT_KEYS, text, "defaults")
    _reject_unknown(digest, DIGEST_KEYS, text, "digest")
    parsed = tuple(_parse_search(item, defaults, text, path.name) for item in searches)
    ids = [item.id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"{path.name}: two searches share an id")
    weekday_raw = digest.get("weekday", "off")
    return WatchConfig(parsed, _parse_digest_weekday(weekday_raw, text, path.name))


def _parse_search(item: object, defaults: dict, text: str, filename: str) -> Search:
    if not isinstance(item, dict):
        raise ConfigError(f"{filename}: a search entry is not a table")
    _reject_unknown(item, SEARCH_KEYS, text, "search")
    search_id = _required_str(item, "id", text, filename)
    name = _required_str(item, "name", text, filename)
    start = _parse_date(_pick(item, defaults, "from"), text, filename, "from")
    end = _parse_date(_pick(item, defaults, "to"), text, filename, "to")
    if start >= end:
        raise ConfigError(
            f"{filename}:{_line(text, start.isoformat())}: check-out {end.isoformat()} is not after check-in {start.isoformat()}"
        )
    nights = _positive_int(_pick(item, defaults, "nights", 2), text, filename, "nights")
    adults = _positive_int(_pick(item, defaults, "adults", 2), text, filename, "adults")
    top = _parse_top(_pick(item, defaults, "top", 3), text, filename)
    codes = tuple(_string_list(_pick(item, defaults, "codes", []), text, filename, "codes"))
    offer_filter = tuple(_string_list(_pick(item, defaults, "offers", []), text, filename, "offers"))
    room_filter = tuple(_string_list(_pick(item, defaults, "rooms", []), text, filename, "rooms"))
    must = _pick(item, defaults, "must_include", ["sat"])
    alerts = tuple(_parse_alert(entry, text, filename) for entry in item.get("alert") or [])
    return Search(
        id=search_id,
        name=name,
        window_start=start,
        window_end=end,
        nights=nights,
        must_include=_parse_must_include(must, text, filename),
        adults=adults,
        top=top,
        codes=codes,
        offer_filter=offer_filter,
        room_filter=room_filter,
        alerts=alerts,
    )


def _parse_alert(entry: object, text: str, filename: str) -> AlertRule:
    if not isinstance(entry, dict) or len(entry) != 1:
        raise ConfigError(f"{filename}: each [[search.alert]] needs exactly one key")
    key = next(iter(entry))
    if key not in ALERT_KEYS:
        raise ConfigError(f"{filename}:{_line(text, key)}: unknown alert {key!r}")
    value = entry[key]
    if key == "below":
        return Below(_pln(value, text, filename, key))
    if key == "per_night_below":
        return PerNightBelow(_pln(value, text, filename, key))
    if key == "drop_pct":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ConfigError(f"{filename}:{_line(text, key)}: drop_pct must be a whole number from 1 to 100")
        return DropPct(value)
    if value is not True:
        raise ConfigError(f"{filename}:{_line(text, key)}: {key} must be true")
    if key == "below_recent_lowest":
        return BelowRecentLowest()
    return Discounted()


def _parse_must_include(value: object, text: str, filename: str) -> frozenset[int]:
    if not isinstance(value, list):
        raise ConfigError(f"{filename}:{_line(text, 'must_include')}: must_include must be a list of weekdays")
    days: set[int] = set()
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(f"{filename}: must_include entries must be weekday names")
        try:
            days.add(parse_weekday_token(item))
        except ConfigError as exc:
            raise ConfigError(f"{filename}:{_line(text, item)}: {exc}") from exc
    return frozenset(days)


def _parse_digest_weekday(value: object, text: str, filename: str) -> int | None:
    if value is None or (isinstance(value, str) and value.strip().lower() == "off"):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{filename}: digest weekday must be a day name or \"off\"")
    try:
        return parse_weekday_token(value)
    except ConfigError as exc:
        raise ConfigError(f"{filename}:{_line(text, value)}: {exc}") from exc


def _parse_date(value: object, text: str, filename: str, label: str) -> date:
    if not isinstance(value, str):
        raise ConfigError(f"{filename}:{_line(text, label)}: {label} must be a YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"{filename}:{_line(text, value)}: invalid date {value!r}") from exc


def _parse_top(value: object, text: str, filename: str) -> int | None:
    if isinstance(value, str) and value.strip().lower() == "all":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{filename}:{_line(text, 'top')}: top must be a number or \"all\"")
    return value


def _pln(value: object, text: str, filename: str, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{filename}:{_line(text, key)}: {key} must be a price in PLN")
    try:
        grosze = int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception as exc:
        raise ConfigError(f"{filename}:{_line(text, key)}: {key} must be a price in PLN") from exc
    if grosze <= 0:
        raise ConfigError(f"{filename}:{_line(text, key)}: {key} must be greater than 0")
    return grosze


def _positive_int(value: object, text: str, filename: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{filename}:{_line(text, label)}: {label} must be a whole number of 1 or more")
    return value


def _required_str(item: dict, key: str, text: str, filename: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{filename}:{_line(text, key)}: search {key} is missing")
    return value.strip()


def _string_list(value: object, text: str, filename: str, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{filename}:{_line(text, label)}: {label} must be a list of text")
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise ConfigError(f"{filename}:{_line(text, label)}: {label} entries must be text")
        items.append(entry.strip())
    return items


def _pick(item: dict, defaults: dict, key: str, fallback: object = None) -> object:
    if key in item:
        return item[key]
    if key in defaults:
        return defaults[key]
    return fallback


def _reject_unknown(table: dict, allowed: set[str], text: str, label: str) -> None:
    unknown = [key for key in table if key not in allowed]
    if unknown:
        raise ConfigError(f"watch.toml:{_line(text, unknown[0])}: unknown {label} key {unknown[0]!r}")


def _line(text: str, needle: object) -> int:
    target = str(needle)
    for number, line in enumerate(text.splitlines(), 1):
        if target in line:
            return number
    return 1
