import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest

from stay_watch.config import load_secrets, load_watch
from stay_watch.model import Catalog, OfferInfo, tracked_id
from stay_watch.profitroom import FixtureClient
from stay_watch.service import RecordedFailure, execute_scan
from stay_watch.store import Store
from tests.support import make_quote, stay
from tests.test_scan import FIXTURES, run_cli

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
}


def _trip_row(check_in, check_out, adults=2, codes=None, added="2026-11-09", include_id=True):
    codes = list(codes or [])
    row = {
        "check_in": check_in,
        "check_out": check_out,
        "adults": adults,
        "codes": codes,
        "added": added,
    }
    if include_id:
        row["id"] = tracked_id(date.fromisoformat(check_in), date.fromisoformat(check_out), adults, tuple(codes))
    return row


def _write_config(tmp_path, tracked, searches=None):
    payload = {"version": 2, "searches": searches or [], "tracked": tracked}
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


def test_tracked_price_changes_send_no_email(tmp_path, capsys):
    found = stay("2026-12-04", "2026-12-06")
    config = _write_config(tmp_path, [_trip_row("2026-12-04", "2026-12-06")])
    client = _Scripted({found.key: [100_000, 80_000, 120_000]})
    _scan(tmp_path, config, client)
    _scan(tmp_path, config, client)
    _scan(tmp_path, config, client)
    text = capsys.readouterr().out
    assert "Subject:" not in text
    assert "Weekly digest" not in text
    assert "Now tracking" not in text
    state = json.loads((tmp_path / "docs" / "data" / "state.json").read_text(encoding="utf-8"))
    saved = state["tracked"]["2026-12-04_2026-12-06_a2"]
    assert saved == {"first_price": 100_000, "last_seen_price": 120_000}
    latest = json.loads((tmp_path / "docs" / "data" / "latest.json").read_text(encoding="utf-8"))
    row = latest["tracked"][0]
    assert row["price"] == 120_000
    assert row["first_price"] == 100_000
    assert "notify" not in row
    assert "below" not in row
    assert "last_notified_price" not in row


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
        "version": 2,
        "digest": {"weekday": "mon"},
        "searches": [
            {
                "id": "s1",
                "name": None,
                "from": "2026-12-01",
                "to": "2026-12-08",
                "nights": 2,
                "must_include": ["sat"],
                "adults": 2,
                "alert": {"under": 1},
            }
        ],
        "tracked": [_trip_row("2026-12-04", "2026-12-06")],
    }
    (docs / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    proc = run_cli(tmp_path, ["scan", "--config", "docs/config.json", "--fixtures", str(FIXTURES)])
    assert proc.returncode == 0, proc.stderr
    assert "Now tracking" not in proc.stdout
    assert "Subject:" not in proc.stdout
    assert "Weekly digest" not in proc.stdout
    latest = json.loads((docs / "data" / "latest.json").read_text(encoding="utf-8"))
    assert set(latest["tracked"][0]) == TRACKED_KEYS
    row = latest["tracked"][0]
    assert row["id"] == "2026-12-04_2026-12-06_a2"
    assert row["price"] == 127_000
    assert row["first_price"] == 127_000
    assert row["lowest_seen"] == 127_000
    assert row["previous_price"] is None
    assert row["sold_out"] is False
    assert row["history_key"] == "adults=2;codes=;offers=;rooms="
    assert latest["searches"][0]["custom_name"] is False
    assert latest["searches"][0]["name"] == "1 Dec – 8 Dec"
    assert latest["searches"][0]["alert"]["under"] == 1
    assert latest["searches"][0]["stays"][0]["alert_match"] is False
    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    subprocess.run([git, "init"], cwd=tmp_path, check=True, capture_output=True)
    clear = run_cli(tmp_path, ["check-public"])
    assert clear.returncode == 0, clear.stdout + clear.stderr
