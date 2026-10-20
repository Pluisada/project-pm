"""Read and validate the commercial "Cards" workbook (Part A of the Excel flow).

read_commercial_excel() turns uploaded bytes into typed records plus structured
errors and touches nothing else: no database, no users, no cards, no files, no
network, no logging of cell contents. Messages name the Excel row, the column
and the reason, never the cell value. The contract (sheet, columns, limits) is
in excel_contract.py; this module keeps no header list of its own.

File safety reuses the generic low-level checks of bid_excel_reader (extension
and magic bytes, size, zip-bomb limits, macros, content type, defusedxml,
openpyxl without macros or external links); the Bid column contract and
parsers are not used.

Policy:
- file errors (no rows are produced): bad format/limits, no "Cards" sheet,
  merged cells on it, blank/duplicate/forbidden/unknown headers, missing
  `title` column, no data rows, more than 5000 data rows;
- file warnings: other sheets (ignored), absent optional columns;
- row errors: the row is kept, flagged invalid, the bad field left None.
  Any formula cell is a row error (cached values are not trusted);
  values in columns without a header are a row error too.
- deal_total_usd is derived and informative: its content is ignored (never
  validated, stored or compared with the annual values).
- empty text/number cells are "not informed" (None); only
  deal_value_next_year_usd normalizes empty to 0. Explicit 0 stays 0.
- card_id is parsed for syntax only (blank = create, a positive whole number = update); existence, board
  membership and duplicates in the file are the importer's checks.
- Not checked here (need the authenticated company/board): product_line
  catalog, salesperson existence/link/eligibility, stage validity. Preview must
  check them, and confirm must check them again.
"""
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from os.path import basename

import defusedxml.ElementTree
from defusedxml import DefusedXmlException
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._reader import FORMULA_TAG

import excel_contract as contract
from bid_excel_reader import (
    DEFAULT_LIMITS,
    ExcelError,
    FileMetadata,
    FormulaWithoutValue,
    ReaderLimits,
    _check_name_and_bytes,
    _check_zip,
    _ExactCellParser,
    _FileRejected,
    _open_workbook,
    _unreadable,
)

SCOPE_FILE = "file"
SCOPE_ROW = "row"


@dataclass(frozen=True)
class ExcelIssue:
    scope: str                  # SCOPE_FILE | SCOPE_ROW
    code: str                   # stable identifier
    message: str                # how to fix it; never contains the cell value
    row: int | None = None      # Excel row (1 = header); None for the whole file
    column: str | None = None   # contract header, or the header as written for unknown ones


@dataclass(frozen=True)
class CommercialRecord:
    """Normalized values; None = not informed or invalid. No total is kept."""
    row_number: int
    card_id: int | None = None               # syntax only; None = a new card
    title: str | None = None
    company: str | None = None
    contact: str | None = None
    details: str | None = None
    deal_value_usd: Decimal | None = None
    deal_value_next_year_usd: Decimal = Decimal("0.00")
    deal_year_hint: int | None = None        # informational, never used to write
    confidence_level: str | None = None
    expected_close_date: date | None = None
    product_line: str | None = None          # syntax only
    lead_source: str | None = None
    salesperson_email: str | None = None     # syntax only, lower-cased
    stage: str | None = None                 # syntax only


