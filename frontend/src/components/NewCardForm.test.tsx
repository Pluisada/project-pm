import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NewCardForm } from "@/components/NewCardForm";

const open = async (onAdd = vi.fn()) => {
  render(<NewCardForm onAdd={onAdd} />);
  await userEvent.click(screen.getByRole("button", { name: "Add a card" }));
  return onAdd;
};

describe("NewCardForm", () => {
  it("sends both annual Deal Values entered at creation", async () => {
    const onAdd = await open();
    await userEvent.type(screen.getByPlaceholderText("Card title"), "Lead");
    await userEvent.type(screen.getByLabelText("Current year (USD)"), "1000");
    await userEvent.type(screen.getByLabelText("Next year (USD)"), "250.5");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(onAdd).toHaveBeenCalledWith("Lead", "", { deal_value: 1000, deal_value_next_year: 250.5 });
  });

  it("sends no values when the fields are left empty", async () => {
    const onAdd = await open();
    await userEvent.type(screen.getByPlaceholderText("Card title"), "Lead");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(onAdd).toHaveBeenCalledWith("Lead", "", {});
  });
});
