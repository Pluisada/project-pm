import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DragEndEvent } from "@dnd-kit/core";

// Same device as CentralBoardMove.test.tsx: the DndContext exposes its handlers so a drag can be driven in jsdom.
let drop: (event: Partial<DragEndEvent>) => void = () => {};
let dragStart: (event: unknown) => void = () => {};
vi.mock("@dnd-kit/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@dnd-kit/core")>();
  return {
    ...actual,
    DndContext: (props: { children: React.ReactNode; onDragEnd: (e: DragEndEvent) => void; onDragStart?: (e: unknown) => void }) => {
      drop = (event) => props.onDragEnd(event as DragEndEvent);
      dragStart = (event) => props.onDragStart?.(event);
      return props.children;
    },
  };
});

import { CentralBoardView } from "@/components/CentralBoardView";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: false, canManageBoards: false, canImport: false, canExport: false, canManageProducts: false };
const me = { user_id: 7, company_slug: "acme", role: "admin", capabilities: caps };
const card = (id: number, column_id: number, position: number, version: number, title = `Lead ${id}`) =>
  ({ id, column_id, position, title, version, company: "ACME", contact: null, deal_value: null, deal_value_next_year: 0, deal_year: 2026,
     salesperson_id: 7, salesperson_name: null, details: null });
const boardOf = (versions: Record<number, number>) => ({
  id: 3, user_id: 1, title: "Board 3", created_at: "", updated_at: "",
  columns: [
    { id: 30, board_id: 3, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "",
      cards: [card(1, 30, 0, versions[1] ?? 0), card(2, 30, 1, versions[2] ?? 0)] },
    { id: 31, board_id: 3, title: "Proposal", position: 1, stage_kind: "open", created_at: "", updated_at: "", cards: [card(4, 31, 0, versions[4] ?? 0)] },
  ],
});
const conflict = (card_id: number, expected: number, current: number) => json(409, {
  detail: { code: "card_version_conflict", message: "The card was changed by someone else since you opened it. Reload it and try again.",
            card_id, expected_version: expected, current_version: current },
});
const ids = (column: number) => within(screen.getByTestId(`central-column-${column}`)).queryAllByTestId(/^central-card-/).map((e) => e.getAttribute("data-title"));

type Call = { method: string; url: string; body: unknown };
function backend(opts: { versions?: () => Record<number, number>; write?: (c: Call) => Response } = {}) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const call = { method: init?.method ?? "GET", url: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.method !== "GET") return (opts.write ?? (() => json(200, {})))(call);
    if (call.url.endsWith("/me")) return json(200, me);
    if (call.url.endsWith("/lead-options")) return json(200, { currencies: ["USD"], lead_sources: [], product_lines: [], salespeople: [] });
    if (call.url.endsWith("/undo")) return json(200, { available: false, description: null });
    return json(200, boardOf(opts.versions?.() ?? { 1: 2, 2: 0, 4: 0 }));
  }));
  return calls;
}
const writes = (calls: Call[], method?: string) => calls.filter((c) => c.method !== "GET" && (!method || c.method === method));
const boardReads = (calls: Call[]) => calls.filter((c) => c.method === "GET" && /\/boards\/3$/.test(c.url)).length;
const drag = async (cardId: number, over: string) => act(async () => {
  dragStart({ active: { id: `card-${cardId}` } });
  drop({ active: { id: `card-${cardId}` }, over: { id: over } } as unknown as Partial<DragEndEvent>);
});

