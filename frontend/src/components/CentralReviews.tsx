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

const ROLE_LABEL: Record<string, string> = { admin: "Admin", super_admin: "Super Admin" };

const FIELD_LABELS: Record<string, string> = {
  title: "Title", details: "Details", company: "Customer", contact: "Contact", deal_value: "Deal value (current year)",
  deal_value_next_year: "Deal value (next year)", expected_close_date: "Expected close date", lead_source: "Lead source",
  product_line: "Product line", confidence_level: "Probability level", salesperson: "Salesperson",
};

const show = (value: unknown) => (value === null || value === undefined || value === "" ? "(empty)" : String(value));

function describeTrigger(t: ReviewItem["triggers"][number]): string {
  if (t.kind === "backward_move") return `Moved back from ${t.from_title ?? "?"} to ${t.to_title ?? "?"}`;
  const parts = Object.entries(t.changes ?? {}).map(([field, c]) => `${FIELD_LABELS[field] ?? field}: ${show(c?.old)} -> ${show(c?.new)}`);
  return parts.length ? `Edited by a Sales Member - ${parts.join("; ")}` : "Edited by a Sales Member";
}

type Props = {
  companySlug: string;
  boardId: number;
  /** Changes whenever the board may have changed, so the list is read again. */
  refreshToken: number;
  /** Reports the card ids with a pending review of the caller's role (for the badge on the cards). */
  onPendingChange?: (cardIds: number[]) => void;
  onOpenHistory?: (cardId: number) => void;
};

/**
 * Pending reviews of the caller's own role, read from the backend (never derived locally). Shown only to a
 * reviewer: the caller (CentralBoardView) mounts it when the backend's capabilities say canReview, and the endpoints
 * refuse everyone else. "Mark as reviewed" sends the version that is on screen; if the card changed since, the backend
 * answers 409, nothing is cleared, the list is read again and the reviewer sees the latest version.
 */
export const CentralReviews = ({ companySlug, boardId, refreshToken, onPendingChange, onOpenHistory }: Props) => {
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const seq = useRef(0);
  const report = useRef(onPendingChange);
  useEffect(() => { report.current = onPendingChange; });

  const load = useCallback((signal?: AbortSignal) => {
    const mine = ++seq.current;
    return getCompanyReviews(companySlug, boardId, signal).then(
      (list) => {
        if (mine !== seq.current || signal?.aborted) return;
        setItems(list);
        setLoadFailed(false);
        report.current?.(list.map((i) => i.card_id));
      },
      (err: unknown) => {
        if (signal?.aborted || isAbortError(err) || mine !== seq.current) return;
        setLoadFailed(true);
      }
    );
  }, [companySlug, boardId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, refreshToken]);

  const clear = async (item: ReviewItem) => {
    if (busyId !== null) return;
    setBusyId(item.id);
    setNotice(null);
    try {
      await clearCompanyReview(companySlug, boardId, item.card_id, item.reviewer_role, item.card_version);
      setNotice({ kind: "ok", text: `"${item.card_title}" marked as reviewed.` });
    } catch (err) {
      const failure = err as Partial<ApiError>;
      if (failure.status_code === 409 || failure.code === "review_stale") {
        setNotice({ kind: "error", text: "This card changed after you opened it, so it was not marked as reviewed. Review the latest version." });
      } else if (failure.status_code === 404) {
        setNotice({ kind: "error", text: "There is nothing left to review for this card." });
      } else if (failure.status_code === 0) {
        setNotice({ kind: "error", text: "Network error - it is not known whether the review was saved. The list was reloaded." });
      } else {
        setNotice({ kind: "error", text: getErrorMessage(err, "Could not mark the card as reviewed.") });
      }
    } finally {
      setBusyId(null);
      void load(); // always show what the server has now, success or not
    }
  };

  if (items === null && !loadFailed) return null;
  if (items !== null && items.length === 0 && !notice) return null;

  return (
    <section aria-label="Pending reviews" data-testid="central-reviews"
      className="mt-4 rounded-[14px] border border-amber-300 bg-amber-50 p-4">
      <h3 className="font-display text-base font-semibold text-amber-950">
        Pending reviews{items ? ` (${items.length})` : ""}{items?.[0] ? ` - ${ROLE_LABEL[items[0].reviewer_role] ?? ""} review` : ""}
      </h3>
      {loadFailed && (
        <p role="alert" className="mt-2 text-sm font-medium text-red-800">
          The pending reviews could not be loaded.{" "}
          <button type="button" onClick={() => void load()} className="font-semibold underline">Try again</button>
        </p>
      )}
      {notice && (
        <p role={notice.kind === "ok" ? "status" : "alert"} data-testid="review-notice"
          className={`mt-2 text-sm font-medium ${notice.kind === "ok" ? "text-green-800" : "text-red-800"}`}>
          {notice.text}
        </p>
      )}
      <ul className="mt-2 divide-y divide-amber-200">
        {(items ?? []).map((item) => (
          <li key={item.id} data-testid={`review-${item.card_id}`} className="py-2 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <span className="min-w-0 flex-1 truncate font-semibold text-[var(--navy-dark)]">
                {item.card_title} <span className="font-normal text-[var(--gray-text)]">({item.column_title})</span>
              </span>
              {onOpenHistory && (
                <button type="button" onClick={() => onOpenHistory(item.card_id)} aria-label={`Review history of ${item.card_title}`}
                  className="text-xs font-semibold text-[var(--primary-blue)] hover:underline">
                  History
                </button>
              )}
              <button type="button" disabled={busyId !== null} onClick={() => void clear(item)}
                aria-label={`Mark ${item.card_title} as reviewed`}
                className="rounded-lg bg-[var(--secondary-purple)] px-3 py-1.5 text-xs font-semibold text-white transition hover:opacity-90 disabled:opacity-60">
                {busyId === item.id ? "Saving..." : "Mark as reviewed"}
              </button>
            </div>
            <ul className="mt-1 list-disc pl-5 text-xs text-[var(--gray-text)]">
              {item.triggers.slice(-3).map((t, i) => <li key={`${t.version}-${i}`}>{describeTrigger(t)}</li>)}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
};
