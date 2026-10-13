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
