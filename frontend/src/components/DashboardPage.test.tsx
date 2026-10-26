import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

const rate = { value: null, numerator: 0, denominator: 0, unavailable_reason: "none" };
const duration = { count: 0, mean_days: null, median_days: null };
const flow = (over = {}) => ({
  events: 1, opportunities: 1, card_ids: [1], values: { USD: { total: 100, count: 1 } }, without_value: 0,
  value_unavailable: 0, current_values: { USD: { total: 100, count: 1 } }, current_without_value: 0,
  current_next_year_values: { USD: { total: 40, count: 1 } }, current_total_values: { USD: { total: 140, count: 1 } },
  still_in_outcome: 1, ...over,
});
const period = { label: "p", start: "2026-09-01T00:00:00", end: "2026-10-01T00:00:00" };
const cohort = { size: 0, card_ids: [], won_first: 0, lost_first: 0, still_open: 0, win_rate: rate, closed_win_rate: rate };

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getKpiSummary: async () => ({
    as_of: "2026-10-15T12:00:00",
    previous: null,
    current: {
      period, coverage: { status: "complete", tracking_started_at: "2026-01-01T00:00:00" },
      won: flow(), lost: flow({ values: {}, current_values: {}, current_next_year_values: {}, current_total_values: {} }),
      reopened: { events: 0, opportunities: 0 }, win_rate_closed: rate, cohort,
      velocity: { won: duration, lost: duration, excluded_unobserved_creation: 0 },
    },
  }),
  getKpiConversion: async () => ({ as_of: "x", period, coverage: { status: "complete", tracking_started_at: null }, win_rate_closed: rate, cohort, advancement: [] }),
  getKpiStageTime: async () => ({ as_of: "x", period, coverage: { status: "complete", tracking_started_at: null }, stages: [] }),
  getKpiAging: async () => ({
    as_of: "x", tracking_started_at: null,
    opportunities: [
      { card_id: 1, title: "Lead A", column_id: 1, column_title: "RFI", stage_kind: "open", entered_at: null, entry_observed: null,
        age_days: null, history: "none", deal_value: 200, deal_value_next_year: 60, deal_value_total: 260, deal_year: 2026, currency: "USD" },
      { card_id: 2, title: "Lead B", column_id: 1, column_title: "RFI", stage_kind: "open", entered_at: null, entry_observed: null,
        age_days: null, history: "none", deal_value: null, deal_value_next_year: 0, deal_value_total: null, deal_year: 2026, currency: "USD" },
    ],
  }),
  getKpiLeadGeneration: async () => ({
    period_start: "2026-01-01", period_end: "2026-10-24", total: 1, unattributed_count: 0,
    rows: [{ salesperson_id: 1, name: "alice", count: 1, share: 1, cumulative_share: 1 }],
  }),
  getKpiPipeline: async () => ({
    as_of: "x",
    deal_years: [2026],
    stages: [
      { column_id: 1, title: "RFI", stage_kind: "open", count: 2, without_value: 1,
        values: { USD: { total: 200, count: 1 } }, next_year_values: { USD: { total: 60, count: 1 } },
        total_values: { USD: { total: 260, count: 1 } } },
    ],
  }),
}));

import { DashboardPage } from "@/components/DashboardPage";

describe("DashboardPage annual Deal Value", () => {
  it("shows the open pipeline by stage as total plus both years, with the stored years", async () => {
    render(<DashboardPage boardId={1} />);
    const section = (await screen.findByRole("heading", { name: "Open pipeline by stage (now)" })).closest("section")!;
    expect(within(section).getByTestId("annual-totals")).toHaveTextContent("$260");
    expect(within(section).getByTestId("annual-totals")).toHaveTextContent("2026 $200 · 2027 $60");
    expect(within(section).getByTestId("annual-legend")).toHaveTextContent("2026+2027");
    // The lead count is not touched by the money split.
    expect(within(section).getByText("RFI")).toBeInTheDocument();
    expect(within(section).getByText("2", { selector: "span" })).toBeInTheDocument();
  });

  it("aging rows show the two-year value, both years beneath, and a dash when unavailable", async () => {
    render(<DashboardPage boardId={1} />);
    const table = await screen.findByTestId("aging");
    const rowA = within(table).getByText("Lead A").closest("tr")!;
    expect(rowA).toHaveTextContent("$260");
    expect(rowA).toHaveTextContent("2026 $200 · 2027 $60");
    expect(within(table).getByText("Lead B").closest("tr")!.lastElementChild).toHaveTextContent("-");
    expect(within(table).getByRole("columnheader", { name: /2026\+2027/ })).toBeInTheDocument();
  });

  it("never shows a currency other than USD for Deal Value", async () => {
    render(<DashboardPage boardId={1} />);
    await screen.findByTestId("aging");
    expect(document.body.textContent).not.toMatch(/[€₹¥]|R\$|EUR|BRL/);
  });

  it("keeps the closing value as recorded and shows annual amounts only in today's comparison", async () => {
    render(<DashboardPage boardId={1} />);
    const won = await screen.findByTestId("closing-won");
    const cells = within(won).getAllByRole("cell");
    expect(cells[2]).toHaveTextContent("$100");                       // closing value (snapshot): unchanged, one amount
    expect(within(cells[2]).queryByTestId("annual-totals")).toBeNull();
    expect(cells[5]).toHaveTextContent("$140");                       // today: two-year total
    expect(cells[5]).toHaveTextContent("2026 $100 · 2027 $40");
    expect(screen.getByText(/can't be divided by year/)).toBeInTheDocument();
    // An outcome with no cards shows no money at all.
    expect(within(screen.getByTestId("closing-lost")).getAllByRole("cell")[5]).toHaveTextContent("-");
  });
});

describe("DashboardPage lead generation Pareto", () => {
  it("is the first block of the KPIs screen, above the Period filter", async () => {
    render(<DashboardPage boardId={1} />);
    const pareto = (await screen.findByText("Lead Generation Pareto")).closest("section")!;
    const period = screen.getByLabelText("Period");
    expect(pareto.compareDocumentPosition(period) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(pareto).getByTestId("pareto-row")).toHaveTextContent("alice");
  });
});
