import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { LeadGeneration } from "@/lib/api";
import { LeadGenerationPareto } from "@/components/LeadGenerationPareto";

const data = (over: Partial<LeadGeneration> = {}): LeadGeneration => ({
  period_start: "2026-01-01", period_end: "2026-10-24", total: 6, unattributed_count: 0,
  rows: [
    { salesperson_id: 2, name: "bob", count: 3, share: 0.5, cumulative_share: 0.5 },
    { salesperson_id: 3, name: "carol", count: 2, share: 2 / 6, cumulative_share: 5 / 6 },
    { salesperson_id: 1, name: "ana", count: 1, share: 1 / 6, cumulative_share: 1 },
  ],
  ...over,
});

describe("LeadGenerationPareto", () => {
  it("shows the title, the period, and the rows in the order given with count, share and cumulative", () => {
    render(<LeadGenerationPareto data={data()} />);
    expect(screen.getByText("Lead Generation Pareto")).toBeInTheDocument();
    expect(screen.getByTestId("pareto-period")).toHaveTextContent("Jan 1, 2026 to Oct 24, 2026");
    const rows = screen.getAllByTestId("pareto-row");
    expect(rows.map((r) => within(r).getByTestId("pareto-name").textContent)).toEqual(["bob", "carol", "ana"]);
    expect(rows[0]).toHaveTextContent("3 leads · 50.0% · cumulative 50.0%");
    expect(rows[1]).toHaveTextContent("2 leads · 33.3% · cumulative 83.3%");
    expect(rows[2]).toHaveTextContent("1 lead · 16.7% · cumulative 100.0%");
    expect(screen.getByTestId("pareto-total")).toHaveTextContent("6 leads ranked; percentages are of this ranked total.");
  });

  it("mentions leads with no recorded creator without ranking them", () => {
    render(<LeadGenerationPareto data={data({ unattributed_count: 2 })} />);
    const note = screen.getByTestId("pareto-total");
    expect(note).toHaveTextContent("6 leads ranked; percentages are of this ranked total.");
    expect(note).toHaveTextContent("2 leads with no recorded creator are excluded from the ranking and from the percentages.");
    expect(note.textContent).not.toMatch(/not ranked\)/);
    expect(screen.getAllByTestId("pareto-row")).toHaveLength(3);
  });

  it("says credit goes to the original creator, not the current owner", () => {
    render(<LeadGenerationPareto data={data()} />);
    const note = screen.getByTestId("pareto-filter-note");
    expect(note).toHaveTextContent("credited to the person who originally created it, not to the current owner of the card");
    expect(screen.getByTestId("pareto-period")).toHaveTextContent("not affected by the Period filter");
  });

  it("uses the singular for a single unattributed lead", () => {
    render(<LeadGenerationPareto data={data({ unattributed_count: 1 })} />);
    expect(screen.getByTestId("pareto-total")).toHaveTextContent("1 lead with no recorded creator is excluded");
  });

  it("omits the exclusion sentence when no lead is unattributed", () => {
    render(<LeadGenerationPareto data={data()} />);
    expect(screen.getByTestId("pareto-total")).not.toHaveTextContent("excluded");
  });

  it("shows an empty state when nothing was generated", () => {
    render(<LeadGenerationPareto data={data({ rows: [], total: 0 })} />);
    expect(screen.getByTestId("pareto-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("pareto-row")).toBeNull();
    expect(screen.getByTestId("pareto-total")).toHaveTextContent("0 leads ranked.");
    expect(screen.getByTestId("pareto-total")).not.toHaveTextContent("percentages are of");
  });

  it("keeps the empty state when the only leads are unattributed", () => {
    render(<LeadGenerationPareto data={data({ rows: [], total: 0, unattributed_count: 3 })} />);
    expect(screen.getByTestId("pareto-empty")).toBeInTheDocument();
    expect(screen.getByTestId("pareto-total")).toHaveTextContent("0 leads ranked. 3 leads with no recorded creator are excluded");
  });
});
