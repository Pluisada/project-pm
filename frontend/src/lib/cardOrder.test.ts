import { describe, expect, it } from "vitest";
import { sortByDealValue } from "@/lib/cardOrder";

const card = (id: number, position: number, deal_value: number | null, deal_value_next_year = 0) => ({ id, position, deal_value, deal_value_next_year });

describe("sortByDealValue", () => {
  it("orders by value, highest first, and numerically (not as text)", () => {
    const sorted = sortByDealValue([card(1, 0, 900), card(2, 1, 1200), card(3, 2, 85000), card(4, 3, 100)]);
    expect(sorted.map((c) => c.id)).toEqual([3, 2, 1, 4]);
  });

  it("ranks a card by its displayed total (current year plus next year)", () => {
    expect(sortByDealValue([card(1, 0, 500, 0), card(2, 1, 100, 900)]).map((c) => c.id)).toEqual([2, 1]);
  });

  it("puts cards without a value last, and keeps a real zero above them", () => {
    const sorted = sortByDealValue([card(1, 0, null), card(2, 1, 0), card(3, 2, 10), card(4, 3, null, 0)]);
    expect(sorted.map((c) => c.id)).toEqual([3, 2, 1, 4]);
  });

  it("breaks ties by stored position, then id, whatever the input order", () => {
    const cards = [card(7, 2, 50), card(5, 2, 50), card(6, 1, 50), card(8, 0, 10)];
    expect(sortByDealValue(cards).map((c) => c.id)).toEqual([6, 5, 7, 8]);
    expect(sortByDealValue([...cards].reverse()).map((c) => c.id)).toEqual([6, 5, 7, 8]);
  });

  it("does not change the input", () => {
    const cards = [card(1, 0, 1), card(2, 1, 2)];
    sortByDealValue(cards);
    expect(cards.map((c) => c.id)).toEqual([1, 2]);
  });
});
