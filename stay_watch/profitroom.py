from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from stay_watch.model import Catalog, OfferInfo, Quote, Stay

USER_AGENT = "stay-watch/1.0"
PAUSE_SECONDS = 1.0
ATTEMPTS = 3


class ProfitroomError(Exception):
    pass


class FeedChanged(Exception):
    """Every fetched stay was empty after a run that still had prices."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes


def urllib_fetcher(url: str) -> HttpResult:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return HttpResult(response.status, response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        return HttpResult(exc.code, body)


def availability_url(slug: str, stay: Stay, adults: int, codes: tuple[str, ...]) -> str:
    pairs = [
        ("checkIn", stay.check_in.isoformat()),
        ("checkOut", stay.check_out.isoformat()),
        ("occupancy[0][adults]", str(adults)),
        ("lang", "pl"),
        ("currency", "PLN"),
    ]
    for index, code in enumerate(codes):
        pairs.append((f"codes[{index}]", code))
    query = "&".join(
        f"{urllib.parse.quote(key, safe='[]')}={urllib.parse.quote(str(value), safe='')}"
        for key, value in pairs
    )
    safe_slug = urllib.parse.quote(slug, safe="")
    return f"https://booking.profitroom.com/api/{safe_slug}/availability?{query}"


def _money(value: object, field: str) -> int:
    if isinstance(value, dict):
        if "amount" not in value:
            raise ProfitroomError(f"missing {field} amount")
        value = value["amount"]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProfitroomError(f"bad {field}")
    try:
        grosze = (Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except Exception as exc:
        raise ProfitroomError(f"bad {field}") from exc
    return int(grosze)


def _optional_money(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _money(value, field)


def parse_availability(payload: object, stay: Stay) -> list[Quote]:
    # Request dates win. Captured fixtures replace stay.from/stay.to with a
    # placeholder, and a quote is for the stay we asked about.
    if not isinstance(payload, list):
        raise ProfitroomError("availability response is not a list")
    quotes: list[Quote] = []
    for block in payload:
        if not isinstance(block, dict) or not isinstance(block.get("proposals"), list):
            raise ProfitroomError("availability entry has no proposals")
        for item in block["proposals"]:
            if not isinstance(item, dict) or not isinstance(item.get("proposal"), dict):
                raise ProfitroomError("proposal is missing")
            proposal = item["proposal"]
            try:
                offer_id = int(proposal["OfferID"])
                room_id = int(proposal["RoomID"])
                profile_id = int(proposal["profileId"])
                rooms_left = int(item["roomCount"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ProfitroomError(f"proposal is missing a field ({exc})") from exc
            if "price" not in proposal:
                raise ProfitroomError("proposal has no price")
            quotes.append(
                Quote(
                    stay=stay,
                    offer_id=offer_id,
                    room_id=room_id,
                    price=_money(proposal["price"], "price"),
                    recent_lowest=_optional_money(proposal.get("recentLowestPrice"), "recentLowestPrice"),
                    original=_optional_money(proposal.get("originalPrice"), "originalPrice"),
                    rooms_left=rooms_left,
                    profile_id=profile_id,
                )
            )
    return quotes


def _name_from_translations(translations: object) -> str | None:
    by_locale: dict[str, str] = {}
    if not isinstance(translations, list):
        return None
    for block in translations:
        if not isinstance(block, dict):
            continue
        locale = block.get("locale")
        for message in block.get("messages") or []:
            if (
                isinstance(message, dict)
                and message.get("fieldName") == "name"
                and message.get("value")
            ):
                by_locale[str(locale)] = str(message["value"])
                break
    return by_locale.get("pl") or by_locale.get("en")


def meal_plan_label(code: object) -> str:
    if code == 19:
        return "breakfast"
    if code == 20:
        return "half board"
    return f"other (code {code})"


def cancel_label(profile: dict) -> str:
    params = profile.get("params")
    if isinstance(params, dict):
        cancel_time = params.get("cancelTime")
        cancel_unit = params.get("cancelUnit")
        if cancel_time not in (None, "") and cancel_unit not in (None, ""):
            return f"free until {cancel_time} {cancel_unit} before"
    if profile.get("type") == "nonref":
        return "non-refundable"
    return "no free cancellation"


def parse_offers(payload: object) -> dict[int, OfferInfo]:
    if not isinstance(payload, list):
        raise ProfitroomError("offers response is not a list")
    offers: dict[int, OfferInfo] = {}
    for item in payload:
        if not isinstance(item, dict) or "id" not in item:
            raise ProfitroomError("offer is missing an id")
        try:
            offer_id = int(item["id"])
        except (TypeError, ValueError) as exc:
            raise ProfitroomError("offer id is not a number") from exc
        name = _name_from_translations(item.get("translations")) or f"offer {offer_id}"
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        profiles = item.get("profiles") if isinstance(item.get("profiles"), list) else []
        labels: list[tuple[int, str]] = []
        for profile in profiles:
            if not isinstance(profile, dict) or "profileId" not in profile:
                continue
            try:
                profile_id = int(profile["profileId"])
            except (TypeError, ValueError):
                continue
            labels.append((profile_id, cancel_label(profile)))
        fallback = labels[0][1] if labels else "no free cancellation"
        offers[offer_id] = OfferInfo(
            name=name,
            meal_plan_label=meal_plan_label(attributes.get("mealPlanType")),
            cancel_label=fallback,
            profile_cancels=tuple(labels),
        )
    return offers


def parse_rooms(payload: object) -> dict[int, str]:
    if not isinstance(payload, list):
        raise ProfitroomError("rooms response is not a list")
    rooms: dict[int, str] = {}
    for item in payload:
        if not isinstance(item, dict) or "id" not in item:
            raise ProfitroomError("room is missing an id")
        try:
            room_id = int(item["id"])
        except (TypeError, ValueError) as exc:
            raise ProfitroomError("room id is not a number") from exc
        gallery = item.get("gallery") if isinstance(item.get("gallery"), dict) else {}
        title = gallery.get("title")
        name = _name_from_translations(item.get("translations"))
        if not name and title:
            name = str(title)
        rooms[room_id] = name or f"room {room_id}"
    return rooms


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, TimeoutError)


class ProfitroomClient:
    def __init__(self, slug: str, fetcher=None, sleep=None):
        self.slug = slug
        self.fetcher = fetcher or urllib_fetcher
        self.sleep = sleep or time.sleep
        self._cache: dict[tuple, list[Quote]] = {}
        self._requests = 0

    def catalog(self) -> Catalog:
        offers = parse_offers(self._get_json(self._url("offers?status=1&lang=pl")))
        rooms = parse_rooms(self._get_json(self._url("rooms?lang=pl")))
        return Catalog(offers, rooms)

    def quotes(self, stay: Stay, adults: int, codes: tuple[str, ...]) -> list[Quote]:
        key = (stay.key, adults, tuple(codes))
        if key not in self._cache:
            payload = self._get_json(availability_url(self.slug, stay, adults, codes))
            self._cache[key] = parse_availability(payload, stay)
        return self._cache[key]

    def _url(self, path: str) -> str:
        safe_slug = urllib.parse.quote(self.slug, safe="")
        return f"https://booking.profitroom.com/api/{safe_slug}/{path}"

    def _get_json(self, url: str) -> object:
        body = self._get(url)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProfitroomError("booking feed did not return JSON") from exc

    def _get(self, url: str) -> bytes:
        if self._requests:
            self.sleep(PAUSE_SECONDS)
        self._requests += 1
        last = "no response"
        for attempt in range(ATTEMPTS):
            if attempt:
                self.sleep(2 ** (attempt - 1))
            try:
                result = self.fetcher(url)
            except TimeoutError:
                last = "timed out"
                continue
            except urllib.error.URLError as exc:
                if _is_timeout(exc):
                    last = "timed out"
                    continue
                raise ProfitroomError("could not reach the booking feed") from exc
            except OSError:
                last = "timed out"
                continue
            status = result.status
            if status == 429 or 500 <= status <= 599:
                last = f"HTTP {status}"
                continue
            if status != 200:
                raise ProfitroomError(f"booking feed returned HTTP {status}")
            return result.body
        raise ProfitroomError(f"booking feed failed after 3 tries ({last})")


class FixtureClient:
    """Offline stand-in. Missing availability files are sold-out stays."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        offers_path = self.directory / "offers.json"
        rooms_path = self.directory / "rooms.json"
        if not offers_path.is_file() or not rooms_path.is_file():
            raise ProfitroomError(f"fixtures in {self.directory} need offers.json and rooms.json")
        self._catalog = Catalog(
            parse_offers(json.loads(offers_path.read_text(encoding="utf-8"))),
            parse_rooms(json.loads(rooms_path.read_text(encoding="utf-8"))),
        )
        self._cache: dict[tuple, list[Quote]] = {}

    def catalog(self) -> Catalog:
        return self._catalog

    def quotes(self, stay: Stay, adults: int, codes: tuple[str, ...]) -> list[Quote]:
        key = (stay.key, adults, tuple(codes))
        if key not in self._cache:
            path = self.directory / f"availability_{stay.check_in.isoformat()}_{stay.check_out.isoformat()}.json"
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                self._cache[key] = parse_availability(payload, stay)
            else:
                self._cache[key] = []
        return self._cache[key]
