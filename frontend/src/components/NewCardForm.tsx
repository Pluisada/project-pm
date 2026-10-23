import { useState, type FormEvent } from "react";

const initialFormState = { title: "", details: "", dealValue: "", dealValueNextYear: "" };

export type NewCardDeal = { deal_value?: number; deal_value_next_year?: number };

const inputClass =
  "w-full rounded-xl border border-[var(--stroke)] bg-[var(--surface-strong)] px-3 py-2 text-sm text-[var(--navy-dark)] outline-none transition focus:border-[var(--primary-blue)]";

type NewCardFormProps = {
  onAdd: (title: string, details: string, deal: NewCardDeal) => void;
};

export const NewCardForm = ({ onAdd }: NewCardFormProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const [formState, setFormState] = useState(initialFormState);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!formState.title.trim()) {
      return;
    }
    const deal: NewCardDeal = {};
    if (formState.dealValue !== "") deal.deal_value = Number(formState.dealValue);
    if (formState.dealValueNextYear !== "") deal.deal_value_next_year = Number(formState.dealValueNextYear);
    onAdd(formState.title.trim(), formState.details.trim(), deal);
    setFormState(initialFormState);
    setIsOpen(false);
  };

  return (
    <div className="mt-4">
      {isOpen ? (
        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            value={formState.title}
            onChange={(event) =>
              setFormState((prev) => ({ ...prev, title: event.target.value }))
            }
            placeholder="Card title"
            className="w-full rounded-xl border border-[var(--stroke)] bg-[var(--surface-strong)] px-3 py-2 text-sm font-medium text-[var(--navy-dark)] outline-none transition focus:border-[var(--primary-blue)]"
            required
          />
          <textarea
            value={formState.details}
            onChange={(event) =>
              setFormState((prev) => ({ ...prev, details: event.target.value }))
            }
            placeholder="Details"
            rows={3}
            className="w-full resize-none rounded-xl border border-[var(--stroke)] bg-[var(--surface-strong)] px-3 py-2 text-sm text-[var(--gray-text)] outline-none transition focus:border-[var(--primary-blue)]"
          />
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs font-semibold text-[var(--gray-text)]">
              Current year (USD)
              <input
                type="number"
                min={0}
                step="any"
                value={formState.dealValue}
                onChange={(event) => setFormState((prev) => ({ ...prev, dealValue: event.target.value }))}
                className={inputClass}
              />
            </label>
            <label className="text-xs font-semibold text-[var(--gray-text)]">
              Next year (USD)
              <input
                type="number"
                min={0}
                step="any"
                value={formState.dealValueNextYear}
                onChange={(event) => setFormState((prev) => ({ ...prev, dealValueNextYear: event.target.value }))}
                className={inputClass}
              />
            </label>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="submit"
              className="rounded-full bg-[var(--secondary-purple)] px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white transition hover:brightness-110"
            >
              Add card
            </button>
            <button
              type="button"
              onClick={() => {
                setIsOpen(false);
                setFormState(initialFormState);
              }}
              className="rounded-full border border-[var(--stroke)] px-3 py-2 text-xs font-semibold uppercase tracking-wide text-[var(--gray-text)] transition hover:text-[var(--navy-dark)]"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="w-full rounded-full border border-dashed border-[var(--stroke)] px-3 py-2 text-xs font-semibold uppercase tracking-wide text-[var(--primary-blue)] transition hover:border-[var(--primary-blue)]"
        >
          Add a card
        </button>
      )}
    </div>
  );
};
