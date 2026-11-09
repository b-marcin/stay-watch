from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import date, datetime

from stay_watch.alerts import evaluate
from stay_watch.config import Secrets, WatchConfig
from stay_watch.guard import PublicFilesError, check_tree
from stay_watch.model import Search, Stay, StayResult
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
from stay_watch.rank import cheapest, priced_results, rank_search, select_quotes
from stay_watch.render import describe, display_name, render_table
from stay_watch.store import (
    Store,
    build_latest,
    had_availability,
    history_group,
    last_recorded_price,
    update_history,
)
from stay_watch.tracked import evaluate_tracked

# A handful of empty stays is a real sell-out. A wide empty feed, after a run
# that still had prices, is treated as a broken feed.
EMPTY_FEED_MIN = 5


class RecordedFailure(Exception):
    """Failure count is already saved. The caller must not count it again."""


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
    search_jobs, tracked_jobs = _fetch_plan(config, today)
    fetched: dict[tuple[str, int, tuple[str, ...]], list] = {}
    any_quote = False
    for stay, adults, codes in [*search_jobs, *tracked_jobs]:
        quotes = client.quotes(stay, adults, codes)
        fetched[(stay.key, adults, codes)] = quotes
        if quotes:
            any_quote = True

    history = store.load_history()
    state = store.load_state()
    old_failures = int(state.get("consecutive_failures") or 0)
    had_prices = bool(state.get("seen_prices")) or had_availability(previous_latest) or any_quote
    # Tracked trips count toward a broken feed only when the searches fetched nothing.
    feed_count = len(search_jobs) if search_jobs else len(tracked_jobs)
    suspected = feed_count >= EMPTY_FEED_MIN and not any_quote and had_prices
    sent = state.get("alerts_sent") or {}
    ranked_by_search: list[tuple[Search, list]] = []
    hits = []
    observations: dict[str, dict] = {}

    def observe(group: str, result: StayResult) -> None:
        rows = observations.setdefault(group, {})
        if result.stay.key in rows:
            return
        if result.best is None:
            rows[result.stay.key] = (result.stay.key, result.stay.check_in, None, None, None)
            return
        offer_name, room_name, _meal, _cancel = describe(result, catalog)
        rows[result.stay.key] = (result.stay.key, result.stay.check_in, result.best.price, offer_name, room_name)

    for search in config.searches:
        stays = candidate_stays(search, today)
        quotes_by_key = {stay.key: fetched[(stay.key, search.adults, search.codes)] for stay in stays}
        ranked = rank_search(search, stays, quotes_by_key, catalog)
        group = history_group(search.adults, search.codes, search.offer_filter, search.room_filter)
        bucket = history.get(group) if isinstance(history.get(group), dict) else {}
        previous = {result.stay.key: last_recorded_price(bucket.get(result.stay.key) or []) for result in ranked}
        search_hits, sent = evaluate(search, ranked, previous, sent)
        hits.extend(search_hits)
        ranked_by_search.append((search, ranked))
        for result in ranked:
            observe(group, result)

    active = [trip for trip in config.tracked if trip.stay.check_in >= today]
    tracked_ranked = []
    for trip in active:
        quotes = fetched[(trip.stay.key, trip.adults, trip.codes)]
        matched = select_quotes(quotes, catalog, (), ())
        result = StayResult(trip.stay, cheapest(matched), len({quote.offer_id for quote in matched}))
        tracked_ranked.append((trip, result))
        observe(history_group(trip.adults, trip.codes), result)

    tracked_before = state.get("tracked") if isinstance(state.get("tracked"), dict) else {}
    new_tracked: dict = {}
    notices = []
    for trip, result in tracked_ranked:
        notice, entry = evaluate_tracked(trip, result, tracked_before.get(trip.id))
        new_tracked[trip.id] = entry
        if notice:
            notices.append(notice)

    latest = build_latest(ranked_by_search, catalog, history, now, tracked_ranked, new_tracked)
    new_history = history
    for group, rows in observations.items():
        new_history = update_history(
            new_history,
            group,
            list(rows.values()),
            now.isoformat(timespec="seconds"),
            now.date(),
        )

    store.save_latest(latest)
    store.save_history(new_history)
    findings = check_tree(store.docs, secrets.terms)
    if findings:
        raise PublicFilesError(findings[0])

    shown = {search.id: replace(search, name=display_name(search, today)) for search in config.searches}

    def persist() -> None:
        state["alerts_sent"] = sent
        state["tracked"] = new_tracked
        store.save_state(state, today=today, searches=config.searches, tracked=config.tracked)

    if suspected:
        count = old_failures + 1
        state["seen_prices"] = True
        state["alerts_sent"] = sent
        state["consecutive_failures"] = count
        store.save_state(state, today=today, searches=config.searches, tracked=config.tracked)
        if count == 3:
            try:
                deliver(secrets, build_failure_message(secrets, count, "every stay returned zero proposals"))
            except MailError:
                print("could not send the failure email")
        raise RecordedFailure("every stay returned zero proposals")
    if hits or notices:
        deliver(secrets, build_alert_message(secrets, hits, shown, catalog, notices))
        persist()
    if _digest_due(config, state, now) and (config.searches or tracked_ranked):
        sections = [(shown[search.id], priced_results(ranked, search.top)) for search, ranked in ranked_by_search]
        deliver(secrets, build_digest_message(secrets, sections, catalog, tracked_ranked))
        state["last_digest_date"] = now.date().isoformat()
        persist()
    if old_failures >= 3:
        deliver(secrets, build_recovered_message(secrets))

    state["seen_prices"] = had_prices
    state["consecutive_failures"] = 0
    persist()
    stay_count = sum(len(ranked) for _, ranked in ranked_by_search)
    skipped = getattr(client, "skipped_proposals", 0)
    extra = f", skipped {skipped} proposals" if skipped else ""
    tracked_note = f", {len(tracked_ranked)} tracked" if tracked_ranked else ""
    print(f"scan ok: {len(config.searches)} searches, {stay_count} stays, {len(hits)} alerts{tracked_note}{extra}")


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


def _fetch_plan(config: WatchConfig, today: date | None) -> tuple[list, list]:
    seen: set[tuple[str, int, tuple[str, ...]]] = set()
    search_jobs: list[tuple[Stay, int, tuple[str, ...]]] = []
    tracked_jobs: list[tuple[Stay, int, tuple[str, ...]]] = []

    def take(stay: Stay, adults: int, codes: tuple[str, ...], dest: list) -> None:
        key = (stay.key, adults, codes)
        if key in seen:
            return
        seen.add(key)
        dest.append((stay, adults, codes))

    for search in config.searches:
        for stay in candidate_stays(search, today):
            take(stay, search.adults, search.codes, search_jobs)
    for trip in config.tracked:
        if today is not None and trip.stay.check_in < today:
            continue
        take(trip.stay, trip.adults, trip.codes, tracked_jobs)
    return search_jobs, tracked_jobs


def _digest_due(config: WatchConfig, state: dict, now: datetime) -> bool:
    if config.digest_weekday is None:
        return False
    if now.weekday() != config.digest_weekday:
        return False
    return state.get("last_digest_date") != now.date().isoformat()
