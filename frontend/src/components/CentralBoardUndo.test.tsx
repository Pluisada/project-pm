import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DragEndEvent } from "@dnd-kit/core";

// Same approach as CentralBoardMove.test.tsx: jsdom cannot drag, so the DndContext exposes onDragEnd.
let drop: (event: Partial<DragEndEvent>) => void = () => {};
vi.mock("@dnd-kit/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@dnd-kit/core")>();
  return {
    ...actual,
    DndContext: (props: { children: React.ReactNode; onDragEnd: (e: DragEndEvent) => void }) => {
      drop = (event) => props.onDragEnd(event as DragEndEvent);
      return props.children;
    },
  };
});

import { CentralBoardView } from "@/components/CentralBoardView";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const base = { readOnly: false, canEditOwnCards: true, canEditAnyCard: false, canMoveBack: false, canDeleteCards: false,
  canAssignSalesperson: false, canManageBoards: false, canImport: false };
const meOf = (slug: string, caps: Partial<typeof base> = {}) => ({ user_id: 7, company_slug: slug, role: "sales_member", capabilities: { ...base, ...caps } });
const card = (id: number, column_id: number, position: number) => ({ id, column_id, position, title: `Lead ${id}`, salesperson_id: 7 });
const boardOf = (layout: Record<number, number[]>, id = 3) => ({
  id, user_id: 1, title: `Board ${id}`, created_at: "", updated_at: "",
  columns: [30, 31].map((colId, i) => ({
    id: colId, board_id: id, title: i === 0 ? "1st Contact" : "Proposal", position: i, stage_kind: "open", created_at: "", updated_at: "",
    cards: (layout[colId] ?? []).map((cid, pos) => card(cid, colId, pos)),
  })),
});
const initial = { 30: [1, 2, 3], 31: [4] };
const ids = (column: number) => within(screen.getByTestId(`central-column-${column}`)).queryAllByTestId(/^central-card-/).map((e) => e.getAttribute("data-title"));
const dropOn = (cardId: number, over: string) => act(async () => { drop({ active: { id: `card-${cardId}` }, over: { id: over } } as unknown as Partial<DragEndEvent>); });

type Call = { method: string; url: string };
/** Server stub: `board` is the current server state, `undo` the GET status, `post` the answer to POST .../undo. */
function setup(opts: {
  caps?: Partial<typeof base>;
  board?: () => unknown;
  status?: () => Response | Promise<Response>;
  post?: () => Response | Promise<Response>;
  put?: () => Response | Promise<Response>;
  slug?: string;
} = {}) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const u = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url: u });
    if (u.endsWith("/me")) return json(200, meOf(opts.slug ?? "acme", opts.caps));
    if (u.endsWith("/undo")) {
      return method === "POST" ? (opts.post ?? (() => json(200, { available: false, description: null, undone: "Move" })))()
        : (opts.status ?? (() => json(200, { available: true, description: 'Move "Lead 1" back' })))();
    }
    if (method === "PUT") return (opts.put ?? (() => json(200, { ...card(1, 31, 0), column_id: 31 })))();
    return json(200, (opts.board ?? (() => boardOf(initial)))());
  });
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<CentralBoardView companySlug={opts.slug ?? "acme"} boardId={3} />);
  return { calls, fetchMock, ...view };
}
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");

