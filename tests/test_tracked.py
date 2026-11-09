import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest

from stay_watch.config import load_secrets, load_watch
from stay_watch.model import Catalog, OfferInfo, StayResult, tracked_id
from stay_watch.profitroom import FixtureClient
from stay_watch.service import RecordedFailure, execute_scan
from stay_watch.store import Store
from stay_watch.tracked import evaluate_tracked
from tests.support import make_quote, make_search, stay
from tests.test_scan import FIXTURES, REPO, run_cli

TODAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=timezone(timedelta(hours=1)))
TRACKED_KEYS = {
    "id",
    "key",
    "check_in",
    "check_out",
    "nights",
    "adults",
    "codes",
    "notify",
    "below",
    "added",
    "history_key",
    "sold_out",
    "offer_count",
    "price",
    "per_night",
    "recent_lowest",
    "original",
    "rooms_left",
    "offer_name",
    "room_name",
    "meal_plan",
    "cancel",
    "previous_price",
    "first_price",
    "lowest_seen",
    "last_notified_price",
}


def _trip_row(check_in, check_out, adults=2, codes=None, notify="any", below=None, added="2026-11-09", include_id=True):
    codes = list(codes or [])
    row = {
        "check_in": check_in,
        "check_out": check_out,
        "adults": adults,
        "codes": codes,
        "notify": notify,
        "below": below,
        "added": added,
    }
    if include_id:
        row["id"] = tracked_id(date.fromisoformat(check_in), date.fromisoformat(check_out), adults, tuple(codes))
    return row


def _write_config(tmp_path, tracked, searches=None, digest="off"):
    payload = {"version": 1, "digest": {"weekday": digest}, "searches": searches or [], "tracked": tracked}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_watch(path)


def _secrets():
    return load_secrets(
        {
            "HOTEL_SLUG": "examplehotel",
            "HOTEL_NAME_TERMS": "Example Hotel",
            "DRY_RUN": "1",
            "ALERT_TO": "guest@example.com",
        }
    )


def _result(price, when=None):
    found = when or stay("2026-12-04", "2026-12-06")
    quote = None if price is None else make_quote(price, found)
    return StayResult(found, quote, 0 if quote is None else 1)


def _trip(notify="any", below=None, when=None):
    from stay_watch.model import TrackedTrip

    found = when or stay("2026-12-04", "2026-12-06")
    return TrackedTrip(
        tracked_id(found.check_in, found.check_out, 2, ()),
        found,
        2,
        (),
        notify,
        below,
        TODAY,
    )


def test_now_tracking_is_sent_once_and_the_same_price_is_quiet():
    trip = _trip()
    first, entry = evaluate_tracked(trip, _result(100_000), None)
    assert first is not None
    assert first.reasons == ("now_tracking",)
    assert entry["first_price"] == 100_000
    assert entry["last_notified_price"] == 100_000
    second, entry = evaluate_tracked(trip, _result(100_000), entry)
    assert second is None
    assert entry["last_notified_price"] == 100_000


def test_any_sends_on_a_rise_and_on_a_fall():
    trip = _trip("any")
    _first, entry = evaluate_tracked(trip, _result(100_000), None)
    risen, entry = evaluate_tracked(trip, _result(110_000), entry)
    assert risen is not None and "change" in risen.reasons
    assert entry["last_notified_price"] == 110_000
    fallen, entry = evaluate_tracked(trip, _result(90_000), entry)
    assert fallen is not None and "change" in fallen.reasons
    assert fallen.previous_price == 110_000


def test_drop_sends_only_when_the_price_falls_below_the_last_emailed_price():
    trip = _trip("drop")
    _first, entry = evaluate_tracked(trip, _result(100_000), None)
    risen, entry = evaluate_tracked(trip, _result(120_000), entry)
    assert risen is None
    still_above, entry = evaluate_tracked(trip, _result(110_000), entry)
    assert still_above is None
    assert entry["last_notified_price"] == 100_000
    fallen, entry = evaluate_tracked(trip, _result(90_000), entry)
    assert fallen is not None and "change" in fallen.reasons
    assert entry["last_notified_price"] == 90_000


def test_sold_out_then_available_sends_under_both_modes():
    for notify in ("any", "drop"):
        trip = _trip(notify)
        quiet, entry = evaluate_tracked(trip, _result(None), None)
        assert quiet is None
        assert entry["first_price"] is None
        back, entry = evaluate_tracked(trip, _result(90_000), entry)
        assert back is not None and "available" in back.reasons
        assert entry["last_notified_price"] == 90_000


