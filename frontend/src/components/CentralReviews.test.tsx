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

describe("central card reviews: markers on the cards and a dialog per obligation", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("has no Pending reviews section on the board; the reviewer sees a small marker only on the cards that need review", async () => {
    backend("admin", { canReview: true, canEditAnyCard: true }, [review("admin", 1, "Alpha")]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const marker = await screen.findByTestId("review-marker-11");
    expect(marker).toHaveAccessibleName("Admin review required (Sales Member edit): Alpha");
    expect(marker).toHaveAttribute("title", "Admin review required (Sales Member edit)");
    expect(within(screen.getByTestId("central-card-1")).getByTestId("review-marker-11")).toBeInTheDocument();
    expect(screen.queryByTestId("review-marker-12")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("central-card-2")).queryByRole("button", { name: /review required/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId("central-reviews")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Pending reviews" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /review/i })).not.toBeInTheDocument(); // details only when a marker is opened
  });

  it("shows the caller's own pending status and how many of the required reviewers already cleared it", async () => {
    backend("admin", { canReview: true, canEditAnyCard: true }, [
      Object.assign(review("admin", 1, "Alpha"), { my_status: "pending", reviewers_total: 2, reviewers_cleared: 1 })]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByTestId("review-marker-11"));
    const dialog = screen.getByRole("dialog", { name: "Admin review: Alpha" });
    expect(within(dialog).getByTestId("review-status")).toHaveTextContent("Pending");
    expect(within(dialog).getByTestId("review-progress")).toHaveTextContent("1 of 2 cleared");
    expect(within(dialog).getByRole("button", { name: "Clear" })).toBeEnabled();
  });

  it("opens the review dialog from the marker with category, status, who, when and the before and after values", async () => {
    backend("admin", { canReview: true, canEditAnyCard: true }, [review("admin", 1, "Alpha", 1, [
      { kind: "member_edit", version: 1, user_id: 7, at: "2026-10-22T10:00:00",
        changes: { deal_value: { old: 10, new: 20 }, title: { old: "Alpha", new: null } } }])]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByTestId("review-marker-11"));
    const dialog = screen.getByRole("dialog", { name: "Admin review: Alpha" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByText("Admin review required (Sales Member edit)")).toBeInTheDocument();
    expect(within(dialog).getByTestId("review-status")).toHaveTextContent("Pending");
    const trigger = within(dialog).getByTestId("review-trigger");
    expect(trigger).toHaveTextContent("Sales Member edit");
    expect(trigger).toHaveTextContent("By user #7");
    expect(within(trigger).getByRole("row", { name: /Deal value \(current year\) 10 20/ })).toBeInTheDocument();
    expect(within(trigger).getByRole("row", { name: /Title Alpha \(empty\)/ })).toBeInTheDocument(); // an emptied value is shown as such
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("review-marker-11")); // Escape closes it too
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("clears only the opened obligation: the marker goes after the backend confirms, the other card's stays", async () => {
    const { calls } = backend("admin", { canReview: true, canEditAnyCard: true }, [review("admin", 1, "Alpha"), review("admin", 2, "Beta", 4)]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByTestId("review-marker-11"));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" }));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toMatch(/\/api\/companies\/acme\/boards\/3\/cards\/1\/reviews\/admin\/clear$/);
    expect(post.body).toEqual({ card_version: 1 }); // the version on screen; no role or identity in the body
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1); // one request: nothing else was cleared
    expect(await screen.findByTestId("review-notice")).toHaveTextContent('The review of "Alpha" was cleared.');
    await waitFor(() => expect(screen.queryByTestId("review-marker-11")).not.toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("review-marker-21")).toBeInTheDocument(); // Beta stays
  });

  it("keeps a card's other obligation when one of two is cleared", async () => {
    // The caller holds both roles' obligations here only to prove that clearing is per obligation (the real endpoint
    // lists just the caller's own role).
    const both = [review("admin", 1, "Alpha"), review("super_admin", 1, "Alpha")];
    const { calls, state } = backend("admin", { canReview: true, canEditAnyCard: true }, both, (c) => {
      state.reviews = state.reviews.filter((r) => !(r.card_id === 1 && c.url.includes(`/reviews/${r.reviewer_role}/`)));
      return json(200, { card_id: 1, reviewer_role: "admin", card_version: 1, cleared_at: "" });
    });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const card = await screen.findByTestId("central-card-1");
    expect(await within(card).findAllByRole("button", { name: /review required/i })).toHaveLength(2); // both shown, distinct
    expect(within(card).getByTestId("review-marker-11")).toHaveAccessibleName(/^Admin review required/);
    expect(within(card).getByTestId("review-marker-12")).toHaveAccessibleName(/^Super Admin review required/);
    await userEvent.click(within(card).getByTestId("review-marker-11"));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(screen.queryByTestId("review-marker-11")).not.toBeInTheDocument());
    expect(calls.find((c) => c.method === "POST")!.url).toMatch(/\/reviews\/admin\/clear$/);
    expect(screen.getByTestId("review-marker-12")).toBeInTheDocument(); // the other obligation of the same card remains
  });

  it("shows a move into Lost to a Super Admin, with its own label and detail", async () => {
    const lost = review("super_admin", 2, "Beta", 3, [{ kind: "lost_move", version: 3, user_id: 5, at: "2026-10-24T10:00:00", from_title: "Proposal", to_title: "Lost" }]);
    backend("super_admin", { canReview: true, canEditAnyCard: true, canDeleteCards: true }, [lost]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const marker = await screen.findByTestId("review-marker-22");
    expect(marker).toHaveAccessibleName(/Super Admin review required \(Move to Lost\)/);
    await userEvent.click(marker);
    expect(within(screen.getByRole("dialog")).getByTestId("review-trigger")).toHaveTextContent("Moved to Lost from Proposal");
  });

  it("shows a backward move to a Super Admin and clears via the super_admin action", async () => {
    const moved = review("super_admin", 2, "Beta", 3, [{ kind: "backward_move", version: 3, user_id: 5, at: "2026-10-22T10:00:00", from_title: "RFI", to_title: "1st Contact" }]);
    const { calls } = backend("super_admin", { canReview: true, canEditAnyCard: true, canDeleteCards: true }, [moved]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const marker = await screen.findByTestId("review-marker-22");
    expect(marker).toHaveAccessibleName(/Super Admin review required \(Move to an earlier stage\)/);
    await userEvent.click(marker);
    expect(within(screen.getByRole("dialog")).getByTestId("review-trigger")).toHaveTextContent("Moved back from RFI to 1st Contact");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" }));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toMatch(/\/cards\/2\/reviews\/super_admin\/clear$/);
    expect(post.body).toEqual({ card_version: 3 });
    await waitFor(() => expect(screen.queryByTestId("review-marker-22")).not.toBeInTheDocument());
  });

  it.each([
    ["sales member", "sales_member", {}],
    ["visitor", "visitor", { readOnly: true, canEditOwnCards: false }],
  ])("gives a %s no markers or review controls and never asks for reviews", async (_name, role, caps) => {
    const { calls } = backend(role, caps, [review("admin", 1, "Alpha")]);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByTestId("central-board");
    expect(screen.queryByTestId("review-marker-11")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /review required/i })).not.toBeInTheDocument();
    expect(reviewCalls(calls)).toHaveLength(0);
  });

  it("does not report success when the card changed (409): explains it, keeps the marker and the dialog, shows the newer version", async () => {
    const { calls, state } = backend("admin", { canReview: true }, [review("admin", 1, "Alpha", 1)],
      () => json(409, { detail: { code: "review_stale", message: "The card changed since you opened it." } }));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByTestId("review-marker-11"));
    state.reviews = [review("admin", 1, "Alpha", 2, [{ kind: "member_edit", version: 2, at: "", changes: { deal_value: { old: 20, new: 30 } } }])];
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" }));
    const error = await screen.findByTestId("review-error");
    expect(error).toHaveRole("alert");
    expect(error).toHaveTextContent("changed after the review was opened");
    expect(screen.queryByTestId("review-notice")).not.toBeInTheDocument(); // never "cleared"
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByTestId("review-trigger")).toHaveTextContent("30")); // the newer version
    expect(screen.getByTestId("review-marker-11")).toBeInTheDocument();
    expect(reviewCalls(calls).filter((c) => c.method === "GET").length).toBeGreaterThanOrEqual(2);
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" })); // a deliberate second try uses the newer version
    expect(calls.filter((c) => c.method === "POST")[1].body).toEqual({ card_version: 2 });
  });

  it("shows a refused clearing (403) without marking it done", async () => {
    backend("admin", { canReview: true }, [review("admin", 1, "Alpha")], () => json(403, { detail: "Access denied" }));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByTestId("review-marker-11"));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" }));
    expect(await screen.findByTestId("review-error")).toHaveTextContent("not allowed");
    expect(screen.getByTestId("review-marker-11")).toBeInTheDocument();
    expect(screen.queryByTestId("review-notice")).not.toBeInTheDocument();
  });

  it("says so when the obligation is gone on the server and does not offer Clear for it", async () => {
    const { state } = backend("admin", { canReview: true }, [review("admin", 1, "Alpha")], () => json(404, { detail: { code: "review_not_found", message: "none" } }));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByTestId("review-marker-11"));
    state.reviews = []; // cleared elsewhere
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" }));
    expect(await screen.findByTestId("review-error")).toHaveTextContent("nothing left to review");
    await waitFor(() => expect(screen.getByTestId("review-status")).toHaveTextContent("No longer pending"));
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" })).toBeDisabled();
    expect(screen.queryByTestId("review-marker-11")).not.toBeInTheDocument();
  });
});