@dataclass(frozen=True)
class CommercialRow:
    row_number: int
    record: CommercialRecord
    errors: tuple[ExcelIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class CommercialReadResult:
    file: FileMetadata
    rows: tuple[CommercialRow, ...] = ()
    file_errors: tuple[ExcelIssue, ...] = ()
    file_warnings: tuple[ExcelIssue, ...] = ()
    blank_rows_skipped: int = 0
    columns: tuple[str, ...] = ()  # record fields of the contract columns present in the file

    @property
    def ok(self) -> bool:
        return not self.file_errors

    @property
    def valid_rows(self) -> int:
        return sum(row.is_valid for row in self.rows)

    @property
    def invalid_rows(self) -> int:
        return sum(not row.is_valid for row in self.rows)


class _Formula:
    """Marker for a formula cell (cached values are never trusted)."""


FORMULA = _Formula()


class _FieldError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --- cells ------------------------------------------------------------------

class _CellParser(_ExactCellParser):
    def parse_cell(self, element):
        cell = super().parse_cell(element)
        if element.find(FORMULA_TAG) is not None:
            cell["value"] = FORMULA
        return cell


def _iter_rows(workbook, sheet):
    with sheet._get_source() as source:
        parser = _CellParser(
            source,
            sheet._shared_strings,
            data_only=True,
            epoch=workbook.epoch,
            date_formats=workbook._date_formats,
            timedelta_formats=workbook._timedelta_formats,
        )
        for row_number, cells in parser.parse():
            yield row_number, {cell["column"]: cell["value"] for cell in cells}


def _has_merged_cells(sheet) -> bool:
    with sheet._get_source() as source:
        for _event, element in defusedxml.ElementTree.iterparse(source, events=("end",)):
            if element.tag.rpartition("}")[2] == "mergeCell":
                return True
            if element.tag.rpartition("}")[2] == "row":
                element.clear()
    return False


def _is_absent(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _reject_special(value) -> None:
    if value is FORMULA:
        raise _FieldError("formula_not_allowed", "Formulas are not accepted; paste the values instead")
    if isinstance(value, FormulaWithoutValue):
        raise _FieldError("formula_not_allowed", "Formulas are not accepted; paste the values instead")
    if isinstance(value, ExcelError):
        raise _FieldError("excel_error", "Cell holds an Excel error value")
    if isinstance(value, bool):
        raise _FieldError("invalid_type", "Unexpected TRUE/FALSE value")


# --- field parsers -----------------------------------------------------------

def parse_text(value, max_length: int) -> str:
    _reject_special(value)
    if isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, (datetime, date)):
        raise _FieldError("invalid_type", "A date where text was expected")
    elif not isinstance(value, str):  # time of day, duration, ...
        raise _FieldError("invalid_type", "A value of an unsupported type where text was expected")
    else:
        text = unicodedata.normalize("NFC", value).strip()
    if len(text) > max_length:
        raise _FieldError("text_too_long", f"Longer than {max_length} characters")
    return text


_MAX_CARD_ID = 2**63 - 1
_PLAIN_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def parse_money(value) -> Decimal:
    _reject_special(value)
    if isinstance(value, (datetime, date)):
        raise _FieldError("invalid_type", "A date where an amount was expected")
    if isinstance(value, str):
        text = value.strip()
        if not _PLAIN_NUMBER.fullmatch(text):
            raise _FieldError(
                "invalid_amount", "Amount must be a plain number such as 1234.56 (no currency symbol or separators)"
            )
        number = Decimal(text)
    elif isinstance(value, Decimal):
        number = value
    else:  # time of day, duration, ...
        raise _FieldError("invalid_type", "A value of an unsupported type where an amount was expected")
    if not number.is_finite():
        raise _FieldError("invalid_amount", "Amount must be a finite number")
    if number < 0:
        raise _FieldError("negative_amount", "Amount can't be negative")
    if number > contract.MAX_DEAL_VALUE:
        raise _FieldError("amount_too_large", f"Amount above the limit of {contract.MAX_DEAL_VALUE}")
    quantized = number.quantize(contract.MONEY_DECIMALS)
    if quantized != number:
        raise _FieldError("too_many_decimals", "Amount can have at most 2 decimal places")
    return quantized + 0  # normalizes -0


def parse_card_id(value) -> int:
    _reject_special(value)
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        card_id = int(value)
    elif isinstance(value, str) and value.strip().isdigit() and value.strip().isascii():
        card_id = int(value.strip())
    else:
        raise _FieldError("invalid_card_id", "card_id must be a positive whole number, or empty for a new card")
    if not 1 <= card_id <= _MAX_CARD_ID:
        raise _FieldError("invalid_card_id", "card_id must be a positive whole number, or empty for a new card")
    return card_id


def parse_year(value) -> int:
    _reject_special(value)
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        year = int(value)
    elif isinstance(value, str) and value.strip().isdigit() and value.strip().isascii():
        year = int(value.strip())
    else:
        raise _FieldError("invalid_year", "Year must be a whole number")
    if not contract.MIN_YEAR <= year <= contract.MAX_YEAR:
        raise _FieldError("invalid_year", f"Year outside {contract.MIN_YEAR}-{contract.MAX_YEAR}")
    return year


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_date(value) -> date:
    _reject_special(value)
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str) and _ISO_DATE.fullmatch(value.strip()):
        try:
            parsed = date.fromisoformat(value.strip())
        except ValueError:
            raise _FieldError("invalid_date", "Not a valid calendar date") from None
    else:
        raise _FieldError("invalid_date", "Use an Excel date cell or text in YYYY-MM-DD format")
    if not contract.MIN_YEAR <= parsed.year <= contract.MAX_YEAR:
        raise _FieldError("invalid_date", f"Date outside {contract.MIN_YEAR}-{contract.MAX_YEAR}")
    return parsed


