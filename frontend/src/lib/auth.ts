/**
 * Authentication utilities for frontend
 */

const TOKEN_KEY = "pm_auth_token";
const USER_KEY = "pm_auth_user";
const ROLE_KEY = "pm_auth_role";

// Em dev o frontend roda na 3000 e o backend na 8000, entao precisa da origem
// completa. No build estatico o FastAPI serve os arquivos, entao mesma origem.
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

export type UserRole = "admin" | "member";

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  username: string;
  role: UserRole;
}

export interface AuthState {
  isAuthenticated: boolean;
  username: string | null;
  role: UserRole | null;
  token: string | null;
}

/**
 * Get current auth state from localStorage
 */
export function getAuthState(): AuthState {
  if (typeof window === "undefined") {
    return {
      isAuthenticated: false,
      username: null,
      role: null,
      token: null,
    };
  }

  const token = localStorage.getItem(TOKEN_KEY);
  const username = localStorage.getItem(USER_KEY);
  const role = localStorage.getItem(ROLE_KEY) as UserRole | null;

  return {
    isAuthenticated: !!token && !!username,
    username: username || null,
    role: role || null,
    token: token || null,
  };
}

function storeAuth(data: LoginResponse): void {
  localStorage.setItem(TOKEN_KEY, data.access_token);
  localStorage.setItem(USER_KEY, data.username);
  localStorage.setItem(ROLE_KEY, data.role);
}

/**
 * Whether the system still needs its first (admin) user created
 */
export async function getSetupStatus(): Promise<{ needs_setup: boolean }> {
  const response = await fetch(`${API_BASE_URL}/api/setup/status`);

  if (!response.ok) {
    throw new Error("Failed to check setup status");
  }

  return response.json();
}

/**
 * Create the first (admin) user. Only succeeds while no users exist yet.
 */
export async function setupAdmin(credentials: LoginRequest): Promise<LoginResponse> {
  const response = await fetch(`${API_BASE_URL}/api/setup`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(credentials),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "Setup failed");
  }

  const data: LoginResponse = await response.json();
  storeAuth(data);
  return data;
}

/**
 * Login user with credentials
 */
export async function login(credentials: LoginRequest): Promise<LoginResponse> {
  const response = await fetch(`${API_BASE_URL}/api/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(credentials),
  });

  if (!response.ok) {
    throw new Error("Invalid credentials");
  }

  const data: LoginResponse = await response.json();
  storeAuth(data);
  return data;
}

/**
 * Logout user
 */
export async function logout(): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY);

  if (token) {
    try {
      await fetch(`${API_BASE_URL}/api/logout`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
    } catch {
      // Ignore errors on logout
    }
  }

  // Clear localStorage
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(ROLE_KEY);
}

/**
 * Get authorization header for API requests
 */
export function getAuthHeader(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY);

  if (!token) {
    return {};
  }

  return {
    Authorization: `Bearer ${token}`,
  };
}

/**
 * Check if user is authenticated
 */
export function isAuthenticated(): boolean {
  return getAuthState().isAuthenticated;
}

/**
 * Get current username
 */
export function getCurrentUsername(): string | null {
  return getAuthState().username;
}

/**
 * Whether the current user is an admin.
 *
 * This only gates what the UI shows - the backend independently re-checks
 * the role from the database on every admin-only request.
 */
export function isAdmin(): boolean {
  return getAuthState().role === "admin";
}
