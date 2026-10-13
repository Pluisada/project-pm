"""Outgoing email via SMTP."""
import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("uvicorn.error")


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email.

    Without SMTP_HOST configured (local dev), the message is logged instead of
    sent, so the signup link can still be copied from the backend console.
    """
    host = os.getenv("SMTP_HOST")
    if not host:
        logger.warning("SMTP_HOST not set; email to %s not sent:\n%s\n%s", to, subject, body)
        return

    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", "")
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as smtp:
        smtp.starttls()
        user = os.getenv("SMTP_USER")
        if user:
            smtp.login(user, os.getenv("SMTP_PASSWORD", ""))
        smtp.send_message(message)
