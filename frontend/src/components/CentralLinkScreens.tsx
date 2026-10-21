"use client";

import { useState } from "react";
import {
  centralAcceptInvitation,
  centralForgotPassword,
  centralJoinInvitation,
  centralResetPassword,
  type ApiError,
} from "@/lib/api";

/** A one-time link the person opened: an emailed invitation or password reset. The token lives only in memory. */
export type CentralLink = { kind: "invite" | "reset"; token: string };

/** Reads `central_invite_token` / `central_reset_token` from a query string (an invitation wins if both are present). */
export function readCentralLink(search: string): CentralLink | null {
  const params = new URLSearchParams(search);
  const invite = params.get("central_invite_token");
  if (invite) return { kind: "invite", token: invite };
  const reset = params.get("central_reset_token");
  if (reset) return { kind: "reset", token: reset };
  return null;
}

const PASSWORD_HINT = "8 to 128 characters with an upper-case letter, a lower-case letter, a number and a symbol.";
const inputClass =
  "mt-2 w-full rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] text-[var(--navy-dark)] px-4 py-3 text-sm outline-none focus:border-[var(--primary-blue)] disabled:opacity-50";
const labelClass = "block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]";
const submitClass =
  "mt-8 w-full rounded-lg bg-[var(--secondary-purple)] px-6 py-3 font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";

/** Plain-language text for a failed public request; never the raw body, never the token. */
function linkFailure(err: unknown, invalid: string): string {
  const e = err as Partial<ApiError>;
  if (e.status_code === 0) return "Network error - check your connection and try again.";
  if (e.status_code === 400) return invalid;
  if (e.status_code === 401) return "Invalid email or password.";
  if (e.status_code === 422 && typeof e.detail === "string") return e.detail;
  if (e.status_code === 429) return "Too many attempts. Wait a few minutes and try again.";
  return "Something went wrong. Try again.";
}

const Card = ({ title, children, onSubmit }: { title: string; children: React.ReactNode; onSubmit: (e: React.FormEvent) => void }) => (
  <main className="flex min-h-screen items-center justify-center px-6">
    <form onSubmit={onSubmit} className="w-full max-w-md rounded-[24px] border border-[var(--stroke)] bg-[var(--surface-strong)]/80 p-8 shadow-[var(--shadow)]">
      <h1 className="font-display text-3xl font-semibold text-[var(--navy-dark)]">{title}</h1>
      {children}
    </form>
  </main>
);

const PasswordFields = ({ idPrefix, password, confirm, setPassword, setConfirm, disabled, label = "Password" }: {
  idPrefix: string; password: string; confirm: string; disabled: boolean; label?: string;
  setPassword: (v: string) => void; setConfirm: (v: string) => void;
}) => (
  <>
    <label htmlFor={`${idPrefix}-password`} className={`${labelClass} mt-6`}>{label}</label>
    <input id={`${idPrefix}-password`} type="password" value={password} onChange={(e) => setPassword(e.target.value)}
      disabled={disabled} autoComplete="new-password" className={inputClass} />
    <p className="mt-2 text-xs text-[var(--gray-text)]">{PASSWORD_HINT}</p>
    <label htmlFor={`${idPrefix}-confirm`} className={`${labelClass} mt-6`}>Confirm password</label>
    <input id={`${idPrefix}-confirm`} type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
      disabled={disabled} autoComplete="new-password" className={inputClass} />
  </>
);

