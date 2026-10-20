/**
 * Frontend API client for communicating with backend
 */

import { API_BASE_URL, SESSION_EXPIRED_EVENT, clearAuth, extractErrorDetail, getAuthHeader } from "./auth";
import { captureScope } from "./activeCompany";

export interface ApiError {
  detail: string;
  status_code?: number;
  /** Machine code and extra fields of a structured error body (`detail: { code, message, ... }`). */
  code?: string;
  data?: Record<string, unknown>;
}

/** The structured part of an error body, if the server sent one. */
function structuredError(errorData: unknown): Pick<ApiError, "code" | "data"> {
  const detail = (errorData as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return {};
  const data = detail as Record<string, unknown>;
  return { code: typeof data.code === "string" ? data.code : undefined, data };
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

const COMPANY_ENDPOINT = /^\/api\/companies\/([^/?#]+)/;

/** The company header lets the server refuse a request whose URL and caller disagree. It proves nothing: the
 *  server still validates the token, the membership and the role from the URL. */
export const COMPANY_HEADER = "X-Company-Slug";

/**
 * Bind a request to the company named in its own URL, at the moment it starts. A request for a company other than
 * the active one is refused before it is sent (CompanyScopeError); one already under way is aborted when the
 * active company changes. Anything that is not a company endpoint (legacy and central account calls) is untouched.
 * Call `release` when the request has finished.
 */
function scopeCompanyRequest(
  endpoint: string,
  callerSignal?: AbortSignal | null
): { headers: Record<string, string>; signal: AbortSignal | undefined; release: () => void } {
  const match = COMPANY_ENDPOINT.exec(endpoint);
  if (!match) return { headers: {}, signal: callerSignal ?? undefined, release: () => {} };
  const companySlug = decodeURIComponent(match[1]);
  const scope = captureScope(companySlug);
  const merged = new AbortController();
  const abort = () => merged.abort();
  if (scope.signal.aborted || callerSignal?.aborted) merged.abort();
  scope.signal.addEventListener("abort", abort, { once: true });
  callerSignal?.addEventListener("abort", abort, { once: true });
  return {
    headers: { [COMPANY_HEADER]: companySlug },
    signal: merged.signal,
    release: () => {
      scope.signal.removeEventListener("abort", abort);
      callerSignal?.removeEventListener("abort", abort);
    },
  };
}

/**
 * Make an API request with auth headers and error handling
 */
async function apiCall<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;
  const scoped = scopeCompanyRequest(endpoint, options.signal);
  // A FormData body needs the browser to set its own multipart boundary.
  const contentType: Record<string, string> =
    options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const headers = {
    ...contentType,
    ...getAuthHeader(),
    ...scoped.headers,
    ...options.headers,
  };

  try {
    const response = await fetch(url, {
      ...options,
      headers,
      signal: scoped.signal,
    });

    if (!response.ok) {
      await throwApiError(response, "Authorization" in headers);
    }

    // Handle 204 No Content
    if (response.status === 204) {
      return undefined as T;
    }

    return await response.json();
  } catch (error) {
    throw asNetworkError(error);
  } finally {
    scoped.release();
  }
}

/** Throw the ApiError for a failed response; a 401 on a request that carried a token ends the session. */
async function throwApiError(response: Response, hadToken: boolean): Promise<never> {
  // Only a request that carried a token can mean "session expired"; without
  // one the 401 is just the caller's answer, and no event means no loop.
  if (response.status === 401 && hadToken) {
    clearAuth();
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  const errorData = await response.json().catch(() => ({}));
  throw {
    status_code: response.status,
    detail: extractErrorDetail(errorData, `HTTP ${response.status}`),
    ...structuredError(errorData),
  } as ApiError;
}

/** True for the error fetch raises when its AbortSignal fires: an expected interruption, not a failure. */
export function isAbortError(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === "AbortError";
}

function asNetworkError(error: unknown): unknown {
  return error instanceof TypeError
    ? ({ status_code: 0, detail: "Network error - check connection" } as ApiError)
    : error;
}

/**
 * Authenticated GET of a binary file. The Authorization header is sent through
 * fetch (a plain link or navigation would lose it); errors match apiCall's.
 */
async function apiBlob(
  endpoint: string,
  signal?: AbortSignal
): Promise<{ blob: Blob; contentDisposition: string | null }> {
  const scoped = scopeCompanyRequest(endpoint, signal);
  const headers = { ...getAuthHeader(), ...scoped.headers };
  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, { headers, signal: scoped.signal });
    if (!response.ok) {
      await throwApiError(response, "Authorization" in headers);
    }
    return { blob: await response.blob(), contentDisposition: response.headers.get("Content-Disposition") };
  } catch (error) {
    throw asNetworkError(error);
  } finally {
    scoped.release();
  }
}

// ============================================================================
// CENTRAL IDENTITY API (PM_AUTH_MODE=central)
// ============================================================================

/** A company the signed-in person may enter, with their role there (as the backend resolved it). */
export interface CentralCompany {
  slug: string;
  name: string;
  role: string;
}

export interface CentralMe {
  email: string;
  is_global_admin: boolean;
  companies: CentralCompany[];
}

export const ROLE_SUPER_ADMIN = "super_admin";
export const ROLE_COMPANY_ADMIN = "admin";

export async function getCentralMe(): Promise<CentralMe> {
  return apiCall<CentralMe>("/api/central/me");
}

export async function listCompanyBoards(companySlug: string, signal?: AbortSignal): Promise<BoardResponse[]> {
  return apiCall<BoardResponse[]>(`${companyPath(companySlug)}/boards`, { signal });
}

/** What the backend allows the caller to do in one company (GET /api/companies/{slug}/me). A report for the UI only. */
export interface CompanyCapabilities {
  readOnly: boolean;
  canEditOwnCards: boolean;
  canEditAnyCard: boolean;
  canMoveBack: boolean;
  canDeleteCards: boolean;
  canAssignSalesperson: boolean;
  canManageBoards: boolean;
  canImport: boolean;
}

export interface CompanyMe {
  user_id: number;
  company_slug: string;
  role: string;
  capabilities: CompanyCapabilities;
}

function companyPath(companySlug: string): string {
  return `/api/companies/${encodeURIComponent(companySlug)}`;
}

/** Identity and capabilities of the caller in one company. Throws if the answer is for another company. */
export async function getCompanyMe(companySlug: string, signal?: AbortSignal): Promise<CompanyMe> {
  const me = await apiCall<CompanyMe>(`${companyPath(companySlug)}/me`, { signal });
  if (me?.company_slug !== companySlug) throw { status_code: 0, detail: "Unexpected response from the server." } as ApiError;
  return me;
}

/** One board with its columns and cards, read from the company's own database. Throws if it is not the board asked for. */
export async function getCompanyBoard(companySlug: string, boardId: number, signal?: AbortSignal): Promise<BoardDetail> {
  const board = await apiCall<BoardDetail>(`${companyPath(companySlug)}/boards/${Number(boardId)}`, { signal });
  if (board?.id !== boardId || !Array.isArray(board.columns)) {
    throw { status_code: 0, detail: "Unexpected response from the server." } as ApiError;
  }
  return board;
}

/** Lead field choices of one company (selects of the card form). The salespeople list is not used by the central form. */
export async function getCompanyLeadOptions(companySlug: string, signal?: AbortSignal): Promise<LeadOptions> {
  return apiCall<LeadOptions>(`${companyPath(companySlug)}/lead-options`, { signal });
}

/** Create a card in one column of one company board. The central contract takes only title, details, position and Deal Value. */
export async function createCompanyCard(
  companySlug: string,
  boardId: number,
  payload: { column_id: number; title: string; details?: string; deal_value?: number; deal_value_next_year?: number },
  signal?: AbortSignal
): Promise<CardResponse> {
  return apiCall<CardResponse>(`${companyPath(companySlug)}/boards/${Number(boardId)}/cards`, {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });
}

/** Create a board in one company (POST .../boards; Super Admin or company Admin, the backend decides). */
export async function createCompanyBoard(
  companySlug: string,
  payload: { title: string; description?: string },
  signal?: AbortSignal
): Promise<BoardResponse> {
  return apiCall<BoardResponse>(`${companyPath(companySlug)}/boards`, { method: "POST", body: JSON.stringify(payload), signal });
}

/** Create a column. `position` is a 0-based index; past the end means last (the backend renumbers the board). */
export async function createCompanyColumn(
  companySlug: string,
  boardId: number,
  payload: { title: string; position: number; stage_kind: StageKind },
  signal?: AbortSignal
): Promise<ColumnResponse> {
  return apiCall<ColumnResponse>(`${companyPath(companySlug)}/boards/${Number(boardId)}/columns`, {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });
}

/** Partial update of a column (title, stage_kind, 0-based position); only the fields sent change. */
export async function updateCompanyColumn(
  companySlug: string,
  boardId: number,
  columnId: number,
  updates: { title?: string; position?: number; stage_kind?: StageKind },
  signal?: AbortSignal
): Promise<ColumnResponse> {
  return apiCall<ColumnResponse>(`${companyPath(companySlug)}/boards/${Number(boardId)}/columns/${Number(columnId)}`, {
    method: "PUT",
    body: JSON.stringify(updates),
    signal,
  });
}

/** Delete an empty column (204; Super Admin only, 409 while it holds cards). Never retried automatically. */
export async function deleteCompanyColumn(companySlug: string, boardId: number, columnId: number, signal?: AbortSignal): Promise<void> {
  await apiCall<void>(`${companyPath(companySlug)}/boards/${Number(boardId)}/columns/${Number(columnId)}`, { method: "DELETE", signal });
}

/** Partial update of one card of one company board (only the fields in `updates` change; null clears a lead field). */
export async function updateCompanyCard(
  companySlug: string,
  boardId: number,
  cardId: number,
  updates: CardUpdatePayload,
  signal?: AbortSignal
): Promise<CardResponse> {
  return apiCall<CardResponse>(`${companyPath(companySlug)}/boards/${Number(boardId)}/cards/${Number(cardId)}`, {
    method: "PUT",
    body: JSON.stringify(updates),
    signal,
  });
}

/**
 * Move one card of one company board. `position` is the index the card takes among the target column's
 * other cards (the backend renumbers the column). Never retried automatically: a failed attempt may
 * or may not have been applied, so the caller re-reads the board instead.
 */
export async function moveCompanyCard(
  companySlug: string,
  boardId: number,
  cardId: number,
  columnId: number,
  position: number,
  signal?: AbortSignal
): Promise<CardResponse> {
  return apiCall<CardResponse>(`${companyPath(companySlug)}/boards/${Number(boardId)}/cards/${Number(cardId)}/position`, {
    method: "PUT",
    body: JSON.stringify({ column_id: columnId, position }),
    signal,
  });
}

/**
 * Delete one card of one company board (204; admins only, the backend decides). Never retried automatically:
 * after a network or 5xx failure the card may or may not be gone, so the caller re-reads the board instead.
 */
export async function deleteCompanyCard(companySlug: string, boardId: number, cardId: number, signal?: AbortSignal): Promise<void> {
  await apiCall<void>(`${companyPath(companySlug)}/boards/${Number(boardId)}/cards/${Number(cardId)}`, { method: "DELETE", signal });
}

/**
 * Member-edit history of one card of one company board (GET .../history, every role with a membership may read).
 * Newest first, exactly as the backend stored it: only edits made by Sales Members are recorded there, with the
 * old and new value of each changed field. It is not the audit log (creations, assignments, deletions are not in it).
 */
export async function getCompanyCardHistory(
  companySlug: string,
  boardId: number,
  cardId: number,
  signal?: AbortSignal
): Promise<CardEditHistoryEntry[]> {
  const entries = await apiCall<CardEditHistoryEntry[]>(
    `${companyPath(companySlug)}/boards/${Number(boardId)}/cards/${Number(cardId)}/history`,
    { signal }
  );
  if (!Array.isArray(entries)) throw { status_code: 0, detail: "Unexpected response from the server." } as ApiError;
  return entries;
}

/** The caller's undoable last change on one company board (GET .../undo; writers only, a Visitor gets 403). */
export async function getCompanyUndoStatus(companySlug: string, boardId: number, signal?: AbortSignal): Promise<UndoStatus> {
  return apiCall<UndoStatus>(`${companyPath(companySlug)}/boards/${Number(boardId)}/undo`, { signal });
}

/**
 * Reverse the caller's last change on one company board (POST .../undo). Never retried automatically: after a
 * network or 5xx failure the undo may or may not have been applied, so the caller re-reads the board instead.
 * 404: nothing to undo; 409: the change can no longer be undone (the entry is used up either way).
 */
export async function undoCompanyChange(companySlug: string, boardId: number, signal?: AbortSignal): Promise<UndoStatus> {
  return apiCall<UndoStatus>(`${companyPath(companySlug)}/boards/${Number(boardId)}/undo`, { method: "POST", signal });
}

export type ExcelDownload = "export" | "template";

const EXCEL_FALLBACK_NAMES: Record<ExcelDownload, string> = {
  export: "cards-export.xlsx",
  template: "cards-import-template.xlsx",
};

/** The server's filename only if it is a plain, safe .xlsx name; otherwise our fixed fallback. */
export function safeExcelFilename(contentDisposition: string | null, kind: ExcelDownload): string {
  const name = contentDisposition?.match(/filename="?([^";]+)"?/i)?.[1];
  return name && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.xlsx$/.test(name) ? name : EXCEL_FALLBACK_NAMES[kind];
}

/**
 * Download one of the board's Excel files (export or import template) and save it in the browser. The backend
 * decides who may: the Super Admin, and the Admin of that company for its own company only.
 * Aborting `signal` rejects with an AbortError (see isAbortError) and nothing is saved, even if the bytes
 * had already arrived.
 */
export async function downloadCompanyExcel(
  companySlug: string,
  boardId: number,
  kind: ExcelDownload,
  signal?: AbortSignal
): Promise<string> {
  const { blob, contentDisposition } = await apiBlob(
    `/api/companies/${encodeURIComponent(companySlug)}/boards/${Number(boardId)}/excel/${kind}`,
    signal
  );
  signal?.throwIfAborted();
  const filename = safeExcelFilename(contentDisposition, kind);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return filename;
}

// ---- Cards workbook import (central mode; not the Bid import) ----

export interface CardsImportIssue {
  row: number | null;
  column: string | null;
  code: string;
  message: string;
}

export type CardsImportAction = "create" | "update" | "unchanged" | "invalid";

/** One changed field of an update: the card's current value and the proposed one (null = empty or unassigned). */
export interface CardsImportDiff {
  field: string;
  old: string | null;
  new: string | null;
}

export interface CardsImportRow {
  row: number;
  action: CardsImportAction;
  card_id: number | null;
  title: string | null;
  stage: string | null;
  /** Names of the fields an update changes. */
  changed: string[];
  /** Current and proposed value of each changed field (updates only). */
  diffs: CardsImportDiff[];
  errors: CardsImportIssue[];
  warnings: CardsImportIssue[];
}

/** What an applied import recorded: counts and time only. */
export interface CardsImportSummary {
  import_id: number;
  created: number;
  updated: number;
  unchanged: number;
  applied_at: string;
}

export interface CardsImportPreview {
  filename: string;
  file_sha256: string;
  summary: { rows: number; creates: number; updates: number; unchanged: number; invalid: number; warnings: number };
  rows: CardsImportRow[];
  file_warnings: CardsImportIssue[];
  confirmable: boolean;
  /** already_imported | validation_errors | no_changes when not confirmable. */
  blocked_reason: string | null;
  /** The recorded import of this same workbook on this board, when there is one. */
  already_imported: CardsImportSummary | null;
  previous_import: CardsImportSummary | null;
  /** Binds a confirmation to this preview; sent back unchanged. */
  plan_digest: string;
}

export interface CardsImportResult extends CardsImportSummary {
  applied: true;
}

function cardsImportPath(companySlug: string, boardId: number, step: "preview" | "confirm"): string {
  return `${companyPath(companySlug)}/boards/${Number(boardId)}/excel/import/${step}`;
}

/** Validate a Cards workbook against one board; writes nothing. */
export async function previewCompanyCardsImport(
  companySlug: string,
  boardId: number,
  file: File,
  signal?: AbortSignal
): Promise<CardsImportPreview> {
  const form = new FormData();
  form.append("file", file);
  return apiCall<CardsImportPreview>(cardsImportPath(companySlug, boardId, "preview"), { method: "POST", body: form, signal });
}

/**
 * Apply the previewed workbook, all or nothing. A single attempt: never retried here, because after a lost response
 * the outcome is unknown and the caller must check it (a new preview) instead of sending this again.
 */
export async function confirmCompanyCardsImport(
  companySlug: string,
  boardId: number,
  file: File,
  planDigest: string
): Promise<CardsImportResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("confirm", "true");
  form.append("plan_digest", planDigest);
  return apiCall<CardsImportResult>(cardsImportPath(companySlug, boardId, "confirm"), { method: "POST", body: form });
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

/** The sales team's own assessment of an opportunity; null = not assigned. Exact upper case, as the API stores it. */
export type ConfidenceLevel = "LOW" | "MEDIUM" | "HIGH";
/** Report filter token: a level, or UNASSIGNED for cards with no level (never "" or "NULL"). */
export const CONFIDENCE_LEVELS: readonly ConfidenceLevel[] = ["LOW", "MEDIUM", "HIGH"];
export type ConfidenceFilter = ConfidenceLevel | "UNASSIGNED";

/** Sales lead fields on a card; every one is optional (null when unset). */
export interface LeadFields {
  company: string | null;
  contact: string | null;
  deal_value: number | null;
  currency: string | null;
  expected_close_date: string | null; // YYYY-MM-DD
  lead_source: string | null;
  product_line: string | null;
  confidence_level: ConfidenceLevel | null;
  salesperson_id: number | null;
}

/** Read-only Bid report fields of an imported card. Revenue is integer cents of revenue_currency. */
export interface BidDetails {
  plant: string | null;
  customer_type: string | null;
  business_type: string | null;
  product_category: string | null;
  product: string | null;
  solution_type: string | null;
  volume_current_year: number | null;
  volume_next_year: number | null;
  production_start_date: string | null; // YYYY-MM-DD
  revenue_current_year_cents: number | null;
  revenue_next_year_cents: number | null;
  revenue_currency: string;
  status_note: string | null;
  project_lead: string | null; // source name, not a user
  cpm: string | null; // source name, not a user
  next_step: string | null;
  next_step_due: string | null; // YYYY-MM-DD
  close_reason: string | null;
  close_reason_note: string | null;
}

export interface SourceRef {
  source_code: string;
}

export interface CardResponse extends LeadFields {
  id: number;
  column_id: number;
  title: string;
  details?: string;
  position: number;
  has_member_edits: boolean;
  // deal_value is the amount for deal_year; this is deal_year + 1, same currency.
  deal_value_next_year?: number;
  deal_year?: number;
  salesperson_name: string | null;
  bid_details?: BidDetails | null;
  source_ref?: SourceRef | null;
  created_at: string;
  updated_at: string;
}

/** Partial card update: omitted fields are unchanged, null clears a lead field. */
export type CardUpdatePayload = Partial<LeadFields> & {
  title?: string;
  details?: string;
  deal_value_next_year?: number;
  // Next Action Date and its Action (the Action is at most 100 characters).
  next_step_due?: string | null; // YYYY-MM-DD
  next_step?: string | null;
};

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
  position: number = 0,
  deal: { deal_value?: number; deal_value_next_year?: number } = {}
): Promise<CardResponse> {
  return apiCall<CardResponse>(`/api/boards/${boardId}/cards`, {
    method: "POST",
    body: JSON.stringify({ column_id: columnId, title, details, position, ...deal }),
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
  /** Display label. */
  key: string;
  /** Stable id, only in by_salesperson (null: unassigned); equal labels can have different ids. */
  salesperson_id?: number | null;
  open_count: number;
  /** *_totals: current-year amounts (deal_value); *_next_totals: next year; *_sum_totals: both. */
  open_totals: CurrencyTotals;
  open_next_totals?: CurrencyTotals;
  open_sum_totals?: CurrencyTotals;
  won_count: number;
  won_totals: CurrencyTotals;
  won_next_totals?: CurrencyTotals;
  won_sum_totals?: CurrencyTotals;
}

export interface BoardReport {
  kpis: {
    open_count: number;
    open_totals: CurrencyTotals;
    open_next_totals?: CurrencyTotals;
    open_sum_totals?: CurrencyTotals;
    won_count: number;
    won_totals: CurrencyTotals;
    won_next_totals?: CurrencyTotals;
    won_sum_totals?: CurrencyTotals;
    lost_count: number;
    win_rate: number | null;
  };
  stages: {
    column_id: number;
    title: string;
    stage_kind: StageKind;
    count: number;
    totals: CurrencyTotals;
    next_totals?: CurrencyTotals;
    sum_totals?: CurrencyTotals;
  }[];
  /** Years the amounts were entered for (the cards' stored deal_year); several = totals span years. */
  deal_years?: number[];
  by_salesperson: ReportGroup[];
  by_product_line: ReportGroup[];
  by_source: ReportGroup[];
  /** Always LOW, MEDIUM, HIGH, "Not assigned" in this order, zeros included. */
  by_confidence_level?: ReportGroup[];
  closing_soon: {
    card_id: number;
    title: string;
    company: string | null;
    stage: string;
    salesperson_name: string | null;
    deal_value: number | null;
    deal_value_next_year?: number;
    deal_value_total?: number | null;
    deal_year?: number | null;
    currency: string | null;
    expected_close_date: string;
    overdue: boolean;
  }[];
}

export interface ReportFilters {
  salespersonId?: number;
  productLine?: string;
  confidenceLevel?: ConfidenceFilter;
}

export async function getBoardReport(
  boardId: number,
  filters: ReportFilters = {}
): Promise<BoardReport> {
  const params = new URLSearchParams();
  if (filters.salespersonId !== undefined) params.set("salesperson_id", String(filters.salespersonId));
  if (filters.productLine) params.set("product_line", filters.productLine);
  if (filters.confidenceLevel) params.set("confidence_level", filters.confidenceLevel);
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
  /** The same cards' next-year amount and two-year total today (comparison only). */
  current_next_year_values?: MoneyTotals;
  current_total_values?: MoneyTotals;
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
  deal_value_next_year?: number;
  deal_value_total?: number | null;
  deal_year?: number | null;
  currency: string | null;
}

export interface KpiAging {
  as_of: string;
  tracking_started_at: string | null;
  opportunities: KpiAgingRow[];
}

export interface KpiPipeline {
  as_of: string;
  stages: (StageRef & {
    count: number;
    values: MoneyTotals;
    without_value: number;
    next_year_values?: MoneyTotals;
    total_values?: MoneyTotals;
  })[];
  deal_years?: number[];
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

export async function createUser(email: string): Promise<UserResponse> {
  return apiCall<UserResponse>("/api/users", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

// ============================================================================
// BID IMPORT API (admin-only, Part 22E)
// ============================================================================

export type ImportAction = "create" | "update" | "ignore" | "review";
export type DecisionNeeded =
  | "fix_source_row"
  | "confirm_stage"
  | "choose_stage"
  | "resolve_duplicate"
  | "confirm_update"
  | "confirm_move";

/** One explicit choice for one Excel row; the backend resolves everything else. */
export interface ImportDecision {
  row_number: number;
  target_stage?: string;
  confirm_proposed_stage?: boolean;
  skip?: boolean;
  confirm_update?: boolean;
  confirm_move?: boolean;
}

export interface ImportIssue {
  severity: string;
  code: string;
  message: string;
  row: number | null;
  column: string | null;
  field: string | null;
}

export interface ImportStageRef {
  column_id: number;
  title: string;
  stage_kind: StageKind;
}

export interface ImportFieldChange {
  field: string;
  current: unknown;
  incoming: unknown;
}

export interface ImportPreviewRow {
  row_number: number;
  code: string | null;
  customer: string | null;
  project_name: string | null;
  source_status: string | null;
  source_stage: string | null;
  action: ImportAction;
  classification: {
    status: string;
    target: ImportStageRef | null;
    proposed: ImportStageRef | null;
  };
  target_stage: string | null;
  reasons: ImportIssue[];
  errors: ImportIssue[];
  warnings: ImportIssue[];
  required_decisions: DecisionNeeded[];
  duplicate_rows: number[];
  existing_card_id: number | null;
  changed_fields: ImportFieldChange[];
  stage_change: { from: string; to: string } | null;
  move_confirmed: boolean;
}

export interface ImportPreviewSummary {
  total_rows: number;
  valid_rows: number;
  rows_with_errors: number;
  rows_with_warnings: number;
  proposed_creates: number;
  proposed_updates: number;
  unconfirmed_updates: number;
  unchanged: number;
  internal_duplicate_rows: number;
  pending_review: number;
  ignored: number;
  by_target_stage: Record<string, number>;
  by_classification: Record<string, number>;
  by_action: Record<string, number>;
  /** Cents, per currency. */
  revenue_cents_by_currency: Record<string, { current_year: number; next_year: number }>;
}

export interface ImportPreview {
  file: { filename: string; size_bytes: number; sha256: string };
  sheet: { name: string; missing_optional_columns: string[]; ignored_columns: string[] } | null;
  file_warnings: ImportIssue[];
  summary: ImportPreviewSummary;
  rows: ImportPreviewRow[];
  notice: string | null;
  confirmable: boolean;
  preview_token: string | null;
  expires_in_minutes: number;
}

export interface ImportBatchRevert {
  has_ledger: boolean;
  /** Structural only: completed, has a ledger, not yet reverted. Conflicts come from the revert preview. */
  revertible: boolean;
  reason: string | null;
  reverted: boolean;
  reverted_at: string | null;
  reverted_by_user_id: number | null;
  reverted_by_username: string | null;
  outcome: "reverted" | "no_commercial_changes" | null;
  no_commercial_changes: boolean | null;
  cards_deleted: number | null;
  cards_restored: number | null;
  moves_reverted: number | null;
  already_deleted: number | null;
  summary: Record<string, unknown> | null;
}

export interface ImportBatch {
  id: number;
  board_id: number;
  username: string | null;
  filename: string;
  file_sha256: string;
  status: string;
  failure_kind: string | null;
  failure_reason: string | null;
  created_count: number;
  updated_count: number;
  skipped_count: number;
  review_count: number;
  created_at: string;
  completed_at: string | null;
  revert: ImportBatchRevert | null;
}

export interface ImportBatchDetail extends ImportBatch {
  mapping: Record<string, unknown> | null;
  card_changes_count: number;
}

export interface ImportBatchList {
  items: ImportBatch[];
  total: number;
  limit: number;
  offset: number;
}

export interface ImportResult {
  batch: ImportBatch | null;
  no_op?: boolean;
  created_card_ids: number[];
  updated_card_ids: number[];
  moved_card_ids: number[];
  skipped: number;
  review: number;
}

function importForm(file: File, decisions: ImportDecision[]): FormData {
  const form = new FormData();
  form.append("file", file);
  form.append("decisions", JSON.stringify(decisions));
  return form;
}

export async function previewBidImport(
  boardId: number,
  file: File,
  decisions: ImportDecision[]
): Promise<ImportPreview> {
  return apiCall<ImportPreview>(`/api/boards/${boardId}/imports/preview`, {
    method: "POST",
    body: importForm(file, decisions),
  });
}

/** The same file and decisions that produced `previewToken`. */
export async function confirmBidImport(
  boardId: number,
  file: File,
  decisions: ImportDecision[],
  previewToken: string
): Promise<ImportResult> {
  const form = importForm(file, decisions);
  form.append("preview_token", previewToken);
  form.append("confirm", "true");
  return apiCall<ImportResult>(`/api/boards/${boardId}/imports`, { method: "POST", body: form });
}

export async function listBidImports(boardId: number, limit = 50, offset = 0): Promise<ImportBatchList> {
  return apiCall<ImportBatchList>(`/api/boards/${boardId}/imports?limit=${limit}&offset=${offset}`);
}

export async function getBidImport(batchId: number): Promise<ImportBatchDetail> {
  return apiCall<ImportBatchDetail>(`/api/imports/${batchId}`);
}

// Revert (Part 23). Values in blockers are commercial data: show them only in the admin UI, never log them.

export interface RevertBlocker {
  code: string;
  card_id: number | null;
  field: string | null;
  imported: string | number | boolean | null;
  current: string | number | boolean | null;
  batch_ids: number[];
}

export interface RevertOperation {
  kind: "delete_card" | "restore_fields" | "move_card" | "delete_bid_details";
  card_id: number;
  fields: string[] | null;
  from_column_id: number | null;
  to_column_id: number | null;
}

export interface RevertItem {
  item_id: number;
  card_id: number;
  source_key: string;
  action: "created" | "updated" | "observed";
  observation: string | null;
  classification: string;
  operations: RevertOperation[];
  blockers: RevertBlocker[];
}

export interface RevertCounts {
  items: number;
  cards_to_delete: number;
  cards_to_restore: number;
  moves_to_revert: number;
  bid_details_to_delete: number;
  already_deleted: number;
  already_reverted: number;
  observed: number;
  blocked: number;
}

export interface RevertPlan {
  batch_id: number;
  status: "ready" | "blocked" | "not_revertible" | "already_reverted";
  reason: string | null;
  /** status == ready: the confirmation would be accepted now. */
  revertible: boolean;
  no_commercial_changes: boolean;
  ledger_version: number | null;
  plan_fingerprint: string;
  counts: RevertCounts;
  blockers: RevertBlocker[];
  dependent_batches: { batch_id: number; card_ids: number[] }[];
  items: RevertItem[];
}

export interface RevertResult {
  batch: ImportBatch;
  outcome: "reverted" | "no_commercial_changes";
  no_commercial_changes: boolean;
  counts: {
    cards_deleted: number;
    cards_restored: number;
    moves_reverted: number;
    already_deleted: number;
    bid_details_deleted: number;
    already_reverted: number;
    observed: number;
  };
}

export async function getRevertPreview(batchId: number): Promise<RevertPlan> {
  return apiCall<RevertPlan>(`/api/imports/${batchId}/revert-preview`);
}

export async function revertBidImport(batchId: number, planFingerprint: string): Promise<RevertResult> {
  return apiCall<RevertResult>(`/api/imports/${batchId}/revert`, {
    method: "POST",
    body: JSON.stringify({ plan_fingerprint: planFingerprint }),
  });
}

// ============================================================================
// CENTRAL INVITATIONS, PASSWORD RESET AND MEMBERS (PM_AUTH_MODE=central)
// ============================================================================

/**
 * POST to an unauthenticated central endpoint (invitation, reset). It never sends a stored token and a 401 here is
 * the caller's answer (a wrong password), not an expired session. The one-time token only travels in the body.
 */
async function publicPost<T>(endpoint: string, body: unknown): Promise<T> {
  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) await throwApiError(response, false);
    return (await response.json()) as T;
  } catch (error) {
    throw asNetworkError(error);
  }
}

export async function centralForgotPassword(email: string): Promise<void> {
  await publicPost("/api/central/password/forgot", { email });
}

export async function centralResetPassword(token: string, newPassword: string): Promise<void> {
  await publicPost("/api/central/password/reset", { token, new_password: newPassword });
}

/** First password for an invited person with no account yet. */
export async function centralAcceptInvitation(token: string, password: string): Promise<void> {
  await publicPost("/api/central/invitations/accept", { token, password });
}

/** An existing account joins another company; it proves who it is with its own password. */
export async function centralJoinInvitation(token: string, email: string, password: string): Promise<void> {
  await publicPost("/api/central/invitations/join", { token, email, password });
}

/** Roles a Super Admin may invite to. The reserved company Admin role is never offered. */
export type InviteRole = "sales_member" | "visitor";

export interface CompanyMember {
  user_id: number;
  email: string;
  role: string;
  is_active: boolean;
  user_is_active: boolean;
  email_verified: boolean;
  joined_at: string;
}

export interface PendingInvitation {
  invitation_id: number;
  email: string;
  role: string;
  expires_at: string;
}

export async function inviteCompanyMember(companySlug: string, email: string, role: InviteRole): Promise<void> {
  await apiCall(`/api/central/companies/${encodeURIComponent(companySlug)}/invitations`, {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
}

export async function listCompanyMembers(companySlug: string, signal?: AbortSignal): Promise<CompanyMember[]> {
  return apiCall<CompanyMember[]>(`/api/central/companies/${encodeURIComponent(companySlug)}/members`, { signal });
}

export async function listCompanyInvitations(companySlug: string, signal?: AbortSignal): Promise<PendingInvitation[]> {
  return apiCall<PendingInvitation[]>(`/api/central/companies/${encodeURIComponent(companySlug)}/invitations`, { signal });
}

export async function changeCompanyMemberRole(companySlug: string, userId: number, role: InviteRole): Promise<void> {
  await apiCall(`/api/central/companies/${encodeURIComponent(companySlug)}/members/${userId}/role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

export async function deactivateCompanyMember(companySlug: string, userId: number): Promise<void> {
  await apiCall(`/api/central/companies/${encodeURIComponent(companySlug)}/members/${userId}/deactivate`, { method: "POST" });
}
