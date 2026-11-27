from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from stay_watch.config import WatchConfig, load_secrets
from stay_watch.model import Catalog, DropPct, OfferInfo, TrackedTrip, tracked_id
from stay_watch.notify import MailError
from stay_watch.service import RecordedFailure, execute_scan
from stay_watch.store import Store, StoreError, history_group
from tests.support import make_quote, make_search, stay


NOW = datetime(2026, 11, 9, 10, 0, tzinfo=timezone(timedelta(hours=1)))
FOUND = stay("2026-12-04", "2026-12-06")
SEARCH = make_search(window_start=FOUND.check_in, window_end=FOUND.check_out, alerts=(DropPct(10),))
SECRETS = load_secrets(
    {
        "HOTEL_SLUG": "examplehotel",
        "HOTEL_NAME_TERMS": "Example Hotel",
        "DRY_RUN": "1",
        "ALERT_TO": "guest@example.com",
    }
)


class Quotes:
    def __init__(self, price):
        self.price = price

    def catalog(self):
        return Catalog({1: OfferInfo("Rate", "breakfast", "non-refundable")}, {1: "Double"})

    def quotes(self, found, adults, codes):
        price = self.price.get(found.key) if isinstance(self.price, dict) else self.price
        return [] if price is None else [make_quote(price, found)]


class Mailbox:
    def __init__(self):
        self.messages = []
        self.fail = False
        self.fail_digest = False

    def deliver(self, secrets, message):
        if self.fail or (self.fail_digest and str(message["Subject"]).startswith("Weekly digest:")):
            raise MailError("simulated SMTP failure")
        self.messages.append(message)


@pytest.fixture
def mailbox(monkeypatch):
    mailbox = Mailbox()
    monkeypatch.setattr("stay_watch.service.deliver", mailbox.deliver)
    return mailbox


def scan(tmp_path, price, config=None, now=NOW):
    config = config if config is not None else WatchConfig((SEARCH,), (), None)
    execute_scan(config, SECRETS, Store(tmp_path / "docs", SECRETS.terms), Quotes(price), now)


def saved(tmp_path):
    return Store(tmp_path / "docs", SECRETS.terms)


def plain(message):
    return message.get_body(preferencelist=("plain",)).get_content()


def trip():
    return TrackedTrip(
        tracked_id(FOUND.check_in, FOUND.check_out, 2, ()), FOUND, 2, (), "any", None, NOW.date()
    )


def test_failed_percentage_drop_retries_after_reloading_state(tmp_path, mailbox):
    scan(tmp_path, 100_000)
    mailbox.fail = True
    with pytest.raises(MailError):
        scan(tmp_path, 80_000, now=NOW + timedelta(hours=3))

    assert mailbox.messages == []
    rows = saved(tmp_path).load_history()[history_group(2, ())][FOUND.key]
    assert [row[1] for row in rows] == [100_000, 80_000]

    mailbox.fail = False
    scan(tmp_path, 80_000, now=NOW + timedelta(hours=6))
    scan(tmp_path, 80_000, now=NOW + timedelta(hours=9))

    assert len(mailbox.messages) == 1
    assert "800 zł" in plain(mailbox.messages[0])
    assert "was 1 000 zł" in plain(mailbox.messages[0])
    rows = saved(tmp_path).load_history()[history_group(2, ())][FOUND.key]
    assert [row[1] for row in rows] == [100_000, 80_000]


def test_failed_drop_retries_with_the_current_qualifying_price(tmp_path, mailbox):
    scan(tmp_path, 100_000)
    mailbox.fail = True
    with pytest.raises(MailError):
        scan(tmp_path, 80_000)

    mailbox.fail = False
    scan(tmp_path, 70_000)

    assert len(mailbox.messages) == 1
    assert "700 zł" in plain(mailbox.messages[0])
    assert "800 zł" not in plain(mailbox.messages[0])
    assert "was 1 000 zł" in plain(mailbox.messages[0])


def test_a_failed_alert_does_not_accumulate_quiet_stays_drops(tmp_path, mailbox):
    other = stay("2026-12-05", "2026-12-07")
    config = WatchConfig((replace(SEARCH, window_end=other.check_out),), (), None)
    scan(tmp_path, 100_000, config)
    mailbox.fail = True
    with pytest.raises(MailError):
        scan(tmp_path, {FOUND.key: 80_000, other.key: 95_000}, config)

    mailbox.fail = False
    scan(tmp_path, {FOUND.key: 80_000, other.key: 90_000}, config)

    assert len(mailbox.messages) == 1
    assert "Fri 4 Dec" in plain(mailbox.messages[0])
    assert "Sat 5 Dec" not in plain(mailbox.messages[0])


def test_state_write_failure_leaves_history_and_delivery_untouched(tmp_path, mailbox, monkeypatch):
    scan(tmp_path, 100_000)
    original = Store.save_state

    def fail_write(*args, **kwargs):
        raise StoreError("simulated state write failure")

    monkeypatch.setattr(Store, "save_state", fail_write)
    with pytest.raises(StoreError):
        scan(tmp_path, 80_000)

    assert mailbox.messages == []
    rows = saved(tmp_path).load_history()[history_group(2, ())][FOUND.key]
    assert [row[1] for row in rows] == [100_000]

    monkeypatch.setattr(Store, "save_state", original)
    scan(tmp_path, 80_000)
    assert len(mailbox.messages) == 1


