import { describe, expect, it } from "vitest";
import type { CardResponse, ColumnResponse } from "@/lib/api";
import { applyMove, currentIndex, planMove } from "@/lib/centralMove";

const card = (id: number, column_id: number, position: number) => ({ id, column_id, position, title: `c${id}` }) as CardResponse;
const columns = [
  { id: 1, position: 0, cards: [card(1, 1, 0), card(2, 1, 5), card(3, 1, 9)] },
  { id: 2, position: 1, cards: [card(4, 2, 0)] },
  { id: 3, position: 2 },
] as ColumnResponse[];
const order = (cols: ColumnResponse[], id: number) => (cols.find((c) => c.id === id)?.cards ?? []).map((c) => c.id);

describe("planMove", () => {
  it("reorders downward (lands after the card dropped on) and upward (lands before it)", () => {
    expect(planMove(columns, 1, "card-3")).toEqual({ columnId: 1, index: 2 });
    expect(planMove(columns, 3, "card-1")).toEqual({ columnId: 1, index: 0 });
    expect(planMove(columns, 1, "card-2")).toEqual({ columnId: 1, index: 1 });
  });
  it("drops on a card of another column before it, and on a column at its end (also when empty)", () => {
    expect(planMove(columns, 1, "card-4")).toEqual({ columnId: 2, index: 0 });
    expect(planMove(columns, 1, "col-2")).toEqual({ columnId: 2, index: 1 });
    expect(planMove(columns, 1, "col-3")).toEqual({ columnId: 3, index: 0 });
  });
  it("returns null for unknown targets and for the card itself", () => {
    expect(planMove(columns, 1, "col-99")).toBeNull();
    expect(planMove(columns, 1, "card-99")).toBeNull();
    expect(planMove(columns, 1, "card-1")).toBeNull();
    expect(planMove(columns, 1, "junk")).toBeNull();
  });
});

describe("applyMove / currentIndex", () => {
  it("places the card and renumbers the target column densely, leaving the input untouched", () => {
    const moved = applyMove(columns, 1, { columnId: 2, index: 0 });
    expect(order(moved, 1)).toEqual([2, 3]);
    expect(order(moved, 2)).toEqual([1, 4]);
    expect(moved.find((c) => c.id === 2)!.cards!.map((c) => [c.column_id, c.position])).toEqual([[2, 0], [2, 1]]);
    expect(order(columns, 1)).toEqual([1, 2, 3]);
  });
  it("reorders in place and moving back is the inverse", () => {
    const moved = applyMove(columns, 3, { columnId: 1, index: 0 });
    expect(order(moved, 1)).toEqual([3, 1, 2]);
    expect(order(applyMove(moved, 3, currentIndex(columns, 3)!), 1)).toEqual([1, 2, 3]);
  });
  it("fills an empty column and clamps an index past the end", () => {
    expect(order(applyMove(columns, 1, { columnId: 3, index: 7 }), 3)).toEqual([1]);
    expect(applyMove(columns, 99, { columnId: 2, index: 0 })).toBe(columns);
  });
});
