"""Read and structurally validate the Salcomp Bids report (relatorio-leads.xlsx).

read_bid_excel() turns the uploaded bytes into typed, normalized records plus
errors and warnings, and touches nothing else: no database, no users, no
cards, no logging of cell contents. Commercial mapping (CAT/Estágio -> board
column), deduplication, source_key and the import itself are later steps that
consume a BidReadResult without re-reading the file.

Three kinds of problems:
- file errors: the file can't be interpreted (wrong format, corrupted, over a
  limit, no Bid sheet, missing or duplicate headers). The result then has no
  rows at all - nothing usable is produced;
- row errors: one field of one row is invalid. The row is kept (flagged
  invalid, the field left None) and the other rows are still read;
- warnings: reading continues and the value is kept (e.g. a volume of 1).
Empty optional fields ("", "-" or blank) are listed per row in `empty_fields`;
they are neither errors nor zero.

Messages name the Excel row, the column and the reason, never the cell value.

Safety: only .xlsx (not .xlsm/.xls, checked by content type, not just the
name), size and decompression limits are enforced on the zip before any XML is
parsed, macros are never loaded (keep_vba=False), external links are dropped
(keep_links=False) and XML is parsed through defusedxml (no DTDs, entities or
external references) - reading refuses to run without it.

Numbers are taken from the cell's XML text as Decimal, never through float
(openpyxl would cast "1234.56" to float): see _ExactCellParser, which extends
openpyxl 3.1.5's WorkSheetParser (pinned in pyproject.toml).
"""
import io
import os
import re
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from os.path import basename
from typing import Callable

import defusedxml.ElementTree
import openpyxl
from defusedxml import DefusedXmlException
from openpyxl.styles.numbers import BUILTIN_FORMATS, BUILTIN_FORMATS_MAX_SIZE
from openpyxl.worksheet._reader import FORMULA_TAG, VALUE_TAG, WorkSheetParser

from bid_money import InvalidMoneyValue, reais_to_cents

REVENUE_CURRENCY = "BRL"  # the report's revenue columns are "Revenue BRL (...)"


# --- limits -----------------------------------------------------------------

@dataclass(frozen=True)
class ReaderLimits:
    max_rows: int = 5000                       # data rows (records), excluding the header
    max_file_bytes: int = 10 * 1024 * 1024     # the .xlsx as uploaded
    max_uncompressed_bytes: int = 100 * 1024 * 1024  # sum of every zip member, declared
    max_compression_ratio: int = 200           # per member above 1 MiB (zip bombs)
    max_zip_entries: int = 1000
    max_columns: int = 200
    max_text_length: int = 10_000


DEFAULT_LIMITS = ReaderLimits()

XLSX_MAIN_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_CONTENT_TYPES_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_RATIO_CHECK_MIN_BYTES = 1024 * 1024


# --- result types -----------------------------------------------------------

class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ReadIssue:
    severity: Severity
    code: str                 # stable identifier, e.g. "invalid_date"
    message: str              # human-readable reason; never contains the cell value
    row: int | None = None    # Excel row number (1 = header); None for the whole file
    column: str | None = None  # header as written in the file
    field: str | None = None   # BidRecord field


@dataclass(frozen=True)
class FileMetadata:
    filename: str             # base name only
    size_bytes: int
    sha256: str
    uncompressed_bytes: int | None = None


@dataclass(frozen=True)
class SheetInfo:
    name: str
    all_sheet_names: tuple[str, ...]
    headers: tuple[str, ...]          # row 1, as written
    mapped_columns: dict[str, str]    # header -> BidRecord field
    missing_optional_columns: tuple[str, ...]
    ignored_columns: tuple[str, ...]  # headers this reader doesn't know


class FormulaWithoutValue:
    """A formula cell saved without a calculated value (e.g. never recalculated)."""

    def __repr__(self):
        return "FormulaWithoutValue"


FORMULA_WITHOUT_VALUE = FormulaWithoutValue()


@dataclass(frozen=True)
class ExcelError:
    """An error value such as #N/A or #VALUE! stored in the cell."""
    code: str


