import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cardAction, openCardDetails } from "@/test/cardActions";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralBoardView } from "@/components/CentralBoardView";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const base = { readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: true, canManageBoards: false, canImport: false };
const meOf = (slug: string, caps: Partial<typeof base> = {}) => ({ user_id: 7, company_slug: slug, role: "admin", capabilities: { ...base, ...caps } });
const card = (id: number, column_id: number, position: number) => ({ id, column_id, position, title: `Lead ${id}`, salesperson_id: 7 });
const boardOf = (layout: Record<number, number[]>, id = 3) => ({
  id, user_id: 1, title: `Board ${id}`, created_at: "", updated_at: "",
  columns: [30, 31].map((colId, i) => ({
    id: colId, board_id: id, title: i === 0 ? "1st Contact" : "Proposal", position: i, stage_kind: "open", created_at: "", updated_at: "",
    cards: (layout[colId] ?? []).map((cid, pos) => card(cid, colId, pos)),
  })),
});
const titles = (column: number) => within(screen.getByTestId(`central-column-${column}`)).queryAllByTestId(/^central-card-/).map((e) => e.getAttribute("data-title"));

type Call = { method: string; url: string };
function setup(opts: {
  caps?: Partial<typeof base>;
  board?: () => unknown;
  del?: () => Response | Promise<Response>;
  status?: () => Response;
  slug?: string;
} = {}) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const u = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url: u });
    if (u.endsWith("/me")) return json(200, meOf(opts.slug ?? "acme", opts.caps));
    if (u.endsWith("/undo")) return (opts.status ?? (() => json(200, { available: false, description: null })))();
    if (method === "DELETE") return (opts.del ?? (() => new Response(null, { status: 204 })))();
    return json(200, (opts.board ?? (() => boardOf({ 30: [1, 2, 3], 31: [4] })))());
  });
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<CentralBoardView companySlug={opts.slug ?? "acme"} boardId={3} />);
  return { calls, ...view };
}
const deletes = (calls: Call[]) => calls.filter((c) => c.method === "DELETE");
const askDelete = async (n: number) => {
  await cardAction(`Lead ${n}`, "Delete");
};

