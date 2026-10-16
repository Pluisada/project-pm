"use client";

import { useEffect } from "react";
import type { CardEditHistoryEntry } from "@/lib/api";

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
  salesperson: "Salesperson",
};

// 0 is a real deal value, so only null/blank count as empty.
const showValue = (value: string | number | null) =>
  value === null || value === "" ? "(empty)" : value;

const formatTimestamp = (isoString: string) =>
  new Date(isoString).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });

type CardHistoryModalProps = {
  cardTitle: string;
  entries: CardEditHistoryEntry[] | null;
  isLoading: boolean;
  onClose: () => void;
};

export const CardHistoryModal = ({ cardTitle, entries, isLoading, onClose }: CardHistoryModalProps) => {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[80vh] w-full max-w-lg overflow-hidden rounded-[24px] border border-[var(--stroke)] bg-white shadow-[var(--shadow)]"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Edit history for ${cardTitle}`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-[var(--stroke)] px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">
              Edit history
            </p>
            <h3 className="mt-1 font-display text-lg font-semibold text-[var(--navy-dark)]">
              {cardTitle}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--stroke)] text-sm font-semibold text-[var(--gray-text)] transition hover:text-[var(--navy-dark)]"
            aria-label="Close"
          >
            X
          </button>
        </div>

        <div className="max-h-[60vh] overflow-y-auto px-6 py-4">
          {isLoading && (
            <p className="text-sm text-[var(--gray-text)]">Loading history...</p>
          )}
          {!isLoading && entries?.length === 0 && (
            <p className="text-sm text-[var(--gray-text)]">No edits recorded.</p>
          )}
          {!isLoading && entries && entries.length > 0 && (
            <ul className="space-y-4">
              {entries.map((entry) => (
                <li key={entry.id} className="rounded-2xl border border-[var(--stroke)] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold text-[var(--navy-dark)]">
                      {entry.edited_by}
                    </span>
                    <span className="text-xs text-[var(--gray-text)]">
                      {formatTimestamp(entry.created_at)}
                    </span>
                  </div>
                  <dl className="mt-3 space-y-2">
                    {Object.entries(entry.changes).map(([field, { old, new: newValue }]) => (
                      <div key={field}>
                        <dt className="text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]">
                          {FIELD_LABELS[field] || field}
                        </dt>
                        <dd className="mt-1 text-sm leading-6 text-[var(--navy-dark)]">
                          <span className="text-[var(--gray-text)] line-through">{showValue(old)}</span>
                          {" -> "}
                          {showValue(newValue)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
};