CellValue = str | Decimal | datetime | date | bool | ExcelError | FormulaWithoutValue | None


@dataclass(frozen=True)
class RawRow:
    """The row as read: header (as written) -> cell value, before normalization.
    Kept for a later preview; never logged."""
    row_number: int
    cells: dict[str, CellValue]


@dataclass(frozen=True)
class BidRecord:
    """One report row, normalized. None = empty/absent ("-" included) or invalid
    (see the row's errors) - never 0. Nothing here is mapped to a board column,
    a user or a card field yet: CAT/stage stay as written, people stay text."""
    row_number: int
    code: str | None = None                    # Código, as text
    code_needs_review: bool = False            # stored as a number: leading zeros may be lost
    year: int | None = None                    # Ano
    cat: str | None = None                     # CAT, as written
    stage: str | None = None                   # Estágio, as written
    plant: str | None = None
    customer: str | None = None
    customer_type: str | None = None
    business_type: str | None = None
    product_category: str | None = None
    product: str | None = None
    solution_type: str | None = None
    project_name: str | None = None
    prospect_description: str | None = None
    volume_current_year: int | None = None
    volume_next_year: int | None = None
    production_start_date: date | None = None  # Data de MP
    revenue_current_year_cents: int | None = None
    revenue_next_year_cents: int | None = None
    revenue_currency: str = REVENUE_CURRENCY
    status_note: str | None = None             # Descrição do status
    budget_enabled: str | None = None          # Budget habilitado, as written
    budget: str | None = None                  # Budget, as written (not interpreted: no currency stated)
    cpm: str | None = None                     # a name - never turned into a user
    project_lead: str | None = None            # a name - never turned into a user
    next_step: str | None = None
    next_step_due: date | None = None          # Deadline - not an expected close date
    close_reason_note: str | None = None       # Razão
    close_reason: str | None = None            # Status da conclusão
    source_created_on: date | None = None      # Data de criação - not the card's creation date
    source_updated_on: date | None = None      # Atualizado em
    source_last_edited_on: date | None = None  # Última edição


@dataclass(frozen=True)
class BidRow:
    raw: RawRow
    record: BidRecord
    errors: tuple[ReadIssue, ...] = ()
    warnings: tuple[ReadIssue, ...] = ()
    empty_fields: tuple[str, ...] = ()

    @property
    def row_number(self) -> int:
        return self.raw.row_number

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ReadSummary:
    rows_read: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    rows_with_warnings: int = 0
    blank_rows_skipped: int = 0
    row_errors: int = 0
    row_warnings: int = 0


@dataclass(frozen=True)
class BidReadResult:
    file: FileMetadata
    sheet: SheetInfo | None = None
    rows: tuple[BidRow, ...] = ()
    file_errors: tuple[ReadIssue, ...] = ()    # any -> no rows, nothing usable
    file_warnings: tuple[ReadIssue, ...] = ()
    summary: ReadSummary = field(default_factory=ReadSummary)

    @property
    def ok(self) -> bool:
        return not self.file_errors


# --- column mapping -----------------------------------------------------------

@dataclass(frozen=True)
class ColumnSpec:
    header: str
    field: str
    kind: str                     # code | year | text | date | volume | revenue
    required_column: bool = False  # the header must exist (else file error)
    required_value: bool = False   # every row needs a value (else row error)


