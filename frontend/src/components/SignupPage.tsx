"use client";

import { useState } from "react";
import { requestSignup } from "@/lib/auth";

interface SignupPageProps {
  onBackToLogin: () => void;
}

export const SignupPage = ({ onBackToLogin }: SignupPageProps) => {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      await requestSignup(email);
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-up failed. Please try again.");
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
              Sign Up
            </h1>
            <p className="mt-3 text-sm text-[var(--gray-text)]">
              Enter your email and we&apos;ll send you a link to set your password
            </p>
          </div>

          <div className="rounded-[24px] border border-[var(--stroke)] bg-white/80 p-8 shadow-[var(--shadow)] backdrop-blur">
            {sent ? (
              <div className="text-center">
                <h2 className="text-lg font-semibold text-[var(--navy-dark)]">Check your inbox</h2>
                <p className="mt-3 text-sm text-[var(--gray-text)]">
                  If <span className="font-semibold">{email}</span> can be registered, an email
                  with a sign-up link is on its way. The link expires in 24 hours.
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

                {/* Email Field */}
                <div className="mb-8">
                  <label
                    htmlFor="signup-email"
                    className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
                  >
                    Email
                  </label>
                  <input
                    id="signup-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    disabled={loading}
                    className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition placeholder:text-[var(--gray-text)] focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
                    autoComplete="email"
                    autoFocus
                  />
                </div>

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={loading || !email}
                  className="w-full rounded-lg bg-[var(--secondary-purple)] px-6 py-3 font-semibold text-white transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? "Sending..." : "Send Sign-up Link"}
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
