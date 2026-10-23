import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LeadEditModal } from "@/components/LeadEditModal";
import type { CardResponse, LeadOptions } from "@/lib/api";

const options: LeadOptions = { currencies: ["USD"], lead_sources: [], product_lines: [], salespeople: [] };

const YEAR = new Date().getFullYear();

const card = (details: string, extra: Partial<CardResponse> = {}): CardResponse => ({
  id: 1, column_id: 1, title: "Opp", details, position: 0, has_member_edits: false,
  company: null, contact: null, deal_value: null, currency: null,
  expected_close_date: null, lead_source: null, product_line: null,
  confidence_level: null, salesperson_id: null, salesperson_name: null, created_at: "", updated_at: "", ...extra,
});

const open = (details: string, extra: Partial<CardResponse> = {}) => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();
  render(<LeadEditModal card={card(details, extra)} options={options} onSave={onSave} onClose={onClose} />);
  return { onSave, onClose, field: screen.getByLabelText("Details") as HTMLTextAreaElement };
};

describe("LeadEditModal details field", () => {
  it("loads the saved details, multiline included", () => {
    const { field } = open("line one\nline two");
    expect(field.tagName).toBe("TEXTAREA");
    expect(field.value).toBe("line one\nline two");
  });

  it("saves edited multiline details as the only changed field", async () => {
    const { onSave, field } = open("old");
    await userEvent.clear(field);
    await userEvent.type(field, "first{Enter}second");
    await userEvent.click(screen.getByRole("button", { name: "Save lead" }));
    expect(onSave).toHaveBeenCalledWith({ details: "first\nsecond" });
  });

  it("saves cleared details as an empty string", async () => {
    const { onSave, field } = open("something");
    await userEvent.clear(field);
    await userEvent.click(screen.getByRole("button", { name: "Save lead" }));
    expect(onSave).toHaveBeenCalledWith({ details: "" });
  });

  it("does not save when details are untouched", async () => {
    const { onSave, onClose } = open("same");
    await userEvent.click(screen.getByRole("button", { name: "Save lead" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});

describe("LeadEditModal annual Deal Value", () => {
  const priced = { deal_value: 100000, deal_value_next_year: 50000, deal_year: YEAR, currency: "USD" };
  const current = () => screen.getByLabelText(`Deal value - current year (${YEAR})`) as HTMLInputElement;
  const next = () => screen.getByLabelText(`Deal value - next year (${YEAR + 1})`) as HTMLInputElement;
  const save = () => userEvent.click(screen.getByRole("button", { name: "Save lead" }));

  it("loads both annual values, labelled from the stored year", () => {
    open("", priced);
    expect(current().value).toBe("100000");
    expect(next().value).toBe("50000");
  });

  it("shows an existing amount as the current year and next year as 0", () => {
    open("", { deal_value: 8000, currency: "USD", deal_year: YEAR });
    expect(current().value).toBe("8000");
    expect(next().value).toBe("0");
  });

  it("does not relabel amounts stored for another year as the calendar's current year", () => {
    open("", { ...priced, deal_year: YEAR - 1 });
    expect(screen.getByLabelText(`Deal value - ${YEAR - 1}`)).toBeInTheDocument();
    expect(screen.getByLabelText(`Deal value - ${YEAR}`)).toBeInTheDocument();
    expect(screen.queryByLabelText(/current year/)).toBeNull();
  });

  it("saves only the annual value that changed", async () => {
    const { onSave } = open("", priced);
    await userEvent.clear(next());
    await userEvent.type(next(), "75000");
    await save();
    expect(onSave).toHaveBeenCalledWith({ deal_value_next_year: 75000 });
  });

  it("saves both values together", async () => {
    const { onSave } = open("", priced);
    await userEvent.clear(current());
    await userEvent.type(current(), "1");
    await userEvent.clear(next());
    await userEvent.type(next(), "2");
    await save();
    expect(onSave).toHaveBeenCalledWith({ deal_value: 1, deal_value_next_year: 2 });
  });

  it("clears the current year to null and an emptied next year to 0", async () => {
    const { onSave } = open("", priced);
    await userEvent.clear(current());
    await userEvent.clear(next());
    await save();
    expect(onSave).toHaveBeenCalledWith({ deal_value: null, deal_value_next_year: 0 });
  });

  it("has no currency control and saves amounts without ever sending a currency", async () => {
    const { onSave } = open("", { deal_year: YEAR, currency: "EUR" });   // a legacy code on the card
    expect(screen.queryByLabelText("Currency")).toBeNull();
    expect(screen.queryByText("Currency")).toBeNull();
    await userEvent.type(next(), "{Backspace}500");
    await save();
    expect(onSave).toHaveBeenCalledWith({ deal_value_next_year: 500 });
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("currency");
  });

  it("accepts an amount with no currency set (no 'choose a currency' error)", async () => {
    const { onSave } = open("", { deal_year: YEAR });
    await userEvent.type(current(), "250");
    await save();
    expect(screen.queryByText(/currency/i)).toBeNull();
    expect(onSave).toHaveBeenCalledWith({ deal_value: 250 });
  });
});

describe("LeadEditModal Next Action Date and Action", () => {
  const save = () => userEvent.click(screen.getByRole("button", { name: "Save lead" }));
  const action = () => screen.getByLabelText("Next Action Description") as HTMLInputElement;

  it("labels the dates distinctly and caps the Action at 100 characters", () => {
    open("");
    expect(screen.queryByLabelText("Expected Close Date")).toBeNull();
    expect(screen.getByLabelText("Next Action Date")).toBeTruthy();
    expect(action().maxLength).toBe(100);
  });

  it("loads the stored values from the Bid details", () => {
    open("", { bid_details: { next_step: "Call customer", next_step_due: "2026-10-23" } as CardResponse["bid_details"] });
    expect(action().value).toBe("Call customer");
    expect((screen.getByLabelText("Next Action Date") as HTMLInputElement).value).toBe("2026-10-23");
  });

  it("sends only the edited Action and Next Action Date, and null when cleared", async () => {
    const { onSave } = open("");
    await userEvent.type(action(), "Send quote");
    await userEvent.type(screen.getByLabelText("Next Action Date"), "2026-10-23");
    await save();
    expect(onSave).toHaveBeenCalledWith({ next_step: "Send quote", next_step_due: "2026-10-23" });
  });

  it("sends null for a cleared Action", async () => {
    const { onSave } = open("", { bid_details: { next_step: "Old", next_step_due: "2026-10-23" } as CardResponse["bid_details"] });
    await userEvent.clear(action());
    await save();
    expect(onSave).toHaveBeenCalledWith({ next_step: null });
  });
});

describe("LeadEditModal probability level", () => {
  const select = () => screen.getByLabelText("Probability level") as HTMLSelectElement;
  const save = () => userEvent.click(screen.getByRole("button", { name: "Save lead" }));

  it("offers Not assigned, LOW, MEDIUM and HIGH and shows the saved value", () => {
    open("x", { confidence_level: "MEDIUM" });
    expect(Array.from(select().options).map((o) => [o.value, o.text])).toEqual([
      ["", "Not assigned"], ["LOW", "LOW"], ["MEDIUM", "MEDIUM"], ["HIGH", "HIGH"],
    ]);
    expect(select().value).toBe("MEDIUM");
  });

  it("shows Not assigned for a card without a level", () => {
    open("x");
    expect(select().value).toBe("");
  });

  it("saves a chosen level as the only changed field", async () => {
    const { onSave } = open("x");
    await userEvent.selectOptions(select(), "HIGH");
    await save();
    expect(onSave).toHaveBeenCalledWith({ confidence_level: "HIGH" });
  });

  it("clears the level by sending null", async () => {
    const { onSave } = open("x", { confidence_level: "LOW" });
    await userEvent.selectOptions(select(), "");
    await save();
    expect(onSave).toHaveBeenCalledWith({ confidence_level: null });
  });

  it("omits the level when another field is edited, so it is never cleared by accident", async () => {
    const { onSave, field } = open("old", { confidence_level: "LOW" });
    await userEvent.clear(field);
    await userEvent.type(field, "new");
    await save();
    expect(onSave).toHaveBeenCalledWith({ details: "new" });
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("confidence_level");
  });
});
