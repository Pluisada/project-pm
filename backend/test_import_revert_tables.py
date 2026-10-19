"""Part 23 (schema step): import_batch_ledgers / import_batch_items - models,
migrate_import_revert.py and the init_db() guard. Every database here is a
temp file under tmp_path."""
import hashlib
import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import database
import migrate_import_revert as migration
import migrate_import_tables
from database import ImportRevertMigrationRequired, ImportRevertSchemaError, ImportTablesMigrationRequired
from import_schema import (
    ABSENT,
    COMPLETE,
    IMPORT_TABLE_NAMES,
    INCOMPATIBLE,
    PARTIAL,
    REVERT_TABLE_NAMES,
    import_schema_state,
    revert_ddl,
    revert_schema_state,
)
from models import Base, Board, Card, Column, ImportBatch, ImportBatchItem, ImportBatchLedger, User, UserRole


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _engine(path, foreign_keys=True):
    engine = create_engine(f"sqlite:///{path}")
    if foreign_keys:
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
    return engine


def _run_sql(path, *statements):
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _dump(path):
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _revert_state(path):
    conn = sqlite3.connect(path)
    try:
        return revert_schema_state(lambda sql: conn.execute(sql).fetchall())
    finally:
        conn.close()


def _backups(tmp_path):
    return list(tmp_path.glob("*-pre-import-revert-*.db.bak"))


def _part22_db(path):
    """A database at the Part 22 schema (Bid import tables, no revert tables), with one card."""
    engine = _engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    _run_sql(
        path,
        *(f"DROP TABLE {table}" for table in reversed(REVERT_TABLE_NAMES)),
        "INSERT INTO users (id, username, password_hash, role, created_at, updated_at) "
        "VALUES (1, 'admin', 'x', 'admin', '2026-01-01', '2026-01-01')",
        "INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, 1, 'Pipeline', '2026-01-01', '2026-01-01')",
        "INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
        "VALUES (1, 1, '1st Contact', 0, 'open', '2026-01-01', '2026-01-01')",
        "INSERT INTO cards (id, column_id, title, position, deal_value, currency, created_at, updated_at) "
        "VALUES (1, 1, 'Bid lead', 0, 1000, 'USD', '2026-01-01', '2026-01-01')",
        "INSERT INTO import_batches (id, board_id, user_id, filename, file_sha256, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 'bids.xlsx', 'abc', 'completed', '2026-01-01', '2026-01-01')",
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


def _batch(db):
    user = User(username="admin", password_hash="x", role=UserRole.ADMIN.value)
    db.add(user)
    db.flush()
    board = Board(title="Pipeline", user_id=user.id)
    column = Column(board=board, title="1st Contact", position=0, stage_kind="open")
    card = Card(column=column, title="Bid lead", position=0)
    batch = ImportBatch(board=board, user_id=user.id, filename="bids.xlsx", file_sha256="abc", status="completed")
    db.add_all([board, column, card, batch])
    db.commit()
    return batch, card, user


class TestModels:
    def test_defaults_and_relationships(self, session):
        batch, card, _user = _batch(session)
        ledger = ImportBatchLedger(import_batch=batch)
        session.add(ledger)
        ledger.items.append(ImportBatchItem(card_id=card.id, board_id=1, source_key="k1", action="observed",
                                            observation="unchanged"))
        session.add(ledger)
        session.commit()
        assert batch.ledger is ledger
        assert (ledger.ledger_version, ledger.reverted_at, ledger.revert_outcome) == (1, None, None)
        assert ledger.no_commercial_changes is False
        assert (ledger.cards_deleted_count, ledger.cards_restored_count,
                ledger.moves_reverted_count, ledger.already_deleted_count) == (0, 0, 0, 0)
        [item] = ledger.items
        assert item.bid_details_created is False
        assert item.before_json is None and item.after_json is None and item.stage_event_max_id_after is None

    def test_one_ledger_per_batch(self, session):
        batch, _card, _user = _batch(session)
        session.add_all([ImportBatchLedger(import_batch_id=batch.id), ImportBatchLedger(import_batch_id=batch.id)])
        with pytest.raises(IntegrityError):
            session.commit()

    def test_one_item_per_card_and_ledger(self, session):
        batch, card, _user = _batch(session)
        ledger = ImportBatchLedger(import_batch=batch)
        session.add(ledger)
        ledger.items += [
            ImportBatchItem(card_id=card.id, board_id=1, source_key="k", action="created"),
            ImportBatchItem(card_id=card.id, board_id=1, source_key="k", action="updated"),
        ]
        session.add(ledger)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_item_survives_the_card_it_names(self, session):
        batch, card, _user = _batch(session)
        ledger = ImportBatchLedger(import_batch=batch)
        session.add(ledger)
        ledger.items.append(ImportBatchItem(card_id=card.id, board_id=1, source_key="k", action="created",
                                            after_json="{}", bid_details_created=True))
        session.add(ledger)
        session.commit()
        card_id = card.id
        session.delete(card)
        session.commit()
        item = session.query(ImportBatchItem).one()
        assert item.card_id == card_id
        assert session.query(ImportBatchLedger).count() == 1

    def test_deleting_a_batch_removes_its_ledger_and_items(self, session):
        batch, card, _user = _batch(session)
        ledger = ImportBatchLedger(import_batch=batch)
        session.add(ledger)
        ledger.items.append(ImportBatchItem(card_id=card.id, board_id=1, source_key="k", action="created"))
        session.add(ledger)
        session.commit()
        session.delete(batch)
        session.commit()
        assert session.query(ImportBatchLedger).count() == 0
        assert session.query(ImportBatchItem).count() == 0

    def test_deleting_the_user_keeps_the_ledger(self, session):
        batch, _card, user = _batch(session)
        session.add(ImportBatchLedger(import_batch=batch, reverted_by_user_id=user.id))
        session.commit()
        session.query(ImportBatchLedger).update({"reverted_by_user_id": None})  # what SET NULL does
        session.commit()
        assert session.query(ImportBatchLedger).one().reverted_by_user_id is None

    def test_foreign_key_rules(self, tmp_path):
        db = tmp_path / "fk.db"
        engine = _engine(db)
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db)
        fks = {
            table: {(row[2], row[3], row[6]) for row in conn.execute(f"PRAGMA foreign_key_list({table})")}
            for table in REVERT_TABLE_NAMES
        }
        conn.close()
        assert fks["import_batch_ledgers"] == {
            ("import_batches", "import_batch_id", "CASCADE"),
            ("users", "reverted_by_user_id", "SET NULL"),
        }
        # no foreign key to cards or boards: the item must outlive the card
        assert fks["import_batch_items"] == {("import_batch_ledgers", "ledger_id", "CASCADE")}