# The report's 31 columns, in file order. Matching ignores case, accents are kept,
# surrounding/repeated whitespace is collapsed.
BID_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("Código", "code", "code", required_column=True, required_value=True),
    ColumnSpec("Ano", "year", "year"),
    ColumnSpec("CAT", "cat", "text", required_column=True, required_value=True),
    ColumnSpec("Atualizado em", "source_updated_on", "date"),
    ColumnSpec("Estágio", "stage", "text", required_column=True, required_value=True),
    ColumnSpec("Planta", "plant", "text"),
    ColumnSpec("Cliente", "customer", "text", required_column=True, required_value=True),
    ColumnSpec("Tipo de cliente", "customer_type", "text"),
    ColumnSpec("Tipo de negócio", "business_type", "text"),
    ColumnSpec("Categoria de Produto", "product_category", "text"),
    ColumnSpec("Produto", "product", "text"),
    ColumnSpec("Solução", "solution_type", "text"),
    ColumnSpec("Nome do projeto", "project_name", "text", required_column=True),
    ColumnSpec("Descrição da prospecção", "prospect_description", "text"),
    ColumnSpec("Volume (ano atual)", "volume_current_year", "volume"),
    ColumnSpec("Volume (próximo ano)", "volume_next_year", "volume"),
    ColumnSpec("Data de MP", "production_start_date", "date"),
    ColumnSpec("Revenue BRL (ano atual)", "revenue_current_year_cents", "revenue"),
    ColumnSpec("Revenue BRL (próximo ano)", "revenue_next_year_cents", "revenue"),
    ColumnSpec("Descrição do status", "status_note", "text"),
    ColumnSpec("Budget habilitado", "budget_enabled", "text"),
    ColumnSpec("Budget", "budget", "text"),
    ColumnSpec("CPM", "cpm", "text"),
    ColumnSpec("Líder do projeto", "project_lead", "text"),
    ColumnSpec("Próximo passo", "next_step", "text"),
    ColumnSpec("Deadline", "next_step_due", "date"),
    ColumnSpec("Razão", "close_reason_note", "text"),
    ColumnSpec("Status da conclusão", "close_reason", "text"),
    ColumnSpec("Data de criação", "source_created_on", "date"),
    ColumnSpec("Última edição", "source_last_edited_on", "date"),
)
REQUIRED_HEADERS = tuple(spec.header for spec in BID_COLUMNS if spec.required_column)

ABSENT_MARKERS = {"", "-"}
_TEXT_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
# The report's "Nível de Confiança" column is no longer imported (the team's own confidence_level replaced it): it is
# recognised so it does not draw an "unknown column" warning, and its values are never read or validated.
IGNORED_HEADERS = ("Nível de Confiança",)
_EXPECTED_CODE = re.compile(r"\d{6}-\d{2}")
_ZERO_PAD_FORMAT = re.compile(r"0+")
_MIN_YEAR, _MAX_YEAR = 1900, 2100


def normalize_header(header) -> str:
    text = unicodedata.normalize("NFC", str(header))
    return " ".join(text.split()).casefold()


_SPECS_BY_HEADER = {normalize_header(spec.header): spec for spec in BID_COLUMNS}
_IGNORED_KEYS = {normalize_header(header) for header in IGNORED_HEADERS}


# --- exact cell parsing ---------------------------------------------------------

class _ExactCellParser(WorkSheetParser):
    """openpyxl's streaming sheet parser, except that numbers keep their exact
    XML text as Decimal and a formula without a cached value is marked."""

    def parse_cell(self, element):
        cell = super().parse_cell(element)
        raw = element.findtext(VALUE_TAG, None)
        if element.find(FORMULA_TAG) is not None and not raw:
            cell["value"] = FORMULA_WITHOUT_VALUE
        elif cell["data_type"] == "n" and raw:
            cell["value"] = Decimal(raw)
        elif cell["data_type"] == "e":
            cell["value"] = ExcelError(str(cell["value"]))
        return cell


def _iter_rows(workbook, sheet):
    """(Excel row number, {column index: (value, style_id)}) for each stored row."""
    with sheet._get_source() as source:
        parser = _ExactCellParser(
            source,
            sheet._shared_strings,
            data_only=True,
            epoch=workbook.epoch,
            date_formats=workbook._date_formats,
            timedelta_formats=workbook._timedelta_formats,
        )
        for row_number, cells in parser.parse():
            yield row_number, {cell["column"]: (cell["value"], cell["style_id"]) for cell in cells}


def _number_format(workbook, style_id) -> str:
    fmt_id = workbook._cell_styles[style_id].numFmtId
    if fmt_id < BUILTIN_FORMATS_MAX_SIZE:
        return BUILTIN_FORMATS.get(fmt_id, "General")
    return workbook._number_formats[fmt_id - BUILTIN_FORMATS_MAX_SIZE]


