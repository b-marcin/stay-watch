from datetime import date

from stay_watch.config import Secrets
from stay_watch.model import AlertHit, Catalog, OfferInfo, StayResult
from stay_watch.notify import booking_url, build_alert_message
from tests.support import make_alert, make_quote, make_search, stay


def test_alert_email_includes_the_deep_link_and_the_price():
    found = stay("2026-12-04", "2026-12-06")
    search = make_search(
        id="winter-weekends",
        name="Winter weekends",
        alert=make_alert(under=130_000),
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 8),
    )
    quote = make_quote(127_000, found, offer_id=5, room_id=8, profile_id=3, rooms_left=13)
    catalog = Catalog(
        {5: OfferInfo("Oferta bezzwrotna", "breakfast", "non-refundable", ((3, "non-refundable"),))},
        {8: "Standard Room"},
    )
    secrets = Secrets(
        slug="examplehotel",
        terms=("Example Hotel", "examplehotel"),
        smtp_user=None,
        smtp_password=None,
        alert_to="guest@example.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        dry_run=True,
    )
    from stay_watch.alerts import evaluate

    hits, _sent = evaluate(search, [StayResult(found, quote, 1)], {found.key: None}, {})
    message = build_alert_message(secrets, hits, {search.id: search}, catalog)
    plain = message.get_body(preferencelist=("plain",)).get_content()
    assert message["Subject"] == "1 stay under 1 300 zł: Fri 4 Dec → Sun 6 Dec 1 270 zł"
    assert "1 270 zł" in plain
    assert "under 1 300 zł" in plain
    assert "last emailed" not in plain
    assert "was " not in plain
    assert booking_url("examplehotel", found, 2) in plain
    assert "https://booking.profitroom.com/pl/examplehotel/pricelist/rooms?check-in=2026-12-04&check-out=2026-12-06&r1_adults=2&currency=PLN" in plain
    assert "Oferta bezzwrotna" in plain
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "examplehotel" in html
    assert "under 1 300 zł" in html


def test_alert_email_names_the_last_emailed_price():
    found = stay("2026-12-04", "2026-12-06")
    search = make_search(name="Winter weekends", alert=make_alert(under=130_000, again_cheaper_by=5_000))
    quote = make_quote(100_000, found)
    hit = AlertHit(search.id, StayResult(found, quote, 1), 140_000, 110_000)
    secrets = Secrets(
        slug="examplehotel",
        terms=(),
        smtp_user=None,
        smtp_password=None,
        alert_to="guest@example.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        dry_run=True,
    )
    message = build_alert_message(secrets, [hit], {search.id: search}, Catalog({}, {}))
    plain = message.get_body(preferencelist=("plain",)).get_content()
    assert "under 1 300 zł" in plain
    assert "last emailed 1 100 zł" in plain
    assert "was " not in plain
