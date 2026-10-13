"""Sales report aggregation over a board's lead cards.

Aggregated in Python: boards are small, and the groupings (per currency,
won/lost/open by the column's stage_kind) are clearer here than in SQL.
"""
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session, selectinload

from lead_options import STAGE_LOST, STAGE_WON
from models import Card, Column

CLOSING_SOON_DAYS = 30


def _add(totals: dict, card: Card) -> None:
    if card.deal_value is not None and card.currency:
        totals[card.currency] = totals.get(card.currency, 0) + card.deal_value


def _group(cards: list[Card], key_of) -> list[dict]:
    groups = defaultdict(lambda: {"open_count": 0, "open_totals": {}, "won_count": 0, "won_totals": {}})
    for card in cards:
        stage_kind = card.column.stage_kind
        if stage_kind == STAGE_LOST:
            continue
        group = groups[key_of(card)]
        kind = "won" if stage_kind == STAGE_WON else "open"
        group[f"{kind}_count"] += 1
        _add(group[f"{kind}_totals"], card)
    return [{"key": key, **values} for key, values in sorted(groups.items())]


def build_board_report(
    db: Session,
    board_id: int,
    salesperson_id: Optional[int] = None,
    product_line: Optional[str] = None,
    today: Optional[date] = None,
) -> dict:
    today = today or date.today()
    columns = (
        db.query(Column)
        .filter(Column.board_id == board_id)
        .order_by(Column.position)
        .all()
    )
    query = (
        db.query(Card)
        .join(Column)
        .filter(Column.board_id == board_id)
        .options(selectinload(Card.salesperson), selectinload(Card.column))
    )
    if salesperson_id is not None:
        query = query.filter(Card.salesperson_id == salesperson_id)
    if product_line is not None:
        query = query.filter(Card.product_line == product_line)
    cards = query.all()

    stages = {
        col.id: {"column_id": col.id, "title": col.title, "stage_kind": col.stage_kind, "count": 0, "totals": {}}
        for col in columns
    }
    kpis = {"open_count": 0, "open_totals": {}, "won_count": 0, "won_totals": {}, "lost_count": 0}
    closing_soon = []
    horizon = today + timedelta(days=CLOSING_SOON_DAYS)

    for card in cards:
        stage = stages[card.column_id]
        stage["count"] += 1
        _add(stage["totals"], card)

        # Classified by the column's stage_kind, never its (renameable) title.
        if card.column.stage_kind == STAGE_WON:
            kpis["won_count"] += 1
            _add(kpis["won_totals"], card)
        elif card.column.stage_kind == STAGE_LOST:
            kpis["lost_count"] += 1
        else:
            kpis["open_count"] += 1
            _add(kpis["open_totals"], card)
            if card.expected_close_date and card.expected_close_date <= horizon:
                closing_soon.append({
                    "card_id": card.id,
                    "title": card.title,
                    "company": card.company,
                    "stage": card.column.title,
                    "salesperson_name": card.salesperson_name,
                    "deal_value": card.deal_value,
                    "currency": card.currency,
                    "expected_close_date": card.expected_close_date,
                    "overdue": card.expected_close_date < today,
                })

    decided = kpis["won_count"] + kpis["lost_count"]
    kpis["win_rate"] = kpis["won_count"] / decided if decided else None
    closing_soon.sort(key=lambda lead: lead["expected_close_date"])

    return {
        "kpis": kpis,
        "stages": list(stages.values()),
        "by_salesperson": _group(cards, lambda c: c.salesperson_name or "Unassigned"),
        "by_product_line": _group(cards, lambda c: c.product_line or "Not set"),
        "by_source": _group(cards, lambda c: c.lead_source or "Not set"),
        "closing_soon": closing_soon,
    }
