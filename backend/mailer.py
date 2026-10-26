"""Outgoing email via SMTP.

Logging policy: message bodies, links and tokens are never logged, not even when
SMTP is unconfigured (there is deliberately no development switch for it). Only
the subject, a masked recipient and the delivery status are recorded, and
exceptions are logged by type alone, since their text can carry addresses.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger("uvicorn.error")


class EmailDeliveryError(OSError):
    """The message could not be handed to the SMTP server; carries no detail."""


def mask_recipient(address: str) -> str:
    """'alice@example.com' -> 'a***@example.com' (log-safe)."""
    local, sep, domain = address.rpartition("@")
    if not sep or not local:
        return "***"
    return f"{local[0]}***@{domain}"


def is_configured() -> bool:
    """Whether outgoing mail is set up at all (SMTP_HOST present)."""
    return bool(os.getenv("SMTP_HOST"))


def send_email(to: str, subject: str, body: str, html: str | None = None, inline_images=()) -> None:
    """Send an email (plain text, plus an HTML alternative when given), or record that it was not sent.

    Without SMTP_HOST (local dev) nothing is sent and the link cannot be read
    from the logs: configure SMTP, or capture the message in tests.
    """
    recipient = mask_recipient(to)
    host = os.getenv("SMTP_HOST")
    if not host:
        logger.warning("Email not sent (SMTP_HOST not set): to=%s subject=%r", recipient, subject)
        return

    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", "")
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
        for image in inline_images:  # embedded (cid:) images travel inside the message, with no URL to reach
            message.get_payload()[-1].add_related(image.data, "image", image.subtype, cid=f"<{image.cid}>")

    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            user = os.getenv("SMTP_USER")
            if user:
                smtp.login(user, os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        logger.error("Email delivery failed: to=%s subject=%r error=%s", recipient, subject, type(exc).__name__)
        raise EmailDeliveryError("Could not send email") from None
    logger.info("Email sent: to=%s subject=%r", recipient, subject)


def send_in_background(background, to: str, subject: str, body: str, html: str | None = None, inline_images=()) -> None:
    """Deliver after the response, so latency doesn't reveal whether mail was sent.
    A delivery failure is already logged (type only) by `send_email`."""

    def deliver():
        try:
            send_email(to, subject, body, html, inline_images) if html else send_email(to, subject, body)
        except EmailDeliveryError:
            pass

    background.add_task(deliver)
