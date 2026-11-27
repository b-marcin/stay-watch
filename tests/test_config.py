import json
from datetime import date
from pathlib import Path

import pytest

from stay_watch.config import ConfigError, load_secrets, load_watch
from stay_watch.render import derived_window_name

REPO = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _search(**overrides) -> dict:
    item = {
        "id": "s1",
        "name": "Weekends",
        "from": "2026-12-01",
        "to": "2026-12-31",
    }
    item.update(overrides)
    return item


def test_shipped_config_parses_into_a_valid_watch():
    config = load_watch(REPO / "docs" / "config.json")
    assert config.searches
    for search in config.searches:
        assert search.id
        assert search.window_end > search.window_start
    live = config.searches[0]
    assert live.alert is not None
    assert live.alert.under == 120_000
    assert live.alert.again_cheaper_by == 5_000
    assert live.alert.recent_low is False
    assert live.alert.discounted is False
    assert live.alert.drop_pct is None
    assert config.tracked
    assert not hasattr(config.tracked[0], "notify")


def test_defaults_fill_missing_search_fields_and_null_top_means_all(tmp_path):
    path = _write(
        tmp_path,
        {
            "version": 2,
            "defaults": {
                "nights": 4,
                "adults": 2,
                "must_include": ["mon"],
                "top": 5,
                "codes": ["NEWS"],
            },
            "digest": {"weekday": "off"},
            "searches": [
                _search(
                    id="weekend",
                    nights=2,
                    adults=3,
                    must_include=["fri", "sat"],
                    top=None,
                    codes=[],
                ),
                _search(id="inherited", name="Inherited", **{"from": "2026-12-01", "to": "2026-12-15"}),
            ],
            "tracked": [],
            "note": "ignored",
        },
    )
    config = load_watch(path)
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


def test_unknown_keys_are_ignored(tmp_path):
    path = _write(
        tmp_path,
        {
            "version": 2,
            "extra": True,
            "searches": [_search(nickname="nope")],
            "tracked": [
                {
                    "check_in": "2026-12-19",
                    "check_out": "2026-12-21",
                    "adults": 2,
                    "codes": ["SAVE"],
                    "notify": "drop",
                    "below": None,
                    "added": "2026-11-09",
                    "color": "blue",
                }
            ],
        },
    )
    config = load_watch(path)
    assert config.searches[0].id == "s1"
    trip = config.tracked[0]
    assert trip.id == "2026-12-19_2026-12-21_a2_cSAVE"
    assert trip.codes == ("SAVE",)
    assert not hasattr(trip, "notify")
    assert not hasattr(trip, "below")


def test_a_supplied_tracked_id_must_match_the_trip(tmp_path):
    payload = {
        "version": 2,
        "searches": [],
        "tracked": [
            {
                "id": "2026-12-19_2026-12-21_a2",
                "check_in": "2026-12-19",
                "check_out": "2026-12-21",
                "adults": 2,
                "notify": "any",
                "added": "2026-11-09",
            }
        ],
    }
    config = load_watch(_write(tmp_path, payload))
    assert config.tracked[0].id == "2026-12-19_2026-12-21_a2"
    payload["tracked"][0]["id"] = "other"
    with pytest.raises(ConfigError, match=r"tracked\[0\]\.id: expected 2026-12-19_2026-12-21_a2"):
        load_watch(_write(tmp_path, payload))


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda payload: payload.update({"version": 3}), "version: must be 2"),
        (lambda payload: payload["searches"][0].update({"from": "2026-13-01"}), "searches[0].from:"),
        (lambda payload: payload["searches"][0].update({"id": "Winter"}), "searches[0].id:"),
        (lambda payload: payload["searches"][0].update({"to": "2026-12-01"}), "searches[0].to:"),
        (lambda payload: payload["searches"][0].update({"top": 0}), "searches[0].top:"),
        (lambda payload: payload["searches"][0].update({"name": 4}), "searches[0].name:"),
        (
            lambda payload: payload["searches"][0].update({"alert": {"under": "1300"}}),
            "searches[0].alert.under:",
        ),
        (
            lambda payload: payload["searches"][0].update({"alert": {"under": 0}}),
            "searches[0].alert.under:",
        ),
        (
            lambda payload: payload["searches"][0].update({"alert": {"under": 100, "again_cheaper_by": -1}}),
            "searches[0].alert.again_cheaper_by:",
        ),
        (
            lambda payload: payload["searches"][0].update(
                {"alert": {"under": 100, "require": {"drop_pct": 0}}}
            ),
            "searches[0].alert.require.drop_pct:",
        ),
        (
            lambda payload: payload["searches"][0].update({"alerts": [{"kind": "below", "amount": 1300}]}),
            "searches[0].alerts:",
        ),
        (
            lambda payload: payload.update(
                {
                    "tracked": [
                        {
                            "check_in": "nope",
                            "check_out": "2026-12-21",
                            "adults": 2,
                            "added": "2026-11-09",
                        }
                    ]
                }
            ),
            "tracked[0].check_in:",
        ),
    ],
)
def test_invalid_config_names_the_json_path(tmp_path, mutate, fragment):
    payload = {"version": 2, "searches": [_search()], "tracked": []}
    mutate(payload)
    with pytest.raises(ConfigError) as caught:
        load_watch(_write(tmp_path, payload))
    assert fragment in str(caught.value)


