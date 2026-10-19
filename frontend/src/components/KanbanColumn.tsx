import { useState } from "react";
import clsx from "clsx";
import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import type { Card, Column } from "@/lib/kanban";
import { KanbanCard } from "@/components/KanbanCard";
import { NewCardForm } from "@/components/NewCardForm";

const OPEN_TONES = ["blue", "cyan", "turquoise", "lime"] as const;

// Won/lost come from the stage's kind; open stages are tinted by their order
// in the pipeline, never by title (stages can be renamed).
const stageTone = (column: Column, stageIndex?: number) => {
  if (column.stageKind === "won") return "green";
  if (column.stageKind === "lost") return "red";
  if (stageIndex === undefined) return null;
  return OPEN_TONES[Math.min(stageIndex, OPEN_TONES.length - 1)];
};

type KanbanColumnProps = {
  column: Column;
  // Order of this stage among the open stages, and how many there are
  // (API-backed board only); drives the "1/4" indicator.
  stageIndex?: number;
  openStageCount?: number;
  cards: Card[];
  canDeleteCards: boolean;
  // Called once per committed edit (blur or Enter), not per keystroke. May
  // resolve false on failure, which reverts the field to the saved title.
  onRename: (columnId: string, title: string) => void | Promise<boolean>;
  // False renders the title as plain text (members can't rename stages).
  canRename?: boolean;
  onAddCard: (columnId: string, title: string, details: string) => void;
  onDeleteCard: (columnId: string, cardId: string) => void;
  onOpenCardHistory: (cardId: string) => void;
  onOpenDetails?: (cardId: string) => void;
};

export const KanbanColumn = ({
  column,
  stageIndex,
  openStageCount,
  cards,
  canDeleteCards,
  onRename,
  canRename = true,
  onAddCard,
  onDeleteCard,
  onOpenCardHistory,
  onOpenDetails,
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

  const tone = stageTone(column, stageIndex);

  return (
    <section
      ref={setNodeRef}
      className={clsx(
        "flex min-h-[520px] flex-col rounded-[14px] border border-t-[3px] border-[var(--stroke)] p-4 transition",
        !tone && "bg-[var(--surface-strong)]",
        isOver && "ring-2 ring-[var(--accent-yellow)]"
      )}
      style={
        tone
          ? {
              backgroundColor: `var(--stage-${tone}-bg)`,
              borderColor: `color-mix(in srgb, var(--stage-${tone}-accent) 40%, white)`,
              borderTopColor: `var(--stage-${tone}-accent)`,
            }
          : undefined
      }
      data-testid={`column-${column.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="w-full">
          <div className="flex min-h-6 items-center justify-between gap-3">
            <span className="text-xs font-semibold text-[var(--gray-text)]">
              {cards.length} {cards.length === 1 ? "card" : "cards"}
            </span>
            {column.stageKind === "open" && stageIndex !== undefined && openStageCount !== undefined && (
              <span
                className="rounded-full bg-white/70 px-2 py-0.5 text-[11px] font-semibold text-[var(--navy-dark)]"
                aria-label={`Open stage ${stageIndex + 1} of ${openStageCount}`}
              >
                {stageIndex + 1}/{openStageCount}
              </span>
            )}
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
                  "shrink-0 rounded-full bg-white/70 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.1em]",
                  column.stageKind === "won" ? "text-green-800" : "text-red-800"
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
              onOpenHistory={onOpenCardHistory}
              onOpenDetails={onOpenDetails}
            />
          ))}
        </SortableContext>
        {cards.length === 0 && (
          <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-[var(--stroke)] px-3 py-6 text-center text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">
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
