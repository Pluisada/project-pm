"""Part 22: Salcomp Bid import tables - models, migrate_import_tables.py and the
init_db() guard. Every database here is a temp file under tmp_path."""
import hashlib
import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import database
import migrate_import_revert
import migrate_import_tables as migration
from bid_money import MAX_BID_REVENUE_CENTS, cents_to_reais, reais_to_cents
from database import ImportTablesMigrationRequired, ImportTablesSchemaError
from import_schema import ABSENT, COMPLETE, IMPORT_TABLE_NAMES, INCOMPATIBLE, PARTIAL, REVERT_TABLE_NAMES, ddl, import_schema_state
from models import (
    Base,
    Board,
    Card,
    CardBidDetails,
    CardImportChange,
    CardSourceRef,
    Column,
    ImportBatch,
    SOURCE_SYSTEM_SALCOMP_BIDS,
    User,
    UserRole,
)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _engine(path):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _state(path):
    conn = sqlite3.connect(path)
    try:
        return import_schema_state(lambda sql: conn.execute(sql).fetchall())
    finally:
        conn.close()


def _run_sql(path, *statements):
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()


def _dump(path):
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _backups(tmp_path):
    return list(tmp_path.glob("*-pre-import-tables-*.db.bak"))


def _pre_part22_db(path):
    """A database at the Part 21 schema (every earlier migration applied), with one card."""
    engine = _engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    _run_sql(
        path,
        *(f"DROP TABLE {table}" for table in reversed(REVERT_TABLE_NAMES + IMPORT_TABLE_NAMES)),
        "INSERT INTO users (id, username, password_hash, role, created_at, updated_at) "
        "VALUES (1, 'admin', 'x', 'admin', '2026-01-01', '2026-01-01')",
        "INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, 1, 'Pipeline', '2026-01-01', '2026-01-01')",
        "INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
        "VALUES (1, 1, '1st Contact', 0, 'open', '2026-01-01', '2026-01-01')",
        "INSERT INTO cards (id, column_id, title, position, deal_value, currency, created_at, updated_at) "
        "VALUES (1, 1, 'Bid lead', 0, 1000, 'USD', '2026-01-01', '2026-01-01')",
        "INSERT INTO card_stage_events (card_id, board_id, event_type, to_column_id, to_stage_kind, source, occurred_at, "
        "value_snapshot_taken) VALUES (1, 1, 'baseline', 1, 'open', 'migration', '2026-01-01', 0)",
    )


@pytest.fixture
def session(tmp_path):
    engine = _engine(tmp_path / "new.db")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()
    engine.dispose()


def _card(db, title="Bid lead"):
    user = User(username="admin", password_hash="x", role=UserRole.ADMIN.value)
    db.add(user)
    db.flush()
    board = Board(title="Pipeline", user_id=user.id)
    column = Column(board=board, title="1st Contact", position=0, stage_kind="open")
    card = Card(column=column, title=title, position=0)
    db.add_all([board, column, card])
    db.commit()
    return card