describe("central card versions (optimistic concurrency in the UI)", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  describe("edit", () => {
    it("sends the version the form was opened on, and the next edit uses the version the server returned", async () => {
      const calls = backend({ write: (c) => json(200, card(1, 30, 0, c.body && (c.body as { title?: string }).title === "First" ? 3 : 4, (c.body as { title: string }).title)) });
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await userEvent.click(await screen.findByRole("button", { name: "Edit Lead 1" }));
      const title = screen.getByLabelText("Title");
      await userEvent.clear(title);
      await userEvent.type(title, "First");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(writes(calls)[0].body).toMatchObject({ title: "First", expected_version: 2 });
      await userEvent.click(screen.getByRole("button", { name: "Edit First" }));
      const again = screen.getByLabelText("Title");
      await userEvent.clear(again);
      await userEvent.type(again, "Second");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(writes(calls)).toHaveLength(2));
      expect(writes(calls)[1].body).toMatchObject({ title: "Second", expected_version: 3 }); // the returned one, not the board's old 2
    });

    it("keeps the original version when the board is re-read while the form is open", async () => {
      let versions: Record<number, number> = { 1: 2, 2: 0, 4: 0 };
      const calls = backend({ versions: () => versions, write: () => conflict(1, 2, 9) });
      const { rerender } = render(<CentralBoardView companySlug="acme" boardId={3} refreshToken={0} />);
      await userEvent.click(await screen.findByRole("button", { name: "Edit Lead 1" }));
      versions = { 1: 9, 2: 0, 4: 0 }; // someone else saved; the board is re-read in place under the open form
      rerender(<CentralBoardView companySlug="acme" boardId={3} refreshToken={1} />);
      await waitFor(() => expect(boardReads(calls)).toBeGreaterThan(1));
      await userEvent.type(screen.getByLabelText("Title"), " edited");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByRole("alert");
      expect(writes(calls)[0].body).toMatchObject({ expected_version: 2 }); // never swapped for the fresh 9
    });

    it("explains a conflict, keeps what was typed, turns Save off and never retries", async () => {
      const calls = backend({ write: () => conflict(1, 2, 3) });
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await userEvent.click(await screen.findByRole("button", { name: "Edit Lead 1" }));
      await userEvent.type(screen.getByLabelText("Title"), " mine");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("changed by someone else");
      expect(alert).toHaveTextContent("Save is off");
      expect(screen.getByLabelText("Title")).toHaveValue("Lead 1 mine");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Reload board" })).toBeInTheDocument();
      expect(writes(calls)).toHaveLength(1);
      await userEvent.click(screen.getByRole("button", { name: "Reload board" }));
      await waitFor(() => expect(boardReads(calls)).toBeGreaterThan(1));
      expect(writes(calls)).toHaveLength(1); // reloading the board is not a retry
      expect(screen.getByLabelText("Title")).toHaveValue("Lead 1 mine");
    });

    it("treats an unrelated 409 as before (not a version conflict)", async () => {
      backend({ write: () => json(409, { detail: "Something else" }) });
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await userEvent.click(await screen.findByRole("button", { name: "Edit Lead 1" }));
      await userEvent.type(screen.getByLabelText("Title"), "!");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Something else");
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    });
  });

  describe("move", () => {
    it("sends the version the card had when the drag started, even if the board was re-read during the drag", async () => {
      let versions: Record<number, number> = { 1: 2, 2: 0, 4: 0 };
      const calls = backend({ versions: () => versions, write: () => json(200, card(1, 31, 0, 3)) });
      const { rerender } = render(<CentralBoardView companySlug="acme" boardId={3} refreshToken={0} />);
      await screen.findByText("Lead 1");
      act(() => dragStart({ active: { id: "card-1" } }));
      versions = { 1: 7, 2: 0, 4: 0 }; // refreshed under the dragging hand
      rerender(<CentralBoardView companySlug="acme" boardId={3} refreshToken={1} />);
      await waitFor(() => expect(boardReads(calls)).toBeGreaterThan(1));
      await act(async () => { drop({ active: { id: "card-1" }, over: { id: "card-4" } } as unknown as Partial<DragEndEvent>); });
      await waitFor(() => expect(writes(calls, "PUT")).toHaveLength(1));
      expect(writes(calls, "PUT")[0].body).toEqual({ column_id: 31, position: 0, expected_version: 2 });
    });

    it("takes the new version from the server's answer for the next move of the same card", async () => {
      const calls = backend({ write: (c) => json(200, card(1, (c.body as { column_id: number }).column_id, 0, (calls.filter((x) => x.method === "PUT").length) + 10)) });
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await screen.findByText("Lead 1");
      await drag(1, "card-4");
      await waitFor(() => expect(ids(31)).toContain("Lead 1"));
      await drag(1, "col-30");
      await waitFor(() => expect(writes(calls, "PUT")).toHaveLength(2));
      expect(writes(calls, "PUT")[0].body).toMatchObject({ expected_version: 2 });
      expect(writes(calls, "PUT")[1].body).toMatchObject({ expected_version: 11 }); // returned by the first move
    });

    it("puts the card back, explains the conflict, re-reads the board and does not retry", async () => {
      let versions: Record<number, number> = { 1: 2, 2: 0, 4: 0 };
      const calls = backend({ versions: () => versions, write: () => { versions = { 1: 5, 2: 0, 4: 0 }; return conflict(1, 2, 5); } });
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await screen.findByText("Lead 1");
      const readsBefore = boardReads(calls);
      await drag(1, "card-4");
      const error = await screen.findByTestId("move-error");
      expect(error).toHaveTextContent("changed by someone else");
      expect(error).toHaveTextContent("move it again");
      await waitFor(() => expect(boardReads(calls)).toBeGreaterThan(readsBefore));
      expect(writes(calls, "PUT")).toHaveLength(1);
      expect(ids(30)).toEqual(["Lead 1", "Lead 2"]);
      expect(ids(31)).toEqual(["Lead 4"]);
      await drag(1, "card-4"); // a new, deliberate move now starts from the re-read version
      await waitFor(() => expect(writes(calls, "PUT")).toHaveLength(2));
      expect(writes(calls, "PUT")[1].body).toMatchObject({ expected_version: 5 });
    });
  });

  describe("delete", () => {
    it("sends the version the card had when Delete was chosen", async () => {
      const calls = backend({ write: () => new Response(null, { status: 204 }) });
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await userEvent.click(await screen.findByRole("button", { name: "Delete Lead 1" }));
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      await waitFor(() => expect(writes(calls, "DELETE")).toHaveLength(1));
      expect(writes(calls, "DELETE")[0].url).toMatch(/\/cards\/1\?expected_version=2$/);
    });

    it("keeps the card, explains the conflict, re-reads the board and does not retry", async () => {
      const calls = backend({ write: () => conflict(1, 2, 3) });
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await userEvent.click(await screen.findByRole("button", { name: "Delete Lead 1" }));
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      const notice = await screen.findByText(/changed by someone else/);
      expect(notice).toHaveTextContent("was not deleted");
      expect(writes(calls, "DELETE")).toHaveLength(1);
      expect(ids(30)).toContain("Lead 1");
    });
  });

  describe("undo", () => {
    it("explains a version conflict, re-reads the board and never retries", async () => {
      const calls: Call[] = [];
      vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
        const call = { method: init?.method ?? "GET", url: String(input), body: undefined };
        calls.push(call);
        if (call.method === "POST" && call.url.endsWith("/undo")) return conflict(1, 1, 3);
        if (call.url.endsWith("/undo")) return json(200, { available: true, description: "Undo edit to \"Lead 1\"" });
        if (call.url.endsWith("/me")) return json(200, me);
        return json(200, boardOf({ 1: 3, 2: 0, 4: 0 }));
      }));
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await userEvent.click(await screen.findByTestId("undo-button"));
      expect(await screen.findByTestId("undo-notice")).toHaveTextContent("nothing was undone");
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
      await waitFor(() => expect(boardReads(calls)).toBeGreaterThan(1));
    });
  });
});
