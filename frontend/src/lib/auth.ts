/**
 * Authentication utilities for frontend
 */

const TOKEN_KEY = "pm_auth_token";
const USER_KEY = "pm_auth_user";

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  username: string;
}

export interface AuthState {
  isAuthenticated: boolean;
  username: string | null;
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
      token: null,
    };
  }

  const token = localStorage.getItem(TOKEN_KEY);
  const username = localStorage.getItem(USER_KEY);

  return {
    isAuthenticated: !!token && !!username,
    username: username || null,
    token: token || null,
  };
}

/**
 * Login user with credentials
 */
export async function login(credentials: LoginRequest): Promise<LoginResponse> {
  const response = await fetch("/api/login", {
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

  // Store token and username in localStorage
  localStorage.setItem(TOKEN_KEY, data.access_token);
  localStorage.setItem(USER_KEY, data.username);

  return data;
}

/**
 * Logout user
 */
export async function logout(): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY);

  if (token) {
    try {
      await fetch("/api/logout", {
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
