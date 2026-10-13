import { useState } from "react";
import clsx from "clsx";
import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import type { Card, Column } from "@/lib/kanban";
import { KanbanCard } from "@/components/KanbanCard";
import { NewCardForm } from "@/components/NewCardForm";

type KanbanColumnProps = {
  column: Column;
  cards: Card[];
  canDeleteCards: boolean;
  // Called once per committed edit (blur or Enter), not per keystroke. May
  // resolve false on failure, which reverts the field to the saved title.
  onRename: (columnId: string, title: string) => void | Promise<boolean>;
  // False renders the title as plain text (members can't rename stages).
  canRename?: boolean;
  onAddCard: (columnId: string, title: string, details: string) => void;
  onDeleteCard: (columnId: string, cardId: string) => void;
  onUpdateCard: (columnId: string, cardId: string, title: string, details: string) => Promise<boolean>;
  onOpenCardHistory: (cardId: string) => void;
  onEditLead?: (cardId: string) => void;
};

export const KanbanColumn = ({
  column,
  cards,
  canDeleteCards,
  onRename,
  canRename = true,
  onAddCard,
  onDeleteCard,
  onUpdateCard,
  onOpenCardHistory,
  onEditLead,
}: KanbanColumnProps) => {
  const { setNodeRef, isOver } = useDroppable({ id: column.id });
  // The in-progress edit; null when not editing, so the field always falls
  // back to the saved title (including after a failed save or a reload).
  const [draft, setDraft] = useState<string | null>(null);

  const commitTitle = async () => {
    if (draft === null) return;
    const title = draft.trim();
    if (title && title !== column.title) {
      await onRename(column.id, title);
    }
    setDraft(null);
  };

  return (
    <section
      ref={setNodeRef}
      className={clsx(
        "flex min-h-[520px] flex-col rounded-3xl border border-[var(--stroke)] bg-[var(--surface-strong)] p-4 shadow-[var(--shadow)] transition",
        isOver && "ring-2 ring-[var(--accent-yellow)]"
      )}
      data-testid={`column-${column.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="w-full">
          <div className="flex items-center gap-3">
            <div className="h-2 w-10 rounded-full bg-[var(--accent-yellow)]" />
            <span className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">
              {cards.length} cards
            </span>
          </div>
          <div className="mt-3 flex items-center gap-2">
            {canRename ? (
              <input
                value={draft ?? column.title}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={commitTitle}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                  if (event.key === "Escape") setDraft(null);
                }}
                className="min-w-0 flex-1 bg-transparent font-display text-lg font-semibold text-[var(--navy-dark)] outline-none"
                aria-label="Column title"
              />
            ) : (
              <h3
                className="min-w-0 flex-1 truncate font-display text-lg font-semibold text-[var(--navy-dark)]"
                // Not "column-*": tests select columns by that testid prefix.
                data-testid="stage-title"
              >
                {column.title}
              </h3>
            )}
            {column.stageKind && column.stageKind !== "open" && (
              <span
                className={clsx(
                  "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em]",
                  column.stageKind === "won"
                    ? "bg-green-50 text-green-800"
                    : "bg-[var(--surface)] text-[var(--gray-text)]"
                )}
                title={`Deals in this stage count as ${column.stageKind}`}
              >
                {column.stageKind}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="mt-4 flex flex-1 flex-col gap-3">
        <SortableContext items={column.cardIds} strategy={verticalListSortingStrategy}>
          {cards.map((card) => (
            <KanbanCard
              key={card.id}
              card={card}
              canDelete={canDeleteCards}
              onDelete={(cardId) => onDeleteCard(column.id, cardId)}
              onUpdate={(cardId, title, details) => onUpdateCard(column.id, cardId, title, details)}
              onOpenHistory={onOpenCardHistory}
              onEditLead={onEditLead}
            />
          ))}
        </SortableContext>
        {cards.length === 0 && (
          <div className="flex flex-1 items-center justify-center rounded-2xl border border-dashed border-[var(--stroke)] px-3 py-6 text-center text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">
            Drop a card here
          </div>
        )}
      </div>
      <NewCardForm
        onAdd={(title, details) => onAddCard(column.id, title, details)}
      />
    </section>
  );
};