class TestSchemaCheck:
    def test_states(self, tmp_path):
        db = tmp_path / "old.db"
        _part22_db(db)
        assert _revert_state(db) == (ABSENT, [])

        _run_sql(db, revert_ddl()[0])  # import_batch_ledgers only
        state, errors = _revert_state(db)
        assert state == PARTIAL
        assert errors == ["import_batch_items is missing"]

    def test_groups_are_independent(self, tmp_path):
        # A Part 22 database without the Part 23 tables is complete for Part 22.
        db = tmp_path / "old.db"
        _part22_db(db)
        conn = sqlite3.connect(db)
        assert import_schema_state(lambda sql: conn.execute(sql).fetchall()) == (COMPLETE, [])
        conn.close()
        assert _revert_state(db)[0] == ABSENT

    @pytest.mark.parametrize("mutation, expected_error", [
        ("DROP INDEX ix_import_batch_items_card_id", "missing index"),
        ("CREATE INDEX extra ON import_batch_items (source_key)", "unexpected index"),
        ("ALTER TABLE import_batch_ledgers ADD COLUMN note TEXT", "is not in the model"),
        ("ALTER TABLE import_batch_items DROP COLUMN stage_event_max_id_after", "stage_event_max_id_after is missing"),
    ])
    def test_incompatible_schemas_are_named(self, tmp_path, mutation, expected_error):
        db = tmp_path / "old.db"
        _part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        _run_sql(db, mutation)
        state, errors = _revert_state(db)
        assert state == INCOMPATIBLE
        assert any(expected_error in error for error in errors)

    def test_wrong_foreign_key_rule_is_incompatible(self, tmp_path):
        db = tmp_path / "old.db"
        _part22_db(db)
        _run_sql(db, *(s.replace("REFERENCES import_batches (id) ON DELETE CASCADE",
                                 "REFERENCES import_batches (id) ON DELETE SET NULL") for s in revert_ddl()))
        state, errors = _revert_state(db)
        assert state == INCOMPATIBLE
        assert any("foreign key" in e and "SET NULL" in e for e in errors)


