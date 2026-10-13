"""Pydantic schemas for request/response validation."""
from typing import Dict, List, Optional
from datetime import date, datetime
from pydantic import BaseModel, EmailStr, Field, field_validator

from lead_options import Currency, LeadSource, ProductLine, StageKind
from models import UserRole


# User / Setup Schemas
class SetupRequest(BaseModel):
    """Schema for bootstrapping the first (admin) user."""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)


class SignupRequest(BaseModel):
    """Schema for requesting a signup link by email."""
    email: EmailStr


class SignupCompleteRequest(BaseModel):
    """Schema for finishing signup from the emailed link."""
    token: str
    password: str = Field(..., min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    """Schema for requesting a password reset link."""
    identifier: str = Field(..., min_length=1, max_length=255)


class ResetPasswordRequest(BaseModel):
    """Schema for setting a new password from a reset link."""
    token: str
    password: str = Field(..., min_length=8, max_length=128)


class SetupStatusResponse(BaseModel):
    """Schema for whether the system still needs an admin account."""
    needs_setup: bool


class UserCreate(BaseModel):
    """Schema for an admin creating a new (member) user."""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)


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


class CardUpdate(BaseModel):
    """Schema for updating a card's content and lead fields.

    Callers use model_dump(exclude_unset=True): a field left out is
    unchanged, a field sent as null is cleared (title can't be cleared).
    """
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    details: Optional[str] = Field(None, max_length=5000)
    company: Optional[str] = Field(None, max_length=255)
    contact: Optional[str] = Field(None, max_length=255)
    deal_value: Optional[float] = Field(None, ge=0)
    currency: Optional[Currency] = None
    expected_close_date: Optional[date] = None
    lead_source: Optional[LeadSource] = None
    product_line: Optional[ProductLine] = None
    salesperson_id: Optional[int] = None

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
    currency: Optional[str] = None
    expected_close_date: Optional[date] = None
    lead_source: Optional[str] = None
    product_line: Optional[str] = None
    salesperson_id: Optional[int] = None
    salesperson_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

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


class LeadOptionsResponse(BaseModel):
    """Allowed values for the lead fields, for the edit form's selects."""
    currencies: List[str]
    lead_sources: List[str]
    product_lines: List[str]
    salespeople: List[SalespersonOption]


class ReportGroup(BaseModel):
    """One row of a breakdown (by salesperson, product line or source)."""
    key: str
    open_count: int
    open_totals: Dict[str, float]
    won_count: int
    won_totals: Dict[str, float]


class ReportStage(BaseModel):
    column_id: int
    title: str
    stage_kind: str
    count: int
    totals: Dict[str, float]


class ReportKpis(BaseModel):
    open_count: int
    open_totals: Dict[str, float]
    won_count: int
    won_totals: Dict[str, float]
    lost_count: int
    win_rate: Optional[float]


class ClosingSoonLead(BaseModel):
    card_id: int
    title: str
    company: Optional[str]
    stage: str
    salesperson_name: Optional[str]
    deal_value: Optional[float]
    currency: Optional[str]
    expected_close_date: date
    overdue: bool


class BoardReport(BaseModel):
    """Sales report over a board's leads. Money totals are per currency."""
    kpis: ReportKpis
    stages: List[ReportStage]
    by_salesperson: List[ReportGroup]
    by_product_line: List[ReportGroup]
    by_source: List[ReportGroup]
    closing_soon: List[ClosingSoonLead]


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
    currency: Optional[str]


class KpiAgingResponse(BaseModel):
    as_of: datetime
    tracking_started_at: Optional[datetime]
    opportunities: List[AgingRow]


class PipelineStage(BaseModel):
    column_id: int
    title: str
    stage_kind: str
    count: int
    values: Dict[str, MoneyTotal]
    without_value: int


class KpiPipelineResponse(BaseModel):
    as_of: datetime
    stages: List[PipelineStage]


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
