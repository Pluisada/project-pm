import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/** Opens a compact card's details and returns the dialog (the card's only entry point to Edit, History and Delete). */
export async function openCardDetails(title: string): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: `Open details for ${title}` }));
  return screen.findByRole("dialog", { name: title });
}

/** Opens the card's details and presses one of their actions. */
export async function cardAction(title: string, action: "Edit" | "History" | "Delete"): Promise<void> {
  const dialog = await openCardDetails(title);
  await userEvent.click(within(dialog).getByRole("button", { name: action }));
}
