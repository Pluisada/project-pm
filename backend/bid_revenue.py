"""Bid revenue totals: the Bid report's annual revenue, kept apart from Deal Value.

Deal Value (cards.deal_value / deal_value_next_year) is the manually entered forecast and is always USD. Bid revenue
(card_bid_details.revenue_*_cents) is the Bid report's annual revenue in integer cents and in its own
`revenue_currency` (BRL for the Salcomp report). The two are never added, converted or copied into each other.

Rules, per measure ("current" = revenue_current_year_cents, "next" = revenue_next_year_cents):
  - NULL means "not informed": the card is left out of that measure and out of its count, never counted as 0.
  - The two years are separate measures and are never summed together.
  - Totals are grouped by revenue_currency; currencies are never mixed or converted.
  - A card without bid details contributes nothing.
"""
from collections.abc import Iterable


def new_revenue() -> dict:
    return {}


def add_revenue(revenue: dict, bid_details) -> None:
    """Add one card's bid details (or None) to {currency: {current_cents, current_count, next_cents, next_count}}."""
    if bid_details is None:
        return
    current, following = bid_details.revenue_current_year_cents, bid_details.revenue_next_year_cents
    if current is None and following is None:
        return
    row = revenue.setdefault(bid_details.revenue_currency or "BRL",
                             {"current_cents": 0, "current_count": 0, "next_cents": 0, "next_count": 0})
    if current is not None:
        row["current_cents"] += current
        row["current_count"] += 1
    if following is not None:
        row["next_cents"] += following
        row["next_count"] += 1


def revenue_rows(revenue: dict) -> list[dict]:
    return [{"currency": currency, **values} for currency, values in sorted(revenue.items())]


def revenue_for(bid_details: Iterable) -> list[dict]:
    revenue = new_revenue()
    for details in bid_details:
        add_revenue(revenue, details)
    return revenue_rows(revenue)
