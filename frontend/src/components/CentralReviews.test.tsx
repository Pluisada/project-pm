import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralBoardView } from "@/components/CentralBoardView";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const base = { readOnly: false, canEditOwnCards: true, canEditAnyCard: false, canMoveBack: false, canDeleteCards: false,
  canAssignSalesperson: false, canManageBoards: false, canImport: false, canExport: false, canManageProducts: false, canReview: false };
const card = (id: number, title: string) => ({ id, column_id: 30, title, position: id, company: null, contact: null, deal_value: null,
  deal_value_next_year: 0, deal_year: 2026, salesperson_id: 7, salesperson_name: null, details: null });
const board = { id: 3, user_id: 1, title: "Board 3", created_at: "", updated_at: "",
  columns: [{ id: 30, board_id: 3, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "", cards: [card(1, "Alpha"), card(2, "Beta")] }] };
const review = (role: "admin" | "super_admin", cardId: number, title: string, version = 1, triggers: unknown[] = [
  { kind: "member_edit", version, at: "", changes: { deal_value: { old: 10, new: 20 } } }]) =>
  ({ id: cardId * 10 + (role === "admin" ? 1 : 2), board_id: 3, card_id: cardId, card_title: title, column_title: "1st Contact",
    reviewer_role: role, card_version: version, status: "pending", created_at: "", updated_at: "", refresh_count: 0, triggers });

type Call = { url: string; method: string; body: unknown };

/** A stateful fake backend: the review list lives here, like the server's persisted state. */
function backend(role: string, caps: Partial<typeof base>, reviews: ReturnType<typeof review>[], clearAnswer?: (c: Call) => Response) {
  const calls: Call[] = [];
  const state = { reviews: [...reviews] };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const call = { url: String(url), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.method === "POST" && call.url.includes("/reviews/")) {
      if (clearAnswer) return clearAnswer(call);
      const cardId = Number(call.url.match(/cards\/(\d+)\//)![1]);
      state.reviews = state.reviews.filter((r) => r.card_id !== cardId);
      return json(200, { card_id: cardId, reviewer_role: role, card_version: 1, cleared_at: "" });
    }
    if (call.method !== "GET") return json(200, {});
    if (call.url.endsWith("/me")) return json(200, { user_id: 7, company_slug: "acme", role, capabilities: { ...base, ...caps } });
    if (call.url.endsWith("/reviews")) return json(200, { reviewer_role: role, items: state.reviews });
    if (call.url.endsWith("/undo")) return json(200, { available: false });
    return json(200, board);
  }));
  return { calls, state };
}

const reviewCalls = (calls: Call[]) => calls.filter((c) => c.url.includes("/reviews"));

describe("central card reviews", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("shows an Admin its own pending reviews and a badge on the card, and clears only that role's review", async () => {
    const { calls } = backend("admin", { canReview: true, canEditAnyCard: true }, [review("admin", 1, "Alpha")]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const panel = await screen.findByTestId("central-reviews");
    expect(within(panel).getByText(/Admin review/)).toBeInTheDocument();
    expect(within(panel).getByText(/Deal value \(current year\): 10 -> 20/)).toBeInTheDocument();
    expect(await screen.findByTestId("review-badge-1")).toBeInTheDocument();
    expect(screen.queryByTestId("review-badge-2")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark Alpha as reviewed" }));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toMatch(/\/api\/companies\/acme\/boards\/3\/cards\/1\/reviews\/admin\/clear$/);
    expect(post.body).toEqual({ card_version: 1 }); // the version that was on screen; no role or identity in the body
    expect(await screen.findByTestId("review-notice")).toHaveTextContent("marked as reviewed");
    await waitFor(() => expect(screen.queryByTestId("review-badge-1")).not.toBeInTheDocument()); // refreshed from the backend
    expect(screen.queryByRole("button", { name: /Mark Alpha as reviewed/ })).not.toBeInTheDocument();
  });

  it("shows a Super Admin the Super Admin reviews, including a backward move, and clears via the super_admin action", async () => {
    const moved = review("super_admin", 2, "Beta", 3, [{ kind: "backward_move", version: 3, at: "", from_title: "RFI", to_title: "1st Contact" }]);
    const { calls } = backend("super_admin", { canReview: true, canEditAnyCard: true, canDeleteCards: true }, [moved]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const panel = await screen.findByTestId("central-reviews");
    expect(within(panel).getByText(/Super Admin review/)).toBeInTheDocument();
    expect(within(panel).getByText("Moved back from RFI to 1st Contact")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark Beta as reviewed" }));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toMatch(/\/cards\/2\/reviews\/super_admin\/clear$/);
    expect(post.body).toEqual({ card_version: 3 });
    await waitFor(() => expect(screen.queryByTestId("review-badge-2")).not.toBeInTheDocument());
  });

  it.each([
    ["sales member", "sales_member", {}],
    ["visitor", "visitor", { readOnly: true, canEditOwnCards: false }],
  ])("gives a %s no review controls and never asks for reviews", async (_name, role, caps) => {
    const { calls } = backend(role, caps, [review("admin", 1, "Alpha")]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByTestId("central-board");
    expect(screen.queryByTestId("central-reviews")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-badge-1")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /as reviewed/ })).not.toBeInTheDocument();
    expect(reviewCalls(calls)).toHaveLength(0);
  });

  it("does not report success when the card changed (409): shows why, keeps the review and reads the list again", async () => {
    const { calls, state } = backend("admin", { canReview: true }, [review("admin", 1, "Alpha", 1)],
      () => json(409, { detail: { code: "review_stale", message: "The card changed since you opened it." } }));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Mark Alpha as reviewed" }));
    state.reviews = [review("admin", 1, "Alpha", 2)]; // what the server holds now: a newer version
    const notice = await screen.findByTestId("review-notice");
    expect(notice).toHaveRole("alert");
    expect(notice).toHaveTextContent("changed after you opened it");
    expect(notice).not.toHaveTextContent('"Alpha" marked as reviewed');
    await waitFor(() => expect(reviewCalls(calls).filter((c) => c.method === "GET").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByRole("button", { name: "Mark Alpha as reviewed" })).toBeInTheDocument(); // still pending
  });

  it("shows a clear error for a refused review (403) without marking it done", async () => {
    backend("admin", { canReview: true }, [review("admin", 1, "Alpha")], () => json(403, { detail: "Access denied" }));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Mark Alpha as reviewed" }));
    expect(await screen.findByTestId("review-notice")).toHaveTextContent("Access denied");
    expect(screen.getByTestId("review-badge-1")).toBeInTheDocument();
  });
});
