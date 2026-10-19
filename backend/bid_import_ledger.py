"""Canonical snapshots for the import ledger (Part 23, step 2).

A snapshot is the commercial state of one imported card, built from the real
table columns (not a hand-kept field list) and serialised as canonical JSON:
sorted keys, ensure_ascii=False, dates as ISO strings, floats as JSON floats
(repr-stable). Excluded: card id/position/created_at/updated_at (position
changes with unrelated moves) and the timestamps of the two child rows.

    {"card": {...incl. column_id}, "bid_details": {"present": true, "id": 7, ...}
              or {"present": false}, "source_ref": {"id": 3, ... , "last_import_batch_id": 5, ...}}
"""
import json
from datetime import date, datetime

from models import Card, CardBidDetails, CardSourceRef

LEDGER_VERSION = 1
_CARD_SKIP = {"id", "position", "created_at", "updated_at"}
_CHILD_SKIP = {"created_at", "updated_at"}


def _json_value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _row_dict(obj, skip) -> dict:
    return {
        column.key: _json_value(getattr(obj, column.key))
        for column in obj.__table__.columns
        if column.key not in skip
    }


def card_snapshot(card: Card) -> dict:
    details, ref = card.bid_details, card.source_ref
    return {
        "card": _row_dict(card, _CARD_SKIP),
        "bid_details": {"present": True, **_row_dict(details, _CHILD_SKIP)} if details is not None else {"present": False},
        "source_ref": _row_dict(ref, _CHILD_SKIP) if ref is not None else None,
    }


def snapshot_json(snapshot: dict) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
