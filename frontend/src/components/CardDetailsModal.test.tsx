import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CardDetailsModal } from "@/components/CardDetailsModal";
import type { BidDetails, CardResponse } from "@/lib/api";

const bid: BidDetails = {
  plant: "Manaus", customer_type: null, business_type: null, product_category: null,
  product: null, solution_type: null, confidence: 10, volume_current_year: null,
  volume_next_year: null, production_start_date: "2027-03-15",
  revenue_current_year_cents: 123456, revenue_next_year_cents: null, revenue_currency: "USD",
  status_note: null, project_lead: "J. Source", cpm: null, next_step: "Send quotation",
  next_step_due: "2020-01-01", close_reason: null, close_reason_note: null,
};

const card = (overrides: Partial<CardResponse> = {}): CardResponse => ({
  id: 1, column_id: 1, title: "Opp", details: "", position: 0, has_member_edits: false,
  company: "Acme", contact: null, deal_value: 500, currency: "EUR",
  expected_close_date: "2099-01-31", lead_source: null, product_line: null,
  salesperson_id: null, salesperson_name: null, created_at: "", updated_at: "", ...overrides,
});

const open = (c: CardResponse, canEdit = true) => {
  const onEdit = vi.fn();
  const onClose = vi.fn();
  render(<CardDetailsModal card={c} stageTitle="RFI" stageKind="open" canEdit={canEdit} onEdit={onEdit} onClose={onClose} />);
  return { onEdit, onClose, dialog: screen.getByRole("dialog") };
};

describe("CardDetailsModal", () => {
  it("shows Deal Value in USD (whatever legacy code the card holds) and Bid fields in their own currency", () => {
    const { dialog } = open(card({ bid_details: bid, source_ref: { source_code: "OPP-1", source_confidence: "LOW (10%)" } }));
    expect(within(dialog).getByText(/\$500/)).toBeInTheDocument();   // card.currency is a legacy "EUR": ignored
    expect(within(dialog).getByText("$1,234.56")).toBeInTheDocument();
    expect(within(dialog).getByText("10%")).toBeInTheDocument();
    expect(within(dialog).getByText("LOW (10%)")).toBeInTheDocument();
    // Next action due, expected close and production start are three separate dates.
    expect(within(dialog).getByText("Jan 1, 2020")).toBeInTheDocument();
    expect(within(dialog).getByText("Jan 31, 2099")).toBeInTheDocument();
    expect(within(dialog).getByText("Mar 15, 2027")).toBeInTheDocument();
    // Null fields: no row, no zero.
    expect(within(dialog).queryByText("Revenue, next year")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Volume, current year")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("CPM (source)")).not.toBeInTheDocument();
  });

  it("lists the two annual deal values by year, apart from Bid revenue", () => {
    const { dialog } = open(card({ deal_value: 500, deal_value_next_year: 250, deal_year: 2026, bid_details: bid }));
    expect(within(dialog).getByText("Deal value 2026")).toBeInTheDocument();
    expect(within(dialog).getByText("Deal value 2027")).toBeInTheDocument();
    expect(within(dialog).getByText("$500")).toBeInTheDocument();
    expect(within(dialog).getByText("$250")).toBeInTheDocument();
    expect(within(dialog).getByText("$1,234.56")).toBeInTheDocument();
  });

  it("shows no Deal Value row, and no $0, when nothing is entered", () => {
    const { dialog } = open(card({ deal_value: null, deal_value_next_year: 0, deal_year: 2026 }));
    expect(within(dialog).queryByText(/Deal value/)).toBeNull();
    expect(within(dialog).queryByText("$0")).toBeNull();
  });

  it("keeps Bid revenue in its own currency next to a USD Deal Value", () => {
    const { dialog } = open(card({ deal_value: 500, bid_details: { ...bid, revenue_currency: "EUR" } }));
    expect(within(dialog).getByText("$500")).toBeInTheDocument();
    expect(within(dialog).getByText("€1,234.56")).toBeInTheDocument();
  });

  it("states that a card has no Bid data instead of inventing any", () => {
    const { dialog } = open(card());
    expect(within(dialog).getByText("No Bid report data for this opportunity.")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Source reference/)).not.toBeInTheDocument();
  });

  it("offers Edit only when the user may edit", async () => {
    const first = open(card(), true);
    await userEvent.click(within(first.dialog).getByRole("button", { name: "Edit" }));
    expect(first.onEdit).toHaveBeenCalled();
  });

  it("has no Edit action without permission", () => {
    const { dialog } = open(card(), false);
    expect(within(dialog).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("focuses Close first and closes on Escape", async () => {
    const { onClose, dialog } = open(card());
    expect(within(dialog).getByRole("button", { name: "Close" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});
