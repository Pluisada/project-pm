import { dealValueTotal } from "@/lib/format";

type Orderable = { id: number; position: number; deal_value: number | null; deal_value_next_year?: number | null };

/**
 * The order cards are shown in within a column: highest Deal Value first (the card's displayed total, current plus
 * next year), cards with no value last, then the stored position and the id so equal values never shuffle.
 * Display order only: the stored position is untouched, so a reload, an edit or a move always lands on the same order.
 */
export function compareByDealValue(a: Orderable, b: Orderable): number {
  const av = dealValueTotal(a.deal_value, a.deal_value_next_year);
  const bv = dealValueTotal(b.deal_value, b.deal_value_next_year);
  if (av !== bv) {
    if (av === null) return 1;
    if (bv === null) return -1;
    return bv - av;
  }
  return a.position - b.position || a.id - b.id;
}

export function sortByDealValue<T extends Orderable>(cards: readonly T[] | undefined): T[] {
  return [...(cards ?? [])].sort(compareByDealValue);
}
