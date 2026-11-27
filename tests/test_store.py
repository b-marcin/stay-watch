from datetime import date, datetime, timedelta, timezone

from stay_watch.model import Catalog, OfferInfo, StayResult, TrackedTrip, tracked_id
from stay_watch.render import describe
from stay_watch.store import Store, build_latest, describe_quote, redact, update_history
from tests.support import make_alert, make_quote, make_search, stay


def test_latest_writes_a_derived_name_and_a_custom_name_flag():
    when = datetime(2026, 11, 9, 10, 0, tzinfo=timezone(timedelta(hours=1)))
    derived = make_search(name=None, window_start=date(2026, 12, 28), window_end=date(2027, 1, 3))
    payload = build_latest([(derived, [])], Catalog({}, {}), {}, when)
    assert payload["searches"][0]["name"] == "28 Dec – 3 Jan"
    assert payload["searches"][0]["custom_name"] is False
    assert payload["searches"][0]["alert"] is None
    custom = build_latest([(make_search(name="Weekends"), [])], Catalog({}, {}), {}, when)
    assert custom["searches"][0]["name"] == "Weekends"
    assert custom["searches"][0]["custom_name"] is True


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


def test_save_state_drops_past_stays_removed_searches_and_searches_without_an_alert(tmp_path):
    search = make_search(id="winter", alert=make_alert(under=10_000))
    quiet = make_search(id="quiet", alert=None)
    store = Store(tmp_path / "docs", [])
    store.save_state(
        {
            "consecutive_failures": 2,
            "alerts_sent": {
                "winter": {
                    "2026-12-04_2026-12-06": 900,
                    "2026-11-09_2026-11-11": 800,
                    "2026-10-01_2026-10-03": 700,
                    "not-a-date": 1,
                },
                "quiet": {"2026-12-04_2026-12-06": 500},
                "gone": {"2026-12-04_2026-12-06": 400},
            },
        },
        today=date(2026, 11, 9),
        searches=[search, quiet],
    )
    saved = store.load_state()
    assert saved["consecutive_failures"] == 2
    assert "last_digest_date" not in saved
    assert saved["alerts_sent"] == {
        "winter": {
            "2026-12-04_2026-12-06": 900,
            "2026-11-09_2026-11-11": 800,
            "not-a-date": 1,
        }
    }


def test_old_alerts_sent_keeps_the_lowest_price_per_stay(tmp_path):
    store = Store(tmp_path / "docs", [])
    data = tmp_path / "docs" / "data"
    data.mkdir(parents=True)
    (data / "state.json").write_text(
        """
        {
          "alerts_sent": {
            "winter": {
              "below:120000": {"2026-12-04_2026-12-06": 110000, "2026-12-11_2026-12-13": 90000},
              "below_recent_lowest": {"2026-12-04_2026-12-06": 100000}
            }
          },
          "last_digest_date": "2026-11-09"
        }
        """,
        encoding="utf-8",
    )
    loaded = store.load_state()
    assert loaded["alerts_sent"] == {
        "winter": {
            "2026-12-04_2026-12-06": 100000,
            "2026-12-11_2026-12-13": 90000,
        }
    }
    assert "last_digest_date" not in loaded


def test_latest_carries_alert_alert_match_and_no_tracked_notify_fields():
    when = datetime(2026, 11, 9, 10, 0, tzinfo=timezone(timedelta(hours=1)))
    found = stay("2026-12-04", "2026-12-06")
    search = make_search(alert=make_alert(under=130_000, again_cheaper_by=5_000, discounted=True))
    quiet = make_search(id="quiet", name="Quiet", alert=None)
    later = stay("2026-12-11", "2026-12-13")
    matched = StayResult(found, make_quote(120_000, found, original=140_000), 1)
    at_limit = StayResult(
        later,
        make_quote(130_000, later, original=140_000, recent_lowest=90_000),
        1,
    )
    trip = TrackedTrip(tracked_id(found.check_in, found.check_out, 2, ()), found, 2, (), date(2026, 11, 9))
    catalog = Catalog({1: OfferInfo("Rate", "breakfast", "non-refundable")}, {1: "Double"})
    payload = build_latest(
        [(search, [matched, at_limit]), (quiet, [matched])],
        catalog,
        {},
        when,
        [(trip, matched)],
        {trip.id: {"first_price": 120_000, "last_notified_price": 120_000}},
    )
    emailed = payload["searches"][0]
    assert emailed["alert"] == {
        "under": 1300,
        "again_cheaper_by": 50,
        "require": {"recent_low": False, "discounted": True, "drop_pct": None},
    }
    assert emailed["stays"][0]["alert_match"] is True
    assert emailed["stays"][1]["alert_match"] is False
    assert "matched_rules" not in emailed["stays"][0]
    assert "alerts" not in emailed
    assert payload["searches"][1]["alert"] is None
    assert payload["searches"][1]["stays"][0]["alert_match"] is False
    row = payload["tracked"][0]
    assert row["first_price"] == 120_000
    assert "notify" not in row
    assert "below" not in row
    assert "last_notified_price" not in row
