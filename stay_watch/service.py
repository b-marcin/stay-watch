from __future__ import annotations

import os
import re
from datetime import date, datetime

from stay_watch.alerts import evaluate
from stay_watch.config import Secrets, WatchConfig
from stay_watch.guard import PublicFilesError, check_tree
from stay_watch.model import Search, Stay
from stay_watch.notify import (
    MailError,
    build_alert_message,
    build_digest_message,
    build_failure_message,
    build_recovered_message,
    build_search_message,
    deliver,
)
from stay_watch.plan import candidate_stays
from stay_watch.profitroom import FixtureClient, ProfitroomClient

# A handful of empty stays is a real sell-out. A wide empty feed, after a run
# that still had prices, is treated as a broken feed.
EMPTY_FEED_MIN = 5


class RecordedFailure(Exception):
    """Failure count is already saved. The caller must not count it again."""
from stay_watch.rank import priced_results, rank_search
from stay_watch.render import describe, render_table
from stay_watch.store import (
    Store,
    build_latest,
    had_availability,
    history_group,
    last_recorded_price,
    update_history,
)


def execute_scan(
    config: WatchConfig,
    secrets: Secrets,
    store: Store,
    client: ProfitroomClient | FixtureClient,
    now: datetime,
) -> None:
    today = now.date()
    previous_latest = store.load_latest()
    catalog = client.catalog()
    jobs = _jobs(config.searches, today)
    fetched: dict[tuple[str, int, tuple[str, ...]], list] = {}
    any_quote = False
    for stay, adults, codes in jobs:
        quotes = client.quotes(stay, adults, codes)
        fetched[(stay.key, adults, codes)] = quotes
        if quotes:
            any_quote = True

    history = store.load_history()
    state = store.load_state()
    old_failures = int(state.get("consecutive_failures") or 0)
    had_prices = bool(state.get("seen_prices")) or had_availability(previous_latest) or any_quote
    suspected = len(jobs) >= EMPTY_FEED_MIN and not any_quote and had_prices
    sent = state.get("alerts_sent") or {}
    ranked_by_search: list[tuple[Search, list]] = []
    hits = []
    observations: dict[str, list] = {}
    for search in config.searches:
        stays = candidate_stays(search, today)
        quotes_by_key = {
            stay.key: fetched[(stay.key, search.adults, search.codes)]
            for stay in stays
        }
        ranked = rank_search(search, stays, quotes_by_key, catalog)
        group = history_group(search.adults, search.codes, search.offer_filter, search.room_filter)
        bucket = history.get(group) if isinstance(history.get(group), dict) else {}
        previous = {
            result.stay.key: last_recorded_price(bucket.get(result.stay.key) or [])
            for result in ranked
        }
        search_hits, sent = evaluate(search, ranked, previous, sent)
        hits.extend(search_hits)
        ranked_by_search.append((search, ranked))
        rows = observations.setdefault(group, [])
        for result in ranked:
            if result.best is None:
                rows.append((result.stay.key, result.stay.check_in, None, None, None))
            else:
                offer_name, room_name, _meal, _cancel = describe(result, catalog)
                rows.append(
                    (
                        result.stay.key,
                        result.stay.check_in,
                        result.best.price,
                        offer_name,
                        room_name,
                    )
                )

    latest = build_latest(ranked_by_search, catalog, history, now)
    new_history = history
    for group, rows in observations.items():
        new_history = update_history(new_history, group, rows, now.isoformat(timespec="seconds"), now.date())

    store.save_latest(latest)
    store.save_history(new_history)
    findings = check_tree(store.docs, secrets.terms)
    if findings:
        raise PublicFilesError(findings[0])

    searches = {search.id: search for search in config.searches}
    if suspected:
        count = old_failures + 1
        state["seen_prices"] = True
        state["alerts_sent"] = sent
        state["consecutive_failures"] = count
        store.save_state(state, today=today, searches=config.searches)
        if count == 3:
            try:
                deliver(secrets, build_failure_message(secrets, count, "every stay returned zero proposals"))
            except MailError:
                print("could not send the failure email")
        raise RecordedFailure("every stay returned zero proposals")
    if hits:
        deliver(secrets, build_alert_message(secrets, hits, searches, catalog))
        state["alerts_sent"] = sent
        store.save_state(state, today=today, searches=config.searches)
    if _digest_due(config, state, now) and config.searches:
        sections = [(search, priced_results(ranked, search.top)) for search, ranked in ranked_by_search]
        deliver(secrets, build_digest_message(secrets, sections, catalog))
        state["last_digest_date"] = now.date().isoformat()
        store.save_state(state, today=today, searches=config.searches)
    if old_failures >= 3:
        deliver(secrets, build_recovered_message(secrets))

    state["alerts_sent"] = sent
    state["seen_prices"] = had_prices
    state["consecutive_failures"] = 0
    store.save_state(state, today=today, searches=config.searches)
    stay_count = sum(len(ranked) for _, ranked in ranked_by_search)
    skipped = getattr(client, "skipped_proposals", 0)
    extra = f", skipped {skipped} proposals" if skipped else ""
    print(f"scan ok: {len(config.searches)} searches, {stay_count} stays, {len(hits)} alerts{extra}")


