"use client";

import { useMemo, useState, useEffect } from "react";
import { createPortal } from "react-dom";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  pointerWithin,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { KanbanColumn } from "@/components/KanbanColumn";
import { KanbanCardPreview } from "@/components/KanbanCardPreview";
import { CardHistoryModal } from "@/components/CardHistoryModal";
import { CardDetailsModal } from "@/components/CardDetailsModal";
import { LeadEditModal } from "@/components/LeadEditModal";
import { LoadingScreen } from "@/components/LoadingScreen";
import { dealValueTotal, isValidIsoDate, todayIso } from "@/lib/format";
import { getAuthState, type UserRole } from "@/lib/auth";
import {
  getBoard,
  moveCard,
  updateColumn,
  createCard,
  updateCard,
  deleteCard,
  getCardHistory,
  getErrorMessage,
  getLeadOptions,
  getUndoStatus,
  undoLastChange,
  type BoardDetail,
  type CardResponse,
  type ColumnResponse,
  type CardEditHistoryEntry,
  type CardUpdatePayload,
  type LeadOptions,
  type UndoStatus,
} from "@/lib/api";

// Columns and cards are separate DB tables, each with its own autoincrement
// id sequence, so a column and a card can share the same numeric id (e.g.
// column 1 and card 1). dnd-kit registers every draggable/droppable in a
// single id-keyed map, so passing raw numeric ids causes silent collisions
// — one entity's drop target clobbers the other's. Prefixing keeps the two
// id spaces distinct.
const colDndId = (id: number) => `col-${id}`;
const cardDndId = (id: number) => `card-${id}`;
const cardIdFromDndId = (dndId: string) => Number(dndId.slice(5));

// Drop on whatever is under the pointer; fall back to the nearest center only
// when the pointer is over no droppable (e.g. between columns). Plain
// closestCenter misfires once a long column stretches every column in the
// row: a drop into the empty lower part of a short column is measured against
// that column's far-away center, so the card snaps back to the nearest card in
// another column instead.
const pointerFirst: CollisionDetection = (args) => {
  const underPointer = pointerWithin(args);
  return underPointer.length > 0 ? underPointer : closestCenter(args);
};