# --- file checks ------------------------------------------------------------------

def _caused_by_unsafe_xml(exc: BaseException) -> bool:
    """openpyxl re-raises parse failures as a plain ValueError; the defusedxml
    refusal is further down the chain."""
    while exc is not None:
        if isinstance(exc, DefusedXmlException):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _unreadable(exc: BaseException, message: str) -> "_FileRejected":
    if _caused_by_unsafe_xml(exc):
        return _FileRejected("unsafe_xml", "The workbook contains forbidden XML (DTD entities or external references)")
    return _FileRejected("corrupted_file", message)


class _FileRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.issue = ReadIssue(Severity.ERROR, code, message)


def _check_name_and_bytes(filename: str, data: bytes, limits: ReaderLimits) -> None:
    lower = filename.lower()
    if lower.endswith(".xlsm"):
        raise _FileRejected("format_not_allowed", "Macro-enabled workbooks (.xlsm) are not accepted; save as .xlsx")
    if lower.endswith(".xls"):
        raise _FileRejected("format_not_allowed", "Legacy .xls workbooks are not accepted; save as .xlsx")
    if not lower.endswith(".xlsx"):
        raise _FileRejected("invalid_extension", "Only .xlsx files are accepted")
    if not data:
        raise _FileRejected("empty_file", "The file is empty")
    if len(data) > limits.max_file_bytes:
        raise _FileRejected("file_too_large", f"The file is larger than {limits.max_file_bytes} bytes")
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise _FileRejected(
            "format_not_allowed", "This is a legacy .xls (or password-protected) workbook, not .xlsx"
        )
    if not data.startswith(b"PK"):
        raise _FileRejected("not_xlsx", "The file is not an .xlsx workbook")