/** Accept an invitation: choose a first password, or (existing account) prove who you are to join the company. */
export const CentralInviteScreen = ({ token, onDone }: { token: string; onDone: (notice: string) => void }) => {
  const [existing, setExisting] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mismatch = !existing && confirm !== "" && password !== confirm;
  const ready = !!password && (existing ? !!email : password === confirm);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (existing) {
        await centralJoinInvitation(token, email.trim(), password);
        onDone("You joined the company. Sign in to continue.");
      } else {
        await centralAcceptInvitation(token, password);
        onDone("Your account is ready. Sign in with your email and password.");
      }
    } catch (err) {
      setError(linkFailure(err, "This invitation is invalid, expired or was already used. Ask your administrator for a new one."));
      setPassword("");
      setConfirm("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={existing ? "Join the company" : "Accept your invitation"} onSubmit={submit}>
      {error && <p role="alert" className="mt-4 text-sm font-medium text-red-800">{error}</p>}
      {existing ? (
        <>
          <p className="mt-4 text-sm text-[var(--gray-text)]">Enter the email and password of your existing account.</p>
          <label htmlFor="invite-email" className={`${labelClass} mt-6`}>Email</label>
          <input id="invite-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            disabled={busy} autoComplete="username" className={inputClass} />
          <label htmlFor="invite-existing-password" className={`${labelClass} mt-6`}>Password</label>
          <input id="invite-existing-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            disabled={busy} autoComplete="current-password" className={inputClass} />
        </>
      ) : (
        <>
          <p className="mt-4 text-sm text-[var(--gray-text)]">Choose a password to activate your account.</p>
          <PasswordFields idPrefix="invite" password={password} confirm={confirm} setPassword={setPassword}
            setConfirm={setConfirm} disabled={busy} />
          {mismatch && <p role="alert" className="mt-2 text-xs font-medium text-red-800">The passwords do not match.</p>}
        </>
      )}
      <button type="submit" disabled={busy || !ready} className={submitClass}>
        {busy ? "Please wait..." : existing ? "Join" : "Activate account"}
      </button>
      <button type="button" disabled={busy} onClick={() => { setExisting(!existing); setError(null); setPassword(""); setConfirm(""); }}
        className="mt-4 block w-full text-center text-sm font-semibold text-[var(--primary-blue)] hover:underline">
        {existing ? "I do not have an account yet" : "I already have an account"}
      </button>
    </Card>
  );
};

/** Choose a new password with the emailed single-use reset link. */
export const CentralResetScreen = ({ token, onDone }: { token: string; onDone: (notice: string) => void }) => {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await centralResetPassword(token, password);
      onDone("Password updated. Sign in with the new password.");
    } catch (err) {
      setError(linkFailure(err, "This link is invalid or has expired. Request a new one from the sign-in page."));
      setPassword("");
      setConfirm("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Choose a new password" onSubmit={submit}>
      {error && <p role="alert" className="mt-4 text-sm font-medium text-red-800">{error}</p>}
      <PasswordFields idPrefix="reset" label="New password" password={password} confirm={confirm}
        setPassword={setPassword} setConfirm={setConfirm} disabled={busy} />
      {confirm !== "" && password !== confirm && (
        <p role="alert" className="mt-2 text-xs font-medium text-red-800">The passwords do not match.</p>
      )}
      <button type="submit" disabled={busy || !password || password !== confirm} className={submitClass}>
        {busy ? "Saving..." : "Save password"}
      </button>
    </Card>
  );
};

/** Ask for a reset link. The answer is the same for every address, so nothing here reveals whether it has an account. */
export const CentralForgotScreen = ({ onBack }: { onBack: () => void }) => {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await centralForgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(linkFailure(err, "Something went wrong. Try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Forgot my password" onSubmit={submit}>
      {sent ? (
        <p role="status" className="mt-4 text-sm text-green-800">
          If the account exists, a link to choose a new password has been sent.
        </p>
      ) : (
        <>
          {error && <p role="alert" className="mt-4 text-sm font-medium text-red-800">{error}</p>}
          <label htmlFor="forgot-email" className={`${labelClass} mt-6`}>Email</label>
          <input id="forgot-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            disabled={busy} autoComplete="username" className={inputClass} />
          <button type="submit" disabled={busy || !email} className={submitClass}>
            {busy ? "Sending..." : "Send reset link"}
          </button>
        </>
      )}
      <button type="button" onClick={onBack}
        className="mt-4 block w-full text-center text-sm font-semibold text-[var(--primary-blue)] hover:underline">
        Back to sign in
      </button>
    </Card>
  );
};
