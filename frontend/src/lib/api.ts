/**
 * Frontend API client for communicating with backend
 */

import { API_BASE_URL, getAuthHeader } from "./auth";

export interface ApiError {
  detail: string;
  status_code?: number;
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
        detail: errorData.detail || `HTTP ${response.status}`,
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

export interface ColumnResponse {
  id: number;
  board_id: number;
  title: string;
  position: number;
  created_at: string;
  updated_at: string;
  cards?: CardResponse[];
}

export interface CardResponse {
  id: number;
  column_id: number;
  title: string;
  details?: string;
  position: number;
  created_at: string;
  updated_at: string;
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
  title?: string,
  details?: string
): Promise<CardResponse> {
  return apiCall<CardResponse>(`/api/boards/${boardId}/cards/${cardId}`, {
    method: "PUT",
    body: JSON.stringify({ title, details }),
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
