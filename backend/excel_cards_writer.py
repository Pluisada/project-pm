"""Writer of the commercial Excel workbook: the card export and the empty import template.

Both files come from `build_workbook`, which takes its sheet name and headers from
`excel_contract` only; the template is the same workbook with zero cards. Part B of the
Excel work: no import logic here, no database writes.

Rules (see excel_contract for the shared policy):
- Every text value is written as a literal string, never a formula. openpyxl would turn a
  string starting with "=" into a formula cell, so the cell type is forced to "s"; a text
  starting with = + - @ also gets the quote prefix so Excel keeps it literal when edited.
- Numbers stay numbers and dates stay dates. The derived total, the deal year and the legacy close date
  are not written (see excel_contract, legacy columns).
- Bid revenue, audit data, ledger data and anything outside the contract are never exported.
"""
import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session, joinedload

import excel_contract as contract
from identity_models import is_cancelled_email
from models import Card, Column

INSTRUCTIONS_SHEET = "Instructions"
VALUES_SHEET = "Valid values"
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
        "company is the customer. contact is the contact's name; contact_email and contact_phone are the contact's"
        " email address and telephone number.",
        "deal_value_usd (the current year) and deal_value_next_year_usd (the next year) are amounts in USD (not negative,"
        " up to 2 decimals). The application derives the total and the deal year; do not enter them.",
        f"probability_level accepts: {', '.join(contract.ALLOWED_CONFIDENCE)}.",
        f"lead_source accepts: {', '.join(contract.ALLOWED_LEAD_SOURCES)}.",
        "next_action_date is the planned date of the next commercial action (yyyy-mm-dd).",
        "product_line, salesperson_email and stage must name something that already exists: they are checked against"
        " the company and the board at preview, and nothing is created for you. The product must be in the company"
        " catalog, the salesperson must be an active member of the company, and the stage must be a column title of"
        " the board. The sheet 'Valid values' of this file lists the ones of the company and board it was downloaded"
        " from: copy them exactly. Do not invent values; leave the cell empty instead (a new card with an empty"
        " stage goes to the first stage). A card cannot be created in the lost stage by an import.",
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
        .options(joinedload(Card.column), joinedload(Card.bid_details))
        .filter(Column.board_id == board_id)
        .order_by(Column.position, Column.id, Card.position, Card.id)
        .all()
    )
    rows = []
    for card in cards:
        current = _money(card.deal_value)
        next_year = _money(card.deal_value_next_year)
        rows.append({
            "card_id": card.id,
            "title": card.title,
            "company": card.company,
            "contact": card.contact,
            "contact_email": card.contact_email,
            "contact_phone": card.contact_phone,
            "details": card.details,
            "deal_value_usd": current,
            "deal_value_next_year_usd": next_year,
            "confidence_level": card.confidence_level,
            "next_step_due": card.next_step_due,
            "product_line": card.product_line,
            "lead_source": card.lead_source,
            # a cancelled identity's internal placeholder is never exported as a real address
            "salesperson_email": None if is_cancelled_email(salesperson_emails.get(card.salesperson_id)) else salesperson_emails.get(card.salesperson_id),
            "stage": card.column.title,
        })
    return rows


def build_workbook(rows: list[dict], valid_values: dict[str, list[str]] | None = None) -> bytes:
    """The Cards + Instructions workbook. `rows` keys are the contract fields; none -> the empty template.

    `valid_values` (column header -> the values the importer accepts for it in the company and board the file is
    generated for) adds a "Valid values" sheet; the reader ignores every sheet but Cards."""
    wb = Workbook()
    ws = wb.active
    ws.title = contract.SHEET_NAME
    for index, spec in enumerate(contract.OUTPUT_COLUMNS, start=1):
        _put_text(ws, 1, index, spec.header)
    for r, record in enumerate(rows, start=2):
        for index, spec in enumerate(contract.OUTPUT_COLUMNS, start=1):
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
    if valid_values:
        sheet = wb.create_sheet(VALUES_SHEET)
        for index, (header, values) in enumerate(valid_values.items(), start=1):
            _put_text(sheet, 1, index, header).font = Font(bold=True)
            for r, value in enumerate(values, start=2):
                _put_text(sheet, r, index, value)
            sheet.column_dimensions[get_column_letter(index)].width = max([len(header), *(len(v) for v in values)]) + 4
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def valid_values(db: Session, board_id: int, salesperson_emails: list[str]) -> dict[str, list[str]]:
    """What the importer accepts, as the "Valid values" sheet lists it: the board's stages in order (a stage of the
    lost kind is marked, since an import cannot put a card there), the company's product catalog, the people the
    central database lets the company assign (their e-mails, given by the caller) and the fixed value lists."""
    from product_catalog import catalog_names

    stages = [
        f"{column.title} (not available for import)" if column.stage_kind == "lost" else column.title
        for column in db.query(Column).filter(Column.board_id == board_id).order_by(Column.position, Column.id)
    ]
    return {
        "stage": stages,
        "product_line": list(catalog_names(db)),
        "salesperson_email": sorted(salesperson_emails),
        "lead_source": list(contract.ALLOWED_LEAD_SOURCES),
        "probability_level": list(contract.ALLOWED_CONFIDENCE),
    }
