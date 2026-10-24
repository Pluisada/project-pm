"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  clearCompanyReview,
  getCompanyReviews,
  getErrorMessage,
  isAbortError,
  type ApiError,
  type ReviewItem,
} from "@/lib/api";

export const ROLE_LABEL: Record<string, string> = { admin: "Admin", super_admin: "Super Admin" };
const KIND_LABEL: Record<string, string> = { member_edit: "Sales Member edit", backward_move: "Move to an earlier stage", lost_move: "Move to Lost" };

const FIELD_LABELS: Record<string, string> = {
  title: "Title", details: "Details", company: "Customer", contact: "Contact", deal_value: "Deal value (current year)",
  deal_value_next_year: "Deal value (next year)", expected_close_date: "Expected close date", lead_source: "Lead source",
  product_line: "Product line", confidence_level: "Probability level", salesperson: "Salesperson",
};

const show = (value: unknown) => (value === null || value === undefined || value === "" ? "(empty)" : String(value));

type Trigger = ReviewItem["triggers"][number] & { user_id?: number | null };

/** What the marker says: whose review it is and what caused it (from the obligation's own recorded triggers). */
export function reviewLabel(item: ReviewItem): string {
  const kinds = [...new Set(item.triggers.map((t) => KIND_LABEL[t.kind] ?? t.kind))];
  return `${ROLE_LABEL[item.reviewer_role] ?? item.reviewer_role} review required${kinds.length ? ` (${kinds.join(", ")})` : ""}`;
}

const when = (iso: string) => {
  const date = new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
};

/**
 * The caller's own pending reviews on a board, read from the backend (never derived locally). `enabled` is the
 * backend's own canReview capability; the endpoint refuses everyone else. `reload` always shows what the server has now.
 */
export function useCompanyReviews(companySlug: string, boardId: number, enabled: boolean, refreshToken: number) {
  const [state, setState] = useState<{ key: string; items: ReviewItem[]; failed: boolean } | null>(null);
  const seq = useRef(0);
  const key = `${companySlug}:${boardId}`;

  const reload = useCallback((signal?: AbortSignal) => {
    const mine = ++seq.current;
    return getCompanyReviews(companySlug, boardId, signal).then(
      (items) => { if (mine === seq.current && !signal?.aborted) setState({ key, items, failed: false }); },
      (err: unknown) => {
        if (signal?.aborted || isAbortError(err) || mine !== seq.current) return;
        setState((cur) => ({ key, items: cur?.key === key ? cur.items : [], failed: true }));
      }
    );
  }, [companySlug, boardId, key]);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [enabled, reload, refreshToken]);

  const current = enabled && state?.key === key ? state : null;
  return { items: current?.items ?? [], failed: current?.failed ?? false, reload: () => reload() };
}

type DialogProps = {
  companySlug: string;
  boardId: number;
  /** The obligation as the server last reported it. */
  item: ReviewItem;
  /** True while the server still lists it; false once a re-read no longer does (cleared or voided elsewhere). */
  stillPending: boolean;
  onClose: () => void;
  /** The backend confirmed the clearing: the parent drops the marker and reads the list again. */
  onCleared: (item: ReviewItem) => void;
  /** A clearing failed: the parent reads the list again so the marker shows the server's state. */
  onFailed: () => void;
  onOpenHistory?: (cardId: number) => void;
};

/**
 * One review obligation in a focused dialog: its category and status, every recorded change with who made it, when,
 * and the old and new values, and an explicit Clear. Clear sends the card version the reviewer is looking at; if the
 * card changed since, the backend answers 409, nothing is cleared and the marker stays. Nothing disappears before the
 * backend confirms.
 */
