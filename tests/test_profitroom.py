import json
from datetime import date
from pathlib import Path

import pytest

from stay_watch.model import Stay
from stay_watch.profitroom import (
    HttpResult,
    ProfitroomClient,
    ProfitroomError,
    availability_url,
    parse_availability,
    parse_offers,
    parse_rooms,
    urllib_fetcher,
)
from stay_watch.rank import rank_search
from tests.support import make_search

FIXTURES = Path(__file__).parent / "fixtures"
STAY = Stay(date(2026, 12, 4), date(2026, 12, 6))


def test_fixture_cheapest_price_and_sold_out():
    offers = parse_offers(json.loads((FIXTURES / "offers.json").read_text(encoding="utf-8")))
    rooms = parse_rooms(json.loads((FIXTURES / "rooms.json").read_text(encoding="utf-8")))
    catalog_offers = offers
    from stay_watch.model import Catalog

    catalog = Catalog(catalog_offers, rooms)
    dec, _skipped = parse_availability(
        json.loads((FIXTURES / "availability_2026-12-04_2026-12-06.json").read_text(encoding="utf-8")),
        STAY,
    )
    ranked = rank_search(make_search(), [STAY], {STAY.key: dec}, catalog)
    assert ranked[0].best is not None
    assert ranked[0].best.price == 127_000
    assert ranked[0].best.rooms_left == 13
    assert catalog.offers[ranked[0].best.offer_id].name == "Oferta bezzwrotna"
    assert catalog.offers[ranked[0].best.offer_id].meal_plan_label == "breakfast"
    assert catalog.offers[ranked[0].best.offer_id].cancel_for(ranked[0].best.profile_id) == "non-refundable"
    assert catalog.rooms[ranked[0].best.room_id] == "Standard Room"
    assert ranked[0].offer_count == 3

    jan_stay = Stay(date(2027, 1, 8), date(2027, 1, 10))
    jan, _skipped = parse_availability(
        json.loads((FIXTURES / "availability_2027-01-08_2027-01-10.json").read_text(encoding="utf-8")),
        jan_stay,
    )
    jan_ranked = rank_search(make_search(), [jan_stay], {jan_stay.key: jan}, catalog)
    assert jan_ranked[0].best.price == 152_400

    sold, sold_skipped = parse_availability(
        json.loads((FIXTURES / "availability_soldout_2026-12-15_2026-12-17.json").read_text(encoding="utf-8")),
        Stay(date(2026, 12, 15), date(2026, 12, 17)),
    )
    assert sold == []
    assert sold_skipped == 0
    sold_ranked = rank_search(
        make_search(must_include=frozenset()),
        [Stay(date(2026, 12, 15), date(2026, 12, 17))],
        {"2026-12-15_2026-12-17": sold},
        catalog,
    )
    assert sold_ranked[0].best is None
    assert sold_ranked[0].offer_count == 0


def test_offer_and_room_labels():
    offers = parse_offers(json.loads((FIXTURES / "offers.json").read_text(encoding="utf-8")))
    rooms = parse_rooms(json.loads((FIXTURES / "rooms.json").read_text(encoding="utf-8")))
    assert offers[383732].meal_plan_label == "half board"
    assert offers[383732].cancel_for(147793) == "free until 3 days before"
    assert offers[856556].meal_plan_label == "other (code 176)"
    assert offers[856556].name.startswith("Jesienny relaks")
    assert rooms[259790] == "Superior"
    assert rooms[210306] == "Apartament Family"


def test_money_and_names_from_minimal_payloads():
    stay = STAY
    quotes, skipped = parse_availability(
        [
            {
                "proposals": [
                    {
                        "proposal": {
                            "OfferID": 1,
                            "RoomID": 2,
                            "price": {"amount": 10.5, "currency": "PLN"},
                            "originalPrice": 12,
                            "recentLowestPrice": None,
                            "profileId": 9,
                        },
                        "roomCount": 1,
                    }
                ]
            }
        ],
        stay,
    )
    assert skipped == 0
    assert quotes[0].price == 1050
    assert quotes[0].original == 1200
    assert quotes[0].recent_lowest is None
    assert parse_rooms([{"id": 7, "gallery": {"title": "Loft"}, "translations": []}]) == {7: "Loft"}


