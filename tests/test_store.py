from datetime import date

from stay_watch.model import Catalog, StayResult
from stay_watch.render import describe
from stay_watch.store import Store, describe_quote, redact, update_history
from tests.support import make_quote, stay


def test_unknown_offer_and_room_use_fallback_labels():
    quote = make_quote(1000, offer_id=123, room_id=456)
    result = StayResult(stay("2026-12-04", "2026-12-06"), quote, 1)
    catalog = Catalog({}, {})
    assert describe(result, catalog) == ("Offer 123", "Room 456", "other", "no free cancellation")
    described = describe_quote(catalog, result)
    assert described["offer_name"] == "Offer 123"
    assert described["room_name"] == "Room 456"


def test_redact_replaces_every_configured_term_case_insensitively():
    terms = ["Example", "Example Hotel"]
    assert redact("Stay at Example Hotel", terms) == "Stay at the hotel"
    assert redact("EXAMPLE HOTEL resort", ["Example Hotel"]) == "the hotel resort"
    assert redact({"slug": "examplehotel", "ok": 3, "flag": True}, ["examplehotel"]) == {
        "slug": "the hotel",
        "ok": 3,
        "flag": True,
    }


def test_redact_stems_cover_inflection_odd_spaces_and_case():
    assert redact("pokoje w Exampletownach", ["Examplet"]) == "pokoje w the hotel"
    assert redact("Example\u00a0Hotel", ["Example Hotel"]) == "the hotel"
    assert redact("eXaMpLe\u202fHoTeLu", ["example hotel"]) == "the hotel"
    assert redact("Example   Hotel", ["Example Hotel"]) == "the hotel"
    assert redact("ExampleHotel", ["Example Hotel"]) == "ExampleHotel"


def test_store_write_redacts_before_the_file_hits_disk(tmp_path):
    store = Store(tmp_path / "docs", ["Example Hotel", "examplehotel"])
    store.save_latest({"name": "Winter at Example Hotel", "note": "examplehotel"})
    text = (tmp_path / "docs" / "data" / "latest.json").read_text(encoding="utf-8")
    assert "Example Hotel" not in text
    assert "examplehotel" not in text.casefold()
    assert "the hotel" in text


def test_history_appends_only_when_the_price_changes_and_prunes_past_stays():
    group = "adults=2;codes="
    current = "2026-12-04_2026-12-06"
    past = "2026-11-01_2026-11-03"
    history = update_history(
        {},
        group,
        [
            (current, date(2026, 12, 4), 1000, "Offer", "Room"),
            (past, date(2026, 11, 1), 50, "Old", "Room"),
        ],
        "t1",
        date(2026, 11, 9),
    )
    assert past not in history[group]
    assert history[group][current] == [["t1", 1000, "Offer", "Room"]]

    history = update_history(
        history,
        group,
        [(current, date(2026, 12, 4), 1000, "Offer", "Room")],
        "t2",
        date(2026, 11, 9),
    )
    assert len(history[group][current]) == 1

    history = update_history(
        history,
        group,
        [(current, date(2026, 12, 4), 900, "Offer", "Room")],
        "t3",
        date(2026, 11, 9),
    )
    assert [row[1] for row in history[group][current]] == [1000, 900]

    history = update_history(history, group, [], "t4", date(2026, 12, 5))
    assert current not in history.get(group, {})
