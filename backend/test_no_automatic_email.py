"""The application sends no automatic notification email, only transactional mail a person asked for.

Established 2026-10-24 by reading every caller of the mailer: invitations, invitation resend, password reset,
signup verification, the legacy member invitation and the operator's `send-activation`. Cards, reviews, reports,
KPIs, assignments and stage changes send nothing, and there is no scheduler, worker or digest. There is therefore
no notification switch to turn off; these tests keep it that way. A new caller of the mailer fails the allowlist
below until someone classifies it as transactional here (or, for a notification, adds a deliberate on/off setting).
"""
import re
import smtplib
from pathlib import Path

import mailer
from models import Board, Column
from test_routes import admin_headers, client, test_db  # noqa: F401  (fixtures)

BACKEND = Path(__file__).parent

# Every module that reaches the mailer, each only for a transactional message requested by a person or an operator
TRANSACTIONAL_CALLERS = {
    "central_routes.py",       # password reset (forgot), invitation, invitation resend
    "main.py",                 # legacy signup verification, legacy password reset
    "routes.py",               # legacy member invitation (POST /api/users)
    "identity_admin_cli.py",   # operator's send-activation
}
MAILER_USE = re.compile(r"send_email|send_in_background|smtplib")


def test_only_the_known_transactional_modules_call_the_mailer():
    found = {p.name for p in BACKEND.glob("*.py")
             if not p.name.startswith("test_") and p.name not in {"mailer.py", "conftest.py", "production_config.py"}
             and MAILER_USE.search(p.read_text())}
    assert found == TRANSACTIONAL_CALLERS


def test_no_scheduler_or_worker_exists():
    patterns = re.compile(r"apscheduler|celery|BackgroundScheduler|asyncio\.create_task")
    offenders = [p.name for p in BACKEND.glob("*.py") if not p.name.startswith("test_") and patterns.search(p.read_text())]
    assert offenders == []


def test_card_and_board_actions_send_no_email(client, test_db, admin_headers, monkeypatch):  # noqa: F811
    """Even with SMTP configured, creating, editing, moving and deleting cards sends nothing."""
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: sent.append(a))

    def no_connection(*args, **kwargs):
        raise AssertionError("an SMTP connection was attempted")

    monkeypatch.setattr(smtplib, "SMTP", no_connection)

    board = Board(user_id=1, title="Test")
    test_db.add(board)
    test_db.commit()
    first = Column(board_id=board.id, title="1st", position=0)
    second = Column(board_id=board.id, title="2nd", position=1)
    test_db.add_all([first, second])
    test_db.commit()

    created = client.post(f"/api/boards/{board.id}/cards",
                          json={"column_id": first.id, "title": "Lead", "details": "d", "position": 0})
    assert created.status_code == 201
    card_id = created.json()["id"]
    assert client.put(f"/api/boards/{board.id}/cards/{card_id}", json={"title": "Lead 2"}).status_code == 200
    moved = client.put(f"/api/boards/{board.id}/cards/{card_id}/position",
                        json={"column_id": second.id, "position": 0})
    assert moved.status_code in (200, 204), moved.text
    client.delete(f"/api/boards/{board.id}/cards/{card_id}", headers=admin_headers)

    assert sent == []
