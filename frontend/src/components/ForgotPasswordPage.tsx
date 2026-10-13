"use client";

import { useState } from "react";
import { requestPasswordReset } from "@/lib/auth";

interface ForgotPasswordPageProps {
  onBackToLogin: () => void;
}

export const ForgotPasswordPage = ({ onBackToLogin }: ForgotPasswordPageProps) => {
  const [identifier, setIdentifier] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const result = await requestPasswordReset(identifier);
      setSentTo(result.email);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send reset link. Please try again.");
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
              Forgot Password
            </h1>
            <p className="mt-3 text-sm text-[var(--gray-text)]">
              Enter your username or email and we&apos;ll email you a link to choose a new password
            </p>
          </div>

          <div className="rounded-[24px] border border-[var(--stroke)] bg-white/80 p-8 shadow-[var(--shadow)] backdrop-blur">
            {sentTo ? (
              <div className="text-center">
                <h2 className="text-lg font-semibold text-[var(--navy-dark)]">Check your inbox</h2>
                <p className="mt-3 text-sm text-[var(--gray-text)]">
                  Reset link sent to <span className="font-semibold">{sentTo}</span>. The link
                  expires in 1 hour.
                </p>
              </div>
            ) : (
              <form onSubmit={handleSubmit}>
                {/* Error Message */}
                {error && (
                  <div className="mb-6 rounded-lg border border-red-300 bg-red-50 p-3">
                    <p className="text-sm font-medium text-red-800">{error}</p>
                  </div>
                )}

                {/* Username or Email Field */}
                <div className="mb-8">
                  <label
                    htmlFor="forgot-identifier"
                    className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
                  >
                    Username or email
                  </label>
                  <input
                    id="forgot-identifier"
                    type="text"
                    value={identifier}
                    onChange={(e) => setIdentifier(e.target.value)}
                    placeholder="Your username or email"
                    disabled={loading}
                    className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition placeholder:text-[var(--gray-text)] focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
                    autoComplete="username"
                    autoFocus
                  />
                </div>

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={loading || !identifier}
                  className="w-full rounded-lg bg-[var(--secondary-purple)] px-6 py-3 font-semibold text-white transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? "Sending..." : "Send Reset Link"}
                </button>
              </form>
            )}
          </div>

          <p className="mt-6 text-center text-sm text-[var(--gray-text)]">
            <button
              type="button"
              onClick={onBackToLogin}
              className="font-semibold text-[var(--primary-blue)] hover:underline"
            >
              Back to sign in
            </button>
          </p>
        </div>
      </main>
    </div>
  );
};