def _item(price=500, rooms=3, profile=9, stay=None):
    proposal = {
        "OfferID": 1,
        "RoomID": 2,
        "price": None if price is None else {"amount": price, "currency": "PLN"},
        "profileId": profile,
        "recentLowestPrice": None,
        "originalPrice": None,
    }
    if stay is not False:
        proposal["stay"] = {"from": "2026-12-04", "to": "2026-12-06"} if stay is None else stay
    item = {"proposal": proposal}
    if rooms is not None:
        item["roomCount"] = rooms
    return item


def test_bad_proposals_are_skipped_and_a_missing_room_count_is_kept():
    null_price = _item(price=None)
    wrong_dates = _item(stay={"from": "2026-01-01", "to": "2026-01-03"})
    null_profile = _item()
    null_profile["proposal"]["profileId"] = None
    missing_rooms = _item(price=400, rooms=None)
    good = _item(price=500, rooms=2)
    quotes, skipped = parse_availability(
        [{"proposals": [null_price, {"proposal": "nope"}, wrong_dates, null_profile, missing_rooms, good]}],
        STAY,
    )
    assert skipped == 4
    assert [quote.price for quote in quotes] == [40_000, 50_000]
    assert quotes[0].rooms_left is None
    assert quotes[1].rooms_left == 2
    assert quotes[0].stay == STAY


def test_no_valid_quotes_from_a_non_empty_list_is_an_error():
    with pytest.raises(ProfitroomError, match="no valid quotes"):
        parse_availability([{"proposals": [_item(price=None)]}], STAY)


def test_bad_availability_is_a_clear_error():
    with pytest.raises(ProfitroomError):
        parse_availability({"proposals": []}, STAY)


def test_availability_url_keeps_brackets_and_codes():
    url = availability_url("examplehotel", STAY, 2, ("NEWS", "TEN OFF"))
    assert url.startswith("https://booking.profitroom.com/api/examplehotel/availability?")
    assert "checkIn=2026-12-04" in url
    assert "checkOut=2026-12-06" in url
    assert "occupancy[0][adults]=2" in url
    assert "codes[0]=NEWS" in url
    assert "codes[1]=TEN%20OFF" in url
    assert "lang=pl" in url
    assert "currency=PLN" in url


def test_client_pauses_and_retries_then_parses():
    calls = []
    sleeps = []

    def fetcher(url):
        calls.append(url)
        if "offers" in url and len([item for item in calls if "offers" in item]) == 1:
            return HttpResult(429, b"")
        if "offers" in url:
            return HttpResult(200, b"[]")
        if "rooms" in url:
            return HttpResult(200, b"[]")
        raise AssertionError(url)

    client = ProfitroomClient("examplehotel", fetcher=fetcher, sleep=sleeps.append)
    catalog = client.catalog()
    assert catalog.offers == {}
    assert catalog.rooms == {}
    assert sleeps[0] == 1


def test_client_fails_clearly_on_404_and_gives_up_on_500():
    calls = []

    def missing(url):
        calls.append(url)
        return HttpResult(404, b"no")

    client = ProfitroomClient("examplehotel", fetcher=missing, sleep=lambda _seconds: None)
    with pytest.raises(ProfitroomError, match="404"):
        client.catalog()
    assert len(calls) == 1

    def down(url):
        calls.append("down")
        return HttpResult(503, b"")

    client = ProfitroomClient("examplehotel", fetcher=down, sleep=lambda _seconds: None)
    with pytest.raises(ProfitroomError, match="3"):
        client.catalog()
    assert calls.count("down") == 3


def test_client_fetches_a_shared_stay_once():
    calls = []

    def fetcher(url):
        calls.append(url)
        if "availability" in url:
            body = b'[{"occupancy": {"adults": 2, "children": []}, "proposals": []}]'
            return HttpResult(200, body)
        return HttpResult(200, b"[]")

    client = ProfitroomClient("examplehotel", fetcher=fetcher, sleep=lambda _seconds: None)
    client.catalog()
    first = client.quotes(STAY, 2, ())
    second = client.quotes(STAY, 2, ())
    assert first == second == []
    assert len([url for url in calls if "availability" in url]) == 1


def test_user_agent_is_generic(monkeypatch):
    seen = {}

    class Response:
        status = 200

        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=0):
        seen["agent"] = request.get_header("User-agent")
        return Response()

    monkeypatch.setattr("stay_watch.profitroom.urllib.request.urlopen", fake_urlopen)
    result = urllib_fetcher("https://booking.profitroom.com/api/examplehotel/rooms?lang=pl")
    assert result.status == 200
    assert seen["agent"] == "stay-watch/1.0"
    assert "@" not in seen["agent"]
