from __future__ import annotations

import html
import smtplib
import ssl
import urllib.parse
from email.message import EmailMessage

from stay_watch.config import Secrets
from stay_watch.model import AlertHit, Catalog, Search, Stay, StayResult
from stay_watch.render import (
    alert_subject,
    describe,
    format_pln,
    format_stay,
    ordered_hits,
    render_table,
)


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
) -> EmailMessage:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for hit in ordered_hits(hits):
        search = searches[hit.search_id]
        result = hit.stay_result
        offer, room, meal, cancel = describe(result, catalog)
        price = format_pln(result.best.price)
        each = format_pln(result.best.price // result.stay.nights)
        when = format_stay(result.stay)
        link = booking_url(secrets.slug, result.stay, search.adults)
        under = format_pln(search.alert.under)
        lines = [
            search.name or "",
            when,
            f"{price} ({each} a night)",
            f"{room}; {meal}; {offer}",
            cancel,
            f"under {under}",
        ]
        if hit.last_emailed is not None:
            lines.append(f"last emailed {format_pln(hit.last_emailed)}")
        lines.append(link)
        plain_parts.append("\n".join(lines))
        remembered = ""
        if hit.last_emailed is not None:
            remembered = f"<br>{html.escape(f'last emailed {format_pln(hit.last_emailed)}')}"
        html_parts.append(
            "<section>"
            f"<h2>{html.escape(search.name or '')}</h2>"
            f"<p><strong>{html.escape(when)}</strong><br>{html.escape(price)} "
            f"({html.escape(each)} a night)</p>"
            f"<p>{html.escape(room)} · {html.escape(meal)} · {html.escape(offer)}<br>"
            f"{html.escape(cancel)}</p>"
            f"<p>{html.escape(f'under {under}')}{remembered}</p>"
            f"<p><a href=\"{html.escape(link, quote=True)}\">Open the booking page</a></p>"
            "</section>"
        )
    plain = "\n\n".join(plain_parts) + "\n"
    body = (
        "<!DOCTYPE html><html><body style=\"font-family:sans-serif;color:#1c1915\">"
        + "".join(html_parts)
        + "</body></html>"
    )
    return _message(secrets, alert_subject(hits, searches), plain, body)


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
