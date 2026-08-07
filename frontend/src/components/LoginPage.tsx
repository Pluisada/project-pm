"use client";

import { useState } from "react";
import { login } from "@/lib/auth";

interface LoginPageProps {
  onLoginSuccess: (username: string) => void;
}

export const LoginPage = ({ onLoginSuccess }: LoginPageProps) => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const result = await login({ username, password });
      onLoginSuccess(result.username);
    } catch (err) {
      setError("Invalid username or password");
      setPassword(""); // Clear password on error
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden">
      {/* Decorative gradients */}
      <div className="pointer-events-none absolute left-0 top-0 h-[420px] w-[420px] -translate-x-1/3 -translate-y-1/3 rounded-full bg-[radial-gradient(circle,_rgba(32,157,215,0.25)_0%,_rgba(32,157,215,0.05)_55%,_transparent_70%)]" />
      <div className="pointer-events-none absolute bottom-0 right-0 h-[520px] w-[520px] translate-x-1/4 translate-y-1/4 rounded-full bg-[radial-gradient(circle,_rgba(117,57,145,0.18)_0%,_rgba(117,57,145,0.05)_55%,_transparent_75%)]" />

      <main className="relative flex min-h-screen items-center justify-center px-6">
        <div className="w-full max-w-md">
          {/* Header */}
          <div className="mb-12 text-center">
            <p className="text-xs font-semibold uppercase tracking-[0.35em] text-[var(--gray-text)]">
              Project Management MVP
            </p>
            <h1 className="mt-4 font-display text-4xl font-semibold text-[var(--navy-dark)]">
              Sign In
            </h1>
            <p className="mt-3 text-sm text-[var(--gray-text)]">
              Enter your credentials to access the Kanban board
            </p>
          </div>

          {/* Login Form */}
          <form
            onSubmit={handleSubmit}
            className="rounded-[24px] border border-[var(--stroke)] bg-white/80 p-8 shadow-[var(--shadow)] backdrop-blur"
          >
            {/* Error Message */}
            {error && (
              <div className="mb-6 rounded-lg border border-red-300 bg-red-50 p-3">
                <p className="text-sm font-medium text-red-800">{error}</p>
              </div>
            )}

            {/* Username Field */}
            <div className="mb-6">
              <label
                htmlFor="username"
                className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
              >
                Username
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="user"
                disabled={loading}
                className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition placeholder:text-[var(--gray-text)] focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
                autoComplete="username"
              />
            </div>

            {/* Password Field */}
            <div className="mb-8">
              <label
                htmlFor="password"
                className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="password"
                disabled={loading}
                className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition placeholder:text-[var(--gray-text)] focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
                autoComplete="current-password"
              />
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading || !username || !password}
              className="w-full rounded-lg bg-[var(--secondary-purple)] px-6 py-3 font-semibold text-white transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "Signing in..." : "Sign In"}
            </button>

            {/* Demo Info */}
            <div className="mt-6 rounded-lg bg-[var(--surface)] p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">
                Demo Credentials
              </p>
              <p className="mt-2 text-sm text-[var(--navy-dark)]">
                Username: <code className="font-mono">user</code>
              </p>
              <p className="text-sm text-[var(--navy-dark)]">
                Password: <code className="font-mono">password</code>
              </p>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
};
