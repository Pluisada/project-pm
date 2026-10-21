"""Pydantic schemas for request/response validation."""
from typing import Any, Dict, List, Optional
from datetime import date, datetime
from pydantic import BaseModel, EmailStr, Field, field_validator

from lead_options import DEAL_CURRENCY, ConfidenceLevel, Currency, LeadSource, StageKind
from models import UserRole
from password_policy import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, validate_password


# User / Setup Schemas
class SetupRequest(BaseModel):
    """Schema for bootstrapping the first (admin) user."""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def password_meets_policy(cls, value):
        return validate_password(value)


class SignupRequest(BaseModel):
    """Schema for requesting a signup link by email."""
    email: EmailStr


class SignupCompleteRequest(BaseModel):
    """Schema for finishing signup from the emailed link."""
    token: str
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def password_meets_policy(cls, value):
        return validate_password(value)


class ForgotPasswordRequest(BaseModel):
    """Schema for requesting a password reset link."""
    identifier: str = Field(..., min_length=1, max_length=255)


class ResetPasswordRequest(BaseModel):
    """Schema for setting a new password from a reset link."""
    token: str
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def password_meets_policy(cls, value):
        return validate_password(value)


class SetupStatusResponse(BaseModel):
    """Schema for whether the system still needs an admin account."""
    needs_setup: bool
    setup_enabled: bool


class UserCreate(BaseModel):
    """Schema for an admin inviting a new (member) user. No password: the
    invitee sets it from an emailed link, which also verifies the address."""
    email: EmailStr


class UserResponse(BaseModel):
    """Schema for user response."""
    id: int
    username: str
    role: UserRole
    created_at: datetime

    class Config:
        from_attributes = True


# Board Schemas
class BoardCreate(BaseModel):
    """Schema for creating a board."""
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class BoardUpdate(BaseModel):
    """Schema for updating a board."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class BoardResponse(BaseModel):
    """Schema for board response."""
    id: int
    user_id: int
    title: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DefaultBoardResponse(BoardResponse):
    """The company's default board; `created` is true only when this request created it (HTTP 201)."""
    created: bool


# Column Schemas
class ColumnCreate(BaseModel):
    """Schema for creating a column."""
    title: str = Field(..., min_length=1, max_length=255)
    position: int = Field(default=0, ge=0)
    stage_kind: StageKind = "open"


