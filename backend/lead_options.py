"""Allowed values for sales lead fields and the pipeline stages.

Single source for the schemas (validation), seeding and the AI system prompt.
"""
from typing import Literal

# A column's commercial meaning is its stage_kind, never its title: titles are
# display text an admin may rename freely, and reports must not change when
# they do. Only these three kinds exist.
STAGE_OPEN = "open"
STAGE_WON = "won"
STAGE_LOST = "lost"
STAGE_KINDS = (STAGE_OPEN, STAGE_WON, STAGE_LOST)
StageKind = Literal[STAGE_KINDS]

# The default pipeline seeded on a fresh board: (display title, stage kind).
PIPELINE_STAGES = [
    ("1st Contact", STAGE_OPEN),
    ("F2F Meeting", STAGE_OPEN),
    ("RFI", STAGE_OPEN),
    ("Proposal", STAGE_OPEN),
    ("Award", STAGE_WON),
    ("Lost", STAGE_LOST),
]

# Every opportunity Deal Value (current and next year) is in US dollars: this
# app tracks bids, not the official forecast, so there is one currency and no
# conversion. Legacy currency codes in cards.currency are ignored on read and
# amounts are read as USD unchanged. Bid revenue has its own revenue_currency.
DEAL_CURRENCY = "USD"
CURRENCIES = [DEAL_CURRENCY]
LEAD_SOURCES = ["Website", "Referral", "Trade show", "Cold outreach", "Existing customer", "Other"]
PRODUCT_LINES = ["Chargers", "Power supplies"]
# The sales team's own assessment of an opportunity (cards.confidence_level); NULL means "not assigned".
# Exactly these upper-case values, nothing else.
CONFIDENCE_LEVELS = ["LOW", "MEDIUM", "HIGH"]

Currency = Literal[tuple(CURRENCIES)]
LeadSource = Literal[tuple(LEAD_SOURCES)]
ProductLine = Literal[tuple(PRODUCT_LINES)]
ConfidenceLevel = Literal[tuple(CONFIDENCE_LEVELS)]
