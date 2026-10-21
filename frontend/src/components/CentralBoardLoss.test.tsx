import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DragEndEvent } from "@dnd-kit/core";

// Same technique as CentralBoardMove.test.tsx: jsdom has no layout, so the DndContext is replaced by one that exposes
// onDragEnd and the tests "drop" by calling it. The component logic is real.
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
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: false, canMoveBack: false, canDeleteCards: false,
  canAssignSalesperson: false, canManageBoards: false, canImport: false, canExport: false, canManageProducts: false, canReview: false };
const card = (id: number, column_id: number, position: number) =>
  ({ id, column_id, position, title: `Lead ${id}`, salesperson_id: 7 });
const board = {
  id: 3, user_id: 1, title: "Board 3", created_at: "", updated_at: "",
  columns: [
    { id: 30, board_id: 3, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "", cards: [card(1, 30, 0), card(2, 30, 1)] },
    { id: 31, board_id: 3, title: "Proposal", position: 1, stage_kind: "open", created_at: "", updated_at: "", cards: [] },
    // The title says nothing: the stage is lost because of its kind.
    { id: 32, board_id: 3, title: "Archive", position: 2, stage_kind: "lost", created_at: "", updated_at: "", cards: [card(5, 32, 0)] },
  ],
};
const ids = (column: number) => within(screen.getByTestId(`central-column-${column}`)).queryAllByTestId(/^central-card-/).map((e) => e.getAttribute("data-title"));
const dropOn = (cardId: number, over: string) => act(async () => { drop({ active: { id: `card-${cardId}` }, over: { id: over } } as unknown as Partial<DragEndEvent>); });

type Handler = (body: Record<string, unknown>) => Response | Promise<Response>;
const creates: Record<string, unknown>[] = [];
function setup(move?: Handler) {
  creates.length = 0;
  const moves: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const u = String(input);
    if (init?.method === "PUT" && u.endsWith("/position")) {
      const body = JSON.parse(String(init.body));
      moves.push(body);
      return (move ?? ((b) => json(200, { ...card(1, Number(b.column_id), 0), column_id: Number(b.column_id) })))(body);
    }
    if (init?.method === "POST" && u.endsWith("/cards")) {
      const body = JSON.parse(String(init.body));
      creates.push(body);
      return json(201, { ...card(9, Number(body.column_id), 0), title: body.title });
    }
    return u.endsWith("/me") ? json(200, { user_id: 7, company_slug: "acme", role: "sales_member", capabilities: caps }) : json(200, board);
  }));
  render(<CentralBoardView companySlug="acme" boardId={3} />);
  return moves;
}