export const CentralReviewDialog = ({ companySlug, boardId, item, stillPending, onClose, onCleared, onFailed, onOpenHistory }: DialogProps) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const busyRef = useRef(false);

  useEffect(() => { closeRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busyRef.current) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const clear = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await clearCompanyReview(companySlug, boardId, item.card_id, item.reviewer_role, item.card_version);
      onCleared(item);
      return;
    } catch (err) {
      const failure = err as Partial<ApiError>;
      if (failure.status_code === 409 || failure.code === "review_stale") {
        setError("This card changed after the review was opened, so nothing was cleared. The review below is the latest version: read it again before clearing.");
      } else if (failure.status_code === 404) {
        setError("There is nothing left to review for this card; nothing was cleared by this request.");
      } else if (failure.status_code === 403) {
        setError("You are not allowed to clear this review.");
      } else if (failure.status_code === 0) {
        setError("Network error - it is not known whether the review was cleared. The list was read again; check whether the marker is still there.");
      } else {
        setError(getErrorMessage(err, "The review could not be cleared."));
      }
      onFailed();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const triggers = [...item.triggers].reverse() as Trigger[]; // newest first
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="review-dialog-title" data-testid="review-dialog"
        className="flex max-h-[90vh] w-full max-w-xl flex-col gap-3 overflow-y-auto rounded-[24px] border border-[var(--stroke)] bg-[var(--surface-strong)] p-6 shadow-[var(--shadow)]">
        <h3 id="review-dialog-title" className="font-display text-lg font-semibold text-[var(--navy-dark)]">
          {ROLE_LABEL[item.reviewer_role] ?? item.reviewer_role} review: {item.card_title}
        </h3>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="font-semibold text-[var(--gray-text)]">Category</dt>
          <dd>{reviewLabel(item)}</dd>
          <dt className="font-semibold text-[var(--gray-text)]">Status</dt>
          <dd data-testid="review-status">{stillPending ? "Pending" : "No longer pending on the server"}</dd>
          <dt className="font-semibold text-[var(--gray-text)]">Stage</dt>
          <dd>{item.column_title}</dd>
          <dt className="font-semibold text-[var(--gray-text)]">Opened</dt>
          <dd>{when(item.created_at)}</dd>
          <dt className="font-semibold text-[var(--gray-text)]">Last change</dt>
          <dd>{when(item.updated_at)}</dd>
        </dl>
        <h4 className="text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]">What changed</h4>
        {triggers.length === 0 ? (
          <p className="text-sm text-[var(--gray-text)]">No change details were recorded for this review.</p>
        ) : (
          <ul className="space-y-3">
            {triggers.map((t, i) => (
              <li key={`${t.version}-${i}`} data-testid="review-trigger" className="rounded-lg border border-[var(--stroke)] p-3 text-sm">
                <p className="font-semibold text-[var(--navy-dark)]">{KIND_LABEL[t.kind] ?? t.kind}</p>
                <p className="text-xs text-[var(--gray-text)]">
                  {t.user_id != null ? `By user #${t.user_id}` : "By a user not recorded"} - {when(t.at)}
                </p>
                {t.kind === "backward_move" ? (
                  <p className="mt-1">Moved back from <strong>{t.from_title ?? "?"}</strong> to <strong>{t.to_title ?? "?"}</strong></p>
                ) : t.kind === "lost_move" ? (
                  <p className="mt-1">Moved to Lost from <strong>{t.from_title ?? "?"}</strong> (<strong>{t.to_title ?? "?"}</strong>)</p>
                ) : Object.keys(t.changes ?? {}).length ? (
                  <table className="mt-1 w-full text-left text-xs">
                    <thead><tr className="text-[var(--gray-text)]"><th className="pr-2 font-semibold">Field</th><th className="pr-2 font-semibold">Before</th><th className="font-semibold">After</th></tr></thead>
                    <tbody>
                      {Object.entries(t.changes ?? {}).map(([field, c]) => (
                        <tr key={field}><td className="pr-2 align-top">{FIELD_LABELS[field] ?? field}</td><td className="pr-2 align-top">{show(c?.old)}</td><td className="align-top">{show(c?.new)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="mt-1 text-xs text-[var(--gray-text)]">No field details were recorded.</p>
                )}
              </li>
            ))}
          </ul>
        )}
        {error && <p role="alert" data-testid="review-error" className="text-sm font-medium text-red-800">{error}</p>}
        <div className="mt-2 flex flex-wrap justify-end gap-2">
          {onOpenHistory && (
            <button type="button" onClick={() => onOpenHistory(item.card_id)} disabled={busy}
              className="mr-auto text-sm font-semibold text-[var(--primary-blue)] hover:underline disabled:opacity-50">
              History
            </button>
          )}
          <button ref={closeRef} type="button" onClick={onClose} disabled={busy}
            className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-sm font-semibold text-[var(--gray-text)] disabled:opacity-50">
            Close
          </button>
          <button type="button" onClick={() => void clear()} disabled={busy || !stillPending}
            className="rounded-lg bg-[var(--secondary-purple)] px-5 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50">
            {busy ? "Clearing..." : "Clear"}
          </button>
        </div>
      </div>
    </div>
  );
};
