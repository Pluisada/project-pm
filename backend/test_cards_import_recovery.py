"""Recovery of cards missing from a board from a workbook the at-most-once ledger already refuses: explicit, narrow,
bound to its own preview. Disposable tmp_path databases only."""
import json

import pytest

import cards_excel_import
from test_cards_import_unmatched_ids import OPTION, board_cards, exported, post
from test_company_card_writes import admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)
from test_company_cards_excel_import import book, counts, row

RECOVER = {"recover_missing_cards": "true"}


def preview(rig, token, content, mode=None, board=1):
    return post(rig, token, "preview", content, board, **(mode or {}))


def confirm(rig, token, content, digest, mode=None, board=1):
    return post(rig, token, "confirm", content, board, confirm="true", plan_digest=digest, **(mode or {}))


def state(rig):
    return {t: rig.sql(f"SELECT * FROM {t} ORDER BY 1") for t in
            ("cards", "card_versions", "audit_events", "card_stage_events", "card_excel_imports", "undo_entries")}


def delete_all(rig, token, ids):
    for card_id in ids:
        assert rig.call("delete", f"/cards/{card_id}", token).status_code == 204


@pytest.fixture
def restored_once(rig):
    """The user's history: export two cards, delete them, import the export with the unmatched-id choice (cards 3 and 4,
    one ledger row), then delete those two as well. The same workbook is now refused by the ledger."""
    token = admin(rig)
    content = exported(rig, token)
    delete_all(rig, token, (1, 2))
    body = preview(rig, token, content, OPTION).json()
    assert confirm(rig, token, content, body["plan_digest"], OPTION).status_code == 201
    assert [i for i, _ in rig.sql("SELECT id, title FROM cards ORDER BY id")] == [3, 4]
    return rig, token, content


def test_an_ordinary_repeat_stays_blocked_and_recovery_is_not_offered_while_the_restored_cards_exist(restored_once):
    rig, token, content = restored_once
    body = preview(rig, token, content, OPTION).json()
    assert body["blocked_reason"] == "already_imported" and body["confirmable"] is False
    assert body["recovery"]["available"] is False and body["recovery"]["eligible"] == 0  # both rows were restored as cards 3 and 4
    before = state(rig)
    for mode in (OPTION, None):
        r = confirm(rig, token, content, body["plan_digest"], mode)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "already_imported"
    attempt = preview(rig, token, content, RECOVER).json()
    assert attempt["confirmable"] is False and attempt["blocked_reason"] == "recovery_unavailable"
    assert confirm(rig, token, content, attempt["plan_digest"], RECOVER).status_code == 409
    assert state(rig) == before


def test_the_user_scenario_deleted_cards_are_restored_with_fresh_ids_after_an_explicit_recovery(restored_once):
    rig, token, content = restored_once
    delete_all(rig, token, (3, 4))
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(0,)]
    ordinary = preview(rig, token, content, OPTION).json()
    assert ordinary["blocked_reason"] == "already_imported" and ordinary["recovery"] == {
        "mode": False, "available": True, "eligible": 2, "skipped": {"exists": 0, "empty_id": 0, "already_restored": 0}, "message": ""}
    before = state(rig)
    body = preview(rig, token, content, RECOVER).json()
    assert body["confirmable"] is True and body["blocked_reason"] is None and body["recovery"]["mode"] is True
    assert body["summary"]["creates"] == 2 and body["summary"]["updates"] == 0 and body["summary"]["skipped"] == 0
    assert [r["action"] for r in body["rows"]] == ["create", "create"] and body["creates_from_unmatched_ids"] == 2
    assert state(rig) == before  # the preview wrote nothing
    # ordinary confirmation of the same preview digest is still refused, and a confirmation without the mode is stale
    assert confirm(rig, token, content, body["plan_digest"], OPTION).status_code == 409
    done = confirm(rig, token, content, body["plan_digest"], RECOVER)
    assert done.status_code == 201 and done.json()["created"] == 2 and done.json()["updated"] == 0
    restored = rig.sql("SELECT id, title FROM cards ORDER BY id")
    assert [t for _, t in restored] == ["card 1", "card 2"] and all(i > 4 for i, _ in restored)  # never 1, 2, 3 or 4
    rows = rig.sql("SELECT repeat_guard, fingerprint, created_count FROM card_excel_imports ORDER BY id")
    assert len(rows) == 2 and rows[1][0] != rows[1][1] and rows[0][0] == rows[0][1] and rows[1][2] == 2  # its own guard; the old row untouched


