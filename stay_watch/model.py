from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


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
    rooms_left: int
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


@dataclass(frozen=True)
class Search:
    id: str
    name: str
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
