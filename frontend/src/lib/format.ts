/**
 * Display formatting for sales lead values.
 */

export function formatMoney(value: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

/** Short form for headline numbers: EUR 1.2M, $420K. */
export function formatMoneyCompact(value: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

/**
 * Per-currency totals as one line each, by currency code; "-" when empty.
 * Currencies are never summed or ranked against each other - there's no
 * conversion, so EUR 1M and INR 1M aren't comparable.
 */
export function formatTotals(totals: Record<string, number>, compact = false): string[] {
  const format = compact ? formatMoneyCompact : formatMoney;
  const entries = Object.entries(totals).sort(([a], [b]) => a.localeCompare(b));
  return entries.length ? entries.map(([currency, value]) => format(value, currency)) : ["-"];
}

/** A YYYY-MM-DD date, formatted without shifting it through a timezone. */
export function formatDate(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Today as YYYY-MM-DD in local time, comparable with lead close dates. */
export function todayIso(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
