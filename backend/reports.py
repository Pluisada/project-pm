"""Sales report aggregation over a board's lead cards.

Aggregated in Python: boards are small, and the groupings (per currency,
won/lost/open by the column's stage_kind) are clearer here than in SQL.
"""
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session, selectinload

from bid_revenue import add_revenue, new_revenue, revenue_rows
from lead_options import DEAL_CURRENCY, STAGE_LOST, STAGE_WON
from models import Card, Column, CompanyUser

CLOSING_SOON_DAYS = 30
FORMER_USER_LABEL = "Former user"


def user_labels(db: Session, user_ids: Iterable[int | None]) -> dict[int, str]:
    """Display label for each user id, read from the session's own `users` table only.

    The label is CompanyUser.username. An id with no local row maps to
    FORMER_USER_LABEL. None (no assignment) is skipped, so a caller still tells
    "unassigned" (id None, absent here) from "assigned to a missing user" (id
    present, FORMER_USER_LABEL). Ids are the key, never labels, so equal labels
    stay separate. Read-only; consults no central state and creates no rows.
    """
    ids = {user_id for user_id in user_ids if user_id is not None}
    if not ids:
        return {}
    found = dict(db.query(CompanyUser.id, CompanyUser.username).filter(CompanyUser.id.in_(ids)).all())
    return {user_id: found.get(user_id, FORMER_USER_LABEL) for user_id in ids}


def annual_amounts(card) -> Optional[tuple]:
    """(current-year, next-year, two-year total) of a card, or None when nothing
    is entered for either year. Amounts are USD whatever cards.currency holds.

    deal_value is the amount for deal_year and is NULL when never entered;
    deal_value_next_year (deal_year + 1) is never NULL, 0 meaning none. The
    current-year measure keeps skipping a card whose deal_value is NULL (it is
    unavailable, not zero); the total counts it as the next-year amount alone,
    exactly as the card shows it.
    """
    next_year = card.deal_value_next_year or 0
    if card.deal_value is None and next_year == 0:
        return None
    return card.deal_value, next_year, (card.deal_value or 0) + next_year


def new_money() -> dict:
    """Empty USD totals ({"USD": amount}) for the three measures."""
    return {"current": {}, "next": {}, "sum": {}}


def add_money(money: dict, card) -> None:
    amounts = annual_amounts(card)
    if amounts is None:
        return
    current, next_year, total = amounts
    currency = DEAL_CURRENCY
    if current is not None:
        money["current"][currency] = money["current"].get(currency, 0) + current
    money["next"][currency] = money["next"].get(currency, 0) + next_year
    money["sum"][currency] = money["sum"].get(currency, 0) + total


def money_fields(money: dict, prefix: str = "") -> dict:
    """Flatten into <prefix>totals (current year, the existing field), <prefix>next_totals, <prefix>sum_totals."""
    return {f"{prefix}totals": money["current"], f"{prefix}next_totals": money["next"], f"{prefix}sum_totals": money["sum"]}


def _group(cards: list[Card], key_of, id_of=None) -> list[dict]:
    """Breakdown rows. With id_of, rows are grouped by (key, id): equal display keys with different ids stay apart."""
    groups = defaultdict(lambda: {"open_count": 0, "open": new_money(), "won_count": 0, "won": new_money()})
    for card in cards:
        stage_kind = card.column.stage_kind
        if stage_kind == STAGE_LOST:
            continue
        group = groups[(key_of(card), id_of(card) if id_of else None)]
        kind = "won" if stage_kind == STAGE_WON else "open"
        group[f"{kind}_count"] += 1
        add_money(group[kind], card)
    return [
        {
            "key": key,
            "salesperson_id": person_id,
            "open_count": values["open_count"], **money_fields(values["open"], "open_"),
            "won_count": values["won_count"], **money_fields(values["won"], "won_"),
        }
        for (key, person_id), values in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] is not None, item[0][1] or 0))
    ]


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
        .options(selectinload(Card.column), selectinload(Card.bid_details))
    )
    if salesperson_id is not None:
        query = query.filter(Card.salesperson_id == salesperson_id)
    if product_line is not None:
        query = query.filter(Card.product_line == product_line)
    cards = query.all()
    # Unassigned (salesperson_id None) is told apart from assigned to a user with no local row (FORMER_USER_LABEL).
    labels = user_labels(db, (card.salesperson_id for card in cards))

    stages = {
        col.id: {"column_id": col.id, "title": col.title, "stage_kind": col.stage_kind, "count": 0, "money": new_money(), "bid": new_revenue()}
        for col in columns
    }
    open_money, won_money = new_money(), new_money()
    open_bid, won_bid = new_revenue(), new_revenue()
    kpis = {"open_count": 0, "won_count": 0, "lost_count": 0}
    closing_soon = []
    horizon = today + timedelta(days=CLOSING_SOON_DAYS)

    for card in cards:
        stage = stages[card.column_id]
        stage["count"] += 1
        add_money(stage["money"], card)
        add_revenue(stage["bid"], card.bid_details)

        # Classified by the column's stage_kind, never its (renameable) title.
        if card.column.stage_kind == STAGE_WON:
            kpis["won_count"] += 1
            add_money(won_money, card)
            add_revenue(won_bid, card.bid_details)
        elif card.column.stage_kind == STAGE_LOST:
            kpis["lost_count"] += 1
        else:
            kpis["open_count"] += 1
            add_money(open_money, card)
            add_revenue(open_bid, card.bid_details)
            if card.expected_close_date and card.expected_close_date <= horizon:
                closing_soon.append({
                    "card_id": card.id,
                    "title": card.title,
                    "company": card.company,
                    "stage": card.column.title,
                    "salesperson_name": labels.get(card.salesperson_id),
                    "deal_value": card.deal_value,
                    "deal_value_next_year": card.deal_value_next_year or 0,
                    "deal_value_total": (annual_amounts(card) or (None, None, None))[2],
                    "deal_year": card.deal_year,
                    "currency": DEAL_CURRENCY,
                    "expected_close_date": card.expected_close_date,
                    "overdue": card.expected_close_date < today,
                })

    kpis.update(money_fields(open_money, "open_"), **money_fields(won_money, "won_"))
    kpis.update(open_bid_revenue=revenue_rows(open_bid), won_bid_revenue=revenue_rows(won_bid))
    decided = kpis["won_count"] + kpis["lost_count"]
    kpis["win_rate"] = kpis["won_count"] / decided if decided else None
    closing_soon.sort(key=lambda lead: lead["expected_close_date"])

    return {
        "kpis": kpis,
        "stages": [
            {"column_id": st["column_id"], "title": st["title"], "stage_kind": st["stage_kind"],
             "count": st["count"], **money_fields(st["money"]), "bid_revenue": revenue_rows(st["bid"])}
            for st in stages.values()
        ],
        # Years the amounts above were entered for (cards' stored deal_year):
        # one entry normally; several mean the totals span more than one year.
        "deal_years": sorted({c.deal_year for c in cards if annual_amounts(c) is not None}),
        "by_salesperson": _group(cards, lambda c: labels.get(c.salesperson_id) or "Unassigned", lambda c: c.salesperson_id),
        "by_product_line": _group(cards, lambda c: c.product_line or "Not set"),
        "by_source": _group(cards, lambda c: c.lead_source or "Not set"),
        "closing_soon": closing_soon,
    }
