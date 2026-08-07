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
import {
  getBoard,
  moveCard,
  updateColumn,
  createCard,
  deleteCard,
  type BoardDetail,
  type CardResponse,
} from "@/lib/api";

// Columns and cards are separate DB tables, each with its own autoincrement
// id sequence, so a column and a card can share the same numeric id (e.g.
// column 1 and card 1). dnd-kit registers every draggable/droppable in a
// single id-keyed map, so passing raw numeric ids causes silent collisions
// — one entity's drop target clobbers the other's. Prefixing keeps the two
// id spaces distinct.
const colDndId = (id: number) => `col-${id}`;
const cardDndId = (id: number) => `card-${id}`;

export const KanbanBoardAPI = ({ boardId }: { boardId: number }) => {
  const [board, setBoard] = useState<BoardDetail | null>(null);
  const [activeCardId, setActiveCardId] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    })
  );

  // Load board on mount
  useEffect(() => {
    const loadBoard = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await getBoard(boardId);
        setBoard(data);
      } catch (err: any) {
        setError(err.detail || "Failed to load board");
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
    setActiveCardId(raw.startsWith("card-") ? Number(raw.slice(5)) : null);
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveCardId(null);

    if (!over || active.id === over.id || !board) {
      return;
    }

    const activeRaw = active.id as string;
    if (!activeRaw.startsWith("card-")) return;
    const cardId = Number(activeRaw.slice(5));
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
      const overCardId = Number(overRaw.slice(5));
      targetColumn = board.columns.find((col) =>
        col.cards?.some((c) => c.id === overCardId)
      );
    }

    if (!targetColumn) return;

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
    } catch (err: any) {
      // Revert on error
      setBoard(oldBoard);
      setError(err.detail || "Failed to move card");
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
    } catch (err: any) {
      setBoard(oldBoard);
      setError(err.detail || "Failed to rename column");
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
    } catch (err: any) {
      setError(err.detail || "Failed to create card");
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
    } catch (err: any) {
      setBoard(oldBoard);
      setError(err.detail || "Failed to delete card");
    }
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

  if (error || !board) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <p className="text-lg font-semibold text-red-600">{error || "Failed to load board"}</p>
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
                }}
                cards={column.cards?.map((c) => ({ ...c, id: cardDndId(c.id), details: c.details || "No details yet." })) || []}
                onRename={(_colId, title) => handleRenameColumn(column.id, title)}
                onAddCard={(_colId, title, details) => handleAddCard(column.id, title, details)}
                onDeleteCard={(_colId, cardDndIdValue) =>
                  handleDeleteCard(column.id, Number(cardDndIdValue.slice(5)))
                }
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
    </div>
  );
};
