import { describe, expect, it } from "vitest";
import { formatDays, formatPeriodDates, formatRate, formatTotals, moneyTotalsToNumbers } from "@/lib/format";

describe("KPI formatting", () => {
  it("adapts KPI money to per-currency totals without mixing currencies", () => {
    const totals = moneyTotalsToNumbers({ EUR: { total: 0.3, count: 2 }, USD: { total: 1000, count: 1 } });
    expect(totals).toEqual({ EUR: 0.3, USD: 1000 });
    expect(formatTotals(totals)).toHaveLength(2);
  });

  it("shows an unavailable rate as '-', never as 0%", () => {
    expect(formatRate(null)).toBe("-");
    expect(formatRate(0)).toBe("0%");
    expect(formatRate(0.5)).toBe("50%");
  });

  it("marks lower-bound durations and unknown ones", () => {
    expect(formatDays(null)).toBe("-");
    expect(formatDays(2.345)).toBe("2.3 d");
    expect(formatDays(1.75, true)).toBe("at least 1.8 d");
  });

  it("shows a KPI period as UTC dates with the end inclusive", () => {
    const text = formatPeriodDates({ start: "2026-09-13T00:00:00", end: "2026-10-14T00:00:00" });
    expect(text).toContain("13");
    expect(text).toContain("Oct");
    expect(text).not.toContain("14");
  });
});
