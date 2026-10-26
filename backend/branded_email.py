"""Branded transactional emails (invitation, password reset): one shared layout, plain text and responsive HTML.

Branding comes only from the server-side table below, keyed by the company slug of the invitation record (never from
request input). It mirrors the identity already shipped in the frontend (`lib/branding.ts`, `globals.css`,
`public/brand/`). A company without an entry (and every email that belongs to no single company, such as a password
reset) gets a neutral design. The logo is embedded in the message (inline CID image) from the repository's own brand
files, so it does not depend on a public URL that a mail client could not reach (localhost is never reachable from
outside the machine); when the file cannot be read the header shows the company name as text. Nothing here logs, and a
link is rendered only into the message it is built for.
"""
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

INVITATION_SUBJECT = "You're invited to Sales Tracker"
RESET_SUBJECT = "Reset your Sales Tracker password"
NEUTRAL_COLOR = "#1f3a5f"  # white text on it: contrast above 10:1
LOGO_CID = "company-logo"
_HERE = Path(__file__).resolve().parent
_BRAND_DIRS = (_HERE / "static" / "brand", _HERE.parent / "frontend" / "public" / "brand")
_IMAGE_TYPES = {".png": "png", ".webp": "webp", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif"}


@dataclass(frozen=True)
class EmailBrand:
    logo_file: str | None = None
    color: str | None = None  # button/accent background; white text must stay readable on it


BRANDS: dict[str, EmailBrand] = {
    "salcomp": EmailBrand("salcomp-logo.png", "#be3124"),
    "lity": EmailBrand("lity-logo.webp", "#c62a2f"),
    "valor-log": EmailBrand("valorlog-wordmark.png", "#b8450b"),
}


@dataclass(frozen=True)
class InlineImage:
    cid: str
    data: bytes
    subtype: str


@dataclass(frozen=True)
class BrandedEmail:
    subject: str
    text: str
    html: str
    inline_images: tuple[InlineImage, ...] = field(default_factory=tuple)


InvitationEmail = BrandedEmail  # the original name, kept for callers


def _plain(value: str) -> str:
    """Single line, no control characters: keeps a company name from adding lines or headers to the text part."""
    return " ".join("".join(ch for ch in value if ch.isprintable() or ch.isspace()).split())


def _load_logo(logo_file: str | None) -> InlineImage | None:
    """The brand file from the fixed table (never a request value), or None when it cannot be read."""
    if not logo_file:
        return None
    subtype = _IMAGE_TYPES.get(Path(logo_file).suffix.lower())
    if subtype is None:
        return None
    for directory in _BRAND_DIRS:
        try:
            return InlineImage(LOGO_CID, (directory / logo_file).read_bytes(), subtype)
        except OSError:
            continue
    return None


def _render(*, subject: str, brand: EmailBrand, company_name: str, headline: str, intro: str, intro_html: str,
            cta: str, link: str, notice: str, closing: str) -> BrandedEmail:
    color = brand.color or NEUTRAL_COLOR
    name = _plain(company_name)
    logo = _load_logo(brand.logo_file)
    e_name, e_link, e_color = escape(name), escape(link, quote=True), escape(color, quote=True)

    text = f"{headline}\n\n{intro}\n\n{cta}:\n{link}\n\n{notice}\n\n{closing}\n"
    header = ""
    if logo is not None:
        header += (f'<img src="cid:{logo.cid}" alt="{e_name} logo" height="40" '
                   'style="display:block;border:0;height:40px;max-width:240px;width:auto;margin:0 auto;">')
    if name:  # also the readable fallback when an image is blocked or cannot be shown
        header += (f'<div style="font-size:16px;line-height:24px;font-weight:bold;color:#1c1f24;'
                   f'padding-top:{"12" if logo is not None else "0"}px;">{e_name}</div>')
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f5f7;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:#ffffff;border-top:4px solid {e_color};font-family:Arial,Helvetica,sans-serif;color:#1c1f24;">
<tr><td align="center" style="padding:28px 32px 8px 32px;">{header}</td></tr>
<tr><td style="padding:16px 32px 0 32px;"><h1 style="margin:0;font-size:24px;line-height:32px;color:#1c1f24;">{escape(headline)}</h1></td></tr>
<tr><td style="padding:12px 32px 0 32px;font-size:16px;line-height:24px;">{intro_html}</td></tr>
<tr><td align="center" style="padding:28px 32px;"><a href="{e_link}" style="display:inline-block;background:{e_color};color:#ffffff;font-size:16px;font-weight:bold;line-height:24px;text-decoration:none;padding:12px 28px;border-radius:4px;">{escape(cta)}</a></td></tr>
<tr><td style="padding:0 32px;font-size:14px;line-height:20px;color:#4a525c;">If the button does not work, copy this address into your browser:<br><a href="{e_link}" style="color:{e_color};word-break:break-all;">{e_link}</a></td></tr>
<tr><td style="padding:16px 32px 0 32px;font-size:14px;line-height:20px;color:#4a525c;">{escape(notice)}</td></tr>
<tr><td style="padding:16px 32px 32px 32px;font-size:16px;line-height:24px;">{escape(closing)}</td></tr>
</table></td></tr></table></body></html>
"""
    return BrandedEmail(subject, text, html, (logo,) if logo is not None else ())


def build_invitation_email(company_slug: str, company_name: str, link: str, base_url: str = "") -> BrandedEmail:
    """`base_url` is accepted for compatibility; the logo is embedded and needs no URL."""
    brand = BRANDS.get(company_slug, EmailBrand())
    name = _plain(company_name) or "your company"
    intro = (f"You have been invited to join Sales Tracker for {name}. "
             "Use the link below to access your invitation and complete the account setup process.")
    intro_html = (f"You have been invited to join Sales Tracker for <strong>{escape(name)}</strong>. "
                  "Use the button below to access your invitation and complete the account setup process.")
    return _render(
        subject=INVITATION_SUBJECT, brand=brand, company_name=name, headline="Welcome to Sales Tracker",
        intro=intro, intro_html=intro_html, cta="Accept Invitation", link=link,
        notice="The link works once and expires automatically. If you were not expecting this invitation, you can ignore this email.",
        closing="We look forward to working with you.")


def build_password_reset_email(link: str) -> BrandedEmail:
    """A reset belongs to a person, not to one company, so it uses the neutral design."""
    intro = "We received a request to reset the password of your Sales Tracker account. Use the link below to choose a new password."
    intro_html = ("We received a request to reset the password of your Sales Tracker account. "
                  "Use the button below to choose a new password.")
    return _render(
        subject=RESET_SUBJECT, brand=EmailBrand(), company_name="Sales Tracker", headline="Reset your password",
        intro=intro, intro_html=intro_html, cta="Reset Password", link=link,
        notice="The link expires in one hour and works once. If you did not ask for this, you can ignore this email; your password stays as it is.",
        closing="Sales Tracker")
