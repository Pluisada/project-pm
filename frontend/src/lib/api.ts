/**
 * Frontend API client for communicating with backend
 */

import { API_BASE_URL, extractErrorDetail, getAuthHeader } from "./auth";

export interface ApiError {
  detail: string;
  status_code?: number;
}

/**
 * Narrow a caught (unknown-typed) error thrown by apiCall down to its detail
 * message, falling back for anything that isn't a recognizable ApiError.
 */
export function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as Partial<ApiError>).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

/**
 * Make an API request with auth headers and error handling
 */
async function apiCall<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;
  const headers = {
    "Content-Type": "application/json",
    ...getAuthHeader(),
    ...options.headers,
  };

  try {
    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw {
        status_code: response.status,
        detail: extractErrorDetail(errorData, `HTTP ${response.status}`),
      } as ApiError;
    }

    // Handle 204 No Content
    if (response.status === 204) {
      return undefined as T;
    }

    return await response.json();
  } catch (error) {
    if (error instanceof TypeError) {
      throw {
        status_code: 0,
        detail: "Network error - check connection",
      } as ApiError;
    }
    throw error;
  }
}

// ============================================================================
// BOARD API
// ============================================================================

export interface BoardResponse {
  id: number;
  user_id: number;
  title: string;
  description?: string;
  created_at: string;
  updated_at: string;
}

/** A stage's commercial meaning; reports key on this, never on the title. */
export type StageKind = "open" | "won" | "lost";

export interface ColumnResponse {
  id: number;
  board_id: number;
  title: string;
  position: number;
  stage_kind: StageKind;
  created_at: string;
  updated_at: string;
  cards?: CardResponse[];
}

/** Sales lead fields on a card; every one is optional (null when unset). */
export interface LeadFields {
  company: string | null;
  contact: string | null;
  deal_value: number | null;
  currency: string | null;
  expected_close_date: string | null; // YYYY-MM-DD
  lead_source: string | null;
  product_line: string | null;
  salesperson_id: number | null;
}

export interface CardResponse extends LeadFields {
  id: number;
  column_id: number;
  title: string;
  details?: string;
  position: number;
  has_member_edits: boolean;
  salesperson_name: string | null;
  created_at: string;
  updated_at: string;
}

/** Partial card update: omitted fields are unchanged, null clears a lead field. */
export type CardUpdatePayload = Partial<LeadFields> & { title?: string; details?: string };

export interface CardEditHistoryEntry {
  id: number;
  edited_by: string;
  changes: Record<string, { old: string | number | null; new: string | number | null }>;
  created_at: string;
}

export interface BoardDetail extends BoardResponse {
  columns: ColumnResponse[];
}

export async function listBoards(): Promise<BoardResponse[]> {
  return apiCall<BoardResponse[]>("/api/boards");
}

export async function getBoard(boardId: number): Promise<BoardDetail> {
  return apiCall<BoardDetail>(`/api/boards/${boardId}`);
}

export async function createBoard(
  title: string,
  description?: string
): Promise<BoardResponse> {
  return apiCall<BoardResponse>("/api/boards", {
    method: "POST",
    body: JSON.stringify({ title, description }),
  });
}

export async function updateBoard(
  boardId: number,
  title?: string,
  description?: string
): Promise<BoardResponse> {
  return apiCall<BoardResponse>(`/api/boards/${boardId}`, {
    method: "PUT",
    body: JSON.stringify({ title, description }),
  });
}

export async function deleteBoard(boardId: number): Promise<void> {
  return apiCall<void>(`/api/boards/${boardId}`, {
    method: "DELETE",
  });
}

// ============================================================================
// COLUMN API
// ============================================================================

export async function createColumn(
  boardId: number,
  title: string,
  position: number = 0
): Promise<ColumnResponse> {
  return apiCall<ColumnResponse>(`/api/boards/${boardId}/columns`, {
    method: "POST",
    body: JSON.stringify({ title, position }),
  });
}

export async function updateColumn(
  boardId: number,
  columnId: number,
  title?: string,
  position?: number
): Promise<ColumnResponse> {
  return apiCall<ColumnResponse>(`/api/boards/${boardId}/columns/${columnId}`, {
    method: "PUT",
    body: JSON.stringify({ title, position }),
  });
}

export async function deleteColumn(
  boardId: number,
  columnId: number
): Promise<void> {
  return apiCall<void>(`/api/boards/${boardId}/columns/${columnId}`, {
    method: "DELETE",
  });
}

// ============================================================================
// CARD API
// ============================================================================

export async function createCard(
  boardId: number,
  columnId: number,
  title: string,
  details?: string,
  position: number = 0
): Promise<CardResponse> {
  return apiCall<CardResponse>(`/api/boards/${boardId}/cards`, {
    method: "POST",
    body: JSON.stringify({ column_id: columnId, title, details, position }),
  });
}

