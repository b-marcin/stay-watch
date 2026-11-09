from __future__ import annotations

import html
import smtplib
import ssl
import urllib.parse
from email.message import EmailMessage

from stay_watch.config import Secrets
from stay_watch.model import AlertHit, Catalog, Search, Stay, StayResult, TrackedTrip
from stay_watch.render import (
    alert_subject,
    describe,
    format_pln,
    format_stay,
    group_hits,
    render_table,
    rule_label,
    scan_subject,
    tracked_notice_plain,
    tracked_status_line,
)
from stay_watch.tracked import TrackedNotice


class MailError(Exception):
    pass


def booking_url(slug: str, stay: Stay, adults: int) -> str:
    safe_slug = urllib.parse.quote(slug, safe="")
    return (
        f"https://booking.profitroom.com/pl/{safe_slug}/pricelist/rooms"
        f"?check-in={stay.check_in.isoformat()}"
        f"&check-out={stay.check_out.isoformat()}"
        f"&r1_adults={adults}&currency=PLN"
    )


def deliver(secrets: Secrets, message: EmailMessage) -> None:
    if secrets.dry_run:
        print(_dry_text(message))
        return
    if not secrets.smtp_user or not secrets.smtp_password:
        raise MailError("SMTP_USER and SMTP_PASSWORD are required to send mail")
    if not secrets.alert_to:
        raise MailError("ALERT_TO or SMTP_USER is required to send mail")
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(secrets.smtp_host, secrets.smtp_port, context=context, timeout=30) as smtp:
            smtp.login(secrets.smtp_user, secrets.smtp_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError("could not send email") from exc


def build_alert_message(
    secrets: Secrets,
    hits: list[AlertHit],
    searches: dict[str, Search],
    catalog: Catalog,
    notices: list[TrackedNotice] | None = None,
) -> EmailMessage:
    notices = list(notices or [])
    groups = group_hits(hits) if hits else []
    plain_parts: list[str] = []
    html_parts: list[str] = []
    if notices:
        plain_parts.append("Tracked trips")
        html_parts.append("<h2>Tracked trips</h2>")
        for notice in notices:
            link = booking_url(secrets.slug, notice.result.stay, notice.trip.adults)
            plain = tracked_notice_plain(notice, catalog, link)
            plain_parts.append(plain)
            html_parts.append(_html_lines(plain))
    for group in groups:
        lead = group[0]
        search = searches[lead.search_id]
        result = lead.stay_result
        offer, room, meal, cancel = describe(result, catalog)
        reasons = ", ".join(rule_label(hit.rule) for hit in group)
        price = format_pln(result.best.price)
        each = format_pln(result.best.price // result.stay.nights)
        when = format_stay(result.stay)
        link = booking_url(secrets.slug, result.stay, search.adults)
        was = ""
        if lead.previous_price is not None and lead.previous_price != result.best.price:
            was = f"\nwas {format_pln(lead.previous_price)}"
        plain_parts.append(
            f"{search.name}\n{when}\n{price} ({each} a night)\n{room}; {meal}; {offer}\n{cancel}\n{reasons}{was}\n{link}"
        )
        html_parts.append(
            "<section>"
            f"<h2>{html.escape(search.name)}</h2>"
            f"<p><strong>{html.escape(when)}</strong><br>{html.escape(price)} "
            f"({html.escape(each)} a night)</p>"
            f"<p>{html.escape(room)} · {html.escape(meal)} · {html.escape(offer)}<br>"
            f"{html.escape(cancel)}</p>"
            f"<p>{html.escape(reasons)}</p>"
            f"<p><a href=\"{html.escape(link, quote=True)}\">Open the booking page</a></p>"
            "</section>"
        )
    plain = "\n\n".join(plain_parts) + "\n"
    body = (
        "<!DOCTYPE html><html><body style=\"font-family:sans-serif;color:#1c1915\">"
        + "".join(html_parts)
        + "</body></html>"
    )
    subject = scan_subject(notices, hits) if notices else alert_subject(hits)
    return _message(secrets, subject, plain, body)


def build_digest_message(
    secrets: Secrets,
    sections: list[tuple[Search, list[StayResult]]],
    catalog: Catalog,
    tracked: list[tuple[TrackedTrip, StayResult]] | None = None,
) -> EmailMessage:
    priced: list[tuple[Search, StayResult]] = []
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for trip, result in tracked or []:
        if not plain_parts:
            plain_parts.append("Tracked trips")
            html_parts.append("<h2>Tracked trips</h2>")
        status = tracked_status_line(result, catalog)
        link = booking_url(secrets.slug, result.stay, trip.adults)
        plain_parts.append(status)
        plain_parts.append(link)
        html_parts.append(
            "<p>"
            f"{html.escape(status)}<br>"
            f"<a href=\"{html.escape(link, quote=True)}\">Open the booking page</a>"
            "</p>"
        )
    for search, results in sections:
        plain_parts.append(search.name)
        plain_parts.append(render_table(results, catalog, search.top).rstrip())
        html_parts.append(f"<h2>{html.escape(search.name)}</h2>")
        visible = [result for result in results if result.best is not None]
        if search.top is not None:
            visible = visible[: search.top]
        if not visible:
            html_parts.append("<p>No prices.</p>")
            continue
        items = []
        for result in visible:
            offer, room, meal, cancel = describe(result, catalog)
            priced.append((search, result))
            when = format_stay(result.stay)
            price = format_pln(result.best.price)
            link = booking_url(secrets.slug, result.stay, search.adults)
            plain_parts.append(link)
            items.append(
                "<li>"
                f"{html.escape(when)} — {html.escape(price)} — "
                f"{html.escape(room)}, {html.escape(meal)}, {html.escape(offer)}"
                f"<br><a href=\"{html.escape(link, quote=True)}\">Open the booking page</a>"
                "</li>"
            )
        html_parts.append("<ul>" + "".join(items) + "</ul>")
    if priced:
        priced.sort(key=lambda item: (item[1].best.price, item[1].stay.check_in))
        best = priced[0][1]
        subject = f"Weekly digest: {format_stay(best.stay)} {format_pln(best.best.price)}"
    else:
        available = [result for _trip, result in (tracked or []) if result.best is not None]
        if available:
            best = min(available, key=lambda result: (result.best.price, result.stay.check_in))
            subject = f"Weekly digest: {format_stay(best.stay)} {format_pln(best.best.price)}"
        else:
            subject = "Weekly digest: no prices"
    plain = "\n".join(plain_parts) + "\n"
    body = (
        "<!DOCTYPE html><html><body style=\"font-family:sans-serif;color:#1c1915\">"
        + "".join(html_parts)
        + "</body></html>"
    )
    return _message(secrets, subject, plain, body)


def build_search_message(
    secrets: Secrets,
    search: Search,
    results: list[StayResult],
    catalog: Catalog,
) -> EmailMessage:
    visible = results if search.top is None else [result for result in results if result.best is not None][: search.top]
    priced = [result for result in visible if result.best is not None]
    if priced:
        best = min(priced, key=lambda result: (result.best.price, result.stay.check_in))
        subject = f"Search: {format_stay(best.stay)} {format_pln(best.best.price)}"
    else:
        subject = "Search: no prices"
    plain_lines = [render_table(results, catalog, search.top).rstrip()]
    html_items = []
    for result in priced:
        offer, room, meal, cancel = describe(result, catalog)
        link = booking_url(secrets.slug, result.stay, search.adults)
        plain_lines.append(link)
        html_items.append(
            "<li>"
            f"{html.escape(format_stay(result.stay))} — {html.escape(format_pln(result.best.price))}"
            f" — {html.escape(room)}, {html.escape(meal)}, {html.escape(offer)}, {html.escape(cancel)}"
            f"<br><a href=\"{html.escape(link, quote=True)}\">Open the booking page</a>"
            "</li>"
        )
    plain = "\n".join(plain_lines) + "\n"
    body = (
        "<!DOCTYPE html><html><body style=\"font-family:sans-serif;color:#1c1915\">"
        "<ul>" + "".join(html_items) + "</ul></body></html>"
    )
    return _message(secrets, subject, plain, body)


def build_failure_message(secrets: Secrets, count: int, reason: str) -> EmailMessage:
    plain = f"The watcher failed {count} times in a row.\n\n{reason}\n"
    body = (
        "<!DOCTYPE html><html><body style=\"font-family:sans-serif;color:#1c1915\">"
        f"<p>The watcher failed {count} times in a row.</p>"
        f"<p>{html.escape(reason)}</p></body></html>"
    )
    return _message(secrets, "Stay Watch is failing", plain, body)


def build_recovered_message(secrets: Secrets) -> EmailMessage:
    plain = "The watcher completed a scan after earlier failures.\n"
    body = (
        "<!DOCTYPE html><html><body style=\"font-family:sans-serif;color:#1c1915\">"
        "<p>The watcher completed a scan after earlier failures.</p></body></html>"
    )
    return _message(secrets, "Stay Watch recovered", plain, body)


def _html_lines(plain: str) -> str:
    parts: list[str] = []
    for line in plain.splitlines():
        if line.startswith("https://"):
            href = html.escape(line, quote=True)
            parts.append(f'<a href="{href}">Open the booking page</a>')
        else:
            parts.append(html.escape(line))
    return "<p>" + "<br>".join(parts) + "</p>"


def _message(secrets: Secrets, subject: str, plain: str, html_body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = " ".join(subject.split())
    sender = secrets.smtp_user or secrets.alert_to or "stay-watch"
    message["From"] = sender
    message["To"] = secrets.alert_to or secrets.smtp_user or ""
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")
    return message


def _dry_text(message: EmailMessage) -> str:
    plain = message.get_body(preferencelist=("plain",))
    text = plain.get_content() if plain is not None else ""
    return f"To: {message['To']}\nSubject: {message['Subject']}\n\n{text.rstrip()}\n"