def parse_confidence(value) -> str:
    text = parse_text(value, 20).upper()
    if text not in contract.ALLOWED_CONFIDENCE:
        raise _FieldError("invalid_probability_level", f"Allowed values: {', '.join(contract.ALLOWED_CONFIDENCE)}")
    return text


def parse_lead_source(value) -> str:
    text = parse_text(value, 100)
    by_key = {allowed.casefold(): allowed for allowed in contract.ALLOWED_LEAD_SOURCES}
    if text.casefold() not in by_key:
        raise _FieldError("invalid_lead_source", f"Allowed values: {', '.join(contract.ALLOWED_LEAD_SOURCES)}")
    return by_key[text.casefold()]


_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def parse_email(value, max_length: int) -> str:
    text = parse_text(value, max_length)
    if not _EMAIL.fullmatch(text):
        raise _FieldError("invalid_email", "Not a valid e-mail address")
    return text.lower()


# --- headers -------------------------------------------------------------------

def _header_text(value) -> str:
    if value is None or not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _issue(code, message, row=None, column=None, scope=SCOPE_FILE) -> ExcelIssue:
    return ExcelIssue(scope, code, message, row=row, column=column)


def _map_headers(cells, limits: ReaderLimits):
    """({column index: ColumnSpec}, file errors, file warnings, header texts by index)."""
    last = max((index for index, value in cells.items() if not _is_absent(value)), default=0)
    if last > limits.max_columns:
        return {}, [_issue("too_many_columns", f"Row 1 has more than {limits.max_columns} columns")], [], {}
    headers = {index: _header_text(cells.get(index)) for index in range(1, last + 1)}
    errors, by_index, seen = [], {}, set()
    for index, header in headers.items():
        if cells.get(index) is FORMULA or not header:
            errors.append(_issue("blank_header", f"Column {_letter(index)} has no usable header", row=1))
            continue
        key = contract.normalize_header(header)
        if key in seen:
            errors.append(_issue("duplicate_header", "A header appears more than once", row=1, column=header))
            continue
        seen.add(key)
        if contract.is_forbidden_header(header):
            errors.append(_issue("forbidden_column", "This column is not part of the import", row=1, column=header))
        elif key in contract.SPECS_BY_KEY:
            by_index[index] = contract.SPECS_BY_KEY[key]
        else:
            errors.append(_issue("unknown_column", "Column is not in the contract; remove it", row=1, column=header))
    warnings = []
    for spec in contract.COLUMNS:
        if contract.normalize_header(spec.header) in seen:
            continue
        if spec.required_column:
            errors.append(_issue("missing_column", "Required column is missing", row=1, column=spec.header))
        else:
            warnings.append(_issue(
                "missing_optional_column", "Optional column absent; the field stays empty", row=1, column=spec.header,
                scope=SCOPE_FILE,
            ))
    return by_index, errors, warnings, headers


def _letter(index: int) -> str:
    return get_column_letter(index)


# --- rows ------------------------------------------------------------------------

def _read_row(row_number, cells, by_index, headers) -> CommercialRow:
    values: dict = {}
    errors: list[ExcelIssue] = []

    for index, spec in by_index.items():
        value = cells.get(index)
        header = spec.header

        def error(code, message, _header=header):
            return ExcelIssue(SCOPE_ROW, code, message, row=row_number, column=_header)

        if _is_absent(value):
            if spec.required_value:
                errors.append(error("required_value_missing", "Required value is empty"))
            continue
        try:
            kind = spec.kind
            if kind == contract.KIND_REFERENCE:
                values["card_id"] = parse_card_id(value)
            elif kind == contract.KIND_TEXT:
                text = parse_text(value, spec.max_length)
                if not text:
                    if spec.required_value:
                        raise _FieldError("required_value_missing", "Required value is empty")
                else:
                    values[spec.field] = text
            elif kind == contract.KIND_MONEY:
                values[spec.field] = parse_money(value)
            elif kind == contract.KIND_DERIVED_MONEY:
                pass  # informative only: neither validated, stored nor used
            elif kind == contract.KIND_YEAR:
                values["deal_year_hint"] = parse_year(value)
            elif kind == contract.KIND_CONFIDENCE:
                values[spec.field] = parse_confidence(value)
            elif kind == contract.KIND_DATE:
                values[spec.field] = parse_date(value)
            elif kind == contract.KIND_LEAD_SOURCE:
                values[spec.field] = parse_lead_source(value)
            elif kind == contract.KIND_EMAIL:
                values[spec.field] = parse_email(value, spec.max_length)
        except _FieldError as exc:
            errors.append(error(exc.code, str(exc)))

    extra = [i for i, value in cells.items() if i not in headers and not _is_absent(value)]
    if extra:
        errors.append(ExcelIssue(
            SCOPE_ROW, "value_outside_headers", f"{len(extra)} value(s) in columns without a header",
            row=row_number,
        ))
    values.setdefault("deal_value_next_year_usd", Decimal("0.00"))
    return CommercialRow(row_number, CommercialRecord(row_number=row_number, **values), tuple(errors))


