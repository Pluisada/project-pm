"""Versioning foundation for optimistic concurrency (central mode): card_versions semantics, the Bid-details gap,
the version in central card responses and the stable conflict error. Disposable tmp_path databases only.

Listener-level tests drive the ORM directly on a company session (the same flush listener the routes use); the
response tests go through the company routes.
"""
from contextlib import contextmanager
from datetime import date

from test_company_admin_products import company_admin
import pytest

import card_reviews
from company_database import company_session_factory, open_company_engine
from company_routes import version_conflict
from models import Card, CardBidDetails
from test_company_card_writes import admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)


@contextmanager
def orm(rig, slug="lity"):
    """A company-tagged session on the rig's disposable database (the one the flush listeners act on)."""
    engine = open_company_engine(slug, rig.e.env)
    session = company_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def version(rig, card_id=1):
    rows = rig.sql("SELECT version FROM card_versions WHERE card_id = ?", (card_id,))
    return rows[0][0] if rows else 0


# --- version 0 and the bumping rules ---------------------------------------------------------------------------

def test_a_card_without_a_version_row_has_version_zero(rig):
    assert rig.sql("SELECT COUNT(*) FROM card_versions") == [(0,)]
    with orm(rig) as db:
        assert card_reviews.card_version(db, 1) == 0
        assert card_reviews.card_versions(db, [1, 2, 99]) == {1: 0, 2: 0, 99: 0}
        assert card_reviews.board_card_versions(db, 1) == {}


def test_a_tracked_field_change_raises_the_version_once(rig):
    with orm(rig) as db:
        db.get(Card, 1).title = "renamed"
        db.commit()
    assert version(rig) == 1
    with orm(rig) as db:
        card = db.get(Card, 1)
        card.title, card.company = "again", "ACME"  # several fields in one flush are one change
        db.commit()
    assert version(rig) == 2 and version(rig, 2) == 0


def test_a_column_change_raises_the_version(rig):
    with orm(rig) as db:
        db.get(Card, 1).column_id = 2
        db.commit()
    assert version(rig) == 1


def test_a_position_only_change_does_not_raise_the_version(rig):
    with orm(rig) as db:
        db.get(Card, 1).position = 5
        db.get(Card, 2).position = 0
        db.commit()
    assert rig.sql("SELECT position FROM cards ORDER BY id") == [(5,), (0,)]
    assert rig.sql("SELECT COUNT(*) FROM card_versions") == [(0,)]


def test_a_no_op_does_not_raise_the_version(rig):
    with orm(rig) as db:
        card = db.get(Card, 1)
        card.title = card.title
        card.next_step = None  # no details row exists and none is created for an empty value
        db.commit()
        assert db.query(CardBidDetails).count() == 0
    assert rig.sql("SELECT COUNT(*) FROM card_versions") == [(0,)]


# --- the Bid-details gap ---------------------------------------------------------------------------------------

def test_next_step_and_its_due_date_raise_the_version_without_a_member_review_intent(rig):
    with orm(rig) as db:
        db.get(Card, 1).next_step = "Call the buyer"  # creates the details row: a plain admin-style persist
        db.commit()
    assert version(rig) == 1
    with orm(rig) as db:
        db.get(Card, 1).next_step_due = date(2026, 12, 1)
        db.commit()
    assert version(rig) == 2
    with orm(rig) as db:
        db.get(Card, 1).next_step = "Send the quote"
        db.commit()
    assert version(rig) == 3
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(0,)]  # no review was created by it


def test_unchanged_bid_details_values_do_not_raise_the_version(rig):
    with orm(rig) as db:
        db.get(Card, 1).next_step = "same"
        db.commit()
    with orm(rig) as db:
        card = db.get(Card, 1)
        card.next_step = "same"
        card.bid_details.plant = card.bid_details.plant
        db.commit()
    assert version(rig) == 1


def test_import_style_bid_details_writes_cannot_bypass_the_version(rig):
    with orm(rig) as db:  # a details row built from card_id alone, as an importer may
        db.add(CardBidDetails(card_id=1, plant="Manaus", revenue_current_year_cents=12345))
        db.commit()
    assert version(rig) == 1
    with orm(rig) as db:
        db.query(CardBidDetails).filter_by(card_id=1).one().revenue_current_year_cents = 99999
        db.commit()
    assert version(rig) == 2
    with orm(rig) as db:
        db.delete(db.query(CardBidDetails).filter_by(card_id=1).one())  # the card stays; its details go
        db.commit()
    assert version(rig) == 3
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


def test_an_empty_new_bid_details_row_is_not_a_change(rig):
    with orm(rig) as db:
        db.add(CardBidDetails(card_id=1))
        db.commit()
    assert version(rig) == 0


def test_a_card_and_its_bid_details_changed_together_are_one_increment(rig):
    with orm(rig) as db:
        card = db.get(Card, 1)
        card.title = "both"
        card.next_step = "and the action"
        db.commit()
    assert version(rig) == 1
    with orm(rig) as db:  # a details row changed in the same flush as the card's column and a field
        card = db.get(Card, 1)
        card.column_id = 2
        card.next_step_due = date(2027, 1, 1)
        card.bid_details.plant = "x"
        db.commit()
    assert version(rig) == 2


