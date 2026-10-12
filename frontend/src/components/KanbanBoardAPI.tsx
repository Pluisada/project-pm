"use client";

import { useMemo, useState, useEffect } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { KanbanColumn } from "@/components/KanbanColumn";
import { KanbanCardPreview } from "@/components/KanbanCardPreview";
import { CardHistoryModal } from "@/components/CardHistoryModal";
import type { UserRole } from "@/lib/auth";
import {
  getBoard,
  moveCard,
  updateColumn,
  createCard,
  updateCard,
  deleteCard,
  getCardHistory,
  getErrorMessage,
  type BoardDetail,
  type CardResponse,
  type CardEditHistoryEntry,
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
        const data = await getBoard(boardId);
        setBoard(data);
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
      setBoard((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          columns: prev.columns.map((col) => ({
            ...col,
            cards: col.id === targetColumn.id
              ? [...(col.cards || []).filter((c) => c.id !== cardId), { ...card, column_id: targetColumn.id, position: newPosition }]
              : col.cards?.filter((c) => c.id !== cardId),
          })),
        };
      });

      // Make API call
      await moveCard(boardId, cardId, targetColumn.id, newPosition);
    } catch (err: unknown) {
      // Revert on error
      setBoard(oldBoard);
      setActionError(getErrorMessage(err, "Failed to move card"));
    }
  };

  const handleRenameColumn = async (columnId: number, title: string) => {
    if (!board) return;

    // Optimistic update
    const oldBoard = board;
    try {
      setBoard((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          columns: prev.columns.map((col) =>
            col.id === columnId ? { ...col, title } : col
          ),
        };
      });

      await updateColumn(boardId, columnId, title);
    } catch (err: unknown) {
      setBoard(oldBoard);
      setActionError(getErrorMessage(err, "Failed to rename column"));
    }
  };

  const handleAddCard = async (columnId: number, title: string, details: string) => {
    if (!board) return;

    try {
      const newCard = await createCard(boardId, columnId, title, details);
      setBoard((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          columns: prev.columns.map((col) =>
            col.id === columnId
              ? {
                  ...col,
                  cards: [...(col.cards || []), newCard],
                }
              : col
          ),
        };
      });
    } catch (err: unknown) {
      setActionError(getErrorMessage(err, "Failed to create card"));
    }
  };

  const handleUpdateCard = async (
    columnId: number,
    cardId: number,
    title: string,
    details: string
  ): Promise<boolean> => {
    if (!board) return false;

    try {
      const updated = await updateCard(boardId, cardId, title, details);
      setBoard((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          columns: prev.columns.map((col) =>
            col.id === columnId
              ? { ...col, cards: col.cards?.map((c) => (c.id === cardId ? updated : c)) }
              : col
          ),
        };
      });
      return true;
    } catch (err: unknown) {
      setActionError(getErrorMessage(err, "Failed to update card"));
      return false;
    }
  };

  const handleDeleteCard = async (columnId: number, cardId: number) => {
    if (!board) return;

    const oldBoard = board;
    try {
      setBoard((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          columns: prev.columns.map((col) =>
            col.id === columnId
              ? {
                  ...col,
                  cards: col.cards?.filter((c) => c.id !== cardId),
                }
              : col
          ),
        };
      });

      await deleteCard(boardId, cardId);
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
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="inline-block">
            <div className="h-12 w-12 animate-spin rounded-full border-4 border-[var(--primary-blue)] border-t-transparent"></div>
          </div>
          <p className="mt-4 text-sm text-[var(--gray-text)]">Loading board...</p>
        </div>
      </div>
    );
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

  const activeCard = activeCardId ? cardsById[activeCardId] : null;
  const historyCard = historyCardId ? cardsById[historyCardId] : null;

  return (
    <div className="relative overflow-hidden">
      <div className="pointer-events-none absolute left-0 top-0 h-[420px] w-[420px] -translate-x-1/3 -translate-y-1/3 rounded-full bg-[radial-gradient(circle,_rgba(32,157,215,0.25)_0%,_rgba(32,157,215,0.05)_55%,_transparent_70%)]" />
      <div className="pointer-events-none absolute bottom-0 right-0 h-[520px] w-[520px] translate-x-1/4 translate-y-1/4 rounded-full bg-[radial-gradient(circle,_rgba(117,57,145,0.18)_0%,_rgba(117,57,145,0.05)_55%,_transparent_75%)]" />

      <main className="relative mx-auto flex min-h-screen max-w-[1500px] flex-col gap-10 px-6 pb-16 pt-12">
        <header className="flex flex-col gap-6 rounded-[32px] border border-[var(--stroke)] bg-white/80 p-8 shadow-[var(--shadow)] backdrop-blur">
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.35em] text-[var(--gray-text)]">
                Single Board Kanban
              </p>
              <h1 className="mt-3 font-display text-4xl font-semibold text-[var(--navy-dark)]">
                {board.title}
              </h1>
              <p className="mt-3 max-w-xl text-sm leading-6 text-[var(--gray-text)]">
                {board.description || "Keep momentum visible. Rename columns, drag cards between stages, and capture quick notes without getting buried in settings."}
              </p>
            </div>
            <div className="rounded-2xl border border-[var(--stroke)] bg-[var(--surface)] px-5 py-4">
              <p className="text-xs font-semibold uppercase tracking-[0.25em] text-[var(--gray-text)]">
                Focus
              </p>
              <p className="mt-2 text-lg font-semibold text-[var(--primary-blue)]">
                {board.columns.length} columns. Zero clutter.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-4">
            {board.columns.map((column) => (
              <div
                key={column.id}
                className="flex items-center gap-2 rounded-full border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)]"
              >
                <span className="h-2 w-2 rounded-full bg-[var(--accent-yellow)]" />
                {column.title}
              </div>
            ))}
          </div>
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
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <section className="grid gap-6 lg:grid-cols-5">
            {board.columns.map((column) => (
              <KanbanColumn
                key={column.id}
                column={{
                  id: colDndId(column.id),
                  title: column.title,
                  cardIds: column.cards?.map((c) => cardDndId(c.id)) || [],
                  position: column.position,
                }}
                cards={column.cards?.map((c) => ({
                  ...c,
                  id: cardDndId(c.id),
                  details: c.details || "",
                  hasMemberEdits: c.has_member_edits,
                })) || []}
                canDeleteCards={isAdminUser}
                onRename={(_colId, title) => handleRenameColumn(column.id, title)}
                onAddCard={(_colId, title, details) => handleAddCard(column.id, title, details)}
                onDeleteCard={(_colId, cardDndIdValue) =>
                  handleDeleteCard(column.id, cardIdFromDndId(cardDndIdValue))
                }
                onUpdateCard={(_colId, cardDndIdValue, title, details) =>
                  handleUpdateCard(column.id, cardIdFromDndId(cardDndIdValue), title, details)
                }
                onOpenCardHistory={(cardDndIdValue) => handleOpenHistory(cardIdFromDndId(cardDndIdValue))}
              />
            ))}
          </section>
          <DragOverlay>
            {activeCard ? (
              <div className="w-[260px]">
                <KanbanCardPreview card={{ ...activeCard, id: cardDndId(activeCard.id), details: activeCard.details || "No details yet." }} />
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      </main>

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
