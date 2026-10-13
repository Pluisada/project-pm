import { useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import clsx from "clsx";
import type { Card } from "@/lib/kanban";
import { formatDate, formatMoney } from "@/lib/format";

type KanbanCardProps = {
  card: Card;
  canDelete: boolean;
  onDelete: (cardId: string) => void;
  // Resolves false on failure (e.g. a blank title rejected by the backend),
  // so the field can be reverted instead of being left showing an unsaved,
  // invalid value.
  onUpdate: (cardId: string, title: string, details: string) => Promise<boolean>;
  onOpenHistory: (cardId: string) => void;
  // API-backed board only: opens the lead fields editor.
  onEditLead?: (cardId: string) => void;
};

export const KanbanCard = ({
  card,
  canDelete,
  onDelete,
  onUpdate,
  onOpenHistory,
  onEditLead,
}: KanbanCardProps) => {
  const lead = card.lead;
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: card.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  // Edited locally and only committed (via onUpdate) on blur, so typing
  // doesn't fire an API call - and a member edit history entry - per
  // keystroke. KanbanColumn keys this component by card.id, so a genuinely
  // different card mounts fresh rather than needing a resync effect here.
  const [title, setTitle] = useState(card.title);
  const [details, setDetails] = useState(card.details);

  const commitIfChanged = async () => {
    if (title === card.title && details === card.details) return;
    const succeeded = await onUpdate(card.id, title, details);
    if (!succeeded) {
      // Revert to the last known-good server value instead of leaving the
      // field showing an unsaved (and possibly invalid) edit indefinitely.
      setTitle(card.title);
      setDetails(card.details);
    }
  };

  return (
    <article
      ref={setNodeRef}
      style={style}
      className={clsx(
        "rounded-2xl border border-transparent bg-white px-4 py-4 shadow-[0_12px_24px_rgba(3,33,71,0.08)]",
        "transition-all duration-150",
        isDragging && "opacity-60 shadow-[0_18px_32px_rgba(3,33,71,0.16)]"
      )}
      data-testid={`card-${card.id}`}
      data-title={card.title}
    >
      {/* A slim action row above the content, rather than beside it, so
          title/details always get the card's full width to work with -
          narrow columns would otherwise squeeze the text down to nothing
          alongside the drag handle and delete button. */}
      <div className="flex items-center justify-between gap-2">
        {/* Title/details are editable text, so the drag gesture lives on its
            own handle rather than the whole card - otherwise picking up the
            card would fight with clicking into a field to edit or select
            text. */}
        <button
          type="button"
          {...attributes}
          {...listeners}
          className="shrink-0 cursor-grab touch-none rounded p-1 text-[var(--gray-text)] active:cursor-grabbing"
          aria-label={`Drag ${card.title}`}
        >
          ⠿
        </button>
        <div className="flex shrink-0 items-center gap-1">
          {card.hasMemberEdits && (
            <button
              type="button"
              onClick={() => onOpenHistory(card.id)}
              className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--accent-yellow)] text-[10px] font-bold text-[var(--navy-dark)] transition hover:brightness-95"
              aria-label={`View edit history for ${card.title}`}
              title="Edited by a member - view history"
            >
              M
            </button>
          )}
          {canDelete && (
            <button
              type="button"
              onClick={() => onDelete(card.id)}
              className="flex h-5 w-5 items-center justify-center rounded-full text-sm leading-none text-[var(--gray-text)] transition hover:bg-[var(--surface)] hover:text-[var(--navy-dark)]"
              aria-label={`Delete ${card.title}`}
              title="Delete card"
            >
              ×
            </button>
          )}
        </div>
      </div>
      <input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        onBlur={commitIfChanged}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
        className="mt-1 w-full bg-transparent font-display text-base font-semibold text-[var(--navy-dark)] outline-none"
        aria-label="Card title"
      />
      <textarea
        value={details}
        onChange={(event) => setDetails(event.target.value)}
        onBlur={commitIfChanged}
        placeholder="Nothing here yet"
        rows={2}
        className="mt-2 w-full resize-none bg-transparent text-sm leading-6 text-[var(--gray-text)] outline-none"
        aria-label="Card details"
      />
      {onEditLead && (
        <div className="mt-2 border-t border-[var(--stroke)] pt-2 text-xs leading-5 text-[var(--gray-text)]">
          {(lead?.company || lead?.contact) && (
            <p className="font-semibold text-[var(--navy-dark)]">
              {[lead.company, lead.contact].filter(Boolean).join(" - ")}
            </p>
          )}
          {lead?.dealValue != null && lead.currency && (
            <p className="text-sm font-semibold text-[var(--navy-dark)]">
              {formatMoney(lead.dealValue, lead.currency)}
            </p>
          )}
          {lead?.expectedCloseDate && (
            <p className={clsx(lead.overdue && "font-semibold text-red-700")}>
              Close {formatDate(lead.expectedCloseDate)}
              {lead.overdue && " (overdue)"}
            </p>
          )}
          {lead?.salespersonName && <p>{lead.salespersonName}</p>}
          <div className="mt-2 flex items-center justify-between gap-2">
            {lead?.productLine ? (
              <span className="whitespace-nowrap rounded-full bg-[var(--surface)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--secondary-purple)]">
                {lead.productLine}
              </span>
            ) : (
              <span />
            )}
            <button
              type="button"
              onClick={() => onEditLead(card.id)}
              className="shrink-0 whitespace-nowrap rounded-full border border-[var(--stroke)] px-3 py-0.5 text-[11px] font-semibold text-[var(--primary-blue)] transition hover:border-[var(--primary-blue)]"
              aria-label={`Edit lead ${card.title}`}
            >
              Edit lead
            </button>
          </div>
        </div>
      )}
    </article>
  );
};
