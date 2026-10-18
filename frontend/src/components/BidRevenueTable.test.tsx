import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BidRevenueTable } from "@/components/BidRevenueTable";

const rows = [
  { title: "Proposal", count: 3, bid_revenue: [{ currency: "BRL", current_cents: 1_000_000, current_count: 1, next_cents: 2_500_000, next_count: 2 }] },
  { title: "Lost", count: 1, bid_revenue: [] },
];

describe("BidRevenueTable", () => {
  it("shows each year in the bid's own currency with the number of leads counted, and '-' where nothing is informed", () => {
    render(<BidRevenueTable rows={rows} testId="t" />);
    const table = screen.getByTestId("t");
    expect(table).toHaveTextContent(/10[.,]000/);
    expect(table).toHaveTextContent(/25[.,]000/);
    expect(table).toHaveTextContent("(2)");
    const lost = screen.getByText("Lost").closest("tr")!;
    expect(lost.textContent).toContain("-");
    expect(table.textContent).toMatch(/R\$|BRL/); // shown as the bid's own currency, never as USD
  });

  it("says so when no revenue is informed at all", () => {
    render(<BidRevenueTable rows={[{ title: "New", count: 2 }]} testId="t" />);
    expect(screen.getByText("No Bid revenue informed.")).toBeInTheDocument();
  });
});