def test_a_completed_recovery_cannot_be_replayed_or_repeated(restored_once):
    rig, token, content = restored_once
    delete_all(rig, token, (3, 4))
    digest = preview(rig, token, content, RECOVER).json()["plan_digest"]
    assert confirm(rig, token, content, digest, RECOVER).status_code == 201
    after = state(rig)
    assert confirm(rig, token, content, digest, RECOVER).status_code == 409  # the same confirmation again: stale
    again = preview(rig, token, content, RECOVER).json()
    assert again["confirmable"] is False and again["blocked_reason"] == "recovery_unavailable"  # the restored cards exist
    assert state(rig) == after
    # after deleting the restored cards a new, deliberate recovery is possible (and gets its own guard)
    delete_all(rig, token, [i for i, _ in rig.sql("SELECT id, title FROM cards")])
    third = preview(rig, token, content, RECOVER).json()
    assert third["confirmable"] is True
    assert confirm(rig, token, content, third["plan_digest"], RECOVER).status_code == 201
    assert len({g for (g,) in rig.sql("SELECT repeat_guard FROM card_excel_imports")}) == 3


def test_a_mixed_workbook_recovers_only_the_unmatched_id_rows(rig):
    token = admin(rig)
    content = book([row(card_id=1, title="Renamed by the workbook"), row(card_id=99, title="Gone card"), row(title="No id")])
    body = preview(rig, token, content, OPTION).json()
    assert confirm(rig, token, content, body["plan_digest"], OPTION).status_code == 201  # updates card 1, creates two cards
    created = [i for i, t in rig.sql("SELECT id, title FROM cards") if t in ("Gone card", "No id")]
    delete_all(rig, token, created)
    assert rig.call("put", "/cards/1", token, json={"title": "Edited since"}).status_code == 200  # must survive the recovery

    rec = preview(rig, token, content, RECOVER).json()
    by_row = {r["row"]: r for r in rec["rows"]}
    assert [by_row[n]["action"] for n in (2, 3, 4)] == ["skipped", "create", "skipped"]
    assert [by_row[n]["skip_reason"] for n in (2, 4)] == ["exists", "empty_id"]
    assert rec["summary"]["creates"] == 1 and rec["summary"]["updates"] == 0 and rec["summary"]["skipped"] == 2
    assert rec["recovery"]["skipped"] == {"exists": 1, "empty_id": 1, "already_restored": 0}
    assert confirm(rig, token, content, rec["plan_digest"], RECOVER).status_code == 201
    titles = [t for _, t in rig.sql("SELECT id, title FROM cards ORDER BY id")]
    assert titles.count("Gone card") == 1 and "No id" not in titles          # only the unmatched-id row came back
    assert "Edited since" in titles and "Renamed by the workbook" not in titles  # the existing card was neither updated nor copied


def test_recovery_is_refused_when_an_eligible_id_now_exists_or_anything_else_changed_since_the_preview(restored_once):
    rig, token, content = restored_once
    delete_all(rig, token, (3, 4))
    rec = preview(rig, token, content, RECOVER).json()
    before = state(rig)
    # a card with the source id 1 appears on the board: that row would now match it (an update), never a recreation
    rig.sql("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) VALUES (1, 1, 'back again', 0, '2026-01-01', '2026-01-01')")
    r = confirm(rig, token, content, rec["plan_digest"], RECOVER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "preview_stale"
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("back again",)] and rig.sql("SELECT COUNT(*) FROM cards") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM card_excel_imports") == [(1,)]
    rig.sql("DELETE FROM cards WHERE id = 1")
    assert state(rig) == before
    # any card allocation after the preview makes it stale (the highest id moved), even with the same cards on the board
    owner, _ = member(rig, "owner@example.com")
    rig.sql("INSERT OR IGNORE INTO users (id, username, created_at, updated_at) VALUES (?, 'owner', '2026-01-01', '2026-01-01')", (owner,))
    made = rig.call("post", "/cards", token, json={"column_id": 1, "title": "x", "salesperson_id": owner})
    assert made.status_code == 201, made.text
    assert rig.call("delete", f"/cards/{made.json()['id']}", token).status_code == 204
    r2 = confirm(rig, token, content, rec["plan_digest"], RECOVER)
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "preview_stale"
    digest2 = preview(rig, token, content, RECOVER).json()["plan_digest"]
    assert confirm(rig, token, content, digest2, RECOVER).status_code == 201  # a fresh preview is accepted


