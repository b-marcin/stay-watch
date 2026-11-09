import json
from datetime import date
from pathlib import Path

from stay_watch.model import Catalog, Stay
from stay_watch.profitroom import parse_availability, parse_offers, parse_rooms
from stay_watch.rank import per_night, rank_search
from tests.support import make_search

FIXTURES = Path(__file__).parent / "fixtures"


def catalog():
    return Catalog(
        parse_offers(json.loads((FIXTURES / "offers.json").read_text(encoding="utf-8"))),
        parse_rooms(json.loads((FIXTURES / "rooms.json").read_text(encoding="utf-8"))),
    )


def dec_quotes():
    stay = Stay(date(2026, 12, 4), date(2026, 12, 6))
    quotes = parse_availability(
        json.loads((FIXTURES / "availability_2026-12-04_2026-12-06.json").read_text(encoding="utf-8")),
        stay,
    )
    return stay, quotes


def test_filters_match_offer_and_room_names_case_insensitively():
    data = catalog()
    stay, quotes = dec_quotes()
    by_offer = rank_search(
        make_search(offer_filter=("ELASTYCZNA",)),
        [stay],
        {stay.key: quotes},
        data,
    )
    assert by_offer[0].best.price == 139_200
    assert data.offers[by_offer[0].best.offer_id].name == "Cena dnia - oferta elastyczna"

    by_room = rank_search(
        make_search(room_filter=("suite",)),
        [stay],
        {stay.key: quotes},
        data,
    )
    assert by_room[0].best.price == 271_000
    assert data.rooms[by_room[0].best.room_id] == "Apartament Suite"

    both = rank_search(
        make_search(offer_filter=("elastyczna",), room_filter=("Suite",)),
        [stay],
        {stay.key: quotes},
        data,
    )
    assert both[0].best.price == 299_200

    none = rank_search(
        make_search(room_filter=("does-not-exist",)),
        [stay],
        {stay.key: quotes},
        data,
    )
    assert none[0].best is None


def test_ordering_is_price_then_date_with_sold_out_last():
    data = catalog()
    stay, quotes = dec_quotes()
    later = Stay(date(2026, 12, 11), date(2026, 12, 13))
    empty = Stay(date(2026, 12, 5), date(2026, 12, 7))
    ranked = rank_search(
        make_search(),
        [empty, later, stay],
        {stay.key: quotes, later.key: quotes, empty.key: []},
        data,
    )
    assert [item.stay.key for item in ranked] == [stay.key, later.key, empty.key]
    assert ranked[-1].best is None
    assert per_night(ranked[0].best.price, 2) == 63_500