def _is_blank(cells) -> bool:
    return all(_is_absent(value) for value in cells.values())


# --- entry point -------------------------------------------------------------------

def read_commercial_excel(
    data: bytes, filename: str, limits: ReaderLimits = DEFAULT_LIMITS
) -> CommercialReadResult:
    """Read an uploaded commercial workbook. Never raises for a bad file - see file_errors."""
    metadata = FileMetadata(filename=basename(filename), size_bytes=len(data), sha256=sha256(data).hexdigest())
    try:
        _check_name_and_bytes(metadata.filename, data, limits)
        metadata = replace(metadata, uncompressed_bytes=_check_zip(data, limits))
        workbook = _open_workbook(data)
        try:
            return _read_workbook(workbook, metadata, limits)
        finally:
            workbook.close()
    except _FileRejected as exc:
        return CommercialReadResult(
            file=metadata, file_errors=(_issue(exc.issue.code, exc.issue.message),)
        )


def _find_sheet(workbook):
    wanted = contract.normalize_header(contract.SHEET_NAME)
    matches = [s for s in workbook.worksheets if contract.normalize_header(s.title) == wanted]
    if not matches:
        raise _FileRejected("sheet_not_found", f'The workbook has no "{contract.SHEET_NAME}" sheet')
    if len(matches) > 1:
        raise _FileRejected("ambiguous_sheet", f'More than one sheet is named "{contract.SHEET_NAME}"')
    return matches[0]


def _read_workbook(workbook, metadata: FileMetadata, limits: ReaderLimits) -> CommercialReadResult:
    file_warnings: list[ExcelIssue] = []
    try:
        sheet = _find_sheet(workbook)
        others = len(workbook.worksheets) - 1
        if others:
            file_warnings.append(_issue("unexpected_sheet", f"{others} other sheet(s) in the workbook are ignored"))
        if _has_merged_cells(sheet):
            raise _FileRejected("merged_cells", f'The "{contract.SHEET_NAME}" sheet has merged cells; unmerge them')

        rows, blank, by_index, headers = [], 0, None, {}
        for row_number, cells in _iter_rows(workbook, sheet):
            if row_number == 1:
                by_index, errors, warnings, headers = _map_headers(cells, limits)
                file_warnings.extend(warnings)
                if errors:
                    return CommercialReadResult(file=metadata, file_errors=tuple(errors), file_warnings=tuple(file_warnings))
                continue
            if by_index is None:  # no header row stored at all
                break
            if _is_blank(cells):
                blank += 1
                continue
            if len(rows) == limits.max_rows:
                raise _FileRejected("too_many_rows", f"More than {limits.max_rows} data rows")
            rows.append(_read_row(row_number, cells, by_index, headers))
        if by_index is None:
            raise _FileRejected("missing_header_row", "Row 1 of the sheet has no headers")
    except _FileRejected:
        raise
    except (DefusedXmlException, defusedxml.ElementTree.ParseError) as exc:
        raise _unreadable(exc, "The worksheet is corrupted or can't be read") from None
    except Exception as exc:  # malformed sheet XML, bad CRC, ...
        raise _unreadable(exc, "The worksheet is corrupted or can't be read") from None

    if not rows:
        raise _FileRejected("no_data_rows", f'The "{contract.SHEET_NAME}" sheet has headers but no data rows')
    return CommercialReadResult(
        file=metadata, rows=tuple(rows), file_warnings=tuple(file_warnings), blank_rows_skipped=blank,
        columns=tuple(spec.field for spec in by_index.values()),
    )