def test_a_new_card_with_bid_details_and_a_deleted_card_with_details_leave_no_stray_versions(rig):
    with orm(rig) as db:
        card = Card(column_id=1, title="new", position=9)
        card.next_step = "from day one"
        db.add(card)
        db.commit()
        new_id = card.id
    assert version(rig, new_id) == 0  # a card starts at version 0, details included
    with orm(rig) as db:
        db.delete(db.get(Card, new_id))  # cascades to its details row
        db.commit()
    assert version(rig, new_id) == 0
    assert rig.sql("SELECT COUNT(*) FROM card_bid_details") == [(0,)]


def test_a_member_edit_of_next_step_is_one_increment_and_still_reviewed(rig):
    company_admin(rig)  # an Admin must exist for an Admin obligation (without one only the Super Admin reviews)
    uid, token = member(rig)
    rig.assign(1, uid)
    r = rig.call("put", "/cards/1", token, json={"title": "t", "next_step": "do it"})
    assert r.status_code == 200, r.text
    assert version(rig) == 1 and r.json()["version"] == 1  # the review intent and the details row share one bump
    assert rig.sql("SELECT reviewer_role, card_version FROM card_review_obligations ORDER BY reviewer_role") == [
        ("admin", 1), ("super_admin", 1)]


def test_a_bid_details_change_moves_a_pending_review_to_the_new_version(rig):
    company_admin(rig)
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", token, json={"title": "t"}).status_code == 200
    assert rig.sql("SELECT card_version FROM card_review_obligations") == [(1,), (1,)]
    r = rig.call("put", "/cards/1", admin(rig), json={"next_step": "admin action"})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert rig.sql("SELECT card_version FROM card_review_obligations") == [(2,), (2,)]


def test_a_position_only_move_leaves_versions_and_pending_reviews_alone(rig):
    company_admin(rig)
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", token, json={"title": "t"}).status_code == 200
    r = rig.call("put", "/cards/2/position", admin(rig), json={"column_id": 1, "position": 0})  # renumbers card 1 too
    assert r.status_code == 200
    assert version(rig, 1) == 1 and version(rig, 2) == 0 and r.json()["version"] == 0
    assert rig.sql("SELECT card_version, refresh_count FROM card_review_obligations") == [(1, 0), (1, 0)]


def test_a_rollback_leaves_no_version_behind(rig):
    with orm(rig) as db:
        card = db.get(Card, 1)
        card.title = "will not stick"
        card.next_step = "nor this"
        db.flush()
        assert card_reviews.card_version(db, 1) == 1  # raised inside the transaction...
        db.rollback()
    assert rig.sql("SELECT COUNT(*) FROM card_versions") == [(0,)]  # ...and gone with it
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]
    assert rig.sql("SELECT COUNT(*) FROM card_bid_details") == [(0,)]


def test_a_deleted_cards_id_is_never_given_to_a_new_card(rig):
    with orm(rig) as db:
        db.get(Card, 1).title = "versioned"
        db.commit()
        db.delete(db.get(Card, 2))  # the highest id
        db.commit()
        fresh = Card(column_id=1, title="fresh", position=3)
        db.add(fresh)
        db.commit()
        assert fresh.id > 2  # SQLite alone would hand out 2 again
    assert version(rig, fresh.id) == 0


# --- the version in central responses --------------------------------------------------------------------------

def test_write_responses_carry_the_version_taken_inside_the_transaction(rig):
    token = admin(rig)
    created = rig.call("post", "/cards", token, json={"column_id": 1, "title": "n", "salesperson_id": rig.e.member("s@example.com")})
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 0
    updated = rig.call("put", "/cards/1", token, json={"title": "u"})
    assert updated.json()["version"] == 1
    assert rig.call("put", "/cards/1", token, json={"title": "u"}).json()["version"] == 1  # a no-op keeps it
    moved = rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0})
    assert moved.status_code == 200 and moved.json()["version"] == 2


def test_the_board_lists_every_cards_version_including_zero(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "u"}).status_code == 200
    board = rig.client.get("/api/companies/lity/boards/1", headers={"Authorization": f"Bearer {token}"})
    assert board.status_code == 200, board.text
    cards = {c["id"]: c["version"] for col in board.json()["columns"] for c in col["cards"]}
    assert cards == {1: 1, 2: 0}


def test_the_shared_card_schema_has_no_version():
    from schemas import CardResponse, CompanyCardResponse
    assert "version" not in CardResponse.model_fields
    field = CompanyCardResponse.model_fields["version"]
    assert field.default == 0
    with pytest.raises(ValueError):
        CompanyCardResponse(**{**{n: None for n in CardResponse.model_fields}, "version": -1})


# --- the conflict error ----------------------------------------------------------------------------------------

def test_the_conflict_helper_builds_the_stable_409_body():
    exc = version_conflict(12, 5, 7)
    assert exc.status_code == 409
    assert exc.detail["code"] == "card_version_conflict"
    assert exc.detail["card_id"] == 12 and exc.detail["expected_version"] == 5 and exc.detail["current_version"] == 7
    assert set(exc.detail) == {"code", "message", "card_id", "expected_version", "current_version"}
    assert isinstance(exc.detail["message"], str) and exc.detail["message"]
