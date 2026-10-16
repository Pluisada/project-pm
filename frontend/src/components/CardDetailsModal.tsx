"use client";

import { useEffect, useRef } from "react";
import type { CardResponse, StageKind } from "@/lib/api";
import { DEAL_CURRENCY, formatCents, formatDate, formatMoney, isValidIsoDate } from "@/lib/format";

type CardDetailsModalProps = {
  card: CardResponse;
  stageTitle: string;
  stageKind: StageKind;
  // Whether the current user may edit this card; the backend still decides.
  canEdit: boolean;
  onEdit: () => void;
  onClose: () => void;
};

type Row = { label: string; value: string | null | undefined };

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// A missing value is left out entirely rather than shown as 0 or a guess.
const present = (rows: Row[]) => rows.filter((row) => row.value != null && row.value !== "");

// An imported date that is not a real calendar date is shown as written,
// never silently reformatted into "Invalid Date".
const showDate = (value: string | null) =>
  value == null ? null : isValidIsoDate(value) ? formatDate(value) : value;

const Section = ({ title, rows }: { title: string; rows: Row[] }) => {
  const shown = present(rows);
  if (shown.length === 0) return null;
  return (
    <section className="px-5 py-4 sm:px-6">
      <h4 className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">{title}</h4>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        {shown.map((row) => (
          <div key={row.label} className="min-w-0">
            <dt className="text-xs text-[var(--gray-text)]">{row.label}</dt>
            <dd className="mt-0.5 whitespace-pre-wrap break-words text-sm text-[var(--navy-dark)]">{row.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
};

export const CardDetailsModal = ({
  card,
  stageTitle,
  stageKind,
  canEdit,
  onEdit,
  onClose,
}: CardDetailsModalProps) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // Initial focus, focus restoration, Escape and Tab containment.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const items = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialogRef.current.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialogRef.current.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  const bid = card.bid_details;
  const source = card.source_ref;

  const money = (value: number | null) => (value != null ? formatMoney(value, DEAL_CURRENCY) : null);

  // One row per year; with nothing entered for either year, no row (never an invented $0).
  const hasDealAmount = card.deal_value != null || (card.deal_value_next_year ?? 0) > 0;
  const dealRows: Row[] =
    card.deal_year == null
      ? [{ label: "Deal value", value: money(card.deal_value) }]
      : hasDealAmount
        ? [
            { label: `Deal value ${card.deal_year}`, value: money(card.deal_value) },
            { label: `Deal value ${card.deal_year + 1}`, value: money(card.deal_value_next_year ?? 0) },
          ]
        : [];

  const general: Row[] = [
    { label: "Customer", value: card.company },
    { label: "Contact", value: card.contact },
    { label: "Salesperson", value: card.salesperson_name },
    ...dealRows,
    { label: "Expected Close Date", value: showDate(card.expected_close_date) },
    { label: "Stage", value: `${stageTitle}${stageKind === "open" ? "" : ` (${stageKind})`}` },
    { label: "Lead source", value: card.lead_source },
    { label: "Product line", value: card.product_line },
    { label: "Details", value: card.details },
  ];

  const revenue = (cents: number | null) =>
    cents == null || !bid ? null : formatCents(cents, bid.revenue_currency);
  const number = (value: number | null) => (value == null ? null : value.toLocaleString());

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-[24px] border border-[var(--stroke)] bg-white shadow-[var(--shadow)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="card-details-title"
      >
        <div className="flex items-start justify-between gap-4 border-b border-[var(--stroke)] px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">
              Opportunity
            </p>
            <h3
              id="card-details-title"
              className="mt-1 break-words font-display text-lg font-semibold text-[var(--navy-dark)]"
            >
              {card.title}
            </h3>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--stroke)] text-sm font-semibold text-[var(--gray-text)] transition hover:text-[var(--navy-dark)]"
            aria-label="Close"
          >
            X
          </button>
        </div>

        <div className="min-h-0 flex-1 divide-y divide-[var(--stroke)] overflow-y-auto">
          <Section title="General" rows={general} />

          {bid ? (
            <>
              <Section
                title="Bid: product and customer"
                rows={[
                  { label: "Plant", value: bid.plant },
                  { label: "Customer type", value: bid.customer_type },
                  { label: "Business type", value: bid.business_type },
                  { label: "Product category", value: bid.product_category },
                  { label: "Product", value: bid.product },
                  { label: "Solution type", value: bid.solution_type },
                ]}
              />
              <Section
                title="Bid: forecast"
                rows={[
                  { label: "Confidence", value: bid.confidence == null ? null : `${bid.confidence}%` },
                  { label: "Volume, current year", value: number(bid.volume_current_year) },
                  { label: "Volume, next year", value: number(bid.volume_next_year) },
                  { label: "Production start", value: showDate(bid.production_start_date) },
                  { label: "Revenue, current year", value: revenue(bid.revenue_current_year_cents) },
                  { label: "Revenue, next year", value: revenue(bid.revenue_next_year_cents) },
                  {
                    label: "Revenue currency",
                    value:
                      bid.revenue_current_year_cents != null || bid.revenue_next_year_cents != null
                        ? bid.revenue_currency
                        : null,
                  },
                ]}
              />
              <Section
                title="Bid: next action and status"
                rows={[
                  { label: "Action", value: bid.next_step },
                  { label: "Next Action Date", value: showDate(bid.next_step_due) },
                  { label: "Status note", value: bid.status_note },
                  { label: "Project lead (source)", value: bid.project_lead },
                  { label: "CPM (source)", value: bid.cpm },
                ]}
              />
              <Section
                title="Bid: closure"
                rows={[
                  { label: "Close reason", value: bid.close_reason },
                  { label: "Close reason note", value: bid.close_reason_note },
                ]}
              />
            </>
          ) : (
            <p className="px-5 py-4 text-sm text-[var(--gray-text)] sm:px-6">
              No Bid report data for this opportunity.
            </p>
          )}

          {source && (
            <Section
              title="Source reference"
              rows={[
                { label: "Source code", value: source.source_code },
                { label: "Source confidence", value: source.source_confidence },
              ]}
            />
          )}
        </div>

        {canEdit && (
          <div className="flex justify-end border-t border-[var(--stroke)] px-5 py-4 sm:px-6">
            <button
              type="button"
              onClick={onEdit}
              className="rounded-lg bg-[var(--secondary-purple)] px-5 py-2 text-sm font-semibold text-white transition hover:opacity-90"
            >
              Edit
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