export async function updateCard(
  boardId: number,
  cardId: number,
  updates: CardUpdatePayload
): Promise<CardResponse> {
  return apiCall<CardResponse>(`/api/boards/${boardId}/cards/${cardId}`, {
    method: "PUT",
    body: JSON.stringify(updates),
  });
}

export async function moveCard(
  boardId: number,
  cardId: number,
  columnId: number,
  position: number
): Promise<CardResponse> {
  return apiCall<CardResponse>(`/api/boards/${boardId}/cards/${cardId}/position`, {
    method: "PUT",
    body: JSON.stringify({ column_id: columnId, position }),
  });
}

export async function deleteCard(boardId: number, cardId: number): Promise<void> {
  return apiCall<void>(`/api/boards/${boardId}/cards/${cardId}`, {
    method: "DELETE",
  });
}

export async function getCardHistory(
  boardId: number,
  cardId: number
): Promise<CardEditHistoryEntry[]> {
  return apiCall<CardEditHistoryEntry[]>(`/api/boards/${boardId}/cards/${cardId}/history`);
}

// ============================================================================
// UNDO API
// ============================================================================

/** The current user's single undoable change on a board. */
export interface UndoStatus {
  available: boolean;
  description: string | null;
  undone?: string | null; // set by undoLastChange: what was reversed
}

export async function getUndoStatus(boardId: number): Promise<UndoStatus> {
  return apiCall<UndoStatus>(`/api/boards/${boardId}/undo`);
}

export async function undoLastChange(boardId: number): Promise<UndoStatus> {
  return apiCall<UndoStatus>(`/api/boards/${boardId}/undo`, { method: "POST" });
}

// ============================================================================
// SALES LEADS API
// ============================================================================

export interface LeadOptions {
  currencies: string[];
  lead_sources: string[];
  product_lines: string[];
  salespeople: { id: number; username: string }[];
}

export async function getLeadOptions(): Promise<LeadOptions> {
  return apiCall<LeadOptions>("/api/lead-options");
}

/** Money totals keyed by currency code - never summed across currencies. */
export type CurrencyTotals = Record<string, number>;

export interface ReportGroup {
  key: string;
  open_count: number;
  open_totals: CurrencyTotals;
  won_count: number;
  won_totals: CurrencyTotals;
}

export interface BoardReport {
  kpis: {
    open_count: number;
    open_totals: CurrencyTotals;
    won_count: number;
    won_totals: CurrencyTotals;
    lost_count: number;
    win_rate: number | null;
  };
  stages: { column_id: number; title: string; stage_kind: StageKind; count: number; totals: CurrencyTotals }[];
  by_salesperson: ReportGroup[];
  by_product_line: ReportGroup[];
  by_source: ReportGroup[];
  closing_soon: {
    card_id: number;
    title: string;
    company: string | null;
    stage: string;
    salesperson_name: string | null;
    deal_value: number | null;
    currency: string | null;
    expected_close_date: string;
    overdue: boolean;
  }[];
}

export interface ReportFilters {
  salespersonId?: number;
  productLine?: string;
}

export async function getBoardReport(
  boardId: number,
  filters: ReportFilters = {}
): Promise<BoardReport> {
  const params = new URLSearchParams();
  if (filters.salespersonId !== undefined) params.set("salesperson_id", String(filters.salespersonId));
  if (filters.productLine) params.set("product_line", filters.productLine);
  const query = params.toString();
  return apiCall<BoardReport>(`/api/boards/${boardId}/report${query ? `?${query}` : ""}`);
}

// ============================================================================
// KPI API (read-only; definitions in docs/KPIS.md, shapes in backend schemas.py)
// ============================================================================

export type KpiPeriodPreset = "last_30_days" | "last_90_days" | "year_to_date" | "custom";

/** A period filter. start/end (YYYY-MM-DD, inclusive) only with "custom". */
export interface KpiPeriodQuery {
  period: KpiPeriodPreset;
  start?: string;
  end?: string;
}

/** One currency's total. KPI money is always per currency, never summed across. */
export interface MoneyTotal {
  total: number;
  count: number;
}
export type MoneyTotals = Record<string, MoneyTotal>;

/** A ratio of distinct opportunities; value is null (with a reason) when the denominator is 0. */
export interface KpiRate {
  value: number | null;
  numerator: number;
  denominator: number;
  unavailable_reason: string | null;
}

/** Duration statistics in days; mean/median are null when count is 0. */
export interface KpiDuration {
  count: number;
  mean_days: number | null;
  median_days: number | null;
}

export interface KpiPeriod {
  label: string;
  start: string; // inclusive, UTC
  end: string; // exclusive, UTC
}

/** complete: the period starts after tracking started; partial: before it; no_history: no events. */
export interface KpiCoverage {
  status: "complete" | "partial" | "no_history";
  tracking_started_at: string | null;
}

