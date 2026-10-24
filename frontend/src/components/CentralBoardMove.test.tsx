import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import type { DragEndEvent } from "@dnd-kit/core";

// jsdom has no layout, so a real drag cannot hit anything: the DndContext is replaced by one that exposes its
// handlers, and the tests "drop" by calling onDragEnd as dnd-kit would. The component logic is real.
let drop: (event: Partial<DragEndEvent>) => void = () => {};
let dndProps: { onDragStart?: (e: unknown) => void; onDragCancel?: () => void } = {};
vi.mock("@dnd-kit/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@dnd-kit/core")>();
  return {
    ...actual,
    DndContext: (props: { children: React.ReactNode; onDragEnd: (e: DragEndEvent) => void; onDragStart?: (e: unknown) => void; onDragCancel?: () => void }) => {
      drop = (event) => props.onDragEnd(event as DragEndEvent);
      dndProps = props;
      return props.children;
    },
  };
});

import { CentralBoardView } from "@/components/CentralBoardView";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const base = { readOnly: false, canEditOwnCards: true, canEditAnyCard: false, canMoveBack: false, canDeleteCards: false,
  canAssignSalesperson: false, canManageBoards: false, canImport: false };
const meOf = (slug: string, caps: Partial<typeof base> = {}) => ({ user_id: 7, company_slug: slug, role: "sales_member", capabilities: { ...base, ...caps } });
const card = (id: number, column_id: number, position: number, salesperson_id: number | null = 7) =>
  ({ id, column_id, position, title: `Lead ${id}`, salesperson_id });
const boardOf = (id = 3) => ({
  id, user_id: 1, title: `Board ${id}`, created_at: "", updated_at: "",
  columns: [
    { id: 30, board_id: id, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "",
      cards: [card(1, 30, 0), card(2, 30, 1), card(9, 30, 2, 8)] },
    { id: 31, board_id: id, title: "Proposal", position: 1, stage_kind: "open", created_at: "", updated_at: "", cards: [card(4, 31, 0)] },
  ],
});
const url = (input: unknown) => String(input);
const ids = (column: number) => within(screen.getByTestId(`central-column-${column}`)).queryAllByTestId(/^central-card-/).map((e) => e.getAttribute("data-title"));
const dropOn = (cardId: number, over: string) => act(async () => { drop({ active: { id: `card-${cardId}` }, over: { id: over } } as unknown as Partial<DragEndEvent>); });

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;
function setup(opts: { caps?: Partial<typeof base>; move?: Handler; board?: () => unknown } = {}) {
  const moves: { url: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const u = url(input);
    if (init?.method === "PUT" && u.endsWith("/position")) {
      moves.push({ url: u, init });
      return (opts.move ?? ((): Response => json(200, { ...card(1, 31, 0), column_id: 31 })))(u, init);
    }
    return u.endsWith("/me") ? json(200, meOf("acme", opts.caps)) : json(200, opts.board ? opts.board() : boardOf());
  });
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<CentralBoardView companySlug="acme" boardId={3} />);
  return { fetchMock, moves, ...view };
}

