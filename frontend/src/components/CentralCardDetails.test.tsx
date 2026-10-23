import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralBoardView } from "@/components/CentralBoardView";
import { openCardDetails } from "@/test/cardActions";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: true, canManageBoards: false, canImport: false, canExport: false, canManageProducts: false, canReview: false };
const meOf = (over: Partial<typeof caps> = {}) => ({ user_id: 7, company_slug: "acme", role: "admin", capabilities: { ...caps, ...over } });
const lead = {
  id: 1, column_id: 30, position: 0, title: "Lead 1", company: "ACME", salesperson_id: 7, salesperson_name: "Ana",
  deal_value: 1000, deal_value_next_year: 0, deal_year: 2026, version: 4, expected_close_date: "2026-12-31",
  has_member_edits: false, details: "Some notes",
  bid_details: { next_step: "Call the buyer", next_step_due: "2026-11-02", revenue_currency: "USD" },
};
const board = {
  id: 3, user_id: 1, title: "Board", created_at: "", updated_at: "",
  columns: [
    { id: 30, board_id: 3, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "", cards: [lead] },
    { id: 31, board_id: 3, title: "Proposal", position: 1, stage_kind: "open", created_at: "", updated_at: "", cards: [] },
  ],
};

type Call = { method: string; url: string; body: unknown };
function backend(me = meOf()) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    calls.push({ method: init?.method ?? "GET", url: u, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (u.endsWith("/me")) return json(200, me);
    if (u.endsWith("/undo")) return json(200, { available: false, description: null });
    if (u.endsWith("/lead-options")) return json(200, { currencies: ["USD"], lead_sources: [], product_lines: [], salespeople: [] });
    if (init?.method === "PUT") return json(200, { ...lead, version: 5 });
    return json(200, board);
  }));
  return calls;
}
const writes = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

describe("compact central card and its details", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("shows no Edit, History or Delete on the compact card, nor the closing date", async () => {
    backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const card = await screen.findByTestId("central-card-1");
    expect(within(card).queryByRole("button", { name: /edit|history|delete/i })).toBeNull();
    expect(card.textContent).not.toMatch(/Edit|History|Delete|Close:|Dec 31/);
    expect(within(card).getByText("ACME")).toBeInTheDocument();
  });

  it("opens the details on a click and writes nothing", async () => {
    const calls = backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const dialog = await openCardDetails("Lead 1");
    expect(within(dialog).getByText("Next Action Date")).toBeInTheDocument();
    expect(within(dialog).getByText("Nov 2, 2026")).toBeInTheDocument();
    expect(within(dialog).getByText("Next Action Description")).toBeInTheDocument();
    expect(within(dialog).getByText("Call the buyer")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Close Date/i)).toBeNull();
    expect(within(dialog).queryByText(/No Bid report data/)).toBeNull();
    expect(writes(calls)).toEqual([]);
    expect(screen.getByTestId("central-column-30")).toContainElement(screen.getByTestId("central-card-1")); // did not move
  });

  it("offers Edit, History and Delete in the details of an administrator", async () => {
    backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const dialog = await openCardDetails("Lead 1");
    for (const name of ["Edit", "History", "Delete"]) expect(within(dialog).getByRole("button", { name })).toBeInTheDocument();
  });

  it("closes with Escape and gives the focus back to the card", async () => {
    backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await openCardDetails("Lead 1");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Open details for Lead 1" })).toHaveFocus();
  });

  it("opens from the keyboard", async () => {
    backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const open = await screen.findByRole("button", { name: "Open details for Lead 1" });
    open.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("dialog", { name: "Lead 1" })).toBeInTheDocument();
  });

  it("keeps dragging on its own handle: the summary is not the drag handle", async () => {
    backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const handle = await screen.findByTestId("central-handle-1");
    const open = screen.getByTestId("central-open-1");
    expect(handle).not.toContainElement(open);
    expect(open).not.toContainElement(handle);
    expect(handle).toHaveAttribute("aria-roledescription", "sortable");
    expect(open).not.toHaveAttribute("aria-roledescription");
  });

  it("Edit in the details opens the form on the version the card has now", async () => {
    const calls = backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const dialog = await openCardDetails("Lead 1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Edit" }));
    expect(screen.queryByRole("dialog", { name: "Lead 1" })).toBeNull();
    const date = (await screen.findByLabelText("Next Action Date")) as HTMLInputElement;
    expect(date.value).toBe("2026-11-02");
    const action = screen.getByLabelText("Next Action Description") as HTMLInputElement;
    expect(action.value).toBe("Call the buyer");
    expect(action.maxLength).toBe(100);
    expect(screen.queryByLabelText(/close date/i)).toBeNull();
    await userEvent.clear(action);
    await userEvent.type(action, "Send the quote");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].body).toEqual({ next_step: "Send the quote", expected_version: 4 });
  });

  it("clears the next action with null and leaves the old closing date untouched", async () => {
    const calls = backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(within(await openCardDetails("Lead 1")).getByRole("button", { name: "Edit" }));
    await userEvent.clear(await screen.findByLabelText("Next Action Date"));
    await userEvent.clear(screen.getByLabelText("Next Action Description"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].body).toEqual({ next_step_due: null, next_step: null, expected_version: 4 });
  });

  it("rejects an over-long next action description before any request", async () => {
    const calls = backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(within(await openCardDetails("Lead 1")).getByRole("button", { name: "Edit" }));
    const action = (await screen.findByLabelText("Next Action Description")) as HTMLInputElement;
    action.removeAttribute("maxlength"); // the input caps typing; this checks the form's own validation
    await userEvent.clear(action);
    await userEvent.click(action);
    await userEvent.paste("x".repeat(101));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 100 characters");
    expect(writes(calls)).toEqual([]);
  });

  it("offers a read-only user History only", async () => {
    backend(meOf({ readOnly: true, canEditOwnCards: false, canEditAnyCard: false, canDeleteCards: false }));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const dialog = await openCardDetails("Lead 1");
    expect(within(dialog).getByRole("button", { name: "History" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Edit" })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("Delete in the details asks for a confirmation before anything is sent", async () => {
    const calls = backend();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(within(await openCardDetails("Lead 1")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByTestId("delete-confirm")).toHaveTextContent('Delete the card "Lead 1"?');
    expect(writes(calls)).toEqual([]);
  });
});