export interface OutcomeFlow {
  events: number;
  opportunities: number;
  card_ids: number[];
  /** Closing value: recorded on each card's last won/lost event in the period. */
  values: MoneyTotals;
  without_value: number;
  /** Events from before value snapshots existed: value unknown, never replaced. */
  value_unavailable: number;
  /** The same cards at their value today - comparison only, not history. */
  current_values: MoneyTotals;
  current_without_value: number;
  still_in_outcome: number;
}

export interface StageRef {
  column_id: number;
  title: string;
  stage_kind: StageKind;
}

export interface CohortKpis {
  size: number;
  card_ids: number[];
  won_first: number;
  lost_first: number;
  still_open: number;
  deleted_without_outcome: number;
  win_rate: KpiRate;
  closed_win_rate: KpiRate;
  reached: (StageRef & { count: number; rate: KpiRate })[];
}

export interface PeriodKpis {
  period: KpiPeriod;
  coverage: KpiCoverage;
  won: OutcomeFlow;
  lost: OutcomeFlow;
  reopened: { events: number; opportunities: number };
  win_rate_closed: KpiRate;
  cohort: CohortKpis;
  velocity: { won: KpiDuration; lost: KpiDuration; excluded_unobserved_creation: number };
}

export interface KpiSummary {
  as_of: string;
  current: PeriodKpis;
  previous: PeriodKpis | null;
}

export interface KpiConversion {
  as_of: string;
  period: KpiPeriod;
  coverage: KpiCoverage;
  win_rate_closed: KpiRate;
  cohort: CohortKpis;
  /** Open stages only. */
  advancement: (StageRef & {
    position: number;
    entered: number;
    advanced: number;
    lost: number;
    other_exit: number;
    still_in_stage: number;
    rate: KpiRate;
  })[];
}

export interface KpiStageTime {
  as_of: string;
  period: KpiPeriod;
  coverage: KpiCoverage;
  stages: (StageRef & {
    complete: KpiDuration;
    /** Entered at a baseline: lower bounds. */
    partial: KpiDuration;
    ended_by_deletion: number;
    current: KpiDuration & { observed_entries: number; baseline_entries: number };
  })[];
}

export interface KpiAgingRow {
  card_id: number;
  title: string;
  column_id: number;
  column_title: string;
  stage_kind: StageKind;
  entered_at: string | null;
  entry_observed: boolean | null;
  /** A lower bound when entry_observed is false. */
  age_days: number | null;
  history: "observed" | "since_tracking_start" | "none";
  deal_value: number | null;
  currency: string | null;
}

export interface KpiAging {
  as_of: string;
  tracking_started_at: string | null;
  opportunities: KpiAgingRow[];
}

export interface KpiPipeline {
  as_of: string;
  stages: (StageRef & { count: number; values: MoneyTotals; without_value: number })[];
}

function kpiQuery(query: KpiPeriodQuery, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({ period: query.period, ...extra });
  if (query.period === "custom") {
    if (query.start) params.set("start", query.start);
    if (query.end) params.set("end", query.end);
  }
  return params.toString();
}

export async function getKpiSummary(boardId: number, query: KpiPeriodQuery, compare: boolean): Promise<KpiSummary> {
  return apiCall<KpiSummary>(`/api/boards/${boardId}/kpis/summary?${kpiQuery(query, { compare: String(compare) })}`);
}

export async function getKpiConversion(boardId: number, query: KpiPeriodQuery): Promise<KpiConversion> {
  return apiCall<KpiConversion>(`/api/boards/${boardId}/kpis/conversion?${kpiQuery(query)}`);
}

export async function getKpiStageTime(boardId: number, query: KpiPeriodQuery): Promise<KpiStageTime> {
  return apiCall<KpiStageTime>(`/api/boards/${boardId}/kpis/stage-time?${kpiQuery(query)}`);
}

export async function getKpiAging(boardId: number): Promise<KpiAging> {
  return apiCall<KpiAging>(`/api/boards/${boardId}/kpis/aging`);
}

export async function getKpiPipeline(boardId: number): Promise<KpiPipeline> {
  return apiCall<KpiPipeline>(`/api/boards/${boardId}/kpis/pipeline`);
}

// ============================================================================
// AI API
// ============================================================================

export interface AIChatResponse {
  response: string;
  // Empty object when the AI suggested no actions
  actions_applied: {
    successful?: { type: string }[];
    failed?: { type: string }[];
  };
}

export async function sendAIMessage(
  boardId: number,
  message: string
): Promise<AIChatResponse> {
  return apiCall<AIChatResponse>(`/api/boards/${boardId}/ai`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

// ============================================================================
// USER MANAGEMENT API (admin-only)
// ============================================================================

export interface UserResponse {
  id: number;
  username: string;
  role: "admin" | "member";
  created_at: string;
}

export async function listUsers(): Promise<UserResponse[]> {
  return apiCall<UserResponse[]>("/api/users");
}

export async function createUser(
  username: string,
  password: string
): Promise<UserResponse> {
  return apiCall<UserResponse>("/api/users", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}
