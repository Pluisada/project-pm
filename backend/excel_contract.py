"""Column contract of the commercial Excel import (the "Cards" workbook).

The single source of truth for the sheet name, the 15 columns (canonical order),
the forbidden columns and the validation limits. The reader consumes it; a
later exporter and template must be generated from it too, never from a second
list of headers.

Decisions (Part A):
- Header matching ignores only case and extra whitespace; column order in an
  uploaded file is free.
- `card_id` and `title` must exist as columns (a file without `card_id` is rejected, never read as new cards).
  Another contract column that is absent is a file warning and its field stays untouched on update.
- Forbidden columns (`next_step`, `next_step_due`, anything starting with
  `revenue_` or `bid_`) are a file error. Any other column that is not in the
  contract is also a file error ("unknown_column"): ignoring it would silently
  drop data that looks important.
- `card_id` identifies an existing card of the target board: blank = create a new card, filled = update that
  card (a positive whole number; whether it exists, belongs to the board and is unique in the file is checked by
  the importer, never here). The reader only validates its syntax.
- `deal_total_usd` is derived and informative, not a financial source of truth.
  The reader ignores its content entirely: it is never validated, persisted or
  compared with the annual values, and a stale or invalid total never blocks a
  row. `deal_value_usd` and `deal_value_next_year_usd` are the only financial
  inputs; the application computes the total from them. Neither the template
  nor the export carries the column any more (legacy columns, below).
- `deal_year` is informational; creation keeps the backend's own year.
- Legacy columns (`legacy=True`): `deal_total_usd`, `deal_year` and `expected_close_date`. They are still accepted on
  read, so older workbooks keep working (the first two are ignored, `expected_close_date` is still written when
  present), but they are not part of the downloadable template or the export (`HEADERS` / `OUTPUT_COLUMNS`): sales
  users enter the two annual Deal Values, the application derives the total and the year, and the cards screen no longer
  offers a close date. `next_step_due` stays forbidden as a header: the Next Action Date has its own column,
  `next_action_date`, which maps to the same card field the card screen edits.
- `contact_email` and `contact_phone` are the card's two contact fields (the one telephone field is `contact_phone`);
  they follow the same validation as the card screen.
- Context-dependent columns (`context=True`) are parsed for syntax only. The
  product catalog, the salesperson (existence, company link, eligibility) and
  the stage (board columns) must be checked at preview and again at confirm.
"""
from dataclasses import dataclass
from decimal import Decimal

from lead_options import CONFIDENCE_LEVELS, LEAD_SOURCES

SHEET_NAME = "Cards"

# Column kinds
KIND_REFERENCE = "reference"      # card_id: blank = create, a positive whole number = update that card
KIND_TEXT = "text"
KIND_MONEY = "money"              # non-negative USD, at most 2 decimals
KIND_DERIVED_MONEY = "derived_money"  # deal_total_usd: ignored on read, never stored
KIND_YEAR = "year"
KIND_CONFIDENCE = "confidence"
KIND_DATE = "date"
KIND_LEAD_SOURCE = "lead_source"
KIND_EMAIL = "email"
KIND_CONTACT_EMAIL = "contact_email"  # the card's contact address (optional, validated like the card screen)
KIND_CONTACT_PHONE = "contact_phone"

TITLE_MAX = 255
COMPANY_MAX = 255
CONTACT_MAX = 255
DETAILS_MAX = 5000
PRODUCT_LINE_MAX = 100  # CardUpdate.product_line
EMAIL_MAX = 254
CONTACT_PHONE_MAX = 40  # schemas.CONTACT_PHONE_MAX_LENGTH
STAGE_MAX = 255

MONEY_DECIMALS = Decimal("0.01")
MAX_DEAL_VALUE = Decimal("1000000000000")  # 1e12 USD per annual value
MIN_YEAR, MAX_YEAR = 1900, 2100

ALLOWED_CONFIDENCE = tuple(CONFIDENCE_LEVELS)
ALLOWED_LEAD_SOURCES = tuple(LEAD_SOURCES)


@dataclass(frozen=True)
class ColumnSpec:
    header: str
    field: str            # key in the reader's record
    kind: str
    required_column: bool = False
    required_value: bool = False
    max_length: int | None = None
    context: bool = False  # value only checkable against company/board context
    legacy: bool = False   # accepted on read, never written to the template or the export


COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("card_id", "card_id", KIND_REFERENCE, required_column=True),
    ColumnSpec("title", "title", KIND_TEXT, required_column=True, required_value=True, max_length=TITLE_MAX),
    ColumnSpec("company", "company", KIND_TEXT, max_length=COMPANY_MAX),  # the card screen's "Customer"
    ColumnSpec("contact", "contact", KIND_TEXT, max_length=CONTACT_MAX),
    ColumnSpec("contact_email", "contact_email", KIND_CONTACT_EMAIL, max_length=EMAIL_MAX),
    ColumnSpec("contact_phone", "contact_phone", KIND_CONTACT_PHONE, max_length=CONTACT_PHONE_MAX),
    ColumnSpec("details", "details", KIND_TEXT, max_length=DETAILS_MAX),
    ColumnSpec("deal_value_usd", "deal_value_usd", KIND_MONEY),
    ColumnSpec("deal_value_next_year_usd", "deal_value_next_year_usd", KIND_MONEY),
    ColumnSpec("next_action_date", "next_step_due", KIND_DATE),
    ColumnSpec("probability_level", "confidence_level", KIND_CONFIDENCE),
    ColumnSpec("product_line", "product_line", KIND_TEXT, max_length=PRODUCT_LINE_MAX, context=True),
    ColumnSpec("lead_source", "lead_source", KIND_LEAD_SOURCE),
    ColumnSpec("salesperson_email", "salesperson_email", KIND_EMAIL, max_length=EMAIL_MAX, context=True),
    ColumnSpec("stage", "stage", KIND_TEXT, max_length=STAGE_MAX, context=True),
    # Legacy: read for compatibility, never in the template or the export.
    ColumnSpec("deal_total_usd", "deal_total_usd", KIND_DERIVED_MONEY, legacy=True),
    ColumnSpec("deal_year", "deal_year", KIND_YEAR, legacy=True),
    ColumnSpec("expected_close_date", "expected_close_date", KIND_DATE, legacy=True),
)

OUTPUT_COLUMNS = tuple(spec for spec in COLUMNS if not spec.legacy)  # the template and the export
HEADERS = tuple(spec.header for spec in OUTPUT_COLUMNS)  # what the downloadable workbook carries

FORBIDDEN_EXACT = frozenset({"next_step", "next_step_due"})
FORBIDDEN_PREFIXES = ("revenue_", "bid_")


def normalize_header(header) -> str:
    """Case and surrounding/repeated whitespace are the only tolerated differences."""
    return " ".join(str(header).split()).casefold()


SPECS_BY_KEY = {normalize_header(spec.header): spec for spec in COLUMNS}


def is_forbidden_header(header) -> bool:
    key = normalize_header(header)
    return key in FORBIDDEN_EXACT or key.startswith(FORBIDDEN_PREFIXES)
