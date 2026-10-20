/**
 * Authentication utilities for frontend
 */

export const TOKEN_KEY = "pm_auth_token";
const USER_KEY = "pm_auth_user";
const ROLE_KEY = "pm_auth_role";

// Em dev o frontend roda na 3000 e o backend na 8000, entao precisa da origem
// completa. No build estatico o FastAPI serve os arquivos, entao mesma origem.
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

/** Fired (once the stored session is cleared) when an authenticated request gets a 401. */
export const SESSION_EXPIRED_EVENT = "pm:session-expired";

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
 * Backend errors are usually a plain string `detail`, but FastAPI/Pydantic
 * validation errors (422) return `detail` as an array of {type, loc, msg,
 * ...} objects instead. Normalize to a single string so callers never try
 * to render/stringify the array directly.
 */
export function extractErrorDetail(errorData: unknown, fallback: string): string {
  const detail = (errorData as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") {
    return detail[0].msg;
  }
  // The import's unreadable-file refusal: { message, file_errors: [{ message }] }.
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
    const { message, file_errors } = detail as { message: string; file_errors?: { message?: string }[] };
    const reasons = (file_errors ?? []).map((issue) => issue.message).filter(Boolean);
    return reasons.length ? `${message}: ${reasons.join("; ")}` : message;
  }
  return fallback;
}

/**
 * Whether the system still needs its first (admin) user created
 */
export async function getSetupStatus(): Promise<{ needs_setup: boolean; setup_enabled: boolean }> {
  const response = await fetch(`${API_BASE_URL}/api/setup/status`);

  if (!response.ok) {
    throw new Error("Failed to check setup status");
  }

  return response.json();
}

/**
 * Create the first (admin) user. Only succeeds while no users exist yet.
 */
/**
 * POST JSON to an unauthenticated endpoint (setup, signup, password reset).
 * Throws an Error carrying the backend's message (or `fallback`) when the
 * response isn't ok; otherwise returns the response for the caller to read.
 * Deliberately not apiCall: these send no auth header and throw plain Error.
 */
async function postJson(
  path: string,
  body: unknown,
  fallback: string,
  extraHeaders: Record<string, string> = {}
): Promise<Response> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...extraHeaders,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(extractErrorDetail(errorData, fallback));
  }
  return response;
}

export async function setupAdmin(
  credentials: LoginRequest,
  setupKey: string
): Promise<LoginResponse> {
  const response = await postJson("/api/setup", credentials, "Setup failed", {
    "X-Setup-Token": setupKey,
  });
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

export type AuthMode = "legacy" | "central";

/**
 * Which sign-in flow the backend runs (/api/mode, unauthenticated). The answer must be exactly
 * {"auth": "legacy" | "central"}; a network error, timeout, HTTP error or any other body throws, so the
 * caller can offer a retry instead of guessing a mode.
 */
export async function getAuthMode(): Promise<AuthMode> {
  const response = await fetch(`${API_BASE_URL}/api/mode`, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`Could not determine the sign-in mode (HTTP ${response.status}).`);
  const data: unknown = await response.json().catch(() => null);
  const auth = (data as { auth?: unknown } | null)?.auth;
  if (auth !== "legacy" && auth !== "central") throw new Error("Unexpected response from the server.");
  return auth;
}

/**
 * Central sign-in (POST /api/central/login). The access token is stored under the
 * same key as the legacy one so apiCall sends it; the email stands in for the
 * username. No role is stored: roles come from /api/central/me, per company.
 */
export async function centralLogin(email: string, password: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/central/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error(response.status === 401 ? "Invalid email or password" : "Sign in failed");
  }
  const data: { access_token: string } = await response.json();
  clearAuth();
  localStorage.setItem(TOKEN_KEY, data.access_token);
  localStorage.setItem(USER_KEY, email.trim());
  boundToken = data.access_token; // this tab just signed in with it: it is the session this tab runs
  return email.trim();
}

/** Central sign-out: revokes the person's sessions server-side, then clears the stored one. */
export async function centralLogout(): Promise<void> {
  const token = boundToken ?? localStorage.getItem(TOKEN_KEY);
  if (token) {
    try {
      await fetch(`${API_BASE_URL}/api/central/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      // Ignore errors on logout
    }
  }
  clearAuth();
}

export function clearAuth(): void {
  const stored = localStorage.getItem(TOKEN_KEY);
  const foreign = boundToken !== null && stored !== null && stored !== boundToken;
  boundToken = null;
  // The stored session is another tab's, not the one this tab was running: ending ours must not sign that one out.
  if (foreign) return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(ROLE_KEY);
}

// ----------------------------------------------------------------------------
// The session this tab has validated (central mode)
// ----------------------------------------------------------------------------

/**
 * The token of the central session this tab has verified with /api/central/me, held in memory only and never
 * written anywhere. localStorage is shared by every tab of the browser and another tab's sign-in replaces its
 * token at once, while this tab only hears about it in a later `storage` event; a request started in between
 * would read the new token while the tab still shows the previous account. While a token is bound, requests use
 * it instead of the stored one, so a tab only changes identity through bindSessionToken, after it has asked the
 * server who the new token belongs to. Nothing binds in legacy mode, which keeps reading storage as before.
 */
let boundToken: string | null = null;

/** Make `token` (verified by the caller) the one this tab sends, or null to go back to reading storage. */
export function bindSessionToken(token: string | null): void {
  boundToken = token;
}

/** The token this tab sends right now: the one it has bound, or, with none bound, the stored one. */
export function getSessionToken(): string | null {
  return boundToken ?? localStorage.getItem(TOKEN_KEY);
}

/** The token currently in shared storage. Read it only to adopt it at start-up or to verify it after a change. */
export function readStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/** Whether `token` is the one this tab is running. */
export function isBoundSessionToken(token: string | null): boolean {
  return token !== null && token === boundToken;
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

  clearAuth();
}

/**
 * Get authorization header for API requests
 */
export function getAuthHeader(): Record<string, string> {
  const token = boundToken ?? localStorage.getItem(TOKEN_KEY);

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

/**
 * Ask the backend to email a signup link. Resolves the same way whether or
 * not the address is already registered.
 */
export async function requestSignup(email: string): Promise<void> {
  await postJson("/api/signup", { email }, "Sign-up failed");
}

/**
 * Create the account for a signup link's token. Does not log in.
 */
export async function completeSignup(token: string, password: string): Promise<void> {
  await postJson("/api/signup/complete", { token, password }, "Could not create account");
}

/**
 * Ask for a password reset link. The backend answers the same way whether or
 * not the account exists, so this resolves without saying where it was sent.
 */
export async function requestPasswordReset(identifier: string): Promise<void> {
  await postJson("/api/password/forgot", { identifier }, "Could not send reset link");
}

/**
 * Set a new password from a reset link's token. Does not log in.
 */
export async function resetPassword(token: string, password: string): Promise<void> {
  await postJson("/api/password/reset", { token, password }, "Could not reset password");
}
