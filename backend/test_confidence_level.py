"""cards.confidence_level, Stage 1: model, API validation and persistence.

In-memory SQLite only. The field is the sales team's own LOW / MEDIUM / HIGH assessment; NULL = not assigned.
It is unrelated to the Bid report's imported confidence, which these tests also show is never copied into it.
"""
import pytest
from pydantic import ValidationError

from lead_options import CONFIDENCE_LEVELS
from models import Card, CardBidDetails, CardSourceRef
from schemas import CardCreate, CardResponse, CardUpdate
from test_leads import auth, board, card_id, client, test_db, tokens, update  # noqa: F401  (fixtures/helpers)


def read_card(client, token, board, card_id):
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(token)).json()
    return next(c for col in detail["columns"] for c in col["cards"] if c["id"] == card_id)


def test_the_allowed_values_are_exactly_low_medium_high():
    assert CONFIDENCE_LEVELS == ["LOW", "MEDIUM", "HIGH"]


@pytest.mark.parametrize("value", ["LOW", "MEDIUM", "HIGH", None])
def test_update_schema_accepts_the_three_levels_and_null(value):
    assert CardUpdate(confidence_level=value).model_dump(exclude_unset=True) == {"confidence_level": value}


@pytest.mark.parametrize("value", [
    "low", "Low", "lOW", "medium", "Medium", "high", "High", "HIGH ", " HIGH", "", "VERY HIGH", "MED", "0", "50", 50, 1,
    True, ["HIGH"], {"level": "HIGH"},
])
def test_update_schema_rejects_everything_else(value):
    with pytest.raises(ValidationError):
        CardUpdate(confidence_level=value)


def test_a_new_card_is_not_assigned_and_the_create_schema_has_no_level(client, tokens, board, test_db):
    assert "confidence_level" not in CardCreate.model_fields  # set afterwards through an edit, like the other lead fields
    column_id = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()["columns"][0]["id"]
    created = client.post(f"/api/boards/{board['id']}/cards", json={"column_id": column_id, "title": "Fresh"},
                          headers=auth(tokens["admin"]))
    assert created.status_code == 201 and created.json()["confidence_level"] is None
    assert test_db.query(Card).filter(Card.id == created.json()["id"]).one().confidence_level is None


@pytest.mark.parametrize("value", CONFIDENCE_LEVELS)
def test_level_round_trips_through_the_api_and_the_database(client, tokens, board, card_id, test_db, value):
    response = update(client, tokens["admin"], board, card_id, {"confidence_level": value})
    assert response.status_code == 200 and response.json()["confidence_level"] == value
    test_db.expire_all()
    assert test_db.get(Card, card_id).confidence_level == value
    assert read_card(client, tokens["admin"], board, card_id)["confidence_level"] == value


def test_an_existing_card_can_be_changed_cleared_and_left_alone(client, tokens, board, card_id, test_db):
    update(client, tokens["admin"], board, card_id, {"confidence_level": "LOW"})
    assert update(client, tokens["admin"], board, card_id, {"title": "Other edit"}).json()["confidence_level"] == "LOW"  # omitted: kept
    assert update(client, tokens["admin"], board, card_id, {"confidence_level": "HIGH"}).json()["confidence_level"] == "HIGH"
    assert update(client, tokens["admin"], board, card_id, {"confidence_level": None}).json()["confidence_level"] is None  # cleared
    test_db.expire_all()
    assert test_db.get(Card, card_id).confidence_level is None


@pytest.mark.parametrize("value", ["low", "Medium", "high", "HIGH ", "", "MAYBE", 3, True])
def test_the_api_rejects_invalid_levels_and_changes_nothing(client, tokens, board, card_id, test_db, value):
    update(client, tokens["admin"], board, card_id, {"confidence_level": "MEDIUM"})
    assert update(client, tokens["admin"], board, card_id, {"confidence_level": value}).status_code == 422
    test_db.expire_all()
    assert test_db.get(Card, card_id).confidence_level == "MEDIUM"


def test_the_response_schema_carries_the_field_and_defaults_to_null():
    assert "confidence_level" in CardResponse.model_fields
    assert CardResponse.model_fields["confidence_level"].default is None


def test_the_obsolete_bid_confidence_columns_no_longer_exist(client, tokens, board, card_id, test_db):
    assert not hasattr(CardBidDetails, "confidence") and not hasattr(CardSourceRef, "source_confidence")
    test_db.add(CardBidDetails(card_id=card_id, plant="Manaus"))
    test_db.add(CardSourceRef(card_id=card_id, source_system="bid", source_key="k", source_code="000101-01"))
    test_db.commit()
    card = read_card(client, tokens["admin"], board, card_id)
    assert card["confidence_level"] is None and "confidence" not in card["bid_details"]
    assert card["source_ref"] == {"source_code": "000101-01"}


def test_member_permissions_are_unchanged_for_the_new_field(client, tokens, board, card_id, test_db):
    # a Sales Member cannot touch a card that is not theirs, whatever the field
    assert update(client, tokens["member"], board, card_id, {"confidence_level": "HIGH"}).status_code == 403
    test_db.expire_all()
    assert test_db.get(Card, card_id).confidence_level is None