class TestMigration:
    def test_database_must_be_named(self, capsys):
        with pytest.raises(SystemExit) as exc:
            migration.main([])
        assert exc.value.code == 2
        assert "--database" in capsys.readouterr().err

    def test_dry_run_changes_nothing(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _part22_db(db)
        before = _sha(db)
        assert migration.main(["--database", str(db)]) == 0
        out = capsys.readouterr().out
        assert "nothing was changed" in out and "1 cards" in out and "1 import_batches" in out
        assert _sha(db) == before
        assert not _backups(tmp_path)

    def test_apply_creates_empty_tables_backs_up_and_preserves_rows(self, tmp_path):
        db = tmp_path / "old.db"
        _part22_db(db)
        original = _dump(db)
        conn = sqlite3.connect(db)
        before = {t: conn.execute(f"SELECT * FROM {t}").fetchall()
                  for t in ("cards", "card_stage_events", "import_batches", *IMPORT_TABLE_NAMES)}
        conn.close()

        assert migration.main(["--database", str(db), "--apply"]) == 0

        assert _revert_state(db) == (COMPLETE, [])
        conn = sqlite3.connect(db)
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == rows
        for table in REVERT_TABLE_NAMES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
        [backup] = _backups(tmp_path)
        assert backup.name.startswith("old-pre-import-revert-")
        assert _dump(backup) == original

    def test_part22_tables_are_not_modified(self, tmp_path):
        db = tmp_path / "old.db"
        _part22_db(db)
        conn = sqlite3.connect(db)
        sql_before = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN (%s) OR tbl_name IN (%s) ORDER BY name"
            % ((",".join("?" * len(IMPORT_TABLE_NAMES)),) * 2), IMPORT_TABLE_NAMES * 2).fetchall()
        conn.close()
        assert migration.main(["--database", str(db), "--apply"]) == 0
        conn = sqlite3.connect(db)
        assert conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN (%s) OR tbl_name IN (%s) ORDER BY name"
            % ((",".join("?" * len(IMPORT_TABLE_NAMES)),) * 2), IMPORT_TABLE_NAMES * 2).fetchall() == sql_before
        conn.close()

    def test_migrated_database_starts(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        engine = _engine(db)
        monkeypatch.setattr(database, "engine", engine)
        database.init_db()
        engine.dispose()

    def test_second_run(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        before = _sha(db)
        capsys.readouterr()
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert "already exist" in capsys.readouterr().err
        assert migration.main(["--database", str(db)]) == 0
        assert "nothing to do" in capsys.readouterr().out
        assert _sha(db) == before
        assert len(_backups(tmp_path)) == 1

    def test_part22_tables_are_required(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _part22_db(db)
        _run_sql(db, *(f"DROP TABLE {table}" for table in reversed(IMPORT_TABLE_NAMES)))
        before = _sha(db)
        for args in (["--database", str(db)], ["--database", str(db), "--apply"]):
            assert migration.main(args) == 1
            assert "migrate_import_tables.py" in capsys.readouterr().err
        assert _sha(db) == before
        assert not _backups(tmp_path)

    def test_part22_tables_must_match_the_models(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _part22_db(db)
        _run_sql(db, "DROP INDEX ix_card_source_refs_card_id")
        before = _sha(db)
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert "migrate_import_tables.py" in capsys.readouterr().err
        assert _sha(db) == before
        assert not _backups(tmp_path)

    @pytest.mark.parametrize("statements, script", [
        (["ALTER TABLE columns DROP COLUMN stage_kind"], "migrate_stage_kind.py"),
        (["DROP TABLE card_stage_events"], "migrate_stage_events.py"),
    ])
    def test_out_of_order_is_refused(self, tmp_path, capsys, statements, script):
        db = tmp_path / "old.db"
        _part22_db(db)
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
        _part22_db(db)
        if setup == "partial":
            _run_sql(db, revert_ddl()[0])
        else:
            _run_sql(db, *(s.replace("source_key VARCHAR NOT NULL", "source_key TEXT") for s in revert_ddl()))
        before = _sha(db)
        for args in (["--database", str(db)], ["--database", str(db), "--apply"]):
            assert migration.main(args) == 1
            assert setup in capsys.readouterr().err
        assert _sha(db) == before
        assert not _backups(tmp_path)

    def test_failure_inside_the_transaction_rolls_back(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part22_db(db)
        before = _tables(db)
        monkeypatch.setattr(migration, "revert_ddl", lambda: revert_ddl()[:-1])  # one index short
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert _tables(db) == before
        assert _revert_state(db) == (ABSENT, [])
        assert len(_backups(tmp_path)) == 1  # taken before the transaction; the database is untouched

    def test_data_changed_during_the_transaction_rolls_back(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part22_db(db)
        real = migration.revert_ddl

        def ddl_and_touch_a_row():
            return real() + ["UPDATE cards SET title = 'changed'"]

        monkeypatch.setattr(migration, "revert_ddl", ddl_and_touch_a_row)
        assert migration.main(["--database", str(db), "--apply"]) == 1
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT title FROM cards").fetchone()[0] == "Bid lead"
        conn.close()
        assert _revert_state(db) == (ABSENT, [])


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
        assert _revert_state(db) == (COMPLETE, [])

    def test_part22_database_needs_the_migration(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part22_db(db)
        before = _sha(db)
        with pytest.raises(ImportRevertMigrationRequired, match="migrate_import_revert.py"):
            self._init(db, monkeypatch)
        assert _sha(db) == before  # create_all never ran

    def test_database_without_part22_still_asks_for_part22_first(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part22_db(db)
        _run_sql(db, *(f"DROP TABLE {table}" for table in reversed(IMPORT_TABLE_NAMES)))
        with pytest.raises(ImportTablesMigrationRequired):
            self._init(db, monkeypatch)

    def test_partly_migrated_database_is_refused(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part22_db(db)
        _run_sql(db, revert_ddl()[0])
        before = _sha(db)
        with pytest.raises(ImportRevertSchemaError, match="partly migrated"):
            self._init(db, monkeypatch)
        assert _sha(db) == before

    def test_incompatible_database_is_refused(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        _run_sql(db, "DROP INDEX ix_import_batch_items_card_id")
        with pytest.raises(ImportRevertSchemaError, match="don't match"):
            self._init(db, monkeypatch)

    def test_migrated_database_is_accepted_and_unchanged_by_init(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part22_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        before = _dump(db)
        self._init(db, monkeypatch)
        assert _dump(db) == before