class TestModels:
    def test_new_database_gets_the_exact_expected_schema(self, tmp_path):
        engine = _engine(tmp_path / "new.db")
        Base.metadata.create_all(engine)
        engine.dispose()
        assert _state(tmp_path / "new.db") == (COMPLETE, [])

    def test_card_bid_details_is_one_to_one(self, session):
        card = _card(session)
        session.add(CardBidDetails(card_id=card.id))
        session.commit()
        session.add(CardBidDetails(card_id=card.id))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_card_has_at_most_one_source_ref(self, session):
        card = _card(session)
        session.add(CardSourceRef(card_id=card.id, source_key="a", source_code="1"))
        session.commit()
        session.add(CardSourceRef(card_id=card.id, source_key="b", source_code="2"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_source_key_is_unique_per_source_system_in_the_database(self, session):
        first = _card(session, "First")
        second = Card(column_id=first.column_id, title="Second", position=1)
        third = Card(column_id=first.column_id, title="Third", position=2)
        session.add_all([second, third])
        session.commit()
        session.add(CardSourceRef(card_id=first.id, source_key="000123|acme", source_code="000123"))
        session.commit()
        assert session.query(CardSourceRef).one().source_system == SOURCE_SYSTEM_SALCOMP_BIDS
        # Same key from another source system is a different identity.
        session.add(CardSourceRef(card_id=second.id, source_system="other", source_key="000123|acme", source_code="000123"))
        session.commit()
        session.add(CardSourceRef(card_id=third.id, source_key="000123|acme", source_code="000123"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_details_need_an_existing_card(self, session):
        session.add(CardBidDetails(card_id=999))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_deleting_a_card_deletes_its_details_and_source_ref_but_keeps_the_batch(self, session):
        card = _card(session)
        batch = ImportBatch(board_id=card.column.board_id, filename="relatorio-leads.xlsx", file_sha256="a" * 64)
        session.add(batch)
        session.commit()
        session.add_all([
            CardBidDetails(card_id=card.id),
            CardSourceRef(card_id=card.id, source_key="k", source_code="1", last_import_batch_id=batch.id),
        ])
        session.commit()
        session.delete(card)
        session.commit()
        assert session.query(CardBidDetails).count() == 0
        assert session.query(CardSourceRef).count() == 0
        assert session.query(ImportBatch).count() == 1

    def test_deleting_a_batch_keeps_the_source_ref(self, session):
        card = _card(session)
        batch = ImportBatch(board_id=card.column.board_id, filename="r.xlsx", file_sha256="a" * 64)
        session.add(batch)
        session.commit()
        session.add(CardSourceRef(card_id=card.id, source_key="k", source_code="1", last_import_batch_id=batch.id))
        session.commit()
        session.delete(batch)
        session.commit()
        assert session.query(CardSourceRef).one().last_import_batch_id is None

    def test_import_change_links_card_batch_and_user(self, session):
        card = _card(session)
        user = session.query(User).one()
        batch = ImportBatch(board_id=card.column.board_id, user_id=user.id, filename="r.xlsx", file_sha256="a" * 64)
        session.add(batch)
        session.commit()
        session.add(CardImportChange(card_id=card.id, import_batch_id=batch.id, user_id=user.id, changes="{}"))
        session.commit()
        change = session.query(CardImportChange).one()
        assert change.card is card and change.import_batch is batch and change.created_at is not None
        assert card.import_changes == [change] and card.edit_history == [] and not card.has_member_edits

    def test_one_import_change_per_card_and_batch(self, session):
        card = _card(session)
        batch = ImportBatch(board_id=card.column.board_id, filename="r.xlsx", file_sha256="a" * 64)
        session.add(batch)
        session.commit()
        session.add(CardImportChange(card_id=card.id, import_batch_id=batch.id, changes="{}"))
        session.commit()
        session.add(CardImportChange(card_id=card.id, import_batch_id=batch.id, changes="{}"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_import_change_delete_rules(self, session):
        card = _card(session)
        user = session.query(User).one()
        batch = ImportBatch(board_id=card.column.board_id, filename="r.xlsx", file_sha256="a" * 64)
        session.add(batch)
        session.commit()
        session.add(CardImportChange(card_id=card.id, import_batch_id=batch.id, user_id=user.id, changes="{}"))
        session.commit()
        session.delete(card)  # like card_edit_history, it goes with its card
        session.commit()
        assert session.query(CardImportChange).count() == 0

    def test_import_change_needs_a_batch(self, session):
        card = _card(session)
        session.add(CardImportChange(card_id=card.id, import_batch_id=999, changes="{}"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_batch_defaults(self, session):
        card = _card(session)
        batch = ImportBatch(board_id=card.column.board_id, filename="r.xlsx", file_sha256="a" * 64)
        session.add(batch)
        session.commit()
        assert (batch.status, batch.created_count, batch.updated_count, batch.skipped_count, batch.review_count) == (
            "pending", 0, 0, 0, 0,
        )

    def test_revenue_round_trips_exactly_in_cents(self, session):
        card = _card(session)
        largest = reais_to_cents("9999999999999999.99")
        assert largest == MAX_BID_REVENUE_CENTS
        session.add(CardBidDetails(card_id=card.id, revenue_current_year_cents=largest, revenue_next_year_cents=None))
        session.commit()
        session.expire_all()
        loaded = session.query(CardBidDetails).one()
        assert loaded.revenue_current_year_cents == 999999999999999999
        assert str(cents_to_reais(loaded.revenue_current_year_cents)) == "9999999999999999.99"
        assert loaded.revenue_next_year_cents is None  # not informed, not 0
        assert loaded.revenue_currency == "BRL"

    def test_revenue_is_not_the_cards_deal_value(self, session):
        card = _card(session)
        session.add(CardBidDetails(card_id=card.id, revenue_current_year_cents=123456))
        session.commit()
        session.refresh(card)
        assert card.deal_value is None and card.currency is None


class TestSchemaCheck:
    def test_states(self, tmp_path):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        assert _state(db) == (ABSENT, [])

        _run_sql(db, ddl()[0])  # import_batches only
        state, errors = _state(db)
        assert state == PARTIAL
        assert errors == ["card_bid_details is missing", "card_source_refs is missing", "card_import_changes is missing"]

    @pytest.mark.parametrize("mutation, expected_error", [
        ("DROP INDEX ix_card_bid_details_card_id", "missing index"),
        ("CREATE INDEX extra ON card_source_refs (source_code)", "unexpected index"),
        ("ALTER TABLE card_bid_details ADD COLUMN revenue_current_year NUMERIC(18, 2)", "is not in the model"),
        ("ALTER TABLE card_bid_details DROP COLUMN close_reason_note", "close_reason_note is missing"),
    ])
    def test_incompatible_schemas_are_named(self, tmp_path, mutation, expected_error):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        _run_sql(db, mutation)
        state, errors = _state(db)
        assert state == INCOMPATIBLE
        assert any(expected_error in error for error in errors)

    def test_wrong_column_type_and_foreign_key_rule_are_incompatible(self, tmp_path):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        statements = [
            s.replace("revenue_current_year_cents INTEGER", "revenue_current_year_cents NUMERIC(18, 2)")
            .replace("REFERENCES import_batches (id) ON DELETE SET NULL", "REFERENCES import_batches (id) ON DELETE CASCADE")
            for s in ddl()
        ]
        _run_sql(db, *statements)
        state, errors = _state(db)
        assert state == INCOMPATIBLE
        assert any("revenue_current_year_cents" in e and "NUMERIC" in e for e in errors)
        assert any("foreign key" in e and "CASCADE" in e for e in errors)


class TestMigration:
    def test_database_must_be_named(self, capsys):
        with pytest.raises(SystemExit) as exc:
            migration.main([])
        assert exc.value.code == 2
        assert "--database" in capsys.readouterr().err

    def test_dry_run_changes_nothing(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        before = _sha(db)
        assert migration.main(["--database", str(db)]) == 0
        out = capsys.readouterr().out
        assert "nothing was changed" in out and "1 cards" in out
        assert _sha(db) == before
        assert not _backups(tmp_path)

    def test_apply_creates_empty_tables_backs_up_and_preserves_rows(self, tmp_path):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        original = _dump(db)
        conn = sqlite3.connect(db)
        cards_before = conn.execute("SELECT * FROM cards").fetchall()
        events_before = conn.execute("SELECT * FROM card_stage_events").fetchall()
        conn.close()

        assert migration.main(["--database", str(db), "--apply"]) == 0

        assert _state(db) == (COMPLETE, [])
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT * FROM cards").fetchall() == cards_before
        assert conn.execute("SELECT * FROM card_stage_events").fetchall() == events_before
        for table in IMPORT_TABLE_NAMES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
        [backup] = _backups(tmp_path)
        assert backup.name.startswith("old-pre-import-tables-")
        assert _dump(backup) == original

    def test_card_edit_history_is_not_touched(self, tmp_path):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        _run_sql(db, "INSERT INTO card_edit_history (card_id, user_id, changes, created_at) "
                     "VALUES (1, 1, '{\"title\": {\"old\": \"a\", \"new\": \"b\"}}', '2026-01-01')")
        conn = sqlite3.connect(db)
        schema_before = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'card_edit_history'").fetchone()
        rows_before = conn.execute("SELECT * FROM card_edit_history").fetchall()
        conn.close()
        assert migration.main(["--database", str(db), "--apply"]) == 0
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name = 'card_edit_history'").fetchone() == schema_before
        assert conn.execute("SELECT * FROM card_edit_history").fetchall() == rows_before
        assert "source" not in {r[1] for r in conn.execute("PRAGMA table_info(card_edit_history)")}
        conn.close()

    def test_migrated_database_starts(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        assert migrate_import_revert.main(["--database", str(db), "--apply"]) == 0
        engine = _engine(db)
        monkeypatch.setattr(database, "engine", engine)
        database.init_db()
        engine.dispose()

    def test_second_run(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        before = _sha(db)
        capsys.readouterr()
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert "already exist" in capsys.readouterr().err
        assert migration.main(["--database", str(db)]) == 0
        assert "nothing to do" in capsys.readouterr().out
        assert _sha(db) == before
        assert len(_backups(tmp_path)) == 1

    @pytest.mark.parametrize("statements, script", [
        (["ALTER TABLE columns DROP COLUMN stage_kind"], "migrate_stage_kind.py"),
        (["DROP TABLE card_stage_events"], "migrate_stage_events.py"),
        ([f"ALTER TABLE card_stage_events DROP COLUMN {c}" for c in
          ("deal_value_snapshot", "deal_currency_snapshot", "value_snapshot_taken")], "migrate_stage_event_values.py"),
    ])
    def test_out_of_order_is_refused(self, tmp_path, capsys, statements, script):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        _run_sql(db, *statements)
        before = _sha(db)
        for args in (["--database", str(db)], ["--database", str(db), "--apply"]):
            assert migration.main(args) == 1
            assert script in capsys.readouterr().err
        assert _sha(db) == before
        assert not _backups(tmp_path)

    def test_not_an_app_database_is_refused(self, tmp_path):
        db = tmp_path / "other.db"
        _run_sql(db, "CREATE TABLE something (id INTEGER)")
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert not _backups(tmp_path)

    def test_missing_file_is_refused_and_not_created(self, tmp_path):
        db = tmp_path / "missing.db"
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert not db.exists()

    @pytest.mark.parametrize("setup", ["partial", "incompatible"])
    def test_partial_or_incompatible_schema_is_refused(self, tmp_path, capsys, setup):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        if setup == "partial":
            _run_sql(db, ddl()[0])
        else:
            _run_sql(db, *(s.replace("filename VARCHAR NOT NULL", "filename TEXT") for s in ddl()))
        before = _sha(db)
        for args in (["--database", str(db)], ["--database", str(db), "--apply"]):
            assert migration.main(args) == 1
            assert setup in capsys.readouterr().err
        assert _sha(db) == before
        assert not _backups(tmp_path)

    def test_failure_inside_the_transaction_rolls_back(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        before = _tables(db)
        monkeypatch.setattr(migration, "ddl", lambda: ddl()[:-1])  # one index short
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert _tables(db) == before
        assert _state(db) == (ABSENT, [])
        assert len(_backups(tmp_path)) == 1  # taken before the transaction; the database is untouched


class TestInitDb:
    def _init(self, db, monkeypatch):
        engine = _engine(db)
        monkeypatch.setattr(database, "engine", engine)
        try:
            database.init_db()
        finally:
            engine.dispose()

    def test_new_database_gets_the_full_schema(self, tmp_path, monkeypatch):
        db = tmp_path / "brand-new.db"
        self._init(db, monkeypatch)
        assert _state(db) == (COMPLETE, [])

    def test_existing_database_without_tables_needs_the_migration(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        before = _sha(db)
        with pytest.raises(ImportTablesMigrationRequired, match="migrate_import_tables.py"):
            self._init(db, monkeypatch)
        assert _sha(db) == before

    def test_partly_migrated_database_is_refused(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        _run_sql(db, ddl()[0])
        before = _sha(db)
        with pytest.raises(ImportTablesSchemaError, match="partly migrated"):
            self._init(db, monkeypatch)
        assert _sha(db) == before  # create_all never ran

    def test_incompatible_database_is_refused(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _pre_part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        assert migrate_import_revert.main(["--database", str(db), "--apply"]) == 0
        _run_sql(db, "DROP INDEX ix_card_source_refs_card_id")
        with pytest.raises(ImportTablesSchemaError, match="don't match"):
            self._init(db, monkeypatch)
