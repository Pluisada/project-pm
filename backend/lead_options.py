"""Allowed values for sales lead fields and the pipeline stages.

Single source for the schemas (validation), the report (won/lost), seeding
and the AI system prompt.
"""
from typing import Literal

STAGES = ["1st Contact", "F2F Meeting", "RFI", "Proposal", "Award", "Lost"]
WON_STAGE = "Award"
LOST_STAGE = "Lost"

CURRENCIES = ["EUR", "USD", "CNY", "INR", "BRL", "VND"]
LEAD_SOURCES = ["Website", "Referral", "Trade show", "Cold outreach", "Existing customer", "Other"]
PRODUCT_LINES = ["Chargers", "Power supplies"]

Currency = Literal[tuple(CURRENCIES)]
LeadSource = Literal[tuple(LEAD_SOURCES)]
ProductLine = Literal[tuple(PRODUCT_LINES)]