def test_a_later_sellout_sends_only_for_any():
    for notify, expected in (("any", True), ("drop", False)):
        trip = _trip(notify)
        _first, entry = evaluate_tracked(trip, _result(100_000), None)
        notice, _entry = evaluate_tracked(trip, _result(None), entry)
        assert (notice is not None) is expected
        if notice is not None:
            assert "sold_out" in notice.reasons


def test_below_sends_once_and_rearms_after_a_rise():
    # Same watermark as a search alert: a rise keeps the last emailed price,
    # and the next email waits for a price under that, not merely under the threshold.
    trip = _trip("drop", below=130_000)
    entry = {
        "introduced": True,
        "first_price": 200_000,
        "last_notified_price": 200_000,
        "last_seen_price": 120_000,
    }
    first, entry = evaluate_tracked(trip, _result(120_000), entry)
    assert first is not None and first.reasons == ("below",)
    assert entry["below_sent"] == 120_000
    again, entry = evaluate_tracked(trip, _result(120_000), entry)
    assert again is None
    risen, entry = evaluate_tracked(trip, _result(140_000), entry)
    assert risen is None
    assert entry["below_sent"] == 120_000
    still_under_the_line, entry = evaluate_tracked(trip, _result(125_000), entry)
    assert still_under_the_line is None
    lower, entry = evaluate_tracked(trip, _result(110_000), entry)
    assert lower is not None and "below" in lower.reasons


class _Scripted:
    def __init__(self, prices):
        self.prices = {key: list(values) for key, values in prices.items()}
        self.calls = []

    def catalog(self):
        return Catalog({1: OfferInfo("Rate", "breakfast", "non-refundable")}, {1: "Double"})

    def quotes(self, found, adults, codes):
        self.calls.append((found.key, adults, tuple(codes)))
        values = self.prices.get(found.key, [None])
        price = values.pop(0) if len(values) > 1 else values[0]
        if price is None:
            return []
        return [make_quote(price, found)]


def _scan(tmp_path, config, client):
    execute_scan(config, _secrets(), Store(tmp_path / "docs", _secrets().terms), client, NOW)


def test_service_sends_now_tracking_once(tmp_path, capsys):
    found = stay("2026-12-04", "2026-12-06")
    config = _write_config(tmp_path, [_trip_row("2026-12-04", "2026-12-06")])
    client = _Scripted({found.key: [100_000, 100_000]})
    _scan(tmp_path, config, client)
    first = capsys.readouterr().out
    assert "Now tracking" in first
    assert "Subject: Tracked: Fri 4 Dec → Sun 6 Dec now 1 000 zł" in first
    _scan(tmp_path, config, client)
    second = capsys.readouterr().out
    assert "Now tracking" not in second
    assert "Subject:" not in second
    state = json.loads((tmp_path / "docs" / "data" / "state.json").read_text(encoding="utf-8"))
    saved = state["tracked"]["2026-12-04_2026-12-06_a2"]
    assert saved["first_price"] == 100_000
    assert saved["last_notified_price"] == 100_000


def test_a_tracked_stay_that_is_also_searched_is_fetched_once(tmp_path):
    config = _write_config(
        tmp_path,
        [
            _trip_row("2026-12-04", "2026-12-06"),
            _trip_row("2027-01-08", "2027-01-10"),
        ],
        searches=[
            {
                "id": "s1",
                "name": "Weekends",
                "from": "2026-12-01",
                "to": "2026-12-08",
                "nights": 2,
                "must_include": ["sat"],
                "adults": 2,
            }
        ],
    )

    class Counting:
        def __init__(self):
            self.inner = FixtureClient(FIXTURES)
            self.calls = []

        def catalog(self):
            return self.inner.catalog()

        def quotes(self, found, adults, codes):
            self.calls.append((found.key, adults, tuple(codes)))
            return self.inner.quotes(found, adults, codes)

    client = Counting()
    _scan(tmp_path, config, client)
    assert client.calls.count(("2026-12-04_2026-12-06", 2, ())) == 1
    assert ("2027-01-08_2027-01-10", 2, ()) in client.calls
    assert len(client.calls) == len(set(client.calls))


