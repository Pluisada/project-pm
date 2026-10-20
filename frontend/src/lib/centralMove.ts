import type { CardResponse, ColumnResponse } from "@/lib/api";

/** dnd-kit ids carry a prefix: a card and a column may share a numeric id and dnd-kit keeps one id-keyed map. */
export const cardDndId = (id: number) => `card-${id}`;
export const columnDndId = (id: number) => `col-${id}`;

export type MoveTarget = { columnId: number; index: number };

const byPosition = (cards: CardResponse[] | undefined) => [...(cards ?? [])].sort((a, b) => a.position - b.position);

/**
 * Where a drop lands: the target column and the index among that column's other cards (the moved card
 * left out), which is exactly the `position` the backend takes. Null when the drop target is unknown.
 * Dropping on a card puts the moved card where that card is; within one column, moving down lands after it.
 */
export function planMove(columns: ColumnResponse[], cardId: number, overId: string): MoveTarget | null {
  const others = (column: ColumnResponse) => byPosition(column.cards).filter((c) => c.id !== cardId);
  if (overId.startsWith("col-")) {
    const column = columns.find((c) => columnDndId(c.id) === overId);
    return column ? { columnId: column.id, index: others(column).length } : null;
  }
  const overCardId = Number(overId.startsWith("card-") ? overId.slice(5) : NaN);
  const target = columns.find((c) => (c.cards ?? []).some((card) => card.id === overCardId));
  if (!target) return null;
  const list = others(target);
  const overIndex = list.findIndex((c) => c.id === overCardId);
  if (overIndex < 0) return null; // dropped on itself
  const source = columns.find((c) => (c.cards ?? []).some((card) => card.id === cardId));
  const sorted = byPosition(source?.cards);
  const movesDown = source?.id === target.id && sorted.findIndex((c) => c.id === cardId) < sorted.findIndex((c) => c.id === overCardId);
  return { columnId: target.id, index: overIndex + (movesDown ? 1 : 0) };
}

/** The column and index the card has now. */
export function currentIndex(columns: ColumnResponse[], cardId: number): MoveTarget | null {
  for (const column of columns) {
    const index = byPosition(column.cards).findIndex((c) => c.id === cardId);
    if (index >= 0) return { columnId: column.id, index };
  }
  return null;
}

/** The board with the card placed at `target`; the target column is renumbered 0..n-1, as the backend does. */
export function applyMove(columns: ColumnResponse[], cardId: number, target: MoveTarget): ColumnResponse[] {
  const card = columns.flatMap((c) => c.cards ?? []).find((c) => c.id === cardId);
  if (!card) return columns;
  return columns.map((column) => {
    const rest = byPosition(column.cards).filter((c) => c.id !== cardId);
    if (column.id !== target.columnId) return (column.cards ?? []).some((c) => c.id === cardId) ? { ...column, cards: rest } : column;
    rest.splice(Math.min(target.index, rest.length), 0, { ...card, column_id: column.id });
    return { ...column, cards: rest.map((c, position) => ({ ...c, position })) };
  });
}