describe("CentralBoardView delete", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("offers Delete only with the canDeleteCards capability", async () => {
    setup({ caps: { canDeleteCards: false } });
    const dialog = await openCardDetails("Lead 1");
    expect(within(dialog).queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("offers no Delete to a read-only user", async () => {
    setup({ caps: { readOnly: true } });
    const dialog = await openCardDetails("Lead 1");
    expect(within(dialog).queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("shows no Delete on the compact card, only inside its details", async () => {
    setup();
    await screen.findByText("Lead 1");
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();
    const dialog = await openCardDetails("Lead 1");
    expect(within(dialog).getByRole("button", { name: "Delete" })).toBeInTheDocument();
    // Opening the details deletes nothing and asks for nothing yet.
    expect(screen.queryByTestId("delete-confirm")).toBeNull();
  });

  it("cancelling the confirmation deletes nothing", async () => {
    const { calls } = setup();
    await askDelete(2);
    expect(screen.getByTestId("delete-confirm")).toHaveTextContent('Delete the card "Lead 2"?');
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("delete-confirm")).toBeNull();
    expect(deletes(calls)).toHaveLength(0);
    expect(titles(30)).toEqual(["Lead 1", "Lead 2", "Lead 3"]);
  });

  it("removes the card only after the server confirmed, then re-reads the board with the server's positions", async () => {
    let release: (r: Response) => void = () => {};
    let afterDelete = false;
    const { calls } = setup({
      del: () => new Promise<Response>((r) => { release = (x) => { afterDelete = true; r(x); }; }),
      board: () => (afterDelete ? boardOf({ 30: [1, 3], 31: [4] }) : boardOf({ 30: [1, 2, 3], 31: [4] })),
    });
    await askDelete(2);
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(titles(30)).toEqual(["Lead 1", "Lead 2", "Lead 3"]); // not before the server answered
    expect(screen.queryByTestId("delete-notice")).toBeNull();
    release(new Response(null, { status: 204 }));
    expect(await screen.findByText('Deleted "Lead 2".')).toBeInTheDocument();
    expect(titles(30)).toEqual(["Lead 1", "Lead 3"]);
    expect(deletes(calls)).toEqual([{ method: "DELETE", url: expect.stringContaining("/api/companies/acme/boards/3/cards/2") }]);
    expect(calls.filter((c) => !c.url.includes("/api/companies/"))).toHaveLength(0); // no legacy endpoint
  });

  it("refreshes the Undo status after a confirmed delete and shows what the backend offers", async () => {
    let deleted = false;
    const { calls } = setup({
      del: () => { deleted = true; return new Response(null, { status: 204 }); },
      status: () => (deleted ? json(200, { available: true, description: 'Restore deleted card "Lead 2"' }) : json(200, { available: false, description: null })),
    });
    await screen.findByText("Lead 1");
    expect(screen.queryByTestId("undo-button")).toBeNull();
    await askDelete(2);
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByRole("button", { name: "Undo" })).toBeInTheDocument();
    expect(screen.getByTestId("undo-button")).toHaveAttribute("title", 'Restore deleted card "Lead 2"'); // the description is the button's tooltip
    expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/undo")).length).toBeGreaterThanOrEqual(2);
  });

  it("hides Undo when the backend says the deletion is not reversible (e.g. a card with Bid details)", async () => {
    let deleted = false;
    setup({
      del: () => { deleted = true; return new Response(null, { status: 204 }); },
      status: () => json(200, { available: false, description: null }),
    });
    await askDelete(1);
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await screen.findByText('Deleted "Lead 1".');
    expect(deleted).toBe(true);
    expect(screen.queryByTestId("undo-button")).toBeNull();
  });

  it("sends a single request for a double click", async () => {
    let release: (r: Response) => void = () => {};
    const { calls } = setup({ del: () => new Promise<Response>((r) => { release = r; }) });
    await askDelete(1);
    const confirm = screen.getByRole("button", { name: "Confirm delete" });
    await userEvent.dblClick(confirm);
    expect(deletes(calls)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Deleting..." })).toBeDisabled();
    // no second delete can start meanwhile: the details offer none
    expect(within(await openCardDetails("Lead 2")).queryByRole("button", { name: "Delete" })).toBeNull();
    release(new Response(null, { status: 204 }));
    await screen.findByText('Deleted "Lead 1".');
  });

  it.each([
    [401, "Your session expired."],
    [403, "You are not allowed to delete cards."],
  ])("HTTP %i keeps the card and shows the reason", async (status, text) => {
    const { calls } = setup({ del: () => json(status, { detail: "x" }) });
    await askDelete(2);
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByTestId("delete-notice")).toHaveTextContent(text);
    expect(titles(30)).toContain("Lead 2");
    expect(deletes(calls)).toHaveLength(1);
  });

  it("HTTP 404 says the card was not found and re-reads the board", async () => {
    let gone = false;
    const { calls } = setup({
      del: () => { gone = true; return json(404, { detail: "Card not found" }); },
      board: () => (gone ? boardOf({ 30: [1, 3], 31: [4] }) : boardOf({ 30: [1, 2, 3], 31: [4] })),
    });
    await askDelete(2);
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByTestId("delete-notice")).toHaveTextContent("was not found");
    await waitFor(() => expect(titles(30)).toEqual(["Lead 1", "Lead 3"]));
    expect(deletes(calls)).toHaveLength(1);
  });

  it("HTTP 409 and 422 show the server's message and re-read the board", async () => {
    for (const status of [409, 422]) {
      const { calls, unmount } = setup({ del: () => json(status, { detail: `Reason ${status}` }) });
      await askDelete(1);
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      expect(await screen.findByTestId("delete-notice")).toHaveTextContent(`Reason ${status}`);
      await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/boards/3")).length).toBeGreaterThanOrEqual(2));
      unmount();
    }
  });

  it.each([
    ["server error", () => json(503, { detail: "down" }), "Server error"],
    ["network failure", () => { throw new TypeError("Failed to fetch"); }, "Network error"],
  ])("a %s is reported as uncertain, reloads the board and is never retried", async (_n, del, text) => {
    const { calls } = setup({ del: del as () => Response });
    await askDelete(2);
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    const notice = await screen.findByTestId("delete-notice");
    expect(notice).toHaveTextContent(text);
    expect(notice).toHaveTextContent("may or may not have been deleted");
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/boards/3")).length).toBeGreaterThanOrEqual(2));
    expect(deletes(calls)).toHaveLength(1);
  });

  it("ignores a late answer after the board selection changed", async () => {
    let release: (r: Response) => void = () => {};
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      const u = String(input);
      const method = init?.method ?? "GET";
      calls.push({ method, url: u });
      if (u.endsWith("/me")) return json(200, meOf(u.includes("/globex/") ? "globex" : "acme"));
      if (u.endsWith("/undo")) return json(200, { available: false, description: null });
      if (method === "DELETE") return new Promise<Response>((r) => { release = r; });
      return json(200, u.includes("/globex/") ? boardOf({ 30: [9] }, 3) : boardOf({ 30: [1, 2, 3], 31: [4] }));
    }));
    const { rerender } = render(<CentralBoardView companySlug="acme" boardId={3} />);
    await askDelete(1);
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    rerender(<CentralBoardView companySlug="globex" boardId={3} />);
    await screen.findByText("Lead 9");
    release(new Response(null, { status: 204 }));
    await new Promise((r) => setTimeout(r, 20));
    expect(titles(30)).toEqual(["Lead 9"]);
    expect(screen.queryByTestId("delete-notice")).toBeNull();
    expect(screen.queryByTestId("delete-confirm")).toBeNull();
    expect(deletes(calls)).toHaveLength(1);
  });
});
