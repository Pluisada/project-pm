"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import type { CardResponse, CardUpdatePayload, LeadFields, LeadOptions } from "@/lib/api";
import { CONFIDENCE_LEVELS, getErrorMessage } from "@/lib/api";

type LeadEditModalProps = {
  card: CardResponse;
  options: LeadOptions;
  onSave: (updates: CardUpdatePayload) => Promise<void>;
  onClose: () => void;
};

// Form state keeps every field as a string (what inputs/selects hold);
// "" means unset and is sent as null.
type EditField =
  | Exclude<keyof LeadFields, "currency" | "expected_close_date" | "contact_email" | "contact_phone">
  | "title"
  | "details"
  | "deal_value_next_year"
  | "next_step_due"
  | "next_step";

export const ACTION_MAX_LENGTH = 100;
type FormState = Record<EditField, string>;

const toForm = (card: CardResponse): FormState => ({
  title: card.title,
  details: card.details ?? "",
  company: card.company ?? "",
  contact: card.contact ?? "",
  deal_value: card.deal_value != null ? String(card.deal_value) : "",
  deal_value_next_year: String(card.deal_value_next_year ?? 0),
  lead_source: card.lead_source ?? "",
  product_line: card.product_line ?? "",
  confidence_level: card.confidence_level ?? "",
  salesperson_id: card.salesperson_id != null ? String(card.salesperson_id) : "",
  next_step_due: card.bid_details?.next_step_due ?? "",
  next_step: card.bid_details?.next_step ?? "",
});

const fromForm = (field: EditField, value: string) => {
  if (field === "title" || field === "details") return value;
  if (field === "deal_value_next_year") return value === "" ? 0 : Number(value);
  if (value === "") return null;
  if (field === "deal_value" || field === "salesperson_id") return Number(value);
  return value;
};

// The labels name the year the card's amounts were entered for (its stored
// deal_year), so they never change meaning because the calendar did; a
// year rollover is an explicit operator step (docs/DATABASE.md).
const dealYearLabels = (dealYear: number | undefined) => {
  const thisYear = new Date().getFullYear();
  if (dealYear == null || dealYear === thisYear) {
    const year = dealYear ?? thisYear;
    return { current: `Deal value - current year (${year})`, next: `Deal value - next year (${year + 1})` };
  }
  return { current: `Deal value - ${dealYear}`, next: `Deal value - ${dealYear + 1}` };
};

const inputClass =
  "mt-1 w-full rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-3 py-2 text-sm text-[var(--navy-dark)] outline-none transition focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20";
const labelClass = "block text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]";

export const LeadEditModal = ({ card, options, onSave, onClose }: LeadEditModalProps) => {
  const [form, setForm] = useState<FormState>(() => toForm(card));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const set = (field: EditField) => (
    event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>
  ) => setForm((prev) => ({ ...prev, [field]: event.target.value }));

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);

    if (form.title.trim() === "") {
      setError("The opportunity name cannot be empty");
      return;
    }

    if (form.next_step.trim().length > ACTION_MAX_LENGTH) {
      setError(`The next action description can have at most ${ACTION_MAX_LENGTH} characters`);
      return;
    }

    // Send only what changed, so a member's save doesn't log untouched fields.
    const original = toForm(card);
    const updates: CardUpdatePayload = {};
    for (const field of Object.keys(form) as EditField[]) {
      if (form[field] !== original[field]) {
        Object.assign(updates, { [field]: fromForm(field, form[field]) });
      }
    }
    if (Object.keys(updates).length === 0) {
      onClose();
      return;
    }

    setSaving(true);
    try {
      await onSave(updates);
      onClose();
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Failed to save lead"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <form
        onSubmit={handleSubmit}
        onClick={(event) => event.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-[24px] border border-[var(--stroke)] bg-[var(--surface-strong)] shadow-[var(--shadow)]"
        role="dialog"
        aria-modal="true"
        aria-label={`Edit lead ${card.title}`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-[var(--stroke)] px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">
              Edit lead
            </p>
            <h3 className="mt-1 font-display text-lg font-semibold text-[var(--navy-dark)]">
              {card.title}
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

        <div className="grid grid-cols-1 gap-4 px-6 py-5 sm:grid-cols-2">
          {error && (
            <div className="rounded-lg border border-red-300 bg-red-50 p-3 sm:col-span-2">
              <p className="text-sm font-medium text-red-800">{error}</p>
            </div>
          )}

          <label className={clsx(labelClass, "sm:col-span-2")}>
            Opportunity name
            <input value={form.title} onChange={set("title")} className={inputClass} maxLength={255} autoFocus />
          </label>
          <label className={clsx(labelClass, "sm:col-span-2")}>
            Details
            <textarea value={form.details} onChange={set("details")} rows={3} className={inputClass} />
          </label>

          <label className={labelClass}>
            Company
            <input value={form.company} onChange={set("company")} className={inputClass} maxLength={255} />
          </label>
          <label className={labelClass}>
            Contact
            <input value={form.contact} onChange={set("contact")} className={inputClass} maxLength={255} />
          </label>

          <label className={labelClass}>
            {dealYearLabels(card.deal_year).current}
            <input
              type="number"
              min={0}
              step="any"
              value={form.deal_value}
              onChange={set("deal_value")}
              className={inputClass}
            />
          </label>
          <label className={labelClass}>
            {dealYearLabels(card.deal_year).next}
            <input
              type="number"
              min={0}
              step="any"
              value={form.deal_value_next_year}
              onChange={set("deal_value_next_year")}
              className={inputClass}
            />
          </label>

          <label className={labelClass}>
            Lead source
            <select value={form.lead_source} onChange={set("lead_source")} className={inputClass}>
              <option value="">Not set</option>
              {options.lead_sources.map((source) => (
                <option key={source} value={source}>{source}</option>
              ))}
            </select>
          </label>

          <label className={labelClass}>
            Next Action Date
            <input
              type="date"
              value={form.next_step_due}
              onChange={set("next_step_due")}
              className={inputClass}
            />
          </label>
          <label className={labelClass}>
            Next Action Description
            <input
              value={form.next_step}
              onChange={set("next_step")}
              className={inputClass}
              maxLength={ACTION_MAX_LENGTH}
              placeholder="e.g. Call customer to follow up on proposal"
            />
          </label>

          <label className={labelClass}>
            Assigned salesperson
            <select value={form.salesperson_id} onChange={set("salesperson_id")} className={inputClass}>
              <option value="">Unassigned</option>
              {options.salespeople.map((person) => (
                <option key={person.id} value={String(person.id)}>{person.username}</option>
              ))}
            </select>
          </label>
          <label className={labelClass}>
            Probability level
            <select value={form.confidence_level} onChange={set("confidence_level")} className={inputClass}>
              <option value="">Not assigned</option>
              {CONFIDENCE_LEVELS.map((level) => (
                <option key={level} value={level}>{level}</option>
              ))}
            </select>
          </label>
          <label className={labelClass}>
            Product line
            <select value={form.product_line} onChange={set("product_line")} className={inputClass}>
              <option value="">Not set</option>
              {options.product_lines.map((line) => (
                <option key={line} value={line}>{line}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex justify-end gap-3 border-t border-[var(--stroke)] px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-sm font-semibold text-[var(--navy-dark)] transition hover:border-[var(--primary-blue)]"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-[var(--secondary-purple)] px-5 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save lead"}
          </button>
        </div>
      </form>
    </div>
  );
};