class ColumnUpdate(BaseModel):
    """Schema for updating a column. Renaming never changes stage_kind; the
    kind only changes when it's sent explicitly."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    position: Optional[int] = Field(None, ge=0)
    stage_kind: Optional[StageKind] = None


class ColumnResponse(BaseModel):
    """Schema for column response."""
    id: int
    board_id: int
    title: str
    position: int
    stage_kind: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Card Schemas
class CardCreate(BaseModel):
    """Schema for creating a card."""
    column_id: int
    title: str = Field(..., min_length=1, max_length=255)
    details: Optional[str] = Field(None, max_length=5000)
    position: int = Field(default=0, ge=0)
    # Optional annual Deal Value (USD): deal_value is the card's deal_year amount, the next year's is separate.
    deal_value: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    deal_value_next_year: float = Field(default=0, ge=0, allow_inf_nan=False)


NEXT_STEP_MAX_LENGTH = 100


class CardUpdate(BaseModel):
    """Schema for updating a card's content and lead fields.

    Callers use model_dump(exclude_unset=True): a field left out is
    unchanged, a field sent as null is cleared (title can't be cleared).
    """
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    details: Optional[str] = Field(None, max_length=5000)
    company: Optional[str] = Field(None, max_length=255)
    contact: Optional[str] = Field(None, max_length=255)
    deal_value: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    # Same currency as deal_value; "cleared" means 0, the column is never null.
    deal_value_next_year: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    currency: Optional[Currency] = None
    expected_close_date: Optional[date] = None
    lead_source: Optional[LeadSource] = None
    # Checked against the database's product catalog by card_rules, not here.
    product_line: Optional[str] = Field(None, max_length=100)
    # Exactly LOW, MEDIUM or HIGH (upper case), or null = not assigned; anything else is a 422.
    confidence_level: Optional[ConfidenceLevel] = None
    salesperson_id: Optional[int] = None
    # Next Action Date and its Action (stored in card_bid_details).
    next_step_due: Optional[date] = None
    next_step: Optional[str] = Field(None, max_length=NEXT_STEP_MAX_LENGTH)

    @field_validator("next_step", mode="before")
    @classmethod
    def blank_action_is_null(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("deal_value_next_year")
    @classmethod
    def next_year_defaults_to_zero(cls, value):
        return 0.0 if value is None else value

    @field_validator("title")
    @classmethod
    def title_not_null(cls, value):
        if value is None:
            raise ValueError("title cannot be cleared")
        return value


class CardMove(BaseModel):
    """Schema for moving a card to another column."""
    column_id: int
    position: int = Field(default=0, ge=0)


class BidDetailsResponse(BaseModel):
    """Read-only Bid report fields of an imported card (card_bid_details).

    Revenue stays in integer cents of revenue_currency; none of this is
    cards.deal_value / expected_close_date. project_lead and cpm are source
    names, never matched to a user.
    """
    plant: Optional[str] = None
    customer_type: Optional[str] = None
    business_type: Optional[str] = None
    product_category: Optional[str] = None
    product: Optional[str] = None
    solution_type: Optional[str] = None
    volume_current_year: Optional[int] = None
    volume_next_year: Optional[int] = None
    production_start_date: Optional[date] = None
    revenue_current_year_cents: Optional[int] = None
    revenue_next_year_cents: Optional[int] = None
    revenue_currency: str
    status_note: Optional[str] = None
    project_lead: Optional[str] = None
    cpm: Optional[str] = None
    next_step: Optional[str] = None
    next_step_due: Optional[date] = None
    close_reason: Optional[str] = None
    close_reason_note: Optional[str] = None

    class Config:
        from_attributes = True


class SourceRefResponse(BaseModel):
    """Read-only identity of the source opportunity (card_source_refs)."""
    source_code: str

    class Config:
        from_attributes = True


class CardResponse(BaseModel):
    """Schema for card response."""
    id: int
    column_id: int
    title: str
    details: Optional[str]
    position: int
    has_member_edits: bool
    company: Optional[str] = None
    contact: Optional[str] = None
    deal_value: Optional[float] = None
    deal_value_next_year: float = 0
    deal_year: int
    currency: Optional[str] = None
    expected_close_date: Optional[date] = None
    lead_source: Optional[str] = None
    product_line: Optional[str] = None
    confidence_level: Optional[str] = None
    salesperson_id: Optional[int] = None
    salesperson_name: Optional[str] = None
    bid_details: Optional[BidDetailsResponse] = None
    source_ref: Optional[SourceRefResponse] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("currency", mode="before")
    @classmethod
    def deal_value_is_usd(cls, _value):
        """Deal Value is always USD, whatever legacy code the row still holds."""
        return DEAL_CURRENCY

    class Config:
        from_attributes = True


class CardEditHistoryEntry(BaseModel):
    """Schema for a single member-edit history entry on a card."""
    id: int
    edited_by: str
    changes: dict
    created_at: datetime


class SalespersonOption(BaseModel):
    id: int
    username: str


class ProductBody(BaseModel):
    """Create or rename a product."""
    name: str = Field(..., max_length=100)


class ProductResponse(BaseModel):
    id: Optional[int]  # None: a built-in default of a company whose catalog is not initialized
    name: str
    card_count: int


class ProductCatalogResponse(BaseModel):
    initialized: bool
    products: List[ProductResponse]


class LeadOptionsResponse(BaseModel):
    """Allowed values for the lead fields, for the edit form's selects."""
    currencies: List[str]
    lead_sources: List[str]
    product_lines: List[str]
    salespeople: List[SalespersonOption]


class ReportGroup(BaseModel):
    """One row of a breakdown (by salesperson, product line or source).

    key is the display label. salesperson_id is the stable identity, set only in by_salesperson
    (null there means unassigned); equal labels with different ids are separate rows.
    """
    key: str
    salesperson_id: Optional[int] = None
    open_count: int
    # *_totals: current-year amounts (deal_value), as before; *_next_totals:
    # next-year amounts; *_sum_totals: their two-year total. All per currency.
    open_totals: Dict[str, float]
    open_next_totals: Dict[str, float] = {}
    open_sum_totals: Dict[str, float] = {}
    won_count: int
    won_totals: Dict[str, float]
    won_next_totals: Dict[str, float] = {}
    won_sum_totals: Dict[str, float] = {}


class ReportStage(BaseModel):
    column_id: int
    title: str
    stage_kind: str
    count: int
    totals: Dict[str, float]
    next_totals: Dict[str, float] = {}
    sum_totals: Dict[str, float] = {}


class ReportKpis(BaseModel):
    open_count: int
    open_totals: Dict[str, float]
    open_next_totals: Dict[str, float] = {}
    open_sum_totals: Dict[str, float] = {}
    won_count: int
    won_totals: Dict[str, float]
    won_next_totals: Dict[str, float] = {}
    won_sum_totals: Dict[str, float] = {}
    lost_count: int
    win_rate: Optional[float]


class ClosingSoonLead(BaseModel):
    card_id: int
    title: str
    company: Optional[str]
    stage: str
    salesperson_name: Optional[str]
    deal_value: Optional[float]
    deal_value_next_year: float = 0
    deal_value_total: Optional[float] = None
    deal_year: Optional[int] = None
    currency: Optional[str]
    expected_close_date: date
    overdue: bool

    @field_validator("currency", mode="before")
    @classmethod
    def deal_value_is_usd(cls, _value):
        return DEAL_CURRENCY


class BoardReport(BaseModel):
    """Sales report over a board's leads. Money totals are per currency."""
    kpis: ReportKpis
    stages: List[ReportStage]
    by_salesperson: List[ReportGroup]
    by_product_line: List[ReportGroup]
    by_source: List[ReportGroup]
    # Always four rows in this order: LOW, MEDIUM, HIGH, "Not assigned" (zeros included).
    by_confidence_level: List[ReportGroup] = []
    closing_soon: List[ClosingSoonLead]
    # Years the amounts were entered for (the cards' stored deal_year).
    deal_years: List[int] = []


class UndoStatus(BaseModel):
    """Whether the current user has a change to undo, and what it is."""
    available: bool
    description: Optional[str] = None
    undone: Optional[str] = None  # set by POST /undo: what was just reversed


# ---------------------------------------------------------------------------
# KPI responses (Part 19). Definitions: docs/KPIS.md. All read-only.
# ---------------------------------------------------------------------------


class MoneyTotal(BaseModel):
    """One currency's total; currencies are never summed together."""
    total: float
    count: int


class KpiRate(BaseModel):
    """A ratio of distinct opportunities; value is null when denominator is 0."""
    value: Optional[float]
    numerator: int
    denominator: int
    unavailable_reason: Optional[str]


class KpiDuration(BaseModel):
    """Duration statistics in days; mean/median null when count is 0."""
    count: int
    mean_days: Optional[float]
    median_days: Optional[float]


class KpiPeriod(BaseModel):
    label: str
    start: datetime  # inclusive, UTC
    end: datetime    # exclusive, UTC


class KpiCoverage(BaseModel):
    """complete: period starts at/after tracking start; partial: before it;
    no_history: the board has no events at all."""
    status: str
    tracking_started_at: Optional[datetime]


class OutcomeFlow(BaseModel):
    events: int                  # every won (or lost) transition in the period
    opportunities: int           # distinct cards with one
    card_ids: List[int]
    # Closing value: the value recorded on each card's last won (lost) event
    # in the period - historical, unaffected by later edits.
    values: Dict[str, MoneyTotal]
    without_value: int           # recorded, but the card had no value then
    value_unavailable: int       # event predates value snapshots: unknown
    # The same cards at their value today (comparison only, not history).
    current_values: Dict[str, MoneyTotal]
    current_without_value: int   # no current value, or deleted
    # The same cards' next-year amount and two-year total today (comparison
    # only). The closing values above are snapshots of deal_value alone: they
    # predate the annual split and can't be divided by year.
    current_next_year_values: Dict[str, MoneyTotal] = {}
    current_total_values: Dict[str, MoneyTotal] = {}
    still_in_outcome: int        # of those cards, how many are in that stage now


class ReopenFlow(BaseModel):
    events: int
    opportunities: int


class StageReach(BaseModel):
    column_id: int
    title: str
    stage_kind: str
    count: int
    rate: KpiRate


class CohortKpis(BaseModel):
    """Cards whose creation was observed in the period (never baselines)."""
    size: int
    card_ids: List[int]
    won_first: int
    lost_first: int
    still_open: int
    deleted_without_outcome: int
    win_rate: KpiRate          # won_first / size (matures over time)
    closed_win_rate: KpiRate   # won_first / (won_first + lost_first)
    reached: List[StageReach]


class VelocityKpis(BaseModel):
    """created -> first won/lost, for first conclusions in the period."""
    won: KpiDuration
    lost: KpiDuration
    excluded_unobserved_creation: int


class PeriodKpis(BaseModel):
    period: KpiPeriod
    coverage: KpiCoverage
    won: OutcomeFlow
    lost: OutcomeFlow
    reopened: ReopenFlow
    win_rate_closed: KpiRate   # last outcome in period: won / (won + lost)
    cohort: CohortKpis
    velocity: VelocityKpis


class KpiSummaryResponse(BaseModel):
    as_of: datetime
    current: PeriodKpis
    previous: Optional[PeriodKpis] = None  # same-length period ending at current.start


class StageAdvancement(BaseModel):
    column_id: int
    title: str
    stage_kind: str
    position: int
    entered: int          # distinct observed entries in the period (denominator)
    advanced: int         # first exit to a higher-position column
    lost: int
    other_exit: int       # backward, deleted, or to a column that no longer exists
    still_in_stage: int
    rate: KpiRate         # advanced / entered


class KpiConversionResponse(BaseModel):
    as_of: datetime
    period: KpiPeriod
    coverage: KpiCoverage
    win_rate_closed: KpiRate
    cohort: CohortKpis
    advancement: List[StageAdvancement]  # open stages only


class CurrentStays(KpiDuration):
    observed_entries: int
    baseline_entries: int    # durations of these are lower bounds


class StageTime(BaseModel):
    column_id: int
    title: str
    stage_kind: str
    complete: KpiDuration    # observed entry and exit, exited in the period
    partial: KpiDuration     # baseline entry, observed exit (lower bounds)
    ended_by_deletion: int
    current: CurrentStays    # still in the stage at as_of


class KpiStageTimeResponse(BaseModel):
    as_of: datetime
    period: KpiPeriod
    coverage: KpiCoverage
    stages: List[StageTime]


class AgingRow(BaseModel):
    card_id: int
    title: str
    column_id: int
    column_title: str
    stage_kind: str
    entered_at: Optional[datetime]
    entry_observed: Optional[bool]
    age_days: Optional[float]    # a lower bound when entry_observed is false
    history: str                 # observed | since_tracking_start | none
    deal_value: Optional[float]
    deal_value_next_year: float = 0
    deal_value_total: Optional[float] = None
    deal_year: Optional[int] = None
    currency: Optional[str]

    @field_validator("currency", mode="before")
    @classmethod
    def deal_value_is_usd(cls, _value):
        return DEAL_CURRENCY


class KpiAgingResponse(BaseModel):
    as_of: datetime
    tracking_started_at: Optional[datetime]
    opportunities: List[AgingRow]


class PipelineStage(BaseModel):
    column_id: int
    title: str
    stage_kind: str
    count: int
    values: Dict[str, MoneyTotal]      # current-year amounts (deal_value)
    without_value: int
    next_year_values: Dict[str, MoneyTotal] = {}
    total_values: Dict[str, MoneyTotal] = {}


class KpiPipelineResponse(BaseModel):
    as_of: datetime
    stages: List[PipelineStage]
    deal_years: List[int] = []


# Board with nested data
class ColumnWithCards(ColumnResponse):
    """Column with cards."""
    cards: List[CardResponse] = []


class BoardDetail(BoardResponse):
    """Board with all columns and cards."""
    columns: List[ColumnWithCards] = []


# Error Schemas
class ErrorResponse(BaseModel):
    """Schema for error responses."""
    detail: str
    status_code: int


# Success Schemas
class SuccessResponse(BaseModel):
    """Schema for success responses."""
    message: str
    data: Optional[dict] = None


# Salcomp Bid import (Part 22E)
class ImportDecision(BaseModel):
    """One explicit choice for one Excel row (bid_import_preview.RowDecision).
    Rows are named by their Excel row number and stages by column title: no
    card id, source key or column id is ever taken from the client."""
    model_config = {"extra": "forbid", "strict": True}

    row_number: int = Field(..., ge=2)
    target_stage: Optional[str] = Field(None, min_length=1, max_length=200)
    confirm_proposed_stage: bool = False
    skip: bool = False
    confirm_update: bool = False
    confirm_move: bool = False


class ImportBatchRevert(BaseModel):
    """Revert state of one batch (Part 23). `revertible` is structural (completed,
    has a ledger, not yet reverted); whether conflicts block it is only known from
    the revert preview. The counts are the persisted result, None until reverted."""
    has_ledger: bool
    revertible: bool
    reason: Optional[str] = None  # why not: batch_not_completed | no_ledger | already_reverted
    reverted: bool
    reverted_at: Optional[datetime] = None
    reverted_by_user_id: Optional[int] = None
    reverted_by_username: Optional[str] = None
    outcome: Optional[str] = None  # reverted | no_commercial_changes
    no_commercial_changes: Optional[bool] = None
    cards_deleted: Optional[int] = None
    cards_restored: Optional[int] = None
    moves_reverted: Optional[int] = None
    already_deleted: Optional[int] = None
    summary: Optional[dict] = None


class ImportBatchResponse(BaseModel):
    """An import attempt's metadata, state and counts - never row content."""
    id: int
    board_id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    filename: str
    file_sha256: str
    status: str
    failure_kind: Optional[str] = None
    failure_reason: Optional[str] = None
    created_count: int
    updated_count: int
    skipped_count: int
    review_count: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    revert: Optional[ImportBatchRevert] = None


class ImportBatchDetail(ImportBatchResponse):
    """Plus the persisted diagnostics: how the batch was mapped (rule versions,
    stages, decisions) and how many cards it changed."""
    mapping: Optional[dict] = None
    card_changes_count: int


class ImportBatchList(BaseModel):
    items: List[ImportBatchResponse]
    total: int
    limit: int
    offset: int


class ImportResult(BaseModel):
    batch: Optional[ImportBatchResponse] = None  # None for a no-op: nothing was written
    no_op: bool = False
    created_card_ids: List[int]
    updated_card_ids: List[int]
    moved_card_ids: List[int]
    skipped: int
    review: int


class RevertRequest(BaseModel):
    """Confirmation of a revert: the plan_fingerprint of the preview the admin saw."""
    model_config = {"extra": "forbid", "strict": True}

    plan_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class RevertBlockerResponse(BaseModel):
    code: str
    card_id: Optional[int] = None
    field: Optional[str] = None
    imported: Any = None  # the value the import left (long text is cut)
    current: Any = None
    batch_ids: List[int] = []


class RevertOperationResponse(BaseModel):
    kind: str  # delete_card | restore_fields | move_card | delete_bid_details
    card_id: int
    fields: Optional[List[str]] = None  # restore_fields: names only, never values
    from_column_id: Optional[int] = None
    to_column_id: Optional[int] = None


class RevertItemResponse(BaseModel):
    item_id: int
    card_id: int
    source_key: str
    action: str  # created | updated | observed
    observation: Optional[str] = None
    classification: str
    operations: List[RevertOperationResponse]
    blockers: List[RevertBlockerResponse]


class RevertDependentBatch(BaseModel):
    batch_id: int
    card_ids: List[int]


class RevertCounts(BaseModel):
    items: int
    cards_to_delete: int
    cards_to_restore: int
    moves_to_revert: int
    bid_details_to_delete: int
    already_deleted: int
    already_reverted: int
    observed: int
    blocked: int


class RevertPlanResponse(BaseModel):
    batch_id: int
    status: str  # ready | blocked | not_revertible | already_reverted
    reason: Optional[str] = None
    revertible: bool  # status == ready: the confirmation would be accepted now
    no_commercial_changes: bool
    ledger_version: Optional[int] = None
    plan_fingerprint: str
    counts: RevertCounts
    blockers: List[RevertBlockerResponse]
    dependent_batches: List[RevertDependentBatch]
    items: List[RevertItemResponse]


class RevertResultCounts(BaseModel):
    cards_deleted: int
    cards_restored: int
    moves_reverted: int
    already_deleted: int
    bid_details_deleted: int
    already_reverted: int
    observed: int


class RevertResult(BaseModel):
    batch: ImportBatchResponse
    outcome: str  # reverted | no_commercial_changes
    no_commercial_changes: bool
    counts: RevertResultCounts


class AIChatRequest(BaseModel):
    """Chat message. Length is checked in the route (against AI_MAX_MESSAGE_CHARS)
    so an over-long message gets a short error that doesn't echo it back."""
    message: str = ""