describe("CentralBoardView undo", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("offers Undo when the backend reports a reversible change, using the company endpoint", async () => {
    const { calls } = setup();
    const button = await screen.findByRole("button", { name: "Undo" });
    expect(button).toBeEnabled();
    expect(screen.getByText('Move "Lead 1" back')).toBeInTheDocument();
    expect(calls.some((c) => c.method === "GET" && c.url.endsWith("/api/companies/acme/boards/3/undo"))).toBe(true);
  });

  it("hides Undo when nothing is reversible", async () => {
    setup({ status: () => json(200, { available: false, description: null }) });
    await screen.findByText("Lead 1");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("is not offered, and never asked about, in a read-only view", async () => {
    const { calls } = setup({ caps: { readOnly: true } });
    await screen.findByText("Lead 1");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/undo"))).toBe(false);
  });

  it("stays hidden when the status read fails (403)", async () => {
    setup({ status: () => json(403, { detail: "no" }) });
    await screen.findByText("Lead 1");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("undoes a move between columns: POST, then the board is re-read and shows the restored order", async () => {
    let serverBoard = boardOf(initial);
    let available = false;
    const { calls } = setup({
      board: () => serverBoard,
      status: () => json(200, { available, description: 'Move "Lead 1" back' }),
      put: () => { serverBoard = boardOf({ 30: [2, 3], 31: [4, 1] }); available = true; return json(200, { ...card(1, 31, 0), column_id: 31 }); },
      post: () => { serverBoard = boardOf(initial); available = false; return json(200, { available: false, description: null, undone: 'Move "Lead 1" back' }); },
    });
    await screen.findByText("Lead 1");
    await dropOn(1, "col-31");
    await waitFor(() => expect(ids(31)).toEqual(["Lead 4", "Lead 1"]));
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    expect(await screen.findByTestId("undo-notice")).toHaveTextContent('Undone: Move "Lead 1" back.');
    expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 3"]); // dense positions come from the server read
    expect(ids(31)).toEqual(["Lead 4"]);
    expect(posts(calls)).toHaveLength(1);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument());
  });

  it("undoes a reorder inside a column", async () => {
    let serverBoard = boardOf(initial);
    setup({
      board: () => serverBoard,
      put: () => { serverBoard = boardOf({ 30: [2, 3, 1], 31: [4] }); return json(200, { ...card(1, 30, 2) }); },
      post: () => { serverBoard = boardOf(initial); return json(200, { available: false, description: null, undone: "Reorder" }); },
    });
    await screen.findByText("Lead 1");
    await dropOn(1, "card-3");
    await waitFor(() => expect(ids(30)).toEqual(["Lead 2", "Lead 3", "Lead 1"]));
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await screen.findByText(/Undone: Reorder/);
    expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 3"]);
  });

  it("blocks a second Undo while one is in flight and shows a loading state", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const { calls } = setup({ post: async () => { await gate; return json(200, { available: false, description: null, undone: "Move" }); } });
    const button = await screen.findByRole("button", { name: "Undo" });
    await userEvent.dblClick(button);
    expect(await screen.findByRole("button", { name: "Undoing..." })).toBeDisabled();
    expect(posts(calls)).toHaveLength(1);
    release();
    await screen.findByTestId("undo-notice");
    expect(posts(calls)).toHaveLength(1);
  });

  it("does not claim success before the server answers", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    setup({ post: async () => { await gate; return json(200, { available: false, description: null, undone: "Move" }); } });
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    expect(screen.queryByTestId("undo-notice")).not.toBeInTheDocument();
    release();
    expect(await screen.findByTestId("undo-notice")).toHaveTextContent("Undone");
  });

  const failures: [string, () => Response | Promise<Response>, RegExp, "error" | "uncertain"][] = [
    ["403", () => json(403, { detail: "no" }), /not allowed to undo/, "error"],
    ["404", () => json(404, { detail: "Nothing to undo" }), /Nothing to undo/, "error"],
    ["409", () => json(409, { detail: 'Can\'t undo: "Lead 1" was changed since' }), /was changed since/, "error"],
    ["401", () => json(401, { detail: "expired" }), /session expired/, "error"],
    ["500", () => json(500, { detail: "boom" }), /may or may not have been applied/, "uncertain"],
    ["network", () => { throw new TypeError("down"); }, /Network error.*may or may not/, "uncertain"],
  ];
  it.each(failures)("handles %s without retrying the undo", async (_name, post, text) => {
    const { calls } = setup({ post });
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    expect(await screen.findByTestId("undo-notice")).toHaveTextContent(text);
    await new Promise((r) => setTimeout(r, 30));
    expect(posts(calls)).toHaveLength(1);
    expect(screen.getByText("Lead 1")).toBeInTheDocument();
  });

  it("re-reads the board after an uncertain result so the real state is shown", async () => {
    let serverBoard = boardOf(initial);
    const { calls } = setup({
      board: () => serverBoard,
      post: () => { serverBoard = boardOf({ 30: [2, 3], 31: [4, 1] }); return json(500, { detail: "boom" }); },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(ids(31)).toEqual(["Lead 4", "Lead 1"]));
    expect(posts(calls)).toHaveLength(1);
  });

  it("ignores an undo answer that arrives after the company changed, and shows nothing of the old one", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      const u = String(input);
      const old = u.includes("/companies/old/");
      if (u.endsWith("/me")) return json(200, meOf(old ? "old" : "new"));
      if (u.endsWith("/undo")) {
        if (init?.method === "POST") { await gate; return json(200, { available: false, description: null, undone: "OLD UNDO" }); }
        return json(200, { available: old, description: "old change" });
      }
      return json(200, old ? { ...boardOf({ 30: [1] }), id: 3 } : { ...boardOf({ 30: [8] }), id: 5 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<CentralBoardView companySlug="old" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    rerender(<CentralBoardView companySlug="new" boardId={5} />);
    expect(await screen.findByText("Lead 8")).toBeInTheDocument();
    release();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("undo-notice")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Undo/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Lead 1")).not.toBeInTheDocument();
  });

  it("discards a late undo-status answer from the previous selection", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      const u = String(input);
      const old = u.includes("/companies/old/");
      if (u.endsWith("/me")) return json(200, meOf(old ? "old" : "new"));
      if (u.endsWith("/undo")) {
        if (old) { await gate; return json(200, { available: true, description: "old change" }); }
        return json(200, { available: false, description: null });
      }
      return json(200, old ? { ...boardOf({ 30: [1] }), id: 3 } : { ...boardOf({ 30: [8] }), id: 5 });
    }));
    const { rerender } = render(<CentralBoardView companySlug="old" boardId={3} />);
    await screen.findByText("Lead 1");
    rerender(<CentralBoardView companySlug="new" boardId={5} />);
    await screen.findByText("Lead 8");
    release();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("never touches a legacy endpoint", async () => {
    const { calls } = setup();
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await screen.findByTestId("undo-notice");
    for (const c of calls) expect(c.url).toContain("/api/companies/acme/");
    expect(calls.some((c) => /\/api\/boards\//.test(c.url))).toBe(false);
  });
});
