import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from stay_watch.config import load_secrets
from stay_watch.profitroom import FixtureClient
from stay_watch.service import execute_search
from tests.support import make_search

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
PY = sys.executable


def _env(extra=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HOTEL_SLUG"] = "examplehotel"
    env["HOTEL_NAME_TERMS"] = "Example Hotel"
    env["DRY_RUN"] = "1"
    env["ALERT_TO"] = "guest@example.com"
    env["STAY_WATCH_NOW"] = "2026-11-09T10:00:00+01:00"
    env.pop("SMTP_PASSWORD", None)
    git_cmd = Path(r"C:\Program Files\Git\cmd")
    if git_cmd.is_dir():
        env["PATH"] = str(git_cmd) + os.pathsep + env.get("PATH", "")
    if extra:
        env.update(extra)
    return env


def run_cli(tmp_path, args, extra_env=None):
    return subprocess.run(
        [PY, "-m", "stay_watch", *args],
        cwd=tmp_path,
        env=_env(extra_env),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def write_watch(tmp_path, text):
    (tmp_path / "watch.toml").write_text(text, encoding="utf-8")


ALERT_TOML = """
[defaults]
adults = 2
nights = 2
must_include = ["sat"]
top = 3
codes = []

[digest]
weekday = "off"

[[search]]
id = "winter-weekends"
name = "Winter at Example Hotel"
from = "2026-12-01"
to = "2026-12-08"
  [[search.alert]]
  below = 1300
"""

QUIET_TOML = """
[defaults]
adults = 2
nights = 2
must_include = ["sat"]
top = 3
codes = []

[digest]
weekday = "mon"

[[search]]
id = "winter-weekends"
name = "Winter weekends"
from = "2026-12-01"
to = "2026-12-08"
  [[search.alert]]
  below = 1
"""


def test_scan_redacts_public_files_and_emails_the_deep_link(tmp_path):
    write_watch(tmp_path, ALERT_TOML)
    first = run_cli(tmp_path, ["scan", "--fixtures", str(FIXTURES)])
    assert first.returncode == 0, first.stderr
    assert "1 270 zł" in first.stdout
    assert "Example Hotel" in first.stdout
    assert (
        "https://booking.profitroom.com/pl/examplehotel/pricelist/rooms?"
        "check-in=2026-12-04&check-out=2026-12-06&r1_adults=2&currency=PLN"
    ) in first.stdout
    assert "1 stay under 1 300 zł: Fri 4 Dec → Sun 6 Dec 1 270 zł" in first.stdout

    latest_path = tmp_path / "docs" / "data" / "latest.json"
    history_path = tmp_path / "docs" / "data" / "history.json"
    latest_text = latest_path.read_text(encoding="utf-8")
    history_text = history_path.read_text(encoding="utf-8")
    for text in (latest_text, history_text):
        assert "Example Hotel" not in text
        assert "examplehotel" not in text.casefold()
        assert "profitroom" not in text.casefold()
    latest = json.loads(latest_text)
    assert latest["searches"][0]["name"] == "Winter at the hotel"
    stay = next(item for item in latest["searches"][0]["stays"] if item["price"] == 127_000)
    assert stay["offer_name"] == "Oferta bezzwrotna"
    assert stay["room_name"] == "Standard Room"
    assert stay["meal_plan"] == "breakfast"
    assert stay["cancel"] == "non-refundable"
    assert "below:130000" in stay["matched_rules"]
    history = json.loads(history_text)
    series = next(iter(history.values()))
    assert series["2026-12-04_2026-12-06"][0][1] == 127_000
    assert len(series["2026-12-04_2026-12-06"]) == 1

    second = run_cli(tmp_path, ["scan", "--fixtures", str(FIXTURES)])
    assert second.returncode == 0, second.stderr
    assert "booking.profitroom.com" not in second.stdout
    assert len(json.loads(history_path.read_text(encoding="utf-8"))[next(iter(history))]["2026-12-04_2026-12-06"]) == 1

    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    subprocess.run([git, "init"], cwd=tmp_path, check=True, capture_output=True)
    clear = run_cli(tmp_path, ["check-public"])
    assert clear.returncode == 0, clear.stdout + clear.stderr
    (tmp_path / "docs" / "plant.txt").write_text("see Example Hotel\n", encoding="utf-8")
    planted = run_cli(tmp_path, ["check-public"])
    assert planted.returncode != 0
    assert "docs/plant.txt:1:" in planted.stdout


def test_weekly_digest_sends_once_per_day(tmp_path):
    write_watch(tmp_path, QUIET_TOML)
    monday = run_cli(tmp_path, ["scan", "--fixtures", str(FIXTURES)])
    assert monday.returncode == 0, monday.stderr
    assert "Weekly digest: Fri 4 Dec → Sun 6 Dec 1 270 zł" in monday.stdout
    assert "under 1 zł" not in monday.stdout
    again = run_cli(tmp_path, ["scan", "--fixtures", str(FIXTURES)])
    assert again.returncode == 0, again.stderr
    assert "Weekly digest" not in again.stdout
    next_week = run_cli(
        tmp_path,
        ["scan", "--fixtures", str(FIXTURES)],
        {"STAY_WATCH_NOW": "2026-11-16T10:00:00+01:00"},
    )
    assert next_week.returncode == 0, next_week.stderr
    assert "Weekly digest" in next_week.stdout


def test_repeated_empty_feed_alerts_then_recovers(tmp_path):
    write_watch(tmp_path, QUIET_TOML.replace('weekday = "mon"', 'weekday = "off"'))
    empty = tmp_path / "empty-fixtures"
    empty.mkdir()
    shutil.copy(FIXTURES / "offers.json", empty / "offers.json")
    shutil.copy(FIXTURES / "rooms.json", empty / "rooms.json")
    docs = tmp_path / "docs" / "data"
    docs.mkdir(parents=True)
    previous = json.dumps({"searches": [{"stays": [{"price": 100}]}]})
    (docs / "latest.json").write_text(previous, encoding="utf-8")

    outputs = []
    for _ in range(3):
        proc = run_cli(tmp_path, ["scan", "--fixtures", str(empty)])
        assert proc.returncode == 1
        assert "zero proposals" in proc.stderr
        outputs.append(proc.stdout)
        assert (docs / "latest.json").read_text(encoding="utf-8") == previous
    assert "Stay Watch is failing" not in outputs[0]
    assert "Stay Watch is failing" not in outputs[1]
    assert "Stay Watch is failing" in outputs[2]

    recovered = run_cli(tmp_path, ["scan", "--fixtures", str(FIXTURES)])
    assert recovered.returncode == 0, recovered.stderr
    assert "Stay Watch recovered" in recovered.stdout
    state = json.loads((docs / "state.json").read_text(encoding="utf-8"))
    assert state["consecutive_failures"] == 0
    latest = json.loads((docs / "latest.json").read_text(encoding="utf-8"))
    prices = [stay["price"] for stay in latest["searches"][0]["stays"]]
    assert 127_000 in prices


def test_actions_search_prints_a_count_and_not_the_names(tmp_path):
    proc = run_cli(
        tmp_path,
        [
            "search",
            "--from",
            "2026-12-01",
            "--to",
            "2026-12-08",
            "--fixtures",
            str(FIXTURES),
        ],
        {"GITHUB_ACTIONS": "true"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "search ok: 2 stays"
    assert "Oferta" not in proc.stdout
    assert "Example" not in proc.stdout


def test_actions_search_marks_the_count_when_it_emails(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    sent = []
    monkeypatch.setattr("stay_watch.service.deliver", lambda secrets, message: sent.append(message["Subject"]))
    secrets = load_secrets({"HOTEL_SLUG": "examplehotel", "DRY_RUN": "1", "ALERT_TO": "guest@example.com"})
    execute_search(make_search(window_end=date(2026, 12, 8)), secrets, FixtureClient(FIXTURES), True)
    assert sent
    assert capsys.readouterr().out.strip() == "search ok: 2 stays, emailed"


def test_search_prints_a_table_and_does_not_touch_the_store(tmp_path):
    proc = run_cli(
        tmp_path,
        [
            "search",
            "--from",
            "2026-12-01",
            "--to",
            "2026-12-08",
            "--must-include",
            "sob",
            "--fixtures",
            str(FIXTURES),
            "--email",
        ],
    )
    assert proc.returncode == 0, proc.stderr
    assert "1 270 zł" in proc.stdout
    assert "Oferta bezzwrotna" in proc.stdout
    assert "booking.profitroom.com/pl/examplehotel/" in proc.stdout
    assert not (tmp_path / "docs").exists()
