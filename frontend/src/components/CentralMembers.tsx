"use client";

import { useCallback, useEffect, useState } from "react";
import {
  changeCompanyMemberRole,
  deactivateCompanyMember,
  getErrorMessage,
  inviteCompanyMember,
  isAbortError,
  listCompanyInvitations,
  listCompanyMembers,
  type ApiError,
  type CompanyMember,
  type InviteRole,
  type MemberRole,
  type PendingInvitation,
} from "@/lib/api";

const ROLE_LABELS: Record<string, string> = { sales_member: "Sales Member", visitor: "Visitor", admin: "Admin" };
const INVITE_ROLES: InviteRole[] = ["sales_member", "visitor"];
// An existing member can also be made company Admin; an invitation cannot carry that role (backend rule).
const MEMBER_ROLES: MemberRole[] = [...INVITE_ROLES, "admin"];

const fieldClass =
  "mt-1 w-full rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] text-[var(--navy-dark)] px-3 py-2 text-sm outline-none focus:border-[var(--primary-blue)] disabled:opacity-50";
const labelClass = "block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]";
const primaryButton =
  "rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "rounded-lg border border-[var(--stroke)] px-3 py-1.5 text-xs font-semibold text-[var(--primary-blue)] transition hover:border-[var(--primary-blue)] disabled:cursor-not-allowed disabled:opacity-50";

type Notice = { kind: "ok" | "error"; text: string };

function failure(err: unknown, what: string): string {
  const code = (err as Partial<ApiError>).status_code;
  if (code === 0) return `Network error - ${what} may not have been saved. Reload the list and check.`;
  if (code === 401) return "Your session expired.";
  if (code === 403) return `You are not allowed to ${what}.`;
  if (code === 404) return "That member was not found - the list was reloaded.";
  if (code === 409 || code === 422) return getErrorMessage(err, `Could not ${what}.`);
  if (code === 429) return "Too many requests. Wait a moment and try again.";
  return `Could not ${what}.`;
}

/**
 * Members and invitations of one company, for the Super Admin only (the backend re-checks every request and answers
 * 403 to anyone else). The invitation link is emailed to the invitee and is never shown here. An invitation cannot carry
 * the company Admin role; a joined member is promoted from the list (the backend allows it to the Super Admin only).
 */
