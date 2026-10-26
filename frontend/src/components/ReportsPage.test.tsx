import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { BoardReport } from "@/lib/api";

const getBoardReport = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getBoardReport: (...args: unknown[]) => getBoardReport(...args),
  getLeadOptions: async () => ({
    currencies: ["USD"], lead_sources: [], product_lines: ["Chargers"],
    salespeople: [{ id: 1, username: "alice" }],
  }),
}));

import { ReportsPage } from "@/components/ReportsPage";

const group = (key: string) => ({
  key, open_count: 2, open_totals: { USD: 300 }, open_next_totals: { USD: 40 }, open_sum_totals: { USD: 340 },
  won_count: 1, won_totals: { USD: 300 }, won_next_totals: { USD: 10 }, won_sum_totals: { USD: 310 },
});

const report = (years = [2026]): BoardReport => ({
  kpis: {
    open_count: 3, open_totals: { USD: 350 }, open_next_totals: { USD: 65 },
    open_sum_totals: { USD: 415 }, won_count: 1, won_totals: { USD: 300 },
    won_next_totals: { USD: 10 }, won_sum_totals: { USD: 310 }, lost_count: 1, win_rate: 0.5,
  },
  stages: [{ column_id: 1, title: "RFI", stage_kind: "open", count: 2, totals: { USD: 300 }, next_totals: { USD: 40 }, sum_totals: { USD: 340 } }],
  deal_years: years,
  by_salesperson: [group("alice")], by_product_line: [group("Chargers")], by_source: [],
  by_confidence_level: [group("LOW"), group("MEDIUM"), group("HIGH"), group("Not assigned")],
  closing_soon: [{
    card_id: 1, title: "Lead A", company: null, stage: "RFI", salesperson_name: "alice",
    deal_value: 200, deal_value_next_year: 60, deal_value_total: 260, deal_year: 2026, currency: "USD",
    expected_close_date: "2026-10-20", overdue: false,
  }],
});

beforeEach(() => getBoardReport.mockReset());

describe("ReportsPage annual Deal Value", () => {
  it("shows total, current-year and next-year amounts in USD, with real years", async () => {
    getBoardReport.mockResolvedValue(report());
    render(<ReportsPage boardId={1} />);
    const tiles = await screen.findByTestId("report-kpis");
    // Open pipeline tile: two-year total headline, both years in the note.
    expect(tiles).toHaveTextContent("$415");
    expect(tiles).toHaveTextContent("2026 $350 · 2027 $65");
    expect(tiles.textContent).not.toMatch(/[€₹¥]|EUR/);   // one currency: no per-currency lines
    // Counts are unchanged by the money split.
    expect(within(tiles).getByText("Open leads").nextElementSibling).toHaveTextContent("3");
    expect(screen.getByTestId("annual-legend")).toHaveTextContent("2026+2027");
    // The date-driven "closing soon" list is gone with the Closing Date field.
    expect(screen.queryByText("Lead A")).not.toBeInTheDocument();
    expect(screen.queryByText(/Closing in the next 30 days/)).not.toBeInTheDocument();
  });

  it("shows the salesperson breakdown with the same three measures", async () => {
    getBoardReport.mockResolvedValue(report());
    render(<ReportsPage boardId={1} />);
    await screen.findByTestId("report-kpis");
    const section = screen.getByRole("heading", { name: "By salesperson" }).closest("section")!;
    const row = within(section).getByText("alice", { selector: "td" }).closest("tr")!;
    expect(row).toHaveTextContent("$340");
    expect(row).toHaveTextContent("2026 $300 · 2027 $40");
    expect(row).toHaveTextContent("$310");
  });

  it("does not hard-code years and says when amounts span several", async () => {
    getBoardReport.mockResolvedValue(report([2030, 2031]));
    render(<ReportsPage boardId={1} />);
    const legend = await screen.findByTestId("annual-legend");
    expect(legend).toHaveTextContent("Two-year total");
    expect(legend).toHaveTextContent("different years (2030, 2031)");
    expect(screen.queryByText(/2026\+2027/)).toBeNull();
  });

  it("keeps the filters wired to the report request", async () => {
    getBoardReport.mockResolvedValue(report());
    render(<ReportsPage boardId={1} />);
    await screen.findByTestId("report-kpis");
    await userEvent.selectOptions(screen.getByLabelText("Filter by salesperson"), "1");
    await waitFor(() =>
      expect(getBoardReport).toHaveBeenLastCalledWith(1, { salespersonId: 1, productLine: undefined }, undefined)
    );
  });
});

describe("ReportsPage confidence level", () => {
  it("shows the four groups with counts and Deal Value totals", async () => {
    const data = report();
    data.by_confidence_level = [
      { ...group("LOW"), open_count: 2, open_totals: { USD: 30 } },
      group("MEDIUM"),
      group("HIGH"),
      { ...group("Not assigned"), open_count: 0, won_count: 0, open_totals: {}, won_totals: {}, open_next_totals: {}, open_sum_totals: {}, won_next_totals: {}, won_sum_totals: {} },
    ];
    getBoardReport.mockResolvedValue(data);
    render(<ReportsPage boardId={1} />);
    const heading = await screen.findByText("By confidence level");
    const table = heading.closest("section") ?? heading.parentElement!;
    for (const key of ["LOW", "MEDIUM", "HIGH", "Not assigned"]) expect(within(table).getByText(key)).toBeInTheDocument();
    expect(within(table).getByText("LOW").closest("tr")).toHaveTextContent("$30");
    expect(within(table).getByText("Not assigned").closest("tr")).not.toHaveTextContent("$");
  });

  it("does not break against a report without the section", async () => {
    const data = report();
    delete data.by_confidence_level;
    getBoardReport.mockResolvedValue(data);
    render(<ReportsPage boardId={1} />);
    await screen.findByTestId("report-kpis");
    expect(screen.queryByText("By confidence level")).toBeNull();
  });

  it.each([["LOW", "LOW"], ["MEDIUM", "MEDIUM"], ["HIGH", "HIGH"], ["UNASSIGNED", "UNASSIGNED"]])(
    "sends %s as the filter token and keeps the other filters", async (option, token) => {
      getBoardReport.mockResolvedValue(report());
      render(<ReportsPage boardId={1} />);
      await screen.findByTestId("report-kpis");
      await userEvent.selectOptions(screen.getByLabelText("Filter by salesperson"), "1");
      await userEvent.selectOptions(screen.getByLabelText("Filter by confidence level"), option);
      await waitFor(() =>
        expect(getBoardReport).toHaveBeenLastCalledWith(1, { salespersonId: 1, productLine: undefined, confidenceLevel: token }, undefined)
      );
    });

  it("labels the unassigned option Not assigned and sends no filter for All", async () => {
    getBoardReport.mockResolvedValue(report());
    render(<ReportsPage boardId={1} />);
    await screen.findByTestId("report-kpis");
    const select = screen.getByLabelText("Filter by confidence level") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => [o.value, o.text])).toEqual([
      ["", "All"], ["LOW", "LOW"], ["MEDIUM", "MEDIUM"], ["HIGH", "HIGH"], ["UNASSIGNED", "Not assigned"],
    ]);
    await userEvent.selectOptions(select, "HIGH");
    await userEvent.selectOptions(select, "");
    await waitFor(() => expect(getBoardReport.mock.calls.at(-1)![1].confidenceLevel).toBeUndefined());
  });
});
