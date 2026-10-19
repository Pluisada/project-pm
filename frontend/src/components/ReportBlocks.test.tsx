import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnnualLegend, AnnualTotals, StageBars } from "@/components/ReportBlocks";

describe("AnnualTotals", () => {
  it("shows one USD line: the two-year total with both years beneath", () => {
    render(<AnnualTotals measure={{ current: { USD: 100000 }, next: { USD: 50000 }, sum: { USD: 150000 } }} years={[2026]} />);
    const rows = screen.getAllByTestId("annual-totals");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("$150,000");
    expect(rows[0]).toHaveTextContent("2026 $100,000 · 2027 $50,000");
  });

  it("shows an unavailable current-year amount as '-', not 0", () => {
    render(<AnnualTotals measure={{ current: {}, next: { USD: 10 }, sum: { USD: 10 } }} years={[2026]} />);
    expect(screen.getByTestId("annual-totals")).toHaveTextContent("2026 - · 2027 $10");
  });

  it("shows a dash when nothing is set", () => {
    render(<AnnualTotals measure={{ current: {}, next: {}, sum: {} }} years={[2026]} />);
    expect(screen.queryByTestId("annual-totals")).toBeNull();
    expect(document.body).toHaveTextContent("-");
  });
});

describe("AnnualLegend", () => {
  it("names the actual years and says every amount is in USD", () => {
    render(<AnnualLegend years={[2031]} />);
    const text = screen.getByTestId("annual-legend").textContent ?? "";
    expect(text).toContain("2031+2032");
    expect(text).toContain("All amounts are in USD");
    expect(text).not.toContain("different years");
  });

  it("flags amounts that span several years", () => {
    render(<AnnualLegend years={[2026, 2027]} />);
    expect(screen.getByTestId("annual-legend")).toHaveTextContent("different years (2026, 2027)");
  });
});

describe("StageBars", () => {
  const stage = { column_id: 1, title: "RFI", count: 2, totals: { USD: 100 } };

  it("keeps the old one-line totals when no annual measures are given", () => {
    render(<StageBars stages={[stage]} />);
    expect(screen.queryByTestId("annual-totals")).toBeNull();
    expect(screen.getByText("RFI")).toBeInTheDocument();
  });

  it("shows the annual measures and leaves the lead count alone", () => {
    render(<StageBars stages={[{ ...stage, next_totals: { USD: 40 }, sum_totals: { USD: 140 } }]} years={[2026]} />);
    expect(screen.getByTestId("annual-totals")).toHaveTextContent("2026");
    expect(screen.getByTestId("annual-totals")).toHaveTextContent("2027");
    expect(screen.getByText("2", { selector: "span" })).toBeInTheDocument();
  });
});