def test_version_1_and_a_leftover_alerts_list_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="config.json is version 1; this scanner reads version 2"):
        load_watch(_write(tmp_path, {"version": 1, "searches": [_search()], "tracked": []}))
    with pytest.raises(ConfigError, match=r"searches\[0\].alerts: removed; use alert"):
        load_watch(
            _write(
                tmp_path,
                {
                    "version": 2,
                    "searches": [_search(alerts=[{"kind": "below", "amount": 1200}])],
                    "tracked": [],
                },
            )
        )


def test_alert_parses_in_whole_pln_and_fills_defaults(tmp_path):
    path = _write(
        tmp_path,
        {
            "version": 2,
            "searches": [
                _search(id="full", alert={"under": 1200, "again_cheaper_by": 0, "require": {"recent_low": True, "discounted": True, "drop_pct": 10}}),
                _search(id="plain", alert={"under": 900}),
                _search(id="quiet", alert=None),
                _search(id="omitted"),
            ],
            "tracked": [],
        },
    )
    full, plain, quiet, omitted = load_watch(path).searches
    assert full.alert is not None
    assert full.alert.under == 120_000
    assert full.alert.again_cheaper_by == 0
    assert full.alert.recent_low is True
    assert full.alert.discounted is True
    assert full.alert.drop_pct == 10
    assert plain.alert is not None
    assert plain.alert.under == 90_000
    assert plain.alert.again_cheaper_by == 0
    assert plain.alert.recent_low is False
    assert plain.alert.discounted is False
    assert plain.alert.drop_pct is None
    assert quiet.alert is None
    assert omitted.alert is None


def test_derived_names_omit_years_when_the_end_is_within_a_year_of_the_scan():
    scanned = date(2026, 11, 9)
    assert derived_window_name(date(2026, 12, 1), date(2026, 12, 8), scanned) == "1 Dec – 8 Dec"
    assert derived_window_name(date(2026, 12, 28), date(2027, 1, 3), scanned) == "28 Dec – 3 Jan"
    assert derived_window_name(date(2027, 3, 1), date(2027, 3, 8), scanned) == "1 Mar – 8 Mar"


def test_derived_names_show_years_only_when_the_end_is_more_than_365_days_after_the_scan():
    scanned = date(2026, 11, 9)
    assert derived_window_name(date(2026, 12, 1), date(2027, 3, 1), scanned) == "1 Dec – 1 Mar"
    assert derived_window_name(date(2026, 11, 20), date(2026, 11, 30), scanned) == "20 Nov – 30 Nov"
    assert derived_window_name(date(2026, 12, 1), date(2027, 12, 1), scanned) == "1 Dec 2026 – 1 Dec 2027"


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
