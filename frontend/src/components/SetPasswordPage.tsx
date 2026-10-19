"use client";

import { useState } from "react";

interface SetPasswordPageProps {
  title: string;
  subtitle: string;
  submitLabel: string;
  onSubmit: (password: string) => Promise<void>;
  onComplete: () => void;
  onBackToLogin: () => void;
}

/** Password + confirmation form opened from an emailed link (signup or reset). */
export const SetPasswordPage = ({
  title,
  subtitle,
  submitLabel,
  onSubmit,
  onComplete,
  onBackToLogin,
}: SetPasswordPageProps) => {
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    setLoading(true);
    try {
      await onSubmit(password);
      onComplete();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not set your password. Please try again."
      );
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
              {title}
            </h1>
            <p className="mt-3 text-sm text-[var(--gray-text)]">
              {subtitle}
            </p>
          </div>

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

            {/* Password Field */}
            <div className="mb-6">
              <label
                htmlFor="signup-password"
                className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
              >
                Password
              </label>
              <input
                id="signup-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="At least 8 characters"
                disabled={loading}
                minLength={8}
                maxLength={128}
                className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition placeholder:text-[var(--gray-text)] focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
                autoComplete="new-password"
                autoFocus
              />
            </div>

            {/* Confirm Password Field */}
            <div className="mb-8">
              <label
                htmlFor="signup-confirm-password"
                className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
              >
                Confirm Password
              </label>
              <input
                id="signup-confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Repeat your password"
                disabled={loading}
                minLength={8}
                maxLength={128}
                className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition placeholder:text-[var(--gray-text)] focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
                autoComplete="new-password"
              />
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading || !password || !confirmPassword}
              className="w-full rounded-lg bg-[var(--secondary-purple)] px-6 py-3 font-semibold text-white transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "Saving..." : submitLabel}
            </button>
          </form>

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