def execute_search(
    search: Search,
    secrets: Secrets,
    client: ProfitroomClient | FixtureClient,
    email: bool,
    today: date | None = None,
) -> None:
    catalog = client.catalog()
    stays = candidate_stays(search, today)
    quotes_by_key = {stay.key: client.quotes(stay, search.adults, search.codes) for stay in stays}
    ranked = rank_search(search, stays, quotes_by_key, catalog)
    # Actions logs are public. Offer names contain the hotel name, and GitHub
    # only masks a secret as a whole value, so the table must not be printed.
    if os.environ.get("GITHUB_ACTIONS"):
        if email:
            deliver(secrets, build_search_message(secrets, search, ranked, catalog))
        mailed = ", emailed" if email else ""
        print(f"search ok: {len(ranked)} stays{mailed}")
        return
    print(render_table(ranked, catalog, search.top), end="")
    if email:
        deliver(secrets, build_search_message(secrets, search, ranked, catalog))


def record_failure(
    store: Store,
    secrets: Secrets,
    exc: BaseException,
    *,
    today: date | None = None,
    searches: tuple[Search, ...] | None = None,
) -> None:
    try:
        state = store.load_state()
    except Exception:
        from stay_watch.store import default_state

        state = default_state()
    count = int(state.get("consecutive_failures") or 0) + 1
    state["consecutive_failures"] = count
    try:
        store.save_state(state, today=today, searches=searches)
    except Exception:
        pass
    if count == 3:
        try:
            deliver(secrets, build_failure_message(secrets, count, public_error(exc, secrets.slug)))
        except MailError:
            print("could not send the failure email")


def public_error(exc: BaseException, slug: str) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = re.sub(r"https?://\S+", "[url]", text)
    if slug:
        text = re.sub(re.escape(slug), "[hotel]", text, flags=re.IGNORECASE)
    return text[:500]


def _jobs(searches: tuple[Search, ...], today: date | None = None) -> list[tuple[Stay, int, tuple[str, ...]]]:
    jobs: list[tuple[Stay, int, tuple[str, ...]]] = []
    seen: set[tuple[str, int, tuple[str, ...]]] = set()
    for search in searches:
        for stay in candidate_stays(search, today):
            key = (stay.key, search.adults, search.codes)
            if key in seen:
                continue
            seen.add(key)
            jobs.append((stay, search.adults, search.codes))
    return jobs


def _digest_due(config: WatchConfig, state: dict, now: datetime) -> bool:
    if config.digest_weekday is None:
        return False
    if now.weekday() != config.digest_weekday:
        return False
    return state.get("last_digest_date") != now.date().isoformat()