def _check_zip(data: bytes, limits: ReaderLimits) -> int:
    """Validate the package before any XML is parsed; returns the declared uncompressed size."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise _FileRejected("corrupted_file", "The file is corrupted or not an .xlsx workbook") from None
    with archive:
        members = archive.infolist()
        if len(members) > limits.max_zip_entries:
            raise _FileRejected("too_many_entries", "The workbook has too many internal parts")
        total = 0
        for member in members:
            if member.flag_bits & 0x1:
                raise _FileRejected("encrypted_file", "Encrypted workbooks are not accepted")
            total += member.file_size
            if (member.file_size > _RATIO_CHECK_MIN_BYTES
                    and member.file_size > limits.max_compression_ratio * max(member.compress_size, 1)):
                raise _FileRejected("suspicious_compression", "The workbook expands far more than a normal .xlsx")
        if total > limits.max_uncompressed_bytes:
            raise _FileRejected("uncompressed_too_large", "The workbook's content is too large once uncompressed")
        names = {member.filename for member in members}
        if any(name.lower().endswith("vbaproject.bin") for name in names):
            raise _FileRejected("format_not_allowed", "The workbook contains macros; save it as a plain .xlsx")
        if "[Content_Types].xml" not in names:
            raise _FileRejected("not_xlsx", "The file is a zip archive but not an .xlsx workbook")
        try:
            types = defusedxml.ElementTree.fromstring(archive.read("[Content_Types].xml"))
        except (DefusedXmlException, defusedxml.ElementTree.ParseError, zipfile.BadZipFile) as exc:
            raise _unreadable(exc, "The workbook's content types are unreadable") from None
    main_types = {
        override.get("ContentType")
        for override in types.iter(f"{_CONTENT_TYPES_NS}Override")
        if override.get("PartName", "").lower() == "/xl/workbook.xml"
    }
    if main_types != {XLSX_MAIN_CONTENT_TYPE}:
        if any("macroenabled" in (t or "").lower() for t in main_types):
            raise _FileRejected("format_not_allowed", "Macro-enabled workbooks are not accepted; save as .xlsx")
        raise _FileRejected("not_xlsx", "The file is not a standard .xlsx workbook")
    return total


def _open_workbook(data: bytes):
    if not openpyxl.DEFUSEDXML:
        raise RuntimeError("defusedxml is required to read Excel files safely")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return openpyxl.load_workbook(
                io.BytesIO(data), read_only=True, data_only=True, keep_links=False, keep_vba=False
            )
    except Exception as exc:  # openpyxl raises many types for malformed packages
        raise _unreadable(exc, "The workbook is corrupted or can't be read") from None


# --- headers -----------------------------------------------------------------------

def _header_row(workbook, sheet) -> tuple[str, ...] | None:
    for row_number, cells in _iter_rows(workbook, sheet):
        if row_number != 1:
            return None
        headers = [_header_text(cells.get(index, (None, 0))[0]) for index in range(1, max(cells, default=0) + 1)]
        while headers and not headers[-1]:
            headers.pop()
        return tuple(headers) or None
    return None


def _header_text(value) -> str:
    if value is None or isinstance(value, (FormulaWithoutValue, ExcelError)):
        return ""
    return " ".join(str(value).split())


def _find_sheet(workbook) -> tuple:
    candidates = []
    for sheet in workbook.worksheets:
        headers = _header_row(workbook, sheet)
        if headers is None:
            continue
        found = {normalize_header(h) for h in headers if h}
        if all(normalize_header(h) in found for h in REQUIRED_HEADERS):
            candidates.append((sheet, headers))
    names = ", ".join(workbook.sheetnames)
    if not candidates:
        raise _FileRejected(
            "sheet_not_found",
            f"No sheet has the Bid report headers in row 1 ({', '.join(REQUIRED_HEADERS)}); sheets: {names}",
        )
    if len(candidates) > 1:
        raise _FileRejected("ambiguous_sheet", f"More than one sheet has the Bid report headers; sheets: {names}")
    return candidates[0]


def _map_headers(headers, limits: ReaderLimits):
    """{column index: ColumnSpec}, file warnings, missing optional and ignored headers."""
    if len(headers) > limits.max_columns:
        raise _FileRejected("too_many_columns", f"Row 1 has more than {limits.max_columns} columns")
    seen, by_index, ignored, file_warnings = {}, {}, [], []
    for index, header in enumerate(headers, start=1):
        if not header:
            raise _FileRejected("blank_header", f"Column {openpyxl.utils.get_column_letter(index)} has no header")
        key = normalize_header(header)
        if key in seen:
            raise _FileRejected("duplicate_header", f'The header "{header}" appears more than once')
        seen[key] = index
        spec = _SPECS_BY_HEADER.get(key)
        if spec:
            by_index[index] = spec
        elif key in _IGNORED_KEYS:
            ignored.append(header)
        else:
            ignored.append(header)
            file_warnings.append(ReadIssue(
                Severity.WARNING, "unknown_column", "Column not part of the Bid report; ignored", row=1, column=header,
            ))
    missing = [spec for spec in BID_COLUMNS if normalize_header(spec.header) not in seen]
    for spec in missing:
        file_warnings.append(ReadIssue(
            Severity.WARNING, "missing_optional_column", "Optional column absent; the field stays empty",
            row=1, column=spec.header, field=spec.field,
        ))
    return by_index, file_warnings, tuple(spec.header for spec in missing), tuple(ignored)


# --- field parsing -------------------------------------------------------------------

class _FieldError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _is_absent(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in ABSENT_MARKERS)


def _reject_special(value) -> None:
    if isinstance(value, FormulaWithoutValue):
        raise _FieldError("formula_without_value", "Formula has no calculated value; recalculate and save in Excel")
    if isinstance(value, ExcelError):
        raise _FieldError("excel_error", "Cell holds an Excel error value")
    if isinstance(value, bool):
        raise _FieldError("invalid_type", "Unexpected TRUE/FALSE value")


def _integral(value: Decimal) -> int | None:
    return int(value) if value == value.to_integral_value() else None


def _parse_text(value, limits: ReaderLimits) -> str:
    _reject_special(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        raise _FieldError("invalid_type", "A date where text was expected")
    text = value.strip()
    if len(text) > limits.max_text_length:
        raise _FieldError("text_too_long", f"Longer than {limits.max_text_length} characters")
    return text


def _parse_date(value) -> date:
    _reject_special(value)
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        match = _TEXT_DATE.fullmatch(value.strip())
        if not match:
            raise _FieldError("invalid_date", "Not a date in dd/mm/yyyy format")
        day, month, year = (int(part) for part in match.groups())
        try:
            parsed = date(year, month, day)
        except ValueError:
            raise _FieldError("invalid_date", "Not a valid calendar date") from None
    else:
        raise _FieldError("invalid_date", "Expected a date (dd/mm/yyyy text or an Excel date cell)")
    if not _MIN_YEAR <= parsed.year <= _MAX_YEAR:
        raise _FieldError("invalid_date", f"Date outside {_MIN_YEAR}-{_MAX_YEAR}")
    return parsed


def _parse_whole_number(value, what: str) -> int:
    _reject_special(value)
    if isinstance(value, Decimal):
        number = _integral(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise _FieldError("invalid_type", f"{what} must be a whole number")
    if number is None:
        raise _FieldError("invalid_number", f"{what} must be a whole number, not a fraction")
    if number < 0:
        raise _FieldError("invalid_number", f"{what} can't be negative")
    return number


def _parse_year(value) -> int:
    year = _parse_whole_number(value, "Year")
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        raise _FieldError("invalid_number", f"Year outside {_MIN_YEAR}-{_MAX_YEAR}")
    return year


def _parse_revenue(value) -> int:
    _reject_special(value)
    if isinstance(value, (datetime, date)):
        raise _FieldError("invalid_type", "A date where an amount was expected")
    try:
        return reais_to_cents(value.strip() if isinstance(value, str) else value)
    except InvalidMoneyValue as exc:
        raise _FieldError("invalid_amount", f"Amount {exc.reason}") from None  # reason only, never the value


def _parse_code(value, number_format: Callable[[], str]) -> tuple[str, bool, list[tuple[str, str]]]:
    """(code text, needs review, warnings as (code, message))."""
    _reject_special(value)
    if isinstance(value, (datetime, date)):
        raise _FieldError("invalid_type", "A date where a code was expected")
    if isinstance(value, str):
        code = value.strip()
        warnings_ = [] if _EXPECTED_CODE.fullmatch(code) else [
            ("unexpected_code_format", 'Code is not in the usual "999999-99" format')
        ]
        return code, False, warnings_
    number = _integral(value)
    if number is None or number < 0:
        raise _FieldError("invalid_code", "Numeric code must be a whole, non-negative number")
    fmt = number_format()
    if _ZERO_PAD_FORMAT.fullmatch(fmt) and len(fmt) >= len(str(number)):
        return str(number).zfill(len(fmt)), False, [
            ("code_from_number", f"Code stored as a number; zero-padded to {len(fmt)} digits as Excel displays it")
        ]
    return str(number), True, [
        ("code_needs_review", "Code stored as a number: leading zeros may have been lost; review against the source")
    ]


# --- rows ------------------------------------------------------------------------------

def _read_row(row_number, cells, by_index, headers, workbook, limits) -> BidRow:
    values, errors, row_warnings, empty = {"row_number": row_number}, [], [], []
    raw_cells = {}
    for index, spec in by_index.items():
        value, style_id = cells.get(index, (None, 0))
        header = headers[index - 1]
        raw_cells[header] = value

        def issue(severity, code, message):
            return ReadIssue(severity, code, message, row=row_number, column=header, field=spec.field)

        if _is_absent(value):
            empty.append(spec.field)
            if spec.required_value:
                errors.append(issue(Severity.ERROR, "required_value_missing", "Required value is empty"))
            continue
        try:
            if spec.kind == "code":
                code, review, code_warnings = _parse_code(value, lambda: _number_format(workbook, style_id))
                values["code"], values["code_needs_review"] = code, review
                row_warnings.extend(issue(Severity.WARNING, c, m) for c, m in code_warnings)
            elif spec.kind == "text":
                values[spec.field] = _parse_text(value, limits)
            elif spec.kind == "date":
                values[spec.field] = _parse_date(value)
            elif spec.kind == "year":
                values[spec.field] = _parse_year(value)
            elif spec.kind == "volume":
                volume = _parse_whole_number(value, "Volume")
                values[spec.field] = volume
                if volume == 1:
                    row_warnings.append(issue(
                        Severity.WARNING, "volume_one", "Volume is 1, often a placeholder; kept as 1 - please confirm",
                    ))
            elif spec.kind == "revenue":
                values[spec.field] = _parse_revenue(value)
        except _FieldError as exc:
            errors.append(issue(Severity.ERROR, exc.code, str(exc)))

    extra = [i for i, (value, _) in cells.items() if i > len(headers) and not _is_absent(value)]
    if extra:
        row_warnings.append(ReadIssue(
            Severity.WARNING, "value_outside_headers", f"{len(extra)} value(s) in columns without a header ignored",
            row=row_number,
        ))
    return BidRow(
        raw=RawRow(row_number, raw_cells),
        record=BidRecord(**values),
        errors=tuple(errors),
        warnings=tuple(row_warnings),
        empty_fields=tuple(empty),
    )


def _is_blank(cells) -> bool:
    """No value at all ("-" counts as a value here: such a row is reported, not skipped)."""
    return all(value is None or (isinstance(value, str) and not value.strip()) for value, _ in cells.values())


# --- entry points --------------------------------------------------------------------------

def read_bid_excel(data: bytes, filename: str, limits: ReaderLimits = DEFAULT_LIMITS) -> BidReadResult:
    """Read an uploaded Bids report. Never raises for a bad file - see file_errors."""
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
        return BidReadResult(file=metadata, file_errors=(exc.issue,))


def read_bid_excel_file(path: str, limits: ReaderLimits = DEFAULT_LIMITS) -> BidReadResult:
    """Same, from a file path; refuses an oversized file before reading it."""
    size = os.path.getsize(path)
    if size > limits.max_file_bytes:
        name = basename(path)
        return BidReadResult(
            file=FileMetadata(filename=name, size_bytes=size, sha256=""),
            file_errors=(ReadIssue(Severity.ERROR, "file_too_large", f"The file is larger than {limits.max_file_bytes} bytes"),),
        )
    with open(path, "rb") as f:
        return read_bid_excel(f.read(), path, limits)


def _read_workbook(workbook, metadata: FileMetadata, limits: ReaderLimits) -> BidReadResult:
    try:
        sheet, headers = _find_sheet(workbook)
        by_index, file_warnings, missing, ignored = _map_headers(headers, limits)
        rows, blank = [], 0
        for row_number, cells in _iter_rows(workbook, sheet):
            if row_number == 1:
                continue
            if _is_blank(cells):
                blank += 1
                continue
            if len(rows) == limits.max_rows:
                raise _FileRejected("too_many_rows", f"More than {limits.max_rows} data rows")
            rows.append(_read_row(row_number, cells, by_index, headers, workbook, limits))
    except _FileRejected:
        raise
    except Exception as exc:  # malformed sheet XML, bad CRC, ...
        raise _unreadable(exc, "The worksheet is corrupted or can't be read") from None

    if not rows:
        raise _FileRejected("no_data_rows", "The Bid sheet has headers but no data rows")
    sheet_info = SheetInfo(
        name=sheet.title,
        all_sheet_names=tuple(workbook.sheetnames),
        headers=headers,
        mapped_columns={headers[i - 1]: spec.field for i, spec in by_index.items()},
        missing_optional_columns=missing,
        ignored_columns=ignored,
    )
    summary = ReadSummary(
        rows_read=len(rows),
        valid_rows=sum(row.is_valid for row in rows),
        invalid_rows=sum(not row.is_valid for row in rows),
        rows_with_warnings=sum(bool(row.warnings) for row in rows),
        blank_rows_skipped=blank,
        row_errors=sum(len(row.errors) for row in rows),
        row_warnings=sum(len(row.warnings) for row in rows),
    )
    return BidReadResult(
        file=metadata, sheet=sheet_info, rows=tuple(rows), file_warnings=tuple(file_warnings), summary=summary,
    )