def test_past_check_in_is_dropped_from_fetch_latest_and_state(tmp_path):
    future = "2026-12-04_2026-12-06_a2"
    past = "2026-01-02_2026-01-04_a2"
    removed = "2026-12-20_2026-12-22_a2"
    config = _write_config(
        tmp_path,
        [
            _trip_row("2026-01-02", "2026-01-04"),
            _trip_row("2026-12-04", "2026-12-06"),
        ],
    )
    data = tmp_path / "docs" / "data"
    data.mkdir(parents=True)
    (data / "state.json").write_text(
        json.dumps({"tracked": {past: {"introduced": True}, future: {"introduced": True}, removed: {"introduced": True}}}),
        encoding="utf-8",
    )
    calls = []

    class Client:
        def catalog(self):
            return FixtureClient(FIXTURES).catalog()

        def quotes(self, found, adults, codes):
            calls.append(found.key)
            if found.key.startswith("2026-01-02"):
                raise AssertionError("a past stay was fetched")
            return FixtureClient(FIXTURES).quotes(found, adults, codes)

    _scan(tmp_path, config, Client())
    assert "2026-01-02_2026-01-04" not in calls
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in latest["tracked"]] == [future]
    state = json.loads((data / "state.json").read_text(encoding="utf-8"))
    assert set(state["tracked"]) == {future}


def test_tracked_trips_count_only_when_they_are_the_only_stays(tmp_path):
    data = tmp_path / "docs" / "data"
    data.mkdir(parents=True)
    (data / "latest.json").write_text(json.dumps({"searches": [{"stays": [{"price": 100}]}]}), encoding="utf-8")
    extra = [_trip_row(f"2026-12-{day:02d}", f"2026-12-{day + 2:02d}") for day in (10, 11, 12, 13, 14)]

    class Empty:
        def catalog(self):
            return Catalog({}, {})

        def quotes(self, found, adults, codes):
            return []

    small = _write_config(
        tmp_path,
        [_trip_row("2026-12-04", "2026-12-06"), *extra],
        searches=[{"id": "s1", "name": "One", "from": "2026-12-04", "to": "2026-12-06", "nights": 2, "must_include": ["sat"]}],
    )
    _scan(tmp_path, small, Empty())
    state = json.loads((data / "state.json").read_text(encoding="utf-8"))
    assert state["consecutive_failures"] == 0

    only = _write_config(tmp_path, extra)
    with pytest.raises(RecordedFailure):
        _scan(tmp_path, only, Empty())

    four = _write_config(tmp_path, extra[:4])
    _scan(tmp_path, four, Empty())
    state = json.loads((data / "state.json").read_text(encoding="utf-8"))
    assert state["consecutive_failures"] == 0


def test_fixture_scan_writes_the_tracked_section_and_stays_public(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    payload = {
        "version": 1,
        "digest": {"weekday": "off"},
        "searches": [
            {
                "id": "s1",
                "name": None,
                "from": "2026-12-01",
                "to": "2026-12-08",
                "nights": 2,
                "must_include": ["sat"],
                "adults": 2,
                "alerts": [{"kind": "below", "amount": 1}],
            }
        ],
        "tracked": [_trip_row("2026-12-04", "2026-12-06", notify="any", below=None)],
    }
    (docs / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    proc = run_cli(tmp_path, ["scan", "--config", "docs/config.json", "--fixtures", str(FIXTURES)])
    assert proc.returncode == 0, proc.stderr
    assert "Now tracking" in proc.stdout
    assert "Subject: Tracked: Fri 4 Dec → Sun 6 Dec now 1 270 zł" in proc.stdout
    assert "Tracked trips" in proc.stdout
    latest = json.loads((docs / "data" / "latest.json").read_text(encoding="utf-8"))
    assert set(latest["tracked"][0]) == TRACKED_KEYS
    row = latest["tracked"][0]
    assert row["id"] == "2026-12-04_2026-12-06_a2"
    assert row["price"] == 127_000
    assert row["first_price"] == 127_000
    assert row["lowest_seen"] == 127_000
    assert row["last_notified_price"] == 127_000
    assert row["previous_price"] is None
    assert row["below"] is None
    assert row["sold_out"] is False
    assert row["history_key"] == "adults=2;codes=;offers=;rooms="
    assert latest["searches"][0]["custom_name"] is False
    assert latest["searches"][0]["name"] == "1 Dec – 8 Dec"
    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    subprocess.run([git, "init"], cwd=tmp_path, check=True, capture_output=True)
    clear = run_cli(tmp_path, ["check-public"])
    assert clear.returncode == 0, clear.stdout + clear.stderr


def test_digest_lists_tracked_trips_above_searches(tmp_path, capsys):
    config = _write_config(
        tmp_path,
        [_trip_row("2026-12-04", "2026-12-06")],
        searches=[{"id": "s1", "name": "Weekends", "from": "2026-12-01", "to": "2026-12-08"}],
        digest="mon",
    )
    _scan(tmp_path, config, FixtureClient(FIXTURES))
    text = capsys.readouterr().out
    assert "Subject: Weekly digest:" in text
    assert text.index("Tracked trips") < text.index("Weekends")
    assert "Fri 4 Dec → Sun 6 Dec — 1 270 zł" in text
