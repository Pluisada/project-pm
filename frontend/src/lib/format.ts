/**
 * Display formatting for sales lead values.
 */
import type { MoneyTotals } from "@/lib/api";

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

/** KPI money ({currency: {total, count}}) in the shape formatTotals takes. */
export function moneyTotalsToNumbers(totals: MoneyTotals): Record<string, number> {
  return Object.fromEntries(Object.entries(totals).map(([currency, { total }]) => [currency, total]));
}

/** A KPI rate as a whole percentage; "-" when unavailable (never shown as 0%). */
export function formatRate(value: number | null): string {
  return value === null ? "-" : `${Math.round(value * 100)}%`;
}

/** Days with one decimal; "-" when unknown. lowerBound prefixes "at least". */
export function formatDays(days: number | null, lowerBound = false): string {
  if (days === null) return "-";
  return `${lowerBound ? "at least " : ""}${days.toFixed(1)} d`;
}

/** An ISO timestamp (UTC from the API) as a short local date and time. */
export function formatDateTime(iso: string): string {
  return new Date(iso.endsWith("Z") ? iso : `${iso}Z`).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * A KPI period ({start, end} as naive-UTC timestamps, end exclusive) as UTC
 * calendar dates with the end inclusive: "Sep 13, 2026 - Oct 13, 2026".
 */
export function formatPeriodDates(period: { start: string; end: string }): string {
  const asUtc = (iso: string) => new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  const day = (date: Date) =>
    date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const lastDay = new Date(asUtc(period.end).getTime() - 24 * 60 * 60 * 1000);
  return `${day(asUtc(period.start))} - ${day(lastDay)}`;
}