describe("CentralBoardView card moves", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("only cards the person may move get a handle", async () => {
    setup();
    await screen.findByText("Lead 1");
    expect(screen.getByTestId("central-handle-1")).toBeInTheDocument();
    expect(screen.queryByTestId("central-handle-9")).not.toBeInTheDocument(); // someone else's card
    expect(screen.getByTestId("central-handle-1").tagName).toBe("BUTTON"); // focusable: the keyboard sensor starts here
  });

  it("gives a read-only person no handles and ignores drops", async () => {
    const { moves } = setup({ caps: { readOnly: true } });
    await screen.findByText("Lead 1");
    expect(screen.queryByTestId("central-handle-1")).not.toBeInTheDocument();
    await dropOn(1, "col-31");
    expect(moves).toHaveLength(0);
    expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 9"]);
  });

  it("moves a card to another column, sending a company-scoped PUT with column and index", async () => {
    const { moves } = setup();
    await screen.findByText("Lead 1");
    await dropOn(1, "card-4");
    await waitFor(() => expect(moves).toHaveLength(1));
    expect(moves[0].url).toMatch(/\/api\/companies\/acme\/boards\/3\/cards\/1\/position$/);
    expect(JSON.parse(String(moves[0].init?.body))).toEqual({ column_id: 31, position: 0 });
    expect((moves[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(ids(31)).toEqual(["Lead 1", "Lead 4"]);
    expect(ids(30)).toEqual(["Lead 2", "Lead 9"]);
    expect(screen.queryByTestId("move-error")).not.toBeInTheDocument();
  });

  it("sends nothing for a drop inside the card's own column: the column is ordered by Deal Value", async () => {
    const { moves } = setup();
    await screen.findByText("Lead 1");
    await act(async () => { drop({ active: { id: "card-1" }, over: null } as unknown as Partial<DragEndEvent>); });
    await dropOn(1, "card-1");
    await dropOn(1, "card-2");
    await dropOn(2, "card-9");
    await dropOn(9, "col-30");
    expect(moves).toHaveLength(0);
    expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 9"]);
  });

  it("lists a column by Deal Value, highest first, with unvalued cards last and ties in stored order", async () => {
    const valued = (id: number, position: number, deal_value: number | null, next = 0) => ({ ...card(id, 30, position), deal_value, deal_value_next_year: next });
    const withValues = boardOf();
    withValues.columns[0].cards = [valued(1, 0, null), valued(2, 1, 900), valued(3, 2, 85000), valued(9, 3, 900), valued(5, 4, 0)];
    setup({ board: () => withValues });
    await screen.findByText("Lead 3");
    expect(ids(30)).toEqual(["Lead 3", "Lead 2", "Lead 9", "Lead 5", "Lead 1"]);
  });

  it("cancelling a drag changes nothing", async () => {
    const { moves } = setup();
    await screen.findByText("Lead 1");
    await act(async () => { dndProps.onDragStart?.({ active: { id: "card-1" } }); });
    await act(async () => { dndProps.onDragCancel?.(); });
    expect(moves).toHaveLength(0);
    expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 9"]);
  });

  it("refuses a member's backward move without calling the server, but lets an admin ask", async () => {
    const member = setup();
    await screen.findByText("Lead 4");
    await dropOn(4, "col-30");
    expect(await screen.findByTestId("move-error")).toHaveTextContent("only move cards forward");
    expect(member.moves).toHaveLength(0);
    expect(ids(31)).toEqual(["Lead 4"]);
    member.unmount();
    vi.unstubAllGlobals();

    const admin = setup({ caps: { canEditAnyCard: true, canMoveBack: true }, move: () => json(200, card(4, 30, 3)) });
    await screen.findByText("Lead 4");
    await dropOn(4, "col-30");
    await waitFor(() => expect(admin.moves).toHaveLength(1));
    expect(JSON.parse(String(admin.moves[0].init?.body))).toEqual({ column_id: 30, position: 3 });
    expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 9", "Lead 4"]);
  });

  it("reverts and shows the server's reason when the move is rejected (403, 404, 500), without retrying", async () => {
    for (const status of [403, 404, 500]) {
      const { moves, unmount } = setup({ move: () => json(status, { detail: `refused ${status}` }) });
      await screen.findByText("Lead 1");
      await dropOn(1, "col-31");
      expect(await screen.findByTestId("move-error")).toHaveTextContent(`refused ${status}`);
      expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 9"]);
      expect(ids(31)).toEqual(["Lead 4"]);
      await new Promise((r) => setTimeout(r, 30));
      expect(moves).toHaveLength(1);
      unmount();
      vi.unstubAllGlobals();
    }
  });

  it("a demoted session: the refused move reverts, is not shown as saved, and the board turns read-only on its own", async () => {
    // The person was a Sales Member when the board was opened and has since been made a Visitor.
    const opts: { caps?: Partial<typeof base>; move?: Handler } = { caps: {} };
    opts.move = () => { opts.caps = { readOnly: true }; return json(403, { detail: "Access denied" }); };
    const { moves } = setup(opts);
    await screen.findByText("Lead 1");
    expect(screen.getByTestId("central-handle-1")).toBeInTheDocument();
    await dropOn(1, "col-31");
    expect(await screen.findByTestId("move-error")).toHaveTextContent("Access denied"); // an error, not a success
    expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 9"]); // back where the server still has it
    expect(ids(31)).toEqual(["Lead 4"]);
    // The permissions are read again after the refusal: no handles, "View only", and a further drop sends nothing.
    expect(await screen.findByText("View only")).toBeInTheDocument();
    expect(screen.queryByTestId("central-handle-1")).not.toBeInTheDocument();
    await dropOn(1, "col-31");
    await new Promise((r) => setTimeout(r, 30));
    expect(moves).toHaveLength(1);
  });

  it("on a network failure reverts, re-reads the board with a GET and never repeats the PUT", async () => {
    let boardReads = 0;
    const moves: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      const u = url(input);
      if (init?.method === "PUT") { moves.push(u); throw new TypeError("network down"); }
      if (u.endsWith("/me")) return json(200, meOf("acme"));
      if (u.endsWith("/undo")) return json(200, { available: false, description: null });
      boardReads += 1;
      return json(200, boardOf());
    }));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Lead 1");
    await dropOn(1, "col-31");
    expect(await screen.findByTestId("move-error")).toHaveTextContent("Network error");
    await waitFor(() => expect(boardReads).toBe(2));
    expect(ids(30)).toEqual(["Lead 1", "Lead 2", "Lead 9"]);
    await new Promise((r) => setTimeout(r, 30));
    expect(moves).toHaveLength(1);
  });

  it("handles one move at a time: a second drop while one is in flight sends nothing", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const { moves } = setup({ move: async () => { await gate; return json(200, { ...card(1, 31, 0) }); } });
    await screen.findByText("Lead 1");
    await dropOn(1, "col-31");
    await dropOn(2, "col-31");
    expect(moves).toHaveLength(1);
    expect(screen.queryByTestId("central-handle-2")).not.toBeInTheDocument(); // handles are off while saving
    release();
    await waitFor(() => expect(screen.getByTestId("central-handle-2")).toBeInTheDocument());
    expect(moves).toHaveLength(1);
  });

  it("ignores a late move answer (success or failure) after the company changed", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      const u = url(input);
      calls.push(`${init?.method ?? "GET"} ${u}`);
      if (init?.method === "PUT") { await gate; return json(500, { detail: "late failure" }); }
      const slug = u.includes("/companies/other/") ? "other" : "acme";
      if (u.endsWith("/me")) return json(200, meOf(slug));
      const b = boardOf(slug === "other" ? 5 : 3);
      return json(200, slug === "other" ? { ...b, columns: [{ ...b.columns[0], cards: [card(50, b.columns[0].id, 0)] }] } : b);
    }));
    const { rerender } = render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Lead 1");
    await dropOn(1, "col-31");
    rerender(<CentralBoardView companySlug="other" boardId={5} />);
    expect(await screen.findByText("Lead 50")).toBeInTheDocument();
    release();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("move-error")).not.toBeInTheDocument();
    expect(screen.getByText("Lead 50")).toBeInTheDocument();
    expect(screen.queryByText("Lead 1")).not.toBeInTheDocument();
    for (const call of calls) expect(call).not.toMatch(/\/api\/boards\//); // no legacy endpoint, ever
  });

  it("uses only company-scoped endpoints", async () => {
    const { fetchMock } = setup();
    await screen.findByText("Lead 1");
    await dropOn(1, "col-31");
    await waitFor(() => expect(ids(31)).toContain("Lead 1"));
    for (const [u] of fetchMock.mock.calls) expect(url(u)).toContain("/api/companies/acme/");
  });
});
