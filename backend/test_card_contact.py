"""Customer contact email and telephone on a card: validation, persistence, history and the additive migration."""
import sqlite3

import pytest
from pydantic import ValidationError

from migrate_card_contact import COLUMNS, main as migrate_main, migrate_card_contact
from schemas import CardUpdate
from test_company_card_writes import admin
from test_company_card_writes import rig  # noqa: F401  (fixture)


@pytest.mark.parametrize("value, expected", [
    ("ana@example.com", "ana@example.com"), ("  ana.b+x@mail.example.co.uk ", "ana.b+x@mail.example.co.uk"),
    ("", None), ("   ", None), (None, None),
])
def test_email_accepts_ordinary_and_blank(value, expected):
    assert CardUpdate(contact_email=value).contact_email == expected


@pytest.mark.parametrize("value", ["ana", "ana@", "@example.com", "ana@example", "a b@example.com", "a@@example.com", "a@example..com"])
def test_email_rejects_clearly_invalid(value):
    with pytest.raises(ValidationError):
        CardUpdate(contact_email=value)


@pytest.mark.parametrize("value", ["+55 (11) 91234-5678", "+358 40 123 4567", "0044.20.7946.0958", "020 7946 0958 x123", "555-1234"])
def test_phone_accepts_international_formats(value):
    assert CardUpdate(contact_phone=value).contact_phone == " ".join(value.split())


@pytest.mark.parametrize("value", ["call me", "12", "abc123456", "+55; DROP"])
def test_phone_rejects_text_and_too_few_digits(value):
    with pytest.raises(ValidationError):
        CardUpdate(contact_phone=value)


def test_phone_blank_clears_and_length_is_capped():
    assert CardUpdate(contact_phone="  ").contact_phone is None
    with pytest.raises(ValidationError):
        CardUpdate(contact_phone="1" * 41)


def put(rig, token, **fields):
    return rig.call("put", "/cards/1", token, json=fields)


def test_contact_persists_is_returned_and_is_in_history(rig):
    token = admin(rig)
    r = put(rig, token, contact_email="ana@example.com", contact_phone="+55 11 91234-5678")
    assert r.status_code == 200
    assert (r.json()["contact_email"], r.json()["contact_phone"]) == ("ana@example.com", "+55 11 91234-5678")
    board = rig.call("get", "", token).json()
    card = next(c for col in board["columns"] for c in col["cards"] if c["id"] == 1)
    assert card["contact_email"] == "ana@example.com" and card["contact_phone"] == "+55 11 91234-5678"
    # A blank clears; an unrelated edit leaves them alone.
    assert put(rig, token, details="x").json()["contact_email"] == "ana@example.com"
    cleared = put(rig, token, contact_email="", contact_phone=None).json()
    assert cleared["contact_email"] is None and cleared["contact_phone"] is None


def test_invalid_contact_is_refused_and_changes_nothing(rig):
    token = admin(rig)
    assert put(rig, token, contact_email="nope").status_code == 422
    assert put(rig, token, contact_phone="call me").status_code == 422
    assert put(rig, token, details="y").json()["contact_email"] is None


def test_existing_cards_without_contact_are_compatible(rig):
    token = admin(rig)
    r = put(rig, token, title="Renamed")
    assert r.status_code == 200 and r.json()["contact_email"] is None and r.json()["contact_phone"] is None


