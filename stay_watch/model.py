from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal


@dataclass(frozen=True)
class Stay:
    check_in: date
    check_out: date

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def key(self) -> str:
        return f"{self.check_in.isoformat()}_{self.check_out.isoformat()}"

    def covers(self, day: date) -> bool:
        return self.check_in <= day < self.check_out

    def covers_weekday(self, weekday: int) -> bool:
        day = self.check_in
        while day < self.check_out:
            if day.weekday() == weekday:
                return True
            day += timedelta(days=1)
        return False


@dataclass(frozen=True)
class Quote:
    stay: Stay
    offer_id: int
    room_id: int
    price: int
    recent_lowest: int | None
    original: int | None
    rooms_left: int | None
    profile_id: int


@dataclass(frozen=True)
class OfferInfo:
    name: str
    meal_plan_label: str
    cancel_label: str
    profile_cancels: tuple[tuple[int, str], ...] = ()

    def cancel_for(self, profile_id: int) -> str:
        for pid, label in self.profile_cancels:
            if pid == profile_id:
                return label
        return self.cancel_label


@dataclass(frozen=True)
class Catalog:
    offers: dict[int, OfferInfo]
    rooms: dict[int, str]


@dataclass(frozen=True)
class Below:
    amount: int


@dataclass(frozen=True)
class PerNightBelow:
    amount: int


@dataclass(frozen=True)
class DropPct:
    pct: int


@dataclass(frozen=True)
class BelowRecentLowest:
    pass


@dataclass(frozen=True)
class Discounted:
    pass


AlertRule = Below | PerNightBelow | DropPct | BelowRecentLowest | Discounted

NotifyMode = Literal["any", "drop"]


def tracked_id(check_in: date, check_out: date, adults: int, codes: tuple[str, ...]) -> str:
    """Id is the stay, the party, and any promo codes, so a trip is tracked once."""
    base = f"{check_in.isoformat()}_{check_out.isoformat()}_a{adults}"
    if codes:
        return f"{base}_c{'-'.join(codes)}"
    return base


@dataclass(frozen=True)
class TrackedTrip:
    id: str
    stay: Stay
    adults: int
    codes: tuple[str, ...]
    notify: NotifyMode
    below: int | None
    added: date


@dataclass(frozen=True)
class Search:
    id: str
    name: str | None
    window_start: date
    window_end: date
    nights: int
    must_include: frozenset[int]
    adults: int
    top: int | None
    codes: tuple[str, ...]
    offer_filter: tuple[str, ...]
    room_filter: tuple[str, ...]
    alerts: tuple[AlertRule, ...]


@dataclass(frozen=True)
class StayResult:
    stay: Stay
    best: Quote | None
    offer_count: int


@dataclass(frozen=True)
class AlertHit:
    search_id: str
    rule: AlertRule
    stay_result: StayResult
    previous_price: int | None
