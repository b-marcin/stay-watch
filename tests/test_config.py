from datetime import date
from pathlib import Path

import pytest

from stay_watch.config import ConfigError, load_secrets, load_watch
from stay_watch.model import Below, BelowRecentLowest

REPO = Path(__file__).resolve().parents[1]


def test_shipped_watch_file_merges_the_documented_defaults():
    config = load_watch(REPO / "watch.toml")
    assert config.digest_weekday == 0
    assert len(config.searches) == 1
    search = config.searches[0]
    assert search.id == "winter-weekends"
    assert search.name == "Winter weekends"
    assert search.window_start == date(2026, 12, 1)
    assert search.window_end == date(2027, 3, 1)
    assert search.nights == 2
    assert search.adults == 2
    assert search.must_include == frozenset({5})
    assert search.top == 3
    assert search.codes == ()
    assert search.alerts == (Below(130_000), BelowRecentLowest())


def test_defaults_merge_and_polish_weekday_names(tmp_path):
    path = tmp_path / "watch.toml"
    path.write_text(
        """
[defaults]
nights = 4
adults = 2
must_include = ["mon"]
top = 5
codes = ["NEWS"]

[digest]
weekday = "off"

[[search]]
id = "weekend"
name = "Weekend"
from = "2026-12-01"
to = "2026-12-31"
nights = 2
adults = 3
must_include = ["sob", "pt"]
top = "all"
codes = []

[[search]]
id = "inherited"
name = "Inherited"
from = "2026-12-01"
to = "2026-12-15"
""".strip(),
        encoding="utf-8",
    )
    config = load_watch(path)
    assert config.digest_weekday is None
    weekend, inherited = config.searches
    assert weekend.nights == 2
    assert weekend.adults == 3
    assert weekend.must_include == frozenset({4, 5})
    assert weekend.top is None
    assert weekend.codes == ()
    assert inherited.nights == 4
    assert inherited.adults == 2
    assert inherited.must_include == frozenset({0})
    assert inherited.top == 5
    assert inherited.codes == ("NEWS",)


def test_bad_date_names_the_line(tmp_path):
    path = tmp_path / "watch.toml"
    path.write_text(
        "\n".join(
            [
                "[defaults]",
                "adults = 2",
                "",
                "[[search]]",
                'id = "bad"',
                'name = "Bad"',
                'from = "2026-13-01"',
                'to = "2026-12-31"',
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_watch(path)
    message = str(caught.value)
    assert ":7:" in message
    assert "2026-13-01" in message


def test_unknown_key_is_rejected(tmp_path):
    path = tmp_path / "watch.toml"
    path.write_text("[defaults]\nnigths = 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="nigths"):
        load_watch(path)


def test_secrets_include_the_slug_and_longest_terms_first():
    secrets = load_secrets(
        {
            "HOTEL_SLUG": "examplehotel",
            "HOTEL_NAME_TERMS": "Example, Example Hotel",
            "DRY_RUN": "1",
        }
    )
    assert secrets.terms == ("Example Hotel", "examplehotel", "Example")
    assert secrets.dry_run
    assert secrets.smtp_host == "smtp.gmail.com"
    assert secrets.smtp_port == 465
    assert secrets.alert_to is None
    with pytest.raises(ConfigError, match="HOTEL_SLUG"):
        load_secrets({})
