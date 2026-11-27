from stay_watch.alerts import evaluate, qualifies
from stay_watch.model import StayResult
from tests.support import make_alert, make_quote, make_search, stay


def result(price, **quote_overrides):
    quote = None if price is None else make_quote(price, **quote_overrides)
    return StayResult(stay("2026-12-04", "2026-12-06"), quote, 0 if quote is None else 1)


def test_a_stay_at_or_above_under_never_qualifies():
    juicy = dict(recent_lowest=10_000, original=9_000)
    at_limit = result(1_200, **juicy)
    above = result(1_201, **juicy)
    previous = 5_000
    gated = make_alert(under=1_200, recent_low=True, discounted=True, drop_pct=50)
    plain = make_alert(under=1_200)
    for alert in (gated, plain):
        assert not qualifies(alert, at_limit, previous)
        assert not qualifies(alert, above, previous)
        assert not qualifies(alert, result(None), previous)
    search = make_search(alert=gated)
    hits, sent = evaluate(search, [at_limit, above], {at_limit.stay.key: previous}, {})
    assert hits == []
    assert sent == {}


def test_require_filters_narrow_and_a_plain_limit_does_not():
    cheap = result(1_000)
    assert qualifies(make_alert(under=2_000), cheap, None)

    recent = make_alert(under=2_000, recent_low=True)
    assert not qualifies(recent, result(1_000, recent_lowest=None), None)
    assert not qualifies(recent, result(1_000, recent_lowest=1_000), None)
    assert qualifies(recent, result(1_000, recent_lowest=1_001), None)

    discounted = make_alert(under=2_000, discounted=True)
    assert not qualifies(discounted, result(1_000, original=None), None)
    assert qualifies(discounted, result(1_000, original=1_500), None)

    dropped = make_alert(under=200_000, drop_pct=10)
    assert not qualifies(dropped, result(90_000), None)
    assert not qualifies(dropped, result(90_000), 0)
    assert not qualifies(dropped, result(90_001), 100_000)
    assert not qualifies(dropped, result(100_000), 100_000)
    assert qualifies(dropped, result(90_000), 100_000)


def test_again_cheaper_by_remembers_the_last_emailed_price():
    search = make_search(alert=make_alert(under=2_000, again_cheaper_by=50))
    key = "2026-12-04_2026-12-06"
    sent: dict = {}

    def step(price, remembered):
        nonlocal sent
        hits, sent = evaluate(search, [result(price)], {key: None}, remembered)
        return hits

    first = step(1_100, sent)
    assert len(first) == 1
    assert first[0].last_emailed is None
    assert sent[search.id][key] == 1_100

    assert step(1_080, sent) == []
    assert sent[search.id][key] == 1_100

    cheaper = step(1_050, sent)
    assert len(cheaper) == 1
    assert cheaper[0].last_emailed == 1_100
    assert sent[search.id][key] == 1_050

    assert step(1_150, sent) == []
    assert sent[search.id][key] == 1_050
    assert step(1_060, sent) == []
    assert sent[search.id][key] == 1_050

    again = step(1_000, sent)
    assert len(again) == 1
    assert again[0].last_emailed == 1_050
    assert sent[search.id][key] == 1_000


def test_margin_zero_stays_silent_at_the_same_price_and_emails_one_grosz_lower():
    search = make_search(alert=make_alert(under=2_000, again_cheaper_by=0))
    key = "2026-12-04_2026-12-06"
    original: dict = {}
    _first, sent = evaluate(search, [result(1_100)], {key: None}, original)
    assert original == {}
    same, sent = evaluate(search, [result(1_100)], {key: None}, sent)
    assert same == []
    lower, sent = evaluate(search, [result(1_099)], {key: None}, sent)
    assert len(lower) == 1
    assert lower[0].last_emailed == 1_100
    assert sent[search.id][key] == 1_099


def test_a_search_without_an_alert_sends_nothing():
    search = make_search(alert=None)
    hits, sent = evaluate(search, [result(1)], {}, {"other": {"2026-12-04_2026-12-06": 5}})
    assert hits == []
    assert sent == {"other": {"2026-12-04_2026-12-06": 5}}
