from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from stay_watch.clock import now_warsaw
from stay_watch.config import ConfigError, Secrets, load_secrets, load_watch, parse_weekday_token
from stay_watch.guard import check_public
from stay_watch.model import Search
from stay_watch.profitroom import FixtureClient, ProfitroomClient
from stay_watch.service import execute_scan, execute_search, public_error, record_failure
from stay_watch.store import Store


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(prog="stay_watch")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="fetch prices, update the dashboard, send alerts")
    scan.add_argument("--fixtures", type=Path)
    scan.add_argument("--config", type=Path, default=Path("watch.toml"))
    scan.add_argument("--docs", type=Path, default=Path("docs"))

    search = sub.add_parser("search", help="print an ad hoc search without saving it")
    search.add_argument("--from", dest="start", required=True)
    search.add_argument("--to", dest="end", required=True)
    search.add_argument("--nights", type=int, default=2)
    search.add_argument("--must-include", default="sat")
    search.add_argument("--adults", type=int, default=2)
    search.add_argument("--top", default="3")
    search.add_argument("--codes", default="")
    search.add_argument("--email", action="store_true")
    search.add_argument("--fixtures", type=Path)

    check = sub.add_parser("check-public", help="fail if public files name the hotel")
    check.add_argument("--root", type=Path, default=Path("."))

    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            return _scan(args)
        if args.command == "search":
            return _search(args)
        return _check(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2


def _scan(args: argparse.Namespace) -> int:
    secrets = load_secrets()
    config = load_watch(args.config)
    client = _client(args.fixtures, secrets)
    store = Store(args.docs, secrets.terms)
    try:
        execute_scan(config, secrets, store, client, now_warsaw())
    except Exception as exc:
        record_failure(store, secrets, exc)
        print(f"scan failed: {public_error(exc, secrets.slug)}", file=sys.stderr)
        return 1
    return 0


def _search(args: argparse.Namespace) -> int:
    secrets = load_secrets()
    start = _cli_date(args.start, "--from")
    end = _cli_date(args.end, "--to")
    if start >= end:
        raise ConfigError("--to must be after --from")
    if args.nights < 1:
        raise ConfigError("--nights must be 1 or more")
    if args.adults < 1:
        raise ConfigError("--adults must be 1 or more")
    search = Search(
        id="adhoc",
        name="Ad hoc search",
        window_start=start,
        window_end=end,
        nights=args.nights,
        must_include=_cli_weekdays(args.must_include),
        adults=args.adults,
        top=_cli_top(args.top),
        codes=_cli_codes(args.codes),
        offer_filter=(),
        room_filter=(),
        alerts=(),
    )
    client = _client(args.fixtures, secrets)
    try:
        execute_search(search, secrets, client, args.email)
    except Exception as exc:
        print(f"search failed: {public_error(exc, secrets.slug)}", file=sys.stderr)
        return 1
    return 0


def _check(args: argparse.Namespace) -> int:
    secrets = load_secrets()
    findings = check_public(args.root.resolve(), secrets.terms)
    if findings:
        print("\n".join(findings))
        return 1
    print("public files are clear")
    return 0


def _client(fixtures: Path | None, secrets: Secrets) -> ProfitroomClient | FixtureClient:
    if fixtures is not None:
        if not fixtures.is_dir():
            raise ConfigError(f"fixtures directory not found: {fixtures}")
        return FixtureClient(fixtures)
    return ProfitroomClient(secrets.slug)


def _cli_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"invalid date {value!r} for {label}") from exc


def _cli_weekdays(value: str) -> frozenset[int]:
    raw = value.strip()
    if raw.lower() in {"", "all", "any", "-"}:
        return frozenset()
    return frozenset(parse_weekday_token(part) for part in raw.split(",") if part.strip())


def _cli_top(value: str) -> int | None:
    if value.strip().lower() == "all":
        return None
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigError("--top must be a number or all") from exc
    if number < 0:
        raise ConfigError("--top must be 0 or more")
    return number


def _cli_codes(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