export const KanbanBoardAPI = ({ boardId, role }: { boardId: number; role: UserRole }) => {
  const isAdminUser = role === "admin";

  const [board, setBoard] = useState<BoardDetail | null>(null);
  const [activeCardId, setActiveCardId] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Transient error from a single action (move/delete/edit) - shown as a
  // dismissible banner, never replaces the board like loadError does.
  const [actionError, setActionError] = useState<string | null>(null);

  const [historyCardId, setHistoryCardId] = useState<number | null>(null);
  const [historyEntries, setHistoryEntries] = useState<CardEditHistoryEntry[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const [leadOptions, setLeadOptions] = useState<LeadOptions | null>(null);
  const [leadCardId, setLeadCardId] = useState<number | null>(null);
  const [detailsCardId, setDetailsCardId] = useState<number | null>(null);

  const [undoStatus, setUndoStatus] = useState<UndoStatus | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [undoNotice, setUndoNotice] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    })
  );

  // Load board on mount
  useEffect(() => {
    const loadBoard = async () => {
      setIsLoading(true);
      setLoadError(null);
      try {
        const [data, options, undo] = await Promise.all([
          getBoard(boardId),
          getLeadOptions(),
          getUndoStatus(boardId),
        ]);
        setBoard(data);
        setLeadOptions(options);
        setUndoStatus(undo);
      } catch (err: unknown) {
        setLoadError(getErrorMessage(err, "Failed to load board"));
      } finally {
        setIsLoading(false);
      }
    };

    loadBoard();
  }, [boardId]);

  const cardsById = useMemo(() => {
    if (!board) return {};
    const acc: Record<number, CardResponse> = {};
    board.columns.forEach((column) => {
      column.cards?.forEach((card) => {
        acc[card.id] = card;
      });
    });
    return acc;
  }, [board]);

  // Every successful change replaces the user's undo entry on the server, so
  // re-read it after each one. Failures here only leave the button stale.
  const refreshUndo = () => {
    setUndoNotice(null);
    getUndoStatus(boardId).then(setUndoStatus).catch(() => {});
  };

  const handleUndo = async () => {
    setUndoing(true);
    setActionError(null);
    try {
      const result = await undoLastChange(boardId);
      setUndoNotice(result.undone ? `Undone: ${result.undone}` : "Undone");
    } catch (err: unknown) {
      setUndoNotice(null);
      setActionError(getErrorMessage(err, "Failed to undo"));
    } finally {
      // Undo can touch any card (and an AI request several), so reload the
      // whole board rather than patching it. The entry is used up either way.
      try {
        const [data, status] = await Promise.all([getBoard(boardId), getUndoStatus(boardId)]);
        setBoard(data);
        setUndoStatus(status);
      } catch (err: unknown) {
        setActionError(getErrorMessage(err, "Failed to reload board"));
      }
      setUndoing(false);
    }
  };

  // Apply a per-column change to the board in state (a no-op before it loads).
  const updateColumns = (update: (column: ColumnResponse) => ColumnResponse) => {
    setBoard((prev) => (prev ? { ...prev, columns: prev.columns.map(update) } : prev));
  };

  const handleDragStart = (event: DragStartEvent) => {
    const raw = event.active.id as string;
    setActiveCardId(raw.startsWith("card-") ? cardIdFromDndId(raw) : null);
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveCardId(null);

    if (!over || active.id === over.id || !board) {
      return;
    }

    const activeRaw = active.id as string;
    if (!activeRaw.startsWith("card-")) return;
    const cardId = cardIdFromDndId(activeRaw);
    const card = cardsById[cardId];
    if (!card) return;

    // over.id is either a column's own droppable (dropped on empty column
    // area) or a card's droppable (dropped on/near another card) — resolve
    // both prefixed forms to find which column we're targeting.
    const overRaw = over.id as string;
    let targetColumn;
    if (overRaw.startsWith("col-")) {
      const overColumnId = Number(overRaw.slice(4));
      targetColumn = board.columns.find((col) => col.id === overColumnId);
    } else if (overRaw.startsWith("card-")) {
      const overCardId = cardIdFromDndId(overRaw);
      targetColumn = board.columns.find((col) =>
        col.cards?.some((c) => c.id === overCardId)
      );
    }

    if (!targetColumn) return;

    const sourceColumn = board.columns.find((col) => col.id === card.column_id);

    // Members may only advance cards (move to a column further right, or
    // reorder within the same column) — never move them back to an earlier
    // column. The backend enforces this independently; this just avoids the
    // jarring optimistic-update-then-revert for a move we already know will
    // be rejected.
    if (!isAdminUser && sourceColumn && targetColumn.position < sourceColumn.position) {
      setActionError("Members can only move cards forward, not back to an earlier column.");
      return;
    }

    // Calculate new position
    const newPosition = targetColumn.cards?.length || 0;

    // Optimistic update
    const oldBoard = board;
    try {
      // Update UI immediately
      updateColumns((col) => ({
        ...col,
        cards: col.id === targetColumn.id
          ? [...(col.cards || []).filter((c) => c.id !== cardId), { ...card, column_id: targetColumn.id, position: newPosition }]
          : col.cards?.filter((c) => c.id !== cardId),
      }));

      // Make API call
      await moveCard(boardId, cardId, targetColumn.id, newPosition);
      refreshUndo();
    } catch (err: unknown) {
      // Revert on error
      setBoard(oldBoard);
      setActionError(getErrorMessage(err, "Failed to move card"));
    }
  };

  // Called once per committed edit (blur/Enter), not per keystroke. Admin
  // only - members get a read-only title, and the backend refuses them anyway.
  // Renaming never changes the stage's stage_kind, so reports are unaffected.
  const handleRenameColumn = async (columnId: number, title: string): Promise<boolean> => {
    try {
      const updated = await updateColumn(boardId, columnId, title);
      updateColumns((col) => (col.id === columnId ? { ...col, title: updated.title } : col));
      return true;
    } catch (err: unknown) {
      setActionError(getErrorMessage(err, "Failed to rename column"));
      return false;
    }
  };

  const handleAddCard = async (columnId: number, title: string, details: string) => {
    if (!board) return;

    try {
      const newCard = await createCard(boardId, columnId, title, details);
      refreshUndo();
      updateColumns((col) => (col.id === columnId ? { ...col, cards: [...(col.cards || []), newCard] } : col));
    } catch (err: unknown) {
      setActionError(getErrorMessage(err, "Failed to create card"));
    }
  };

  const replaceCard = (updated: CardResponse) => {
    updateColumns((col) =>
      col.id === updated.column_id
        ? { ...col, cards: col.cards?.map((c) => (c.id === updated.id ? updated : c)) }
        : col
    );
  };

  // Errors propagate so the modal can show them and stay open.
  const handleSaveLead = async (cardId: number, updates: CardUpdatePayload) => {
    replaceCard(await updateCard(boardId, cardId, updates));
    refreshUndo();
  };

  const handleDeleteCard = async (columnId: number, cardId: number) => {
    if (!board) return;

    const oldBoard = board;
    try {
      updateColumns((col) =>
        col.id === columnId ? { ...col, cards: col.cards?.filter((c) => c.id !== cardId) } : col
      );

      await deleteCard(boardId, cardId);
      refreshUndo();
    } catch (err: unknown) {
      setBoard(oldBoard);
      setActionError(getErrorMessage(err, "Failed to delete card"));
    }
  };

  const handleOpenHistory = async (cardId: number) => {
    setHistoryCardId(cardId);
    setHistoryEntries(null);
    setHistoryLoading(true);
    try {
      setHistoryEntries(await getCardHistory(boardId, cardId));
    } catch (err: unknown) {
      setActionError(getErrorMessage(err, "Failed to load card history"));
      setHistoryCardId(null);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleCloseHistory = () => {
    setHistoryCardId(null);
    setHistoryEntries(null);
  };

  if (isLoading) {
    return <LoadingScreen message="Loading board..." />;
  }

  if (loadError || !board) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <p className="text-lg font-semibold text-red-600">{loadError || "Failed to load board"}</p>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 rounded bg-[var(--secondary-purple)] px-4 py-2 text-white"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // Mirrors card_rules.can_edit_card for showing the Edit action only; the
  // backend stays the authority. Members edit only cards assigned to them.
  const username = getAuthState().username;
  const canEditCard = (card: CardResponse) =>
    isAdminUser || (card.salesperson_name != null && card.salesperson_name === username);

  // Return focus to the card's summary after the edit form closes (the
  // details view that opened it is gone by then).
  const closeLeadEditor = () => {
    const cardId = leadCardId;
    setLeadCardId(null);
    if (cardId !== null) {
      requestAnimationFrame(() =>
        document
          .querySelector<HTMLElement>(`[data-testid="card-${cardDndId(cardId)}"] [data-open-details]`)
          ?.focus()
      );
    }
  };

  const activeCard = activeCardId ? cardsById[activeCardId] : null;
  // Undo lives in the top navigation; AppNav reserves the element, and it is
  // already mounted by the time the board has loaded.
  const undoSlot = document.getElementById("nav-undo-slot");
  const today = todayIso();
  const historyCard = historyCardId ? cardsById[historyCardId] : null;

  return (
    // overflow-clip, not overflow-hidden: overflow-hidden would make this div
    // a scroll container.
    <div className="relative overflow-clip">
      {undoSlot &&
        createPortal(
          <>
            {undoNotice && (
              <span
                className="sr-only text-xs font-medium text-green-700 lg:not-sr-only lg:max-w-[14rem] lg:truncate"
                role="status"
                title={undoNotice}
              >
                {undoNotice}
              </span>
            )}
            <button
              type="button"
              onClick={handleUndo}
              disabled={!undoStatus?.available || undoing}
              title={undoStatus?.description ?? "Nothing to undo"}
              className="rounded-md border border-[var(--stroke)] px-2 py-1.5 text-sm font-semibold text-[var(--navy-dark)] transition hover:border-[var(--primary-blue)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary-blue)] disabled:cursor-not-allowed disabled:opacity-40 sm:px-3"
            >
              {undoing ? "Undoing..." : "Undo"}
            </button>
          </>,
          undoSlot
        )}

      <main className="relative mx-auto flex max-w-[1500px] flex-col gap-4 px-6 pb-20 pt-4 lg:pb-16">
        <header className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h1 className="font-display text-2xl font-semibold text-[var(--navy-dark)]">
            {board.title}
          </h1>
          <p className="text-sm font-semibold text-[var(--primary-blue)]">
            {board.columns.length} stages. {Object.keys(cardsById).length} leads.
          </p>
        </header>

        {actionError && (
          <div className="flex items-center justify-between gap-4 rounded-2xl border border-red-200 bg-red-50 px-5 py-3 text-sm font-medium text-red-700">
            <span>{actionError}</span>
            <button
              type="button"
              onClick={() => setActionError(null)}
              className="shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold text-red-700 transition hover:bg-red-100"
              aria-label="Dismiss"
            >
              X
            </button>
          </div>
        )}

        <DndContext
          sensors={sensors}
          collisionDetection={pointerFirst}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          {/* Six stages don't fit side by side at a readable width next to
              the AI sidebar, so the row scrolls horizontally instead. */}
          <section className="-m-1 grid auto-cols-[minmax(240px,1fr)] grid-flow-col gap-6 overflow-x-auto p-1 pb-3">
            {board.columns.map((column, index) => (
              <KanbanColumn
                key={column.id}
                stageIndex={
                  board.columns.slice(0, index).filter((c) => c.stage_kind === "open").length
                }
                openStageCount={board.columns.filter((c) => c.stage_kind === "open").length}
                column={{
                  id: colDndId(column.id),
                  title: column.title,
                  cardIds: column.cards?.map((c) => cardDndId(c.id)) || [],
                  position: column.position,
                  stageKind: column.stage_kind,
                }}
                canRename={isAdminUser}
                cards={column.cards?.map((c) => ({
                  ...c,
                  id: cardDndId(c.id),
                  details: c.details || "",
                  hasMemberEdits: c.has_member_edits,
                  lead: {
                    company: c.company,
                    contact: c.contact,
                    dealValue: dealValueTotal(c.deal_value, c.deal_value_next_year),
                    expectedCloseDate: c.expected_close_date,
                    productLine: c.product_line,
                    salespersonName: c.salesperson_name,
                    overdue:
                      isValidIsoDate(c.expected_close_date) &&
                      c.expected_close_date < today &&
                      column.stage_kind === "open",
                    nextStep: c.bid_details?.next_step ?? null,
                    nextStepDue: isValidIsoDate(c.bid_details?.next_step_due)
                      ? c.bid_details!.next_step_due
                      : null,
                    nextStepOverdue:
                      isValidIsoDate(c.bid_details?.next_step_due) &&
                      c.bid_details!.next_step_due! < today &&
                      column.stage_kind === "open",
                    sourceCode: c.source_ref?.source_code ?? null,
                  },
                })) || []}
                canDeleteCards={isAdminUser}
                onRename={(_colId, title) => handleRenameColumn(column.id, title)}
                onAddCard={(_colId, title, details) => handleAddCard(column.id, title, details)}
                onDeleteCard={(_colId, cardDndIdValue) =>
                  handleDeleteCard(column.id, cardIdFromDndId(cardDndIdValue))
                }
                onOpenCardHistory={(cardDndIdValue) => handleOpenHistory(cardIdFromDndId(cardDndIdValue))}
                onOpenDetails={(cardDndIdValue) => setDetailsCardId(cardIdFromDndId(cardDndIdValue))}
              />
            ))}
          </section>
          <DragOverlay>
            {activeCard ? (
              // dnd-kit keeps rendering this during the drop animation and
              // ignores new drags until it's gone; e2e waits on the testid.
              <div className="w-[260px]" data-testid="drag-overlay">
                <KanbanCardPreview card={{ ...activeCard, id: cardDndId(activeCard.id), details: activeCard.details || "No details yet." }} />
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      </main>

      {detailsCardId !== null && cardsById[detailsCardId] && (() => {
        const card = cardsById[detailsCardId];
        const column = board.columns.find((col) => col.id === card.column_id);
        return (
          <CardDetailsModal
            card={card}
            stageTitle={column?.title ?? "-"}
            stageKind={column?.stage_kind ?? "open"}
            canEdit={!!leadOptions && canEditCard(card)}
            onEdit={() => {
              setDetailsCardId(null);
              setLeadCardId(card.id);
            }}
            onClose={() => setDetailsCardId(null)}
          />
        );
      })()}

      {leadCardId !== null && cardsById[leadCardId] && leadOptions && (
        <LeadEditModal
          card={cardsById[leadCardId]}
          options={leadOptions}
          onSave={(updates) => handleSaveLead(leadCardId, updates)}
          onClose={closeLeadEditor}
        />
      )}

      {historyCardId !== null && (
        <CardHistoryModal
          cardTitle={historyCard?.title ?? "Card"}
          entries={historyEntries}
          isLoading={historyLoading}
          onClose={handleCloseHistory}
        />
      )}
    </div>
  );
};
