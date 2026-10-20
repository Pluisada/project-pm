"use client";

import { useEffect, useRef, useState } from "react";
import { getCompanyCardHistory, getErrorMessage, isAbortError, type ApiError, type CardEditHistoryEntry } from "@/lib/api";

const FIELD_LABELS: Record<string, string> = {
  title: "Title",
  details: "Details",
  company: "Company",
  contact: "Contact",
  deal_value: "Deal value (current year)",
  deal_value_next_year: "Deal value (next year)",
  currency: "Currency",
  expected_close_date: "Expected Close Date",
  next_step_due: "Next Action Date",
  next_step: "Action",
  lead_source: "Lead source",
  product_line: "Product line",
  confidence_level: "Probability level",
  salesperson: "Salesperson",
};

type Loaded = { entries: CardEditHistoryEntry[] } | { error: Partial<ApiError> };

/** 0 is a real value; only null/blank/missing count as empty. */
const showValue = (value: unknown, field: string): string => {
  if (value === null || value === undefined || value === "") return field === "confidence_level" ? "Not assigned" : "(empty)";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
};

const formatTimestamp = (iso: unknown): string | null => {
  if (typeof iso !== "string") return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
};

/** The changed fields of a record; a record whose `changes` is missing or malformed simply has none to show. */
const changesOf = (entry: CardEditHistoryEntry): [string, { old?: unknown; new?: unknown }][] => {
  const raw: unknown = entry?.changes;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  return Object.entries(raw as Record<string, unknown>).map(([field, change]) => [
    field,
    change && typeof change === "object" ? (change as { old?: unknown; new?: unknown }) : {},
  ]);
};

function failureText(err: Partial<ApiError>): string {
  switch (err.status_code) {
    case 0: return "Network error - check your connection.";
    case 401: return "Your session expired.";
    case 403: return "You do not have access to this history.";
    case 404: return "This card was not found - it may have been deleted.";
    default: return getErrorMessage(err, "Could not load the history.");
  }
}

/**
 * Edit history of one card, read from the company endpoint. The caller mounts it with a key made of the
 * company, board and card, so a different selection is always a new instance; the request is aborted on unmount
 * and an answer is only applied while this instance is still mounted. Read-only: nothing here writes.
 */
export const CentralCardHistory = ({ companySlug, boardId, cardId, cardTitle, onClose }: {
  companySlug: string; boardId: number; cardId: number; cardTitle: string; onClose: () => void;
}) => {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    getCompanyCardHistory(companySlug, boardId, cardId, controller.signal).then(
      (entries) => { if (!controller.signal.aborted) setLoaded({ entries }); },
      (error: Partial<ApiError>) => { if (!controller.signal.aborted && !isAbortError(error)) setLoaded({ error }); }
    );
    return () => controller.abort();
  }, [companySlug, boardId, cardId, attempt]);

  useEffect(() => { closeRef.current?.focus(); }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={`Edit history for ${cardTitle}`} data-testid="central-history"
        className="max-h-[80vh] w-full max-w-lg overflow-hidden rounded-[24px] border border-[var(--stroke)] bg-white shadow-[var(--shadow)]"
        onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 border-b border-[var(--stroke)] px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">Edit history</p>
            <h3 className="mt-1 font-display text-lg font-semibold text-[var(--navy-dark)]">{cardTitle}</h3>
          </div>
          <button type="button" ref={closeRef} onClick={onClose} aria-label="Close history"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--stroke)] text-sm font-semibold text-[var(--gray-text)] transition hover:text-[var(--navy-dark)]">
            X
          </button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto px-6 py-4">
          {!loaded && <p role="status" className="text-sm text-[var(--gray-text)]">Loading history...</p>}
          {loaded && "error" in loaded && (
            <div>
              <p role="alert" className="text-sm font-medium text-red-800">{failureText(loaded.error)}</p>
              {(loaded.error.status_code === 0 || (loaded.error.status_code ?? 0) >= 500) && (
                <button type="button" onClick={() => { setLoaded(null); setAttempt((n) => n + 1); }}
                  className="mt-3 rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90">
                  Try again
                </button>
              )}
            </div>
          )}
          {loaded && "entries" in loaded && loaded.entries.length === 0 && (
            <p className="text-sm text-[var(--gray-text)]">No edits recorded. Only edits made by Sales Members are kept here.</p>
          )}
          {loaded && "entries" in loaded && loaded.entries.length > 0 && (
            <>
              <p className="mb-3 text-xs text-[var(--gray-text)]">Newest first. Only edits made by Sales Members are recorded.</p>
              <ul className="space-y-4">
                {loaded.entries.map((entry, index) => {
                  const when = formatTimestamp(entry?.created_at);
                  const changes = changesOf(entry);
                  return (
                    <li key={entry?.id ?? `row-${index}`} data-testid="history-entry" className="rounded-2xl border border-[var(--stroke)] p-4">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-semibold text-[var(--navy-dark)]">{entry?.edited_by || "unknown"}</span>
                        <span className="text-xs text-[var(--gray-text)]">{when ?? "Date not recorded"}</span>
                      </div>
                      {changes.length === 0 ? (
                        <p className="mt-3 text-sm text-[var(--gray-text)]">No field details recorded.</p>
                      ) : (
                        <dl className="mt-3 space-y-2">
                          {changes.map(([field, change]) => (
                            <div key={field}>
                              <dt className="text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]">{FIELD_LABELS[field] || field}</dt>
                              <dd className="mt-1 text-sm leading-6 text-[var(--navy-dark)]">
                                <span className="text-[var(--gray-text)] line-through">{showValue(change.old, field)}</span>
                                {" -> "}
                                {showValue(change.new, field)}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      )}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