export const CentralMembers = ({ companySlug, companyName }: { companySlug: string; companyName: string }) => {
  const [members, setMembers] = useState<CompanyMember[] | null>(null);
  const [pending, setPending] = useState<PendingInvitation[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<InviteRole>("sales_member");

  const load = useCallback(
    (signal?: AbortSignal): Promise<void> =>
      Promise.all([listCompanyMembers(companySlug, signal), listCompanyInvitations(companySlug, signal)]).then(
        ([m, p]) => {
          setMembers(m);
          setPending(p);
          setLoadFailed(false);
        },
        (err) => {
          if (isAbortError(err) || (err as Partial<ApiError>).status_code === 401) return;
          setLoadFailed(true);
          setNotice({ kind: "error", text: failure(err, "load the members") });
        }
      ),
    [companySlug]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const run = async (what: string, call: () => Promise<void>, okText: string): Promise<boolean> => {
    setBusy(true);
    setNotice(null);
    try {
      await call();
      setNotice({ kind: "ok", text: okText });
      return true;
    } catch (err) {
      setNotice({ kind: "error", text: failure(err, what) });
      return false;
    } finally {
      await load();
      setBusy(false);
    }
  };

  const invite = async (e: React.FormEvent) => {
    e.preventDefault();
    const target = email.trim();
    const ok = await run("send the invitation", () => inviteCompanyMember(companySlug, target, role),
      `Invitation sent to ${target}. The link was emailed and expires in 7 days.`);
    if (ok) setEmail("");
  };

  return (
    <section aria-label={`Members of ${companyName}`} className="rounded-[24px] border border-[var(--stroke)] bg-[var(--surface-strong)]/80 p-6 shadow-[var(--shadow)]">
      <h2 className="font-display text-xl font-semibold text-[var(--navy-dark)]">Users of {companyName}</h2>
      {notice && (
        <p role={notice.kind === "ok" ? "status" : "alert"} data-testid="members-notice"
          className={`mt-3 text-sm font-medium ${notice.kind === "ok" ? "text-green-800" : "text-red-800"}`}>
          {notice.text}
        </p>
      )}

      <form onSubmit={invite} className="mt-4 grid gap-3 sm:grid-cols-[1fr_200px_auto] sm:items-end">
        <div>
          <label htmlFor="invite-member-email" className={labelClass}>Invite by email</label>
          <input id="invite-member-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            disabled={busy} autoComplete="off" className={fieldClass} />
        </div>
        <div>
          <label htmlFor="invite-member-role" className={labelClass}>Role</label>
          <select id="invite-member-role" value={role} onChange={(e) => setRole(e.target.value as InviteRole)}
            disabled={busy} className={fieldClass}>
            {INVITE_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
          </select>
        </div>
        <button type="submit" disabled={busy || !email.trim()} className={primaryButton}>Send invitation</button>
      </form>
      <p className="mt-2 text-xs text-[var(--gray-text)]">
        Sales Members edit and move only their own cards. Visitors are read-only. To create an Admin, invite the person as a Sales Member; once they have joined, change their role to Admin in the list below.
      </p>

      <h3 className={`${labelClass} mt-6`}>Members</h3>
      {members === null ? (
        loadFailed ? (
          <button type="button" onClick={() => load()} className={`${secondaryButton} mt-2`}>Try again</button>
        ) : (
          <p className="mt-2 text-sm text-[var(--gray-text)]">Loading...</p>
        )
      ) : members.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--gray-text)]">No members yet. Invite someone above.</p>
      ) : (
        <ul className="mt-2 divide-y divide-[var(--stroke)]" data-testid="member-list">
          {members.map((m) => (
            <li key={m.user_id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
              <span className="min-w-0 flex-1 truncate font-medium text-[var(--navy-dark)]">{m.email}</span>
              {m.is_active && MEMBER_ROLES.includes(m.role as MemberRole) ? (
                <select aria-label={`Role of ${m.email}`} value={m.role} disabled={busy}
                  onChange={(e) => {
                    const next = e.target.value as MemberRole;
                    // Granting Admin is the one escalation on this screen: ask first. The backend decides regardless.
                    if (next === "admin" && !window.confirm(`Make ${m.email} an Admin of ${companyName}? Admins can edit, assign and move any card.`)) return;
                    void run("change the role", () => changeCompanyMemberRole(companySlug, m.user_id, next), `Role of ${m.email} changed.`);
                  }}
                  className="rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] text-[var(--navy-dark)] px-2 py-1 text-xs">
                  {MEMBER_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                </select>
              ) : (
                <span className="text-xs text-[var(--gray-text)]">{ROLE_LABELS[m.role] ?? m.role}</span>
              )}
              <span className="text-xs text-[var(--gray-text)]">{m.is_active ? "Active" : "Inactive"}</span>
              {m.is_active && m.role !== "admin" && (
                <button type="button" disabled={busy} aria-label={`Deactivate ${m.email}`}
                  onClick={() => run("deactivate the member", () => deactivateCompanyMember(companySlug, m.user_id), `${m.email} no longer has access.`)}
                  className={secondaryButton}>
                  Deactivate
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {pending.length > 0 && (
        <>
          <h3 className={`${labelClass} mt-6`}>Pending invitations</h3>
          <ul className="mt-2 divide-y divide-[var(--stroke)]" data-testid="pending-list">
            {pending.map((p) => (
              <li key={p.invitation_id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{p.email}</span>
                <span className="text-xs text-[var(--gray-text)]">{ROLE_LABELS[p.role] ?? p.role}</span>
                <span className="text-xs text-[var(--gray-text)]">expires {new Date(p.expires_at).toLocaleDateString()}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
};