def old_database(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO cards (title) VALUES ('kept')")
        conn.execute("PRAGMA user_version = 4")


def test_migration_is_dry_run_then_additive_and_idempotent(tmp_path):
    path = tmp_path / "old.db"
    old_database(path)
    names = [n for n, _ in COLUMNS]
    assert migrate_card_contact(path) == names  # dry run
    assert not {n for n in names} & {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(cards)")}
    assert migrate_card_contact(path, apply=True) == names
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT title, contact_email, contact_phone FROM cards").fetchall() == [("kept", None, None)]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert migrate_card_contact(path, apply=True) == []


def test_migration_never_creates_a_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_card_contact(tmp_path / "missing.db", apply=True)
    assert not (tmp_path / "missing.db").exists()
    assert migrate_main(["--database", str(tmp_path / "missing.db")]) == 1


# --- the migration against a real company-layout database (temporary files only) -------------------------------------

import hashlib
import os

import migrate_card_contact as mcc
from database_targets import company_database_path


def unmigrate(rig, slug="lity"):
    """Make the company database look like one from before this change: drop the two columns, keep everything else."""
    rig.sql("ALTER TABLE cards DROP COLUMN contact_email", slug=slug)
    rig.sql("ALTER TABLE cards DROP COLUMN contact_phone", slug=slug)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_unmigrated_company_is_refused_with_503_and_nothing_is_written(rig):
    token = admin(rig)
    unmigrate(rig)
    before = rig.digest("lity")
    r = rig.client.get("/api/companies/lity/boards/1", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503 and r.json()["detail"] == "Company data is unavailable"
    assert rig.digest("lity") == before  # the refusal is read-only
    other = rig.client.get("/api/companies/valor-log/boards/1", headers={"Authorization": f"Bearer {token}"})
    assert other.status_code != 503  # a migrated company is unaffected


def test_company_migration_end_to_end_restores_service_and_keeps_data(rig, monkeypatch):
    token = admin(rig)
    assert put(rig, token, title="Kept title", details="kept details").status_code == 200
    unmigrate(rig)
    for key, value in rig.e.env.items():  # the project's own PM_COMPANY_DB_<SLUG> naming
        monkeypatch.setenv(key, value)
    path = company_database_path("lity", rig.e.env)
    before = rig.digest("lity")
    assert migrate_main(["--company", "lity"]) == 0  # dry run
    assert rig.digest("lity") == before  # byte-identical: a dry run writes nothing
    version = rig.sql("PRAGMA user_version")[0][0]
    rows_before = rig.sql("SELECT id, title, details, position, salesperson_id FROM cards ORDER BY id")
    history_before = rig.sql("SELECT COUNT(*) FROM card_edit_history")
    assert migrate_main(["--company", "lity", "--apply"]) == 0
    assert rig.sql("PRAGMA user_version")[0][0] == version  # no schema-version change
    assert rig.sql("SELECT id, title, details, position, salesperson_id FROM cards ORDER BY id") == rows_before
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == history_before
    assert rig.sql("SELECT contact_email, contact_phone FROM cards") == [(None, None)] * len(rows_before)
    columns = [r[1] for r in rig.sql("PRAGMA table_info(cards)")]
    assert columns.count("contact_email") == 1 and columns.count("contact_phone") == 1
    again = rig.digest("lity")
    assert migrate_main(["--company", "lity", "--apply"]) == 0  # idempotent: nothing to do
    assert rig.digest("lity") == again
    # And the application works again on the migrated database, including the new fields.
    r = put(rig, token, contact_email="ana@example.com")
    assert r.status_code == 200 and r.json()["contact_email"] == "ana@example.com"
    assert path.exists()


def test_other_companies_are_not_touched(rig, monkeypatch):
    unmigrate(rig)
    for key, value in rig.e.env.items():
        monkeypatch.setenv(key, value)
    other = rig.digest("valor-log")
    assert migrate_main(["--company", "lity", "--apply"]) == 0
    assert rig.digest("valor-log") == other


def test_a_failure_between_the_two_columns_rolls_back_both(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old_database(path)
    real = sqlite3.connect

    class Failing:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            if "contact_phone" in sql and sql.startswith("ALTER"):
                raise sqlite3.OperationalError("simulated failure")
            return self.conn.execute(sql, *args)

        def close(self):
            self.conn.close()

    monkeypatch.setattr(mcc.sqlite3, "connect", lambda *a, **k: Failing(real(*a, **k)))
    before = sha(path)
    with pytest.raises(sqlite3.OperationalError):
        migrate_card_contact(path, apply=True)
    monkeypatch.undo()
    assert sha(path) == before  # contact_email was not left behind
    assert "contact_email" not in {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(cards)")}


def test_unsupported_schema_and_locked_database_change_nothing(tmp_path, capsys):
    no_cards = tmp_path / "other.db"
    with sqlite3.connect(no_cards) as conn:
        conn.execute("CREATE TABLE something (id INTEGER)")
    before = sha(no_cards)
    assert migrate_main(["--database", str(no_cards), "--apply"]) == 1
    assert sha(no_cards) == before
    assert not os.path.exists(f"{no_cards}-journal")

    locked = tmp_path / "locked.db"
    old_database(locked)
    holder = sqlite3.connect(locked, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        before = sha(locked)
        assert migrate_main(["--database", str(locked), "--apply"]) == 1  # reports the failure, no traceback
        assert sha(locked) == before
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert "Traceback" not in capsys.readouterr().err


def test_output_never_contains_data_or_credentials(tmp_path, capsys, monkeypatch):
    path = tmp_path / "old.db"
    old_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE cards SET title = 'SECRET-CUSTOMER-NAME'")
    monkeypatch.setenv("SECRET_KEY", "super-secret-value-for-the-test-0123456789")
    migrate_main(["--database", str(path), "--apply"])
    out = capsys.readouterr()
    assert "SECRET-CUSTOMER-NAME" not in out.out + out.err and "super-secret" not in out.out + out.err


def test_database_option_needs_an_absolute_path(capsys):
    assert migrate_main(["--database", "relative.db", "--apply"]) == 1
