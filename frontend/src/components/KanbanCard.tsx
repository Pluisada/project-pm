import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import clsx from "clsx";
import type { Card } from "@/lib/kanban";
import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { DEAL_CURRENCY, formatDate, formatMoney } from "@/lib/format";

type KanbanCardProps = {
  card: Card;
  canDelete: boolean;
  onDelete: (cardId: string) => void;
  onOpenHistory: (cardId: string) => void;
  // API-backed board only: opens the read-first details view. Without it
  // (the local demo board) the card is a plain summary.
  onOpenDetails?: (cardId: string) => void;
};

// A compact, read-only summary. Everything editable lives in the details
// view and its edit form, so nothing here can be changed by a stray click.
export const KanbanCard = ({
  card,
  canDelete,
  onDelete,
  onOpenHistory,
  onOpenDetails,
}: KanbanCardProps) => {
  const lead = card.lead;
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: card.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const hasValue = lead?.dealValue != null;
  const deadlineOverdue = !!lead?.nextStepOverdue;

  const summary = (
    <>
      <span className="line-clamp-2 break-words font-display text-sm font-semibold leading-5 text-[var(--navy-dark)]">
        {card.title}
      </span>
      {card.details && (
        <span
          className="mt-0.5 line-clamp-3 whitespace-pre-wrap break-words text-xs leading-4 text-[var(--gray-text)]"
          data-testid="card-details-text"
        >
          {card.details}
        </span>
      )}
      {lead?.company && (
        <span className="mt-0.5 block truncate text-xs text-[var(--gray-text)]">{lead.company}</span>
      )}
      {(lead?.salespersonName || hasValue) && (
        <span className="mt-1.5 flex items-baseline justify-between gap-2 text-xs">
          <span className="min-w-0 truncate text-[var(--gray-text)]">{lead?.salespersonName ?? ""}</span>
          {hasValue && (
            <span className="ml-auto shrink-0 whitespace-nowrap font-semibold text-[var(--navy-dark)]">
              {formatMoney(lead!.dealValue!, DEAL_CURRENCY)}
            </span>
          )}
        </span>
      )}
      {(lead?.nextStep || lead?.nextStepDue) && (
        <span
          className="mt-1.5 flex items-baseline gap-1 text-xs leading-4 text-[var(--gray-text)]"
          title={[lead.nextStep, lead.nextStepDue && formatDate(lead.nextStepDue)].filter(Boolean).join(" - ")}
        >
          <span className="shrink-0 font-semibold text-[var(--navy-dark)]">Next:</span>
          {lead.nextStep && <span className="min-w-0 truncate">{lead.nextStep}</span>}
          {lead.nextStepDue && (
            <span className={clsx("shrink-0 whitespace-nowrap", deadlineOverdue && "font-semibold text-red-700")}>
              {lead.nextStep ? "by " : ""}
              {formatDate(lead.nextStepDue)}
            </span>
          )}
        </span>
      )}
      {lead?.confidenceLevel && (
        <span className="mt-1.5 block">
          <ConfidenceBadge level={lead.confidenceLevel} />
        </span>
      )}
      {(deadlineOverdue || (!lead?.nextStep && lead?.overdue)) && (
        <span className="mt-1.5 inline-block rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-red-700">
          {deadlineOverdue ? "Next action overdue" : "Close date overdue"}
        </span>
      )}
      {lead?.sourceCode && (
        <span className="mt-1.5 block truncate text-[10px] uppercase tracking-[0.1em] text-[var(--gray-text)]">
          Ref {lead.sourceCode}
        </span>
      )}
    </>
  );

  return (
    <article
      ref={setNodeRef}
      style={style}
      className={clsx(
        "rounded-xl border border-[rgba(3,33,71,0.1)] bg-white px-3 py-2.5 shadow-[0_1px_2px_rgba(3,33,71,0.06)]",
        "transition-all duration-150",
        isDragging && "opacity-60 shadow-[0_8px_16px_rgba(3,33,71,0.12)]"
      )}
      data-testid={`card-${card.id}`}
      data-title={card.title}
    >
      <div className="flex items-center justify-between gap-2">
        {/* The drag gesture lives on its own handle, so clicking the summary
            to open the details never competes with picking the card up. */}
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
      {onOpenDetails ? (
        <button
          type="button"
          onClick={() => onOpenDetails(card.id)}
          className="mt-1 block w-full rounded-lg px-1 py-0.5 text-left transition hover:bg-[var(--surface)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--primary-blue)]"
          aria-label={`Open details for ${card.title}`}
          aria-haspopup="dialog"
          data-open-details
        >
          {summary}
        </button>
      ) : (
        <div className="mt-1 px-1 py-0.5">{summary}</div>
      )}
    </article>
  );
};
