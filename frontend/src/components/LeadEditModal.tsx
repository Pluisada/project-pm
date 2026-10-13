"use client";

import { useEffect, useState } from "react";
import type { CardResponse, CardUpdatePayload, LeadFields, LeadOptions } from "@/lib/api";
import { getErrorMessage } from "@/lib/api";

type LeadEditModalProps = {
  card: CardResponse;
  options: LeadOptions;
  onSave: (updates: CardUpdatePayload) => Promise<void>;
  onClose: () => void;
};

// Form state keeps every field as a string (what inputs/selects hold);
// "" means unset and is sent as null.
type FormState = Record<keyof LeadFields, string>;

const toForm = (card: CardResponse): FormState => ({
  company: card.company ?? "",
  contact: card.contact ?? "",
  deal_value: card.deal_value != null ? String(card.deal_value) : "",
  currency: card.currency ?? "",
  expected_close_date: card.expected_close_date ?? "",
  lead_source: card.lead_source ?? "",
  product_line: card.product_line ?? "",
  salesperson_id: card.salesperson_id != null ? String(card.salesperson_id) : "",
});

const fromForm = (field: keyof LeadFields, value: string) => {
  if (value === "") return null;
  if (field === "deal_value" || field === "salesperson_id") return Number(value);
  return value;
};

const inputClass =
  "mt-1 w-full rounded-lg border border-[var(--stroke)] bg-white px-3 py-2 text-sm text-[var(--navy-dark)] outline-none transition focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20";
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

  const set = (field: keyof LeadFields) => (
    event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>
  ) => setForm((prev) => ({ ...prev, [field]: event.target.value }));

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);

    if (form.deal_value !== "" && form.currency === "") {
      setError("Choose a currency for the deal value");
      return;
    }

    // Send only what changed, so a member's save doesn't log untouched fields.
    const original = toForm(card);
    const updates: CardUpdatePayload = {};
    for (const field of Object.keys(form) as (keyof LeadFields)[]) {
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
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-[24px] border border-[var(--stroke)] bg-white shadow-[var(--shadow)]"
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

          <label className={labelClass}>
            Company
            <input value={form.company} onChange={set("company")} className={inputClass} maxLength={255} />
          </label>
          <label className={labelClass}>
            Contact
            <input value={form.contact} onChange={set("contact")} className={inputClass} maxLength={255} />
          </label>

          <label className={labelClass}>
            Deal value
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
            Currency
            <select value={form.currency} onChange={set("currency")} className={inputClass}>
              <option value="">Not set</option>
              {options.currencies.map((currency) => (
                <option key={currency} value={currency}>{currency}</option>
              ))}
            </select>
          </label>

          <label className={labelClass}>
            Expected close date
            <input
              type="date"
              value={form.expected_close_date}
              onChange={set("expected_close_date")}
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
            Assigned salesperson
            <select value={form.salesperson_id} onChange={set("salesperson_id")} className={inputClass}>
              <option value="">Unassigned</option>
              {options.salespeople.map((person) => (
                <option key={person.id} value={String(person.id)}>{person.username}</option>
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
