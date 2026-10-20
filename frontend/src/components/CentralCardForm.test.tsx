import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralBoardView } from "@/components/CentralBoardView";
import { SESSION_EXPIRED_EVENT } from "@/lib/auth";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: false, canMoveBack: false, canDeleteCards: false,
  canAssignSalesperson: false, canManageBoards: false, canImport: false };
const meOf = (slug: string, over: Partial<typeof caps> = {}) =>
  ({ user_id: 7, company_slug: slug, role: "sales_member", capabilities: { ...caps, ...over } });
const card = (id: number, columnId: number, title: string, salesperson_id: number | null, extra = {}) =>
  ({ id, column_id: columnId, title, position: id, company: "ACME", contact: null, deal_value: null, deal_value_next_year: 0,
    deal_year: 2026, salesperson_id, salesperson_name: null, details: null, ...extra });
const boardOf = (id: number, cards: ReturnType<typeof card>[]) => ({
  id, user_id: 1, title: `Board ${id}`, created_at: "", updated_at: "",
  columns: [{ id: id * 10, board_id: id, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "", cards }],
});
const options = { currencies: ["USD"], lead_sources: ["Referral"], product_lines: ["Chargers"], salespeople: [] };

type Call = { url: string; method: string; body: unknown };

/** A fake central backend; `write` decides the answer to POST/PUT. */
function backend(opts: { me?: ReturnType<typeof meOf>; cards?: ReturnType<typeof card>[]; write?: (c: Call) => Promise<Response> | Response }) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const call = { url: String(url), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.method !== "GET") return opts.write ? opts.write(call) : json(200, {});
    if (call.url.endsWith("/me")) return json(200, opts.me ?? meOf("acme"));
    if (call.url.endsWith("/lead-options")) return json(200, options);
    return json(200, boardOf(3, opts.cards ?? []));
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const writes = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

