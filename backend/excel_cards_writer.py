"""Writer of the commercial Excel workbook: the card export and the empty import template.

Both files come from `build_workbook`, which takes its sheet name and headers from
`excel_contract` only; the template is the same workbook with zero cards. Part B of the
Excel work: no import logic here, no database writes.

Rules (see excel_contract for the shared policy):
- Every text value is written as a literal string, never a formula. openpyxl would turn a
  string starting with "=" into a formula cell, so the cell type is forced to "s"; a text
  starting with = + - @ also gets the quote prefix so Excel keeps it literal when edited.
- Numbers stay numbers and dates stay dates. `deal_total_usd` is the computed sum of the two
  annual values (a plain number, no Excel formula); it stays empty when neither is informed.
- Bid revenue, audit data, ledger data and anything outside the contract are never exported.
"""
import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session, joinedload

import excel_contract as contract
from models import Card, Column

INSTRUCTIONS_SHEET = "Instructions"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DANGEROUS_PREFIXES = ("=", "+", "-", "@")
_DATE_FORMAT = "yyyy-mm-dd"
_MONEY_FORMAT = "#,##0.00"


def _instructions() -> list[str]:
    return [
        "Import template for the Cards sheet. Keep the header row exactly as it is; one card per row.",
        "title is the only required value. Every other column may stay empty.",
        "card_id: leave it empty to create a new card; keep the exported value to update that card of this board."
        " An unknown card_id, or one repeated in the file, rejects the row.",
        "deal_value_usd and deal_value_next_year_usd are amounts in USD (not negative, up to 2 decimals).",
        "deal_total_usd is derived (sum of the two annual values) and is NOT authoritative: it is ignored on import.",
        "deal_year is informational. It does not change the year the system gives a new card.",
        f"probability_level accepts: {', '.join(contract.ALLOWED_CONFIDENCE)}.",
        f"lead_source accepts: {', '.join(contract.ALLOWED_LEAD_SOURCES)}.",
        "expected_close_date is a date (yyyy-mm-dd).",
        "product_line, salesperson_email and stage are checked against the company and the board at preview:"
        " the product must be in the company catalog, the salesperson must be an active member of the company,"
        " and the stage must be a column title of the board.",
        "Bid revenue and next-step columns are not part of this file.",
    ]


def _put_text(ws: Worksheet, row: int, column: int, value: str):
    cell = ws.cell(row=row, column=column)
    cell.value = value
    cell.data_type = "s"  # literal text, never a formula
    if value.startswith(_DANGEROUS_PREFIXES):
        cell.quotePrefix = True
    return cell


def _money(value) -> Decimal | None:
    return None if value is None else Decimal(str(round(float(value), 2)))


def export_rows(db: Session, board_id: int, salesperson_emails: dict[int, str]) -> list[dict]:
    """Contract-shaped rows for the cards of one board, in column then card position order.

    `salesperson_emails` maps the canonical user id to an email from the identity database; a
    salesperson without an entry leaves `salesperson_email` empty (no email is ever invented).
    """
    cards = (
        db.query(Card)
        .join(Column)
        .options(joinedload(Card.column))
        .filter(Column.board_id == board_id)
        .order_by(Column.position, Column.id, Card.position, Card.id)
        .all()
    )
    rows = []
    for card in cards:
        current = _money(card.deal_value)
        next_year = _money(card.deal_value_next_year)
        total = None if current is None and not next_year else (current or Decimal(0)) + (next_year or Decimal(0))
        rows.append({
            "card_id": card.id,
            "title": card.title,
            "company": card.company,
            "contact": card.contact,
            "details": card.details,
            "deal_value_usd": current,
            "deal_value_next_year_usd": next_year,
            "deal_total_usd": total,
            "deal_year": card.deal_year,
            "confidence_level": card.confidence_level,
            "expected_close_date": card.expected_close_date,
            "product_line": card.product_line,
            "lead_source": card.lead_source,
            "salesperson_email": salesperson_emails.get(card.salesperson_id),
            "stage": card.column.title,
        })
    return rows


def build_workbook(rows: list[dict]) -> bytes:
    """The Cards + Instructions workbook. `rows` keys are the contract fields; none -> the empty template."""
    wb = Workbook()
    ws = wb.active
    ws.title = contract.SHEET_NAME
    for index, spec in enumerate(contract.COLUMNS, start=1):
        _put_text(ws, 1, index, spec.header)
    for r, record in enumerate(rows, start=2):
        for index, spec in enumerate(contract.COLUMNS, start=1):
            value = record.get(spec.field)
            if value is None or value == "":
                continue
            if isinstance(value, str):
                _put_text(ws, r, index, value)
                continue
            cell = ws.cell(row=r, column=index, value=float(value) if isinstance(value, Decimal) else value)
            if isinstance(value, Decimal):
                cell.number_format = _MONEY_FORMAT
            elif isinstance(value, date):
                cell.number_format = _DATE_FORMAT
    ws.freeze_panes = "A2"
    guide = wb.create_sheet(INSTRUCTIONS_SHEET)
    for r, line in enumerate(_instructions(), start=1):
        _put_text(guide, r, 1, line)
    guide.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
