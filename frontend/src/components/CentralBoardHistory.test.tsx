import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralBoardView } from "@/components/CentralBoardView";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: false, canMoveBack: false, canDeleteCards: false,
  canAssignSalesperson: false, canManageBoards: false, canImport: false };
const card = (id: number, column_id: number, position: number) => ({ id, column_id, position, title: `Lead ${id}`, salesperson_id: 7 });
const boardOf = (id = 3, slug = "acme") => ({
  id, user_id: 1, title: `Board ${id} ${slug}`, created_at: "", updated_at: "",
  columns: [{ id: 30, board_id: id, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "",
    cards: [card(1, 30, 0), card(2, 30, 1)] }],
});
const entry = (id: number, extra: Record<string, unknown> = {}) => ({
  id, edited_by: "ana", created_at: "2026-10-02T10:00:00Z",
  changes: { deal_value: { old: 0, new: 5000 }, next_step: { old: null, new: "Call" } }, ...extra,
});

type Call = { method: string; url: string };
function setup(history: (url: string) => Response | Promise<Response>, over: { readOnly?: boolean } = {}) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const u = String(input);
    calls.push({ method: init?.method ?? "GET", url: u });
    const slug = u.includes("/globex/") ? "globex" : "acme";
    if (u.endsWith("/me")) return json(200, { user_id: 7, company_slug: slug, role: "sales_member", capabilities: { ...caps, ...over } });
    if (u.endsWith("/undo")) return json(200, { available: false, description: null });
    if (u.endsWith("/history")) return history(u);
    const boardId = Number(/boards\/(\d+)/.exec(u)?.[1]);
    return json(200, boardOf(boardId, slug));
  }));
  const view = render(<CentralBoardView companySlug="acme" boardId={3} />);
  return { calls, ...view };
}
const open = async (n: number) => userEvent.click(await screen.findByRole("button", { name: `History of Lead ${n}` }));
const hist = (calls: Call[]) => calls.filter((c) => c.url.endsWith("/history"));

describe("CentralBoardView card history", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("opens the history of a card from the company endpoint and shows the records in server order", async () => {
    const { calls } = setup(() => json(200, [entry(2, { edited_by: "bia" }), entry(1)]));
    await open(1);
    const dialog = await screen.findByRole("dialog", { name: "Edit history for Lead 1" });
    const rows = await within(dialog).findAllByTestId("history-entry");
    expect(rows.map((r) => within(r).getByText(/ana|bia/).textContent)).toEqual(["bia", "ana"]);
    expect(within(rows[0]).getByText("Deal value (current year)")).toBeInTheDocument();
    expect(within(rows[0]).getByText(/->\s*5000/)).toBeInTheDocument();
    expect(within(rows[0]).getByText("0")).toBeInTheDocument(); // 0 is a value, not empty
    expect(within(rows[0]).getByText("(empty)")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith("/api/companies/acme/boards/3/cards/1/history"))).toHaveLength(1);
    expect(calls.every((c) => c.url.includes("/api/companies/"))).toBe(true); // no legacy endpoint
  });

  it("is available to a read-only user", async () => {
    setup(() => json(200, []), { readOnly: true });
    await open(1);
    expect(await screen.findByText(/No edits recorded/)).toBeInTheDocument();
  });

  it("shows loading, then an empty history", async () => {
    let release: (r: Response) => void = () => {};
    setup(() => new Promise<Response>((r) => { release = r; }));
    await open(1);
    expect(await screen.findByText("Loading history...")).toBeInTheDocument();
    release(json(200, []));
    expect(await screen.findByText(/No edits recorded/)).toBeInTheDocument();
  });

  it.each([
    [401, "Your session expired."],
    [403, "You do not have access to this history."],
    [404, "not found"],
    [500, "HTTP 500"],
  ])("HTTP %i is shown as an error, not as an empty history", async (status, text) => {
    const { calls } = setup(() => json(status, {}));
    await open(1);
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(screen.queryByText(/No edits recorded/)).toBeNull();
    expect(hist(calls)).toHaveLength(1); // never retried by itself
  });

  it("a network failure offers an explicit retry", async () => {
    let fail = true;
    const { calls } = setup(() => { if (fail) throw new TypeError("Failed to fetch"); return json(200, [entry(1)]); });
    await open(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("Network error");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByTestId("history-entry")).toHaveLength(1);
    expect(hist(calls)).toHaveLength(2);
  });

  it("renders incomplete records without inventing data", async () => {
    setup(() => json(200, [
      { id: 5, edited_by: "", created_at: "not a date", changes: null },
      { id: 6, edited_by: "ana", created_at: "2026-10-02T10:00:00Z", changes: { title: { old: "A" }, odd: "x" } },
    ]));
    await open(1);
    const rows = await screen.findAllByTestId("history-entry");
    expect(within(rows[0]).getByText("unknown")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Date not recorded")).toBeInTheDocument();
    expect(within(rows[0]).getByText("No field details recorded.")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Title")).toBeInTheDocument();
    expect(within(rows[1]).getByText("odd")).toBeInTheDocument();
  });

  it("closing returns to the board untouched, also with Escape, and a late answer is ignored", async () => {
    let release: (r: Response) => void = () => {};
    setup(() => new Promise<Response>((r) => { release = r; }));
    await open(1);
    await screen.findByText("Loading history...");
    expect(screen.getByRole("button", { name: "Close history" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByTestId("central-history")).toBeNull();
    release(json(200, [entry(1)]));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("central-history")).toBeNull();
    expect(screen.getAllByTestId(/^central-card-/).map((e) => e.getAttribute("data-title"))).toEqual(["Lead 1", "Lead 2"]);
    await open(2);
    await userEvent.click(screen.getByRole("button", { name: "Close history" }));
    expect(screen.queryByTestId("central-history")).toBeNull();
  });

  it("the history of the card opened last wins over a pending one", async () => {
    const pending: Record<string, (r: Response) => void> = {};
    setup((u) => new Promise<Response>((r) => { pending[/cards\/(\d+)/.exec(u)![1]] = r; }));
    await open(1);
    await userEvent.keyboard("{Escape}");
    await open(2);
    expect(await screen.findByRole("dialog", { name: "Edit history for Lead 2" })).toBeInTheDocument();
    pending["2"](json(200, [entry(20, { edited_by: "two" })]));
    expect(await screen.findByText("two")).toBeInTheDocument();
    pending["1"](json(200, [entry(10, { edited_by: "one" })]));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("one")).toBeNull();
    expect(screen.getByText("two")).toBeInTheDocument();
  });

  it.each([
    ["company", <CentralBoardView key="b" companySlug="globex" boardId={3} />],
    ["board", <CentralBoardView key="b" companySlug="acme" boardId={4} />],
  ])("changing the %s while loading drops the history and ignores its late answer", async (_n, next) => {
    let release: (r: Response) => void = () => {};
    const { rerender } = setup(() => new Promise<Response>((r) => { release = r; }));
    await open(1);
    await screen.findByText("Loading history...");
    rerender(next);
    await waitFor(() => expect(screen.queryByTestId("central-history")).toBeNull());
    await screen.findByTestId("central-board");
    release(json(200, [entry(1, { edited_by: "stale" })]));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("stale")).toBeNull();
    expect(screen.queryByTestId("central-history")).toBeNull();
  });
});