describe("central card create/edit", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("creates a card with the central contract, then shows it without any legacy call", async () => {
    const calls = backend({ write: (c) => json(201, card(50, 30, (c.body as { title: string }).title, 7)) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "  New lead ");
    await userEvent.type(screen.getByLabelText("Deal value, current year (USD)"), "1500");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByTestId("central-card-50")).toHaveTextContent("New lead");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const [post] = writes(calls);
    expect(post.method).toBe("POST");
    expect(post.url).toMatch(/\/api\/companies\/acme\/boards\/3\/cards$/);
    expect(post.body).toEqual({ column_id: 30, title: "New lead", deal_value: 1500 });
    for (const c of calls) expect(c.url).toMatch(/\/api\/companies\/acme\//);
  });

  it("requires a title and rejects negative values before calling the server", async () => {
    const calls = backend({});
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Title is required");
    await userEvent.type(screen.getByLabelText("Title"), "x");
    await userEvent.type(screen.getByLabelText("Deal value, next year (USD)"), "-3");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(screen.getByRole("alert")).toHaveTextContent("zero or more");
    expect(writes(calls)).toHaveLength(0);
  });

  it("sends one request when the submit is triggered twice", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const calls = backend({ write: async (c) => { await gate; return json(201, card(51, 30, (c.body as { title: string }).title, 7)); } });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "Once");
    const submit = screen.getByRole("button", { name: "Add card" });
    await userEvent.dblClick(submit);
    expect(screen.getByRole("button", { name: "Saving..." })).toBeDisabled();
    release();
    await screen.findByTestId("central-card-51");
    expect(writes(calls)).toHaveLength(1);
  });

  it("edits an own card, sends only the changed fields and null for a cleared one", async () => {
    const own = card(100, 30, "Mine", 7, { company: "ACME", contact: "Bob", deal_value: 10 });
    const calls = backend({ cards: [own], write: (c) => json(200, { ...own, ...(c.body as object), title: "Mine 2", contact: null }) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit Mine" }));
    expect(screen.getByLabelText("Title")).toHaveValue("Mine");
    expect(screen.getByLabelText("Customer")).toHaveValue("ACME");
    expect(screen.getByLabelText("Lead source")).toHaveValue(""); // null stays unset, not a value
    await userEvent.clear(screen.getByLabelText("Title"));
    await userEvent.type(screen.getByLabelText("Title"), "Mine 2");
    await userEvent.clear(screen.getByLabelText("Contact"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByTestId("central-card-100")).toHaveTextContent("Mine 2"));
    const [put] = writes(calls);
    expect(put.method).toBe("PUT");
    expect(put.url).toMatch(/\/api\/companies\/acme\/boards\/3\/cards\/100$/);
    expect(put.body).toEqual({ title: "Mine 2", contact: null });
  });

  it("offers Edit only on own cards for a member, on every card for canEditAnyCard, and nothing when read-only", async () => {
    const cards = [card(1, 30, "Mine", 7), card(2, 30, "Theirs", 8), card(3, 30, "Unassigned", null)];
    backend({ cards });
    const { unmount } = render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Theirs");
    expect(screen.getByRole("button", { name: "Edit Mine" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Theirs" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Unassigned" })).not.toBeInTheDocument();
    unmount();

    backend({ cards, me: meOf("acme", { canEditAnyCard: true }) });
    const admin = render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Theirs");
    expect(screen.getAllByRole("button", { name: /^Edit / })).toHaveLength(3);
    admin.unmount();

    backend({ cards, me: meOf("acme", { readOnly: true, canEditOwnCards: false }) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Theirs");
    // Read-only: no write control at all; the read-only History button is the only one.
    expect(screen.getAllByRole("button").every((b) => /^History of /.test(b.getAttribute("aria-label") ?? ""))).toBe(true);
  });

  it.each([
    [403, { detail: "You can only edit opportunities assigned to you" }, "not allowed"],
    [422, { detail: "Salesperson not found" }, "Salesperson not found"],
  ])("keeps the typed data and shows a %s without retrying", async (status, body, text) => {
    const calls = backend({ write: () => json(status, body) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "Keep me");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(screen.getByLabelText("Title")).toHaveValue("Keep me");
    expect(screen.getByRole("button", { name: "Add card" })).toBeEnabled();
    await new Promise((r) => setTimeout(r, 20));
    expect(writes(calls)).toHaveLength(1);
  });

  it("on a network failure keeps the form, never retries and lets the user reload the board to check", async () => {
    let fail = true;
    const calls = backend({});
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (u: string, i?: RequestInit) => {
      if (fail && i?.method === "POST") { calls.push({ url: String(u), method: "POST", body: undefined }); throw new TypeError("down"); }
      return base(u, i);
    }));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "Maybe saved");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not confirm");
    expect(screen.getByLabelText("Title")).toHaveValue("Maybe saved");
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    fail = false;
    await userEvent.click(within(alert).getByRole("button", { name: "Reload board" }));
    expect(screen.getByLabelText("Title")).toHaveValue("Maybe saved"); // reload does not discard the form
  });

  it("on a 401 ends the session through the existing event and shows no form error", async () => {
    backend({ write: () => json(401, { detail: "Invalid or expired token" }) });
    const expired = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    await waitFor(() => expect(expired).toHaveBeenCalledTimes(1));
    window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores the answer of a write started in a previous company/board", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      const slug = u.includes("/companies/old/") ? "old" : "new";
      if (init?.method === "POST") { await gate; return json(201, card(77, 10, "STALE CARD", 7)); }
      if (u.endsWith("/me")) return json(200, meOf(slug));
      if (u.endsWith("/lead-options")) return json(200, options);
      return json(200, boardOf(slug === "old" ? 1 : 2, []));
    }));
    const { rerender } = render(<CentralBoardView companySlug="old" boardId={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "Stale");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    rerender(<CentralBoardView companySlug="new" boardId={2} />);
    await screen.findByText("Board 2");
    release();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("STALE CARD")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  describe("uncertain create outcome", () => {
    const openAndType = async (title = "Maybe saved") => {
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
      await userEvent.type(screen.getByLabelText("Title"), title);
      await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    };
    const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");

    it("after a 5xx blocks a plain second click, keeps the typed data and never retries by itself", async () => {
      const calls = backend({ write: () => json(503, { detail: "boom" }) });
      await openAndType();
      await screen.findByText(/may or may not have been created/);
      expect(screen.getByLabelText("Title")).toHaveValue("Maybe saved");
      const again = screen.getByRole("button", { name: "Add card again" });
      expect(again).toBeDisabled();
      await userEvent.click(again);
      fireEvent.submit(screen.getByRole("dialog")); // Enter in the field submits the form too
      await new Promise((r) => setTimeout(r, 20));
      expect(posts(calls)).toHaveLength(1);
      for (const c of calls) expect(c.url).toMatch(/\/api\/companies\/acme\//);
    });

    it("after a network failure keeps reload available, states no outcome, and still blocks a plain re-add", async () => {
      const calls = backend({});
      const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
      vi.stubGlobal("fetch", vi.fn(async (u: string, i?: RequestInit) => {
        if (i?.method === "POST") { calls.push({ url: String(u), method: "POST", body: undefined }); throw new TypeError("down"); }
        return base(u, i);
      }));
      await openAndType();
      await userEvent.click(await screen.findByRole("button", { name: "Reload board" }));
      const status = await screen.findByText(/earlier attempt is not confirmed either way/);
      expect(status).toHaveTextContent(/duplicate/);
      expect(status.textContent).not.toMatch(/was created|was not created|was saved\b/);
      expect(screen.getByLabelText("Title")).toHaveValue("Maybe saved");
      expect(screen.getByRole("button", { name: "Add card again" })).toBeDisabled(); // reloading does not unlock it
      expect(posts(calls)).toHaveLength(1);
    });

    it("says so when the reload itself fails, and does not prove anything from a same-title card", async () => {
      const calls = backend({ cards: [card(9, 30, "Maybe saved", 7)], write: () => json(500, {}) });
      const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
      let failReload = false;
      vi.stubGlobal("fetch", vi.fn(async (u: string, i?: RequestInit) => {
        if (failReload && !i?.method && !String(u).endsWith("/me") && !String(u).endsWith("/lead-options")) throw new TypeError("down");
        return base(u, i);
      }));
      await openAndType();
      await screen.findByText(/may or may not/);
      failReload = true;
      await userEvent.click(screen.getByRole("button", { name: "Reload board" }));
      expect(await screen.findByText(/could not be reloaded, so it is still unknown/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Add card again" })).toBeDisabled();
      expect(posts(calls)).toHaveLength(1);
    });

    it("allows one deliberate retry only after the duplicate warning is confirmed, and asks again if it fails again", async () => {
      let n = 0;
      const calls = backend({ write: (c) => (++n === 1 ? json(502, {}) : n === 2 ? json(502, {}) : json(201, card(60, 30, (c.body as { title: string }).title, 7))) });
      await openAndType();
      await screen.findByText(/may or may not/);
      await userEvent.click(screen.getByLabelText(/may create a duplicate card/));
      await userEvent.click(screen.getByRole("button", { name: "Add card again" }));
      await waitFor(() => expect(posts(calls)).toHaveLength(2));
      await screen.findByText(/may or may not/);
      expect(screen.getByRole("button", { name: "Add card again" })).toBeDisabled(); // confirmation is per attempt
      await userEvent.click(screen.getByLabelText(/may create a duplicate card/));
      await userEvent.click(screen.getByRole("button", { name: "Add card again" }));
      expect(await screen.findByTestId("central-card-60")).toBeInTheDocument();
      expect(posts(calls)).toHaveLength(3);
    });

    it("does not lock the form after an explicit rejection (422) and lets the user fix and resubmit", async () => {
      let n = 0;
      const calls = backend({ write: (c) => (++n === 1 ? json(422, { detail: "Some values are not valid." }) : json(201, card(61, 30, (c.body as { title: string }).title, 7))) });
      await openAndType("Fixable");
      await screen.findByText(/not valid/);
      expect(screen.queryByText(/duplicate/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Reload board" })).not.toBeInTheDocument();
      const add = screen.getByRole("button", { name: "Add card" });
      expect(add).toBeEnabled();
      await userEvent.click(add);
      expect(await screen.findByTestId("central-card-61")).toBeInTheDocument();
      expect(posts(calls)).toHaveLength(2);
    });

    it("keeps editing unblocked after an uncertain failure: the guard is for create only", async () => {
      const calls = backend({ cards: [card(5, 30, "Mine", 7)], write: () => json(500, {}) });
      render(<CentralBoardView companySlug="acme" boardId={3} />);
      await userEvent.click(await screen.findByRole("button", { name: "Edit Mine" }));
      await userEvent.clear(screen.getByLabelText("Title"));
      await userEvent.type(screen.getByLabelText("Title"), "Renamed");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText(/may or may not have been updated/);
      expect(screen.queryByText(/duplicate/)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    });
  });
});