describe("moving a card into the lost stage", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("asks for the reason first: nothing is sent and the card stays where it was", async () => {
    const moves = setup();
    await screen.findByText("Lead 1");
    await dropOn(1, "col-32");
    expect(await screen.findByTestId("loss-prompt")).toBeInTheDocument();
    expect(screen.getByLabelText(/Why was "Lead 1" lost/)).toBeInTheDocument();
    expect(moves).toHaveLength(0);
    expect(ids(30)).toEqual(["Lead 1", "Lead 2"]);
    expect(ids(32)).toEqual(["Lead 5"]);
  });

  it.each([["empty", ""], ["whitespace only", "   \n "]])("rejects an %s reason with a clear message and sends nothing", async (_n, text) => {
    const moves = setup();
    await screen.findByText("Lead 1");
    await dropOn(1, "col-32");
    if (text) await userEvent.type(await screen.findByLabelText(/Why was/), text.trim() === "" ? "   " : text);
    await userEvent.click(await screen.findByRole("button", { name: "Move to lost" }));
    expect(await screen.findByTestId("loss-error")).toHaveTextContent("Enter the reason this opportunity was lost.");
    expect(moves).toHaveLength(0);
    expect(ids(30)).toEqual(["Lead 1", "Lead 2"]);
  });

  it("sends the trimmed reason in loss_reason and shows the move only after the server confirms it", async () => {
    let release: (r: Response) => void = () => {};
    const moves = setup(() => new Promise<Response>((resolve) => { release = resolve; }));
    await screen.findByText("Lead 1");
    await dropOn(1, "col-32");
    await userEvent.type(await screen.findByLabelText(/Why was/), "  Lost on price  ");
    await userEvent.click(screen.getByRole("button", { name: "Move to lost" }));
    await waitFor(() => expect(moves).toHaveLength(1));
    expect(moves[0]).toMatchObject({ column_id: 32, loss_reason: "Lost on price" });
    expect(ids(30)).toEqual(["Lead 1", "Lead 2"]); // not shown as moved while the server has not answered
    await act(async () => { release(json(200, { ...card(1, 32, 0), column_id: 32 })); });
    await waitFor(() => expect(ids(32)).toContain("Lead 1"));
    expect(ids(30)).toEqual(["Lead 2"]);
    expect(screen.queryByTestId("loss-prompt")).not.toBeInTheDocument();
  });

  it("shows the backend's validation error, keeps the card in its stage and keeps what was typed", async () => {
    const moves = setup(() => json(422, { detail: { code: "loss_reason_too_long", message: "The loss reason can have at most 1000 characters" } }));
    await screen.findByText("Lead 1");
    await dropOn(1, "col-32");
    await userEvent.type(await screen.findByLabelText(/Why was/), "Too much");
    await userEvent.click(screen.getByRole("button", { name: "Move to lost" }));
    expect(await screen.findByTestId("move-error")).toHaveTextContent("at most 1000 characters");
    expect(moves).toHaveLength(1);
    expect(ids(30)).toEqual(["Lead 1", "Lead 2"]);
    expect(ids(32)).toEqual(["Lead 5"]);
    expect(screen.getByLabelText(/Why was/)).toHaveValue("Too much");
  });

  it("cancel closes the form without any request", async () => {
    const moves = setup();
    await screen.findByText("Lead 1");
    await dropOn(1, "col-32");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("loss-prompt")).not.toBeInTheDocument();
    expect(moves).toHaveLength(0);
  });

  it("leaves moves to other stages unchanged: no prompt, shown at once, no loss_reason", async () => {
    const moves = setup();
    await screen.findByText("Lead 1");
    await dropOn(1, "col-31");
    expect(screen.queryByTestId("loss-prompt")).not.toBeInTheDocument();
    await waitFor(() => expect(moves).toHaveLength(1));
    expect(moves[0]).not.toHaveProperty("loss_reason");
    await waitFor(() => expect(ids(31)).toEqual(["Lead 1"]));
  });

  it("does not ask again for a card that is already in the lost stage", async () => {
    setup();
    await screen.findByText("Lead 5");
    await dropOn(5, "col-32");
    expect(screen.queryByTestId("loss-prompt")).not.toBeInTheDocument();
  });

  it("asks for the reason when a card is created in the lost stage, and sends it", async () => {
    setup();
    await screen.findByText("Lead 5");
    const lostColumn = screen.getByTestId("central-column-32");
    await userEvent.click(within(lostColumn).getByRole("button", { name: "Add a card" }));
    await userEvent.type(await screen.findByLabelText("Title"), "Born lost");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the reason this opportunity was lost.");
    expect(creates).toHaveLength(0);
    await userEvent.type(screen.getByLabelText(/Loss reason/), "  Never qualified ");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    await waitFor(() => expect(creates).toHaveLength(1));
    expect(creates[0]).toMatchObject({ column_id: 32, title: "Born lost", loss_reason: "Never qualified" });
  });

  it("does not ask for a reason when a card is created in an open stage", async () => {
    setup();
    await screen.findByText("Lead 1");
    await userEvent.click(within(screen.getByTestId("central-column-30")).getByRole("button", { name: "Add a card" }));
    expect(screen.queryByLabelText(/Loss reason/)).not.toBeInTheDocument();
    await userEvent.type(await screen.findByLabelText("Title"), "Normal");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    await waitFor(() => expect(creates).toHaveLength(1));
    expect(creates[0]).not.toHaveProperty("loss_reason");
  });
});
