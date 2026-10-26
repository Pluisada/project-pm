"""Branded emails (invitation, password reset): shared layout, copy, link, escaping, branding, logo handling and fallbacks.
Synthetic data; nothing here sends mail or contacts a server."""
import email
import smtplib
from email import policy

import pytest

import branded_email as be
import mailer
from branded_email import NEUTRAL_COLOR, build_invitation_email, build_password_reset_email

BASE = "https://app.example.test"
LINK = f"{BASE}/?central_invite_token=abc_DEF-123"
RESET_LINK = f"{BASE}/?central_reset_token=rst_XYZ-789"


def build(slug, name):
    return build_invitation_email(slug, name, LINK, BASE)


def test_invitation_copy_and_link_in_both_parts():
    m = build("salcomp", "Salcomp")
    assert m.subject == "You're invited to Sales Tracker"
    for part in (m.text, m.html):
        assert "Welcome to Sales Tracker" in part
        assert "You have been invited to join Sales Tracker" in part
        assert "complete the account setup process" in part
        assert "We look forward to working with you." in part
        assert LINK in part and "Accept Invitation" in part
    assert f'href="{LINK}"' in m.html
    assert m.text.split("central_invite_token=")[1].split()[0] == "abc_DEF-123"
    assert "<script" not in m.html and "<link" not in m.html.lower() and "javascript:" not in m.html


def test_reset_copy_cta_and_link_in_both_parts():
    m = build_password_reset_email(RESET_LINK)
    assert m.subject == "Reset your Sales Tracker password"
    for part in (m.text, m.html):
        assert "Reset your password" in part and RESET_LINK in part and "Reset Password" in part
        assert "expires in one hour" in part and "works once" in part
    assert f'href="{RESET_LINK}"' in m.html
    assert m.text.split("central_reset_token=")[1].split()[0] == "rst_XYZ-789"
    assert "Accept Invitation" not in m.html and "invited" not in m.text.lower()


def test_both_emails_share_one_layout():
    inv, rst = build("other", "Other Co"), build_password_reset_email(RESET_LINK)

    def skeleton(html):  # the layout without the words and the link
        import re
        return re.sub(r">[^<]*<", "><", re.sub(r'(href|alt|src)="[^"]*"', r'\1=""', html))
    strip = lambda h: [t for t in skeleton(h).split("\n") if "<h1" not in t and "<title" not in t]
    # same table structure, fonts, spacing, colors and footer rows; only the copy differs
    assert [t for t in strip(inv.html) if "<tr>" in t] == [t for t in strip(rst.html) if "<tr>" in t]
    assert NEUTRAL_COLOR in inv.html and NEUTRAL_COLOR in rst.html
    assert "font-family:Arial,Helvetica,sans-serif" in inv.html and "font-family:Arial,Helvetica,sans-serif" in rst.html


@pytest.mark.parametrize("slug,logo,color", [
    ("salcomp", "salcomp-logo.png", "#be3124"),
    ("lity", "lity-logo.webp", "#c62a2f"),
    ("valor-log", "valorlog-wordmark.png", "#b8450b"),
])
def test_each_company_gets_its_own_brand_logo_and_color(slug, logo, color):
    m = build(slug, "Some Name")
    assert color in m.html and not any(o in m.html for o in {"#be3124", "#c62a2f", "#b8450b"} - {color})
    assert 'src="cid:company-logo"' in m.html and 'alt="Some Name logo"' in m.html
    (image,) = m.inline_images
    assert image.cid == "company-logo" and image.data == (be._BRAND_DIRS[1] / logo).read_bytes()
    assert "http" not in m.html.split("<img")[1].split(">")[0]  # no remote URL: nothing a client must reach


def test_logo_is_the_repository_brand_file_not_a_request_value():
    # An unknown or hostile slug never selects a file.
    for slug in ("../../etc/passwd", "other", ""):
        m = build(slug, "X")
        assert m.inline_images == () and "<img" not in m.html and "X" in m.html and LINK in m.html


def test_unknown_company_is_neutral_and_readable():
    m = build("other", "Other Co")
    assert "<img" not in m.html and NEUTRAL_COLOR in m.html and "Other Co" in m.html and LINK in m.html


def test_missing_logo_file_falls_back_to_the_company_name(monkeypatch):
    monkeypatch.setattr(be, "_BRAND_DIRS", ())
    m = build("salcomp", "Salcomp")
    assert m.inline_images == () and "<img" not in m.html and "Salcomp" in m.html and "#be3124" in m.html and LINK in m.html


def test_incomplete_branding(monkeypatch):
    monkeypatch.setitem(be.BRANDS, "logo-only", be.EmailBrand(logo_file="salcomp-logo.png"))
    monkeypatch.setitem(be.BRANDS, "color-only", be.EmailBrand(color="#112233"))
    a, b = build("logo-only", "A"), build("color-only", "B")
    assert 'src="cid:company-logo"' in a.html and NEUTRAL_COLOR in a.html and len(a.inline_images) == 1
    assert "<img" not in b.html and "#112233" in b.html and b.inline_images == ()


def test_dynamic_values_are_escaped():
    evil = '<script>alert(1)</script> & "Co"\r\nBcc: x@y.z'
    m = build_invitation_email("salcomp", evil, BASE + '/?central_invite_token="><b>', BASE)
    assert "<script>" not in m.html and "&lt;script&gt;" in m.html and "&amp;" in m.html
    assert '"><b>' not in m.html
    assert "\r" not in m.text and "\nBcc:" not in m.text
    r = build_password_reset_email(BASE + '/?central_reset_token="><i>')
    assert '"><i>' not in r.html


def test_local_base_url_needs_no_reachable_image_host():
    m = build_invitation_email("lity", "Lity", "http://127.0.0.1:3010/?central_invite_token=t", "http://127.0.0.1:3010")
    assert "127.0.0.1" not in m.html.replace("http://127.0.0.1:3010/?central_invite_token=t", "")  # only the link itself


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self, **k): pass
    def login(self, *a): pass
    def send_message(self, message): FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP.sent


def test_mailer_embeds_the_logo_and_keeps_plain_text(smtp):
    m = build("salcomp", "Salcomp")
    mailer.send_email("a@example.com", m.subject, m.text, m.html, m.inline_images)
    msg = email.message_from_bytes(smtp[0].as_bytes(), policy=policy.default)
    types = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in types and "text/html" in types and "image/png" in types
    image = next(p for p in msg.walk() if p.get_content_type() == "image/png")
    assert image["Content-ID"] == "<company-logo>" and image.get_content() == m.inline_images[0].data


def test_mailer_stays_compatible_with_plain_and_html_only_callers(smtp):
    mailer.send_email("a@example.com", "s", "plain only")
    m = build("other", "Other Co")
    mailer.send_email("a@example.com", m.subject, m.text, m.html)
    assert not smtp[0].is_multipart() and smtp[1].is_multipart()
    assert "image/png" not in [p.get_content_type() for p in smtp[1].walk()]


def test_mailer_never_logs_the_reset_link_or_token(smtp, caplog):
    import logging
    m = build_password_reset_email(RESET_LINK)
    with caplog.at_level(logging.DEBUG, logger="uvicorn.error"):
        mailer.send_email("someone@example.com", m.subject, m.text, m.html, m.inline_images)
    logged = caplog.text
    assert "rst_XYZ-789" not in logged and "central_reset_token" not in logged and "someone@example.com" not in logged