def test_failed_initial_tracked_email_does_not_publish_delivery(tmp_path, mailbox):
    tracked = trip()
    config = WatchConfig((), (tracked,), None)
    mailbox.fail = True
    with pytest.raises(MailError):
        scan(tmp_path, 100_000, config)

    row = saved(tmp_path).load_latest()["tracked"][0]
    assert row["price"] == 100_000
    assert row["first_price"] == 100_000
    assert row["last_notified_price"] is None
    assert tracked.id not in saved(tmp_path).load_state()["tracked"]

    mailbox.fail = False
    scan(tmp_path, 100_000, config)
    scan(tmp_path, 100_000, config)

    assert len(mailbox.messages) == 1
    assert saved(tmp_path).load_latest()["tracked"][0]["last_notified_price"] == 100_000
    assert saved(tmp_path).load_state()["tracked"][tracked.id]["last_notified_price"] == 100_000


def test_failed_tracked_change_keeps_the_last_delivered_price(tmp_path, mailbox):
    config = WatchConfig((), (trip(),), None)
    scan(tmp_path, 100_000, config)
    mailbox.fail = True
    with pytest.raises(MailError):
        scan(tmp_path, 80_000, config)

    row = saved(tmp_path).load_latest()["tracked"][0]
    assert row["price"] == 80_000
    assert row["first_price"] == 100_000
    assert row["last_notified_price"] == 100_000

    mailbox.fail = False
    scan(tmp_path, 80_000, config)
    scan(tmp_path, 80_000, config)

    assert len(mailbox.messages) == 2
    assert saved(tmp_path).load_latest()["tracked"][0]["last_notified_price"] == 80_000


def test_successful_drop_survives_a_later_digest_failure(tmp_path, mailbox):
    config = WatchConfig((SEARCH,), (), NOW.weekday())
    scan(tmp_path, 100_000, config, NOW - timedelta(days=1))
    mailbox.fail_digest = True
    with pytest.raises(MailError):
        scan(tmp_path, 80_000, config)

    assert len(mailbox.messages) == 1
    assert saved(tmp_path).load_state()["last_digest_date"] is None

    mailbox.fail_digest = False
    scan(tmp_path, 80_000, config)

    assert len(mailbox.messages) == 2
    assert str(mailbox.messages[1]["Subject"]).startswith("Weekly digest:")


def test_successful_tracked_notice_survives_a_later_digest_failure(tmp_path, mailbox):
    tracked = trip()
    config = WatchConfig((), (tracked,), NOW.weekday())
    mailbox.fail_digest = True
    with pytest.raises(MailError):
        scan(tmp_path, 100_000, config)

    assert len(mailbox.messages) == 1
    assert saved(tmp_path).load_latest()["tracked"][0]["last_notified_price"] == 100_000
    assert saved(tmp_path).load_state()["tracked"][tracked.id]["last_notified_price"] == 100_000
    assert saved(tmp_path).load_state()["last_digest_date"] is None

    mailbox.fail_digest = False
    scan(tmp_path, 100_000, config)

    assert len(mailbox.messages) == 2
    assert str(mailbox.messages[1]["Subject"]).startswith("Weekly digest:")


def test_quiet_evaluation_survives_a_later_digest_failure(tmp_path, mailbox):
    config = WatchConfig((SEARCH,), (), NOW.weekday())
    mailbox.fail_digest = True
    with pytest.raises(MailError):
        scan(tmp_path, 100_000, config)

    mailbox.fail_digest = False
    scan(tmp_path, 80_000, config)

    assert len(mailbox.messages) == 2
    assert "800 zł" in plain(mailbox.messages[0])
    assert str(mailbox.messages[1]["Subject"]).startswith("Weekly digest:")


@pytest.mark.parametrize(
    "change",
    [
        {"alerts": (DropPct(20),)},
        {"adults": 3},
        {"codes": ("PROMO",)},
        {"offer_filter": ("Rate",)},
        {"room_filter": ("Double",)},
    ],
)
def test_changed_search_does_not_retry_an_old_comparison(tmp_path, mailbox, change):
    scan(tmp_path, 100_000)
    mailbox.fail = True
    with pytest.raises(MailError):
        scan(tmp_path, 80_000)

    mailbox.fail = False
    config = WatchConfig((replace(SEARCH, **change),), (), None)
    scan(tmp_path, 80_000, config)
    assert mailbox.messages == []

    scan(tmp_path, 60_000, config)
    assert len(mailbox.messages) == 1
    assert "600 zł" in plain(mailbox.messages[0])


def test_removed_search_does_not_retry_its_old_comparison(tmp_path, mailbox):
    scan(tmp_path, 100_000)
    mailbox.fail = True
    with pytest.raises(MailError):
        scan(tmp_path, 80_000)

    mailbox.fail = False
    scan(tmp_path, 80_000, WatchConfig((), (), None))
    scan(tmp_path, 80_000)
    assert mailbox.messages == []


def test_suspected_empty_feed_preserves_a_failed_drop_comparison(tmp_path, mailbox):
    config = WatchConfig((make_search(alerts=(DropPct(10),)),), (), None)
    scan(tmp_path, 100_000, config)
    mailbox.fail = True
    with pytest.raises(MailError):
        scan(tmp_path, 80_000, config)

    mailbox.fail = False
    with pytest.raises(RecordedFailure):
        scan(tmp_path, None, config)
    assert mailbox.messages == []
    assert saved(tmp_path).load_state()["consecutive_failures"] == 1

    scan(tmp_path, 80_000, config)
    assert len(mailbox.messages) == 1
    assert saved(tmp_path).load_state()["consecutive_failures"] == 0


def test_saved_comparison_state_contains_no_hotel_text_or_mail(tmp_path, mailbox):
    search = replace(SEARCH, codes=("examplehotel",))
    scan(tmp_path, 100_000, WatchConfig((search,), (), None))

    state = (tmp_path / "docs" / "data" / "state.json").read_text(encoding="utf-8").casefold()
    assert "examplehotel" not in state
    assert "example hotel" not in state
    assert "profitroom" not in state
    assert "guest@example.com" not in state
    assert "https://" not in state
