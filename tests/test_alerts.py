from stay_watch.alerts import evaluate, rule_matches
from stay_watch.model import (
    Below,
    BelowRecentLowest,
    Discounted,
    DropPct,
    PerNightBelow,
    StayResult,
)
from tests.support import make_quote, make_search, stay


def result(price, **quote_overrides):
    quote = None if price is None else make_quote(price, **quote_overrides)
    return StayResult(stay("2026-12-04", "2026-12-06"), quote, 0 if quote is None else 1)


def test_each_rule_respects_its_boundary():
    priced = result(130_000, recent_lowest=130_000, original=None)
    under = result(129_999, recent_lowest=130_000, original=150_000)
    over = result(130_001, recent_lowest=100_000, original=None)
    assert rule_matches(Below(130_000), under, None)
    assert not rule_matches(Below(130_000), priced, None)
    assert not rule_matches(Below(130_000), over, None)

    assert rule_matches(PerNightBelow(65_000), under, None)
    assert not rule_matches(PerNightBelow(65_000), priced, None)

    assert rule_matches(DropPct(10), result(90_000), 100_000)
    assert not rule_matches(DropPct(10), result(90_001), 100_000)
    assert not rule_matches(DropPct(10), result(100_000), 100_000)
    assert not rule_matches(DropPct(10), result(90_000), None)

    assert rule_matches(BelowRecentLowest(), result(100, recent_lowest=101), None)
    assert not rule_matches(BelowRecentLowest(), result(101, recent_lowest=101), None)
    assert not rule_matches(BelowRecentLowest(), result(100, recent_lowest=None), None)

    assert rule_matches(Discounted(), result(100, original=120), None)
    assert not rule_matches(Discounted(), result(100, original=None), None)
    assert not rule_matches(Below(1), result(None), None)


def test_dedupe_sends_first_hit_lower_price_and_a_drop_below_the_last_sent_price():
    search = make_search(alerts=(Below(130_000),))
    key = "2026-12-04_2026-12-06"
    sent = {}

    first, sent = evaluate(search, [result(100_000)], {key: None}, sent)
    assert len(first) == 1
    assert sent[search.id]["below:130000"][key] == 100_000

    same, sent = evaluate(search, [result(100_000)], {key: 100_000}, sent)
    assert same == []

    lower, sent = evaluate(search, [result(90_000)], {key: 100_000}, sent)
    assert len(lower) == 1
    assert sent[search.id]["below:130000"][key] == 90_000

    risen, sent = evaluate(search, [result(120_000)], {key: 90_000}, sent)
    assert risen == []
    assert sent[search.id]["below:130000"][key] == 90_000

    dropped, sent = evaluate(search, [result(80_000)], {key: 120_000}, sent)
    assert len(dropped) == 1
    assert sent[search.id]["below:130000"][key] == 80_000
