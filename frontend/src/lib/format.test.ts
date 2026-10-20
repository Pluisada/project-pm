import { describe, expect, it } from "vitest";
import { annualLabels, annualLines, dealValueTotal, formatDays, formatPeriodDates, formatRate, formatTotals, isValidIsoDate, moneyTotalsToNumbers } from "@/lib/format";

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

describe("Date validation", () => {
  it("accepts only real YYYY-MM-DD calendar dates", () => {
    expect(isValidIsoDate("2026-10-16")).toBe(true);
    expect(isValidIsoDate("2026-02-31")).toBe(false);
    expect(isValidIsoDate("16/10/2026")).toBe(false);
    expect(isValidIsoDate(null)).toBe(false);
  });
});

describe("dealValueTotal", () => {
  it("adds the current-year and next-year amounts", () => {
    expect(dealValueTotal(100000, 50000)).toBe(150000);
    expect(dealValueTotal(100000, 0)).toBe(100000);
    expect(dealValueTotal(null, 50000)).toBe(50000);
    expect(dealValueTotal(100000, undefined)).toBe(100000);
  });

  it("is unset (null) only when nothing is entered; a stored 0 still shows", () => {
    expect(dealValueTotal(null, 0)).toBeNull();
    expect(dealValueTotal(null, undefined)).toBeNull();
    expect(dealValueTotal(0, 0)).toBe(0);
  });
});

describe("annual labels and lines", () => {
  it("derives the labels from the stored year, never a fixed one", () => {
    expect(annualLabels([2031])).toMatchObject({ current: "2031", next: "2032", total: "2031+2032", single: true });
    expect(annualLabels([])).toMatchObject({ current: "Current year", total: "Two-year total", single: false });
    expect(annualLabels([2026, 2027]).single).toBe(false);
  });

  it("gives one line per currency with total and both years, '-' where never entered", () => {
    const lines = annualLines(
      { current: { EUR: 100 }, next: { EUR: 50, USD: 7 }, sum: { EUR: 150, USD: 7 } },
      [2026]
    );
    expect(lines.map((l) => l.currency)).toEqual(["EUR", "USD"]);
    expect(lines[0].total).toContain("150");
    expect(lines[0].detail).toMatch(/^2026 .*100.* · 2027 .*50/);
    expect(lines[1].detail).toMatch(/^2026 - · 2027 .*7/);
  });

  it("has no lines for an empty measure", () => {
    expect(annualLines({}, [2026])).toEqual([]);
  });
});