def test_legacy_ledger_rows_without_source_ids_block_recovery_while_their_cards_exist(restored_once):
    rig, token, content = restored_once
    changes = json.loads(rig.sql("SELECT changes_json FROM card_excel_imports")[0][0])
    changes.pop("created_from")  # an entry written before source ids were recorded
    rig.sql("UPDATE card_excel_imports SET changes_json = ?", (json.dumps(changes),))
    blocked = preview(rig, token, content, RECOVER).json()
    assert blocked["blocked_reason"] == "recovery_unavailable" and blocked["recovery"]["available"] is False
    assert "earlier import of this workbook still exist" in blocked["recovery"]["message"]
    delete_all(rig, token, (3, 4))
    ok = preview(rig, token, content, RECOVER).json()
    assert ok["confirmable"] is True and ok["summary"]["creates"] == 2


def test_recovery_needs_a_previous_import(rig):
    token = admin(rig)
    content = book([row(card_id=99, title="x")])
    first = preview(rig, token, content, RECOVER).json()
    assert first["blocked_reason"] == "recovery_not_applicable" and first["confirmable"] is False
    assert confirm(rig, token, content, first["plan_digest"], RECOVER).status_code == 409
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


def test_an_invalid_row_still_blocks_a_recovery(rig):
    token = admin(rig)
    content = book([row(card_id=98, title="a", stage="Second")])
    assert confirm(rig, token, content, preview(rig, token, content, OPTION).json()["plan_digest"], OPTION).status_code == 201
    delete_all(rig, token, [i for i, t in rig.sql("SELECT id, title FROM cards") if t == "a"])
    rig.sql("UPDATE columns SET title = 'Renamed stage' WHERE id = 2")  # the row's stage no longer exists on the board
    body = preview(rig, token, content, RECOVER).json()
    assert body["blocked_reason"] == "validation_errors" and body["confirmable"] is False and body["summary"]["invalid"] == 1
    before = state(rig)
    assert confirm(rig, token, content, body["plan_digest"], RECOVER).status_code == 422
    assert state(rig) == before


def test_a_failed_recovery_changes_nothing_and_can_be_retried(restored_once, monkeypatch):
    rig, token, content = restored_once
    delete_all(rig, token, (3, 4))
    digest = preview(rig, token, content, RECOVER).json()["plan_digest"]
    before = state(rig)

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cards_excel_import, "audit_new_card_owner", explode)
    r = confirm(rig, token, content, digest, RECOVER)
    assert r.status_code == 500 and r.json()["detail"]["code"] == "import_failed"
    assert state(rig) == before  # cards, versions, audit, stage events, undo and the ledger are exactly as they were
    monkeypatch.undo()
    assert confirm(rig, token, content, digest, RECOVER).status_code == 201


def test_authorization_and_company_isolation_are_unchanged(restored_once):
    rig, token, content = restored_once
    delete_all(rig, token, (3, 4))
    _, member_token = member(rig, "m@example.com")
    before = (state(rig), rig.digest("valor-log"), rig.digest("salcomp"))
    for step, extra in (("preview", {}), ("confirm", {"confirm": "true", "plan_digest": "x"})):
        assert post(rig, member_token, step, content, 1, **RECOVER, **extra).status_code == 403
    r = rig.client.post("/api/companies/valor-log/boards/1/excel/import/preview", headers={"Authorization": f"Bearer {member_token}"},
                        files={"file": ("cards.xlsx", content, "application/octet-stream")}, data=RECOVER)
    assert r.status_code == 403
    assert (state(rig), rig.digest("valor-log"), rig.digest("salcomp")) == before
    # nothing in the answers tells where an unmatched id lives: the same fields whatever the id was
    body = preview(rig, token, content, RECOVER).json()
    assert {w["code"] for r in body["rows"] for w in r["warnings"]} == {"source_id_new_card"}
