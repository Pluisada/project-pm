import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cardAction, openCardDetails } from "@/test/cardActions";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralBoardView } from "@/components/CentralBoardView";
import { buildCardUpdates } from "@/components/CentralCardForm";
import { SESSION_EXPIRED_EVENT } from "@/lib/auth";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: false, canMoveBack: false, canDeleteCards: false,
  canAssignSalesperson: false, canManageBoards: false, canImport: false, canExport: false, canManageProducts: false };
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
function backend(opts: { me?: ReturnType<typeof meOf>; cards?: ReturnType<typeof card>[]; people?: { id: number; username: string }[]; write?: (c: Call) => Promise<Response> | Response }) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const call = { url: String(url), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.method !== "GET") return opts.write ? opts.write(call) : json(200, {});
    if (call.url.endsWith("/me")) return json(200, opts.me ?? meOf("acme"));
    if (call.url.endsWith("/lead-options")) return json(200, { ...options, salespeople: opts.people ?? [] });
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

  it("lets an administrator choose the salesperson of a new card, requires one, and sends it", async () => {
    const people = [{ id: 11, username: "ana@example.com" }, { id: 12, username: "bo@example.com" }];
    const calls = backend({ me: meOf("acme", { canAssignSalesperson: true, canEditAnyCard: true }), people,
      write: (c) => json(201, card(50, 30, (c.body as { title: string }).title, 12)) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "Assigned lead");
    const select = await screen.findByLabelText("Salesperson");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(3));
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose the salesperson");
    expect(writes(calls)).toHaveLength(0); // nothing is sent, and the card is never silently given to the creator
    await userEvent.selectOptions(select, "12");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByTestId("central-card-50")).toBeInTheDocument();
    expect(writes(calls)[0].body).toEqual({ column_id: 30, title: "Assigned lead", salesperson_id: 12 });
  });

  it("shows the server's message when the chosen salesperson is refused, and keeps the form", async () => {
    const calls = backend({ me: meOf("acme", { canAssignSalesperson: true }), people: [{ id: 11, username: "ana@example.com" }],
      write: () => json(422, { detail: "Salesperson not found" }) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "Lead");
    const select = await screen.findByLabelText("Salesperson");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));
    await userEvent.selectOptions(select, "11");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Salesperson not found");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
  });

  it("shows a Sales Member no salesperson control and sends none", async () => {
    const calls = backend({ people: [{ id: 11, username: "ana@example.com" }], write: () => json(201, card(50, 30, "Mine", 7)) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    expect(screen.queryByLabelText("Salesperson")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Title"), "Mine");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    await screen.findByTestId("central-card-50");
    expect(writes(calls)[0].body).not.toHaveProperty("salesperson_id");
  });

  it("does not abort a save in flight when the form goes away, so its outcome is not hidden", async () => {
    let release: (r: Response) => void = () => {};
    const calls = backend({ write: () => new Promise<Response>((resolve) => { release = resolve; }) });
    const view = render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "Lead");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    const post = (vi.mocked(fetch).mock.calls as unknown as [string, RequestInit][]).find(([, init]) => init?.method === "POST")!;
    view.unmount(); // the company or board changed while the card was being saved
    expect(post[1].signal?.aborted ?? false).toBe(false);
    release(json(201, card(50, 30, "Lead", 7)));
    await Promise.resolve();
    expect(post[1].signal?.aborted ?? false).toBe(false);
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
    await cardAction("Mine", "Edit");
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

  it("lets an administrator reassign an existing card to a Sales Member, and a member sees no such control", async () => {
    const people = [{ id: 11, username: "ana@example.com" }, { id: 12, username: "bo@example.com" }];
    const own = card(100, 30, "Theirs", 11);
    const calls = backend({ me: meOf("acme", { canAssignSalesperson: true, canEditAnyCard: true }), cards: [own], people,
      write: (c) => json(200, { ...own, ...(c.body as object) }) });
    const view = render(<CentralBoardView companySlug="acme" boardId={3} />);
    await cardAction("Theirs", "Edit");
    const select = await screen.findByLabelText("Salesperson");
    await waitFor(() => expect(within(select).getAllByRole("option").length).toBeGreaterThanOrEqual(2));
    expect(select).toHaveValue("11"); // the current owner is preselected
    await userEvent.selectOptions(select, "12");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].body).toEqual({ salesperson_id: 12 });
    view.unmount();

    backend({ cards: [card(101, 30, "Mine", 7)] });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await cardAction("Mine", "Edit");
    expect(screen.queryByLabelText("Salesperson")).toBeNull();
  });

  it("edits the contact email and telephone: prefilled, only changes sent, a cleared one as null", async () => {
    const own = card(100, 30, "Mine", 7, { contact_email: "ana@example.com", contact_phone: "+55 11 91234-5678" } as never);
    const calls = backend({ cards: [own], write: (c) => json(200, { ...own, ...(c.body as object) }) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await cardAction("Mine", "Edit");
    expect(screen.getByLabelText("Contact email")).toHaveValue("ana@example.com");
    expect(screen.getByLabelText("Contact telephone")).toHaveValue("+55 11 91234-5678");
    await userEvent.clear(screen.getByLabelText("Contact email"));
    await userEvent.clear(screen.getByLabelText("Contact telephone"));
    await userEvent.type(screen.getByLabelText("Contact telephone"), "+358 40 123 4567");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].body).toMatchObject({ contact_email: null, contact_phone: "+358 40 123 4567" });
  });

  it("shows the backend's refusal of an invalid contact email and keeps what was typed", async () => {
    const own = card(100, 30, "Mine", 7);
    backend({ cards: [own], write: () => json(422, { detail: "Enter a valid email address" }) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await cardAction("Mine", "Edit");
    await userEvent.type(screen.getByLabelText("Contact email"), "a@b.co");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/valid email/)).toBeInTheDocument();
    expect(screen.getByLabelText("Contact email")).toHaveValue("a@b.co");
  });

  it("offers Edit in the details only on own cards for a member, on every card for canEditAnyCard, and nothing when read-only", async () => {
    const cards = [card(1, 30, "Mine", 7), card(2, 30, "Theirs", 8), card(3, 30, "Unassigned", null)];
    backend({ cards });
    const { unmount } = render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Theirs");
    const editIn = async (title: string) => {
      const dialog = await openCardDetails(title);
      const found = within(dialog).queryByRole("button", { name: "Edit" });
      await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
      return found !== null;
    };
    expect(await editIn("Mine")).toBe(true);
    expect(await editIn("Theirs")).toBe(false);
    expect(await editIn("Unassigned")).toBe(false);
    unmount();

    backend({ cards, me: meOf("acme", { canEditAnyCard: true }) });
    const admin = render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Theirs");
    expect([await editIn("Mine"), await editIn("Theirs"), await editIn("Unassigned")]).toEqual([true, true, true]);
    admin.unmount();

    backend({ cards, me: meOf("acme", { readOnly: true, canEditOwnCards: false }) });
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Theirs");
    // Read-only: no Edit anywhere; History stays available in the details.
    expect([await editIn("Mine"), await editIn("Theirs")]).toEqual([false, false]);
    const dialog = await openCardDetails("Mine");
    expect(within(dialog).getByRole("button", { name: "History" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Delete" })).toBeNull();
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

  it("a save refused with 403 (the role changed) keeps the form, is shown as an error and the board reads the permissions again", async () => {
    let demoted = false;
    const calls = backend({ write: () => { demoted = true; return json(403, { detail: "Access denied" }); } });
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (u: string, i?: RequestInit) =>
      demoted && String(u).endsWith("/me") ? json(200, meOf("acme", { readOnly: true })) : base(u, i)));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
    await userEvent.type(screen.getByLabelText("Title"), "Not saved");
    await userEvent.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not allowed");
    expect(screen.getByLabelText("Title")).toHaveValue("Not saved"); // nothing was saved, nothing is presented as saved
    expect(writes(calls)).toHaveLength(1);
    expect(await screen.findByText("View only")).toBeInTheDocument(); // the permissions were read again
    expect(screen.queryByRole("button", { name: "Add a card" })).not.toBeInTheDocument();
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
      await cardAction("Mine", "Edit");
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

describe("buildCardUpdates next action", () => {
  const base = card(1, 30, "A", 7) as unknown as Parameters<typeof buildCardUpdates>[0];
  const values = (over: Record<string, string>) => ({
    title: "A", details: "", company: "ACME", contact: "", contactEmail: "", contactPhone: "", dealValue: "", dealValueNextYear: "", nextStepDue: "", nextStep: "",
    leadSource: "", productLine: "", confidenceLevel: "", salespersonId: "", lossReason: "", ...over,
  });

  it("sends nothing for a card without a next action and an empty form (null and empty are the same)", () => {
    expect(buildCardUpdates(base, values({}))).toEqual({});
  });

  it("sends only what changed and null for what was cleared", () => {
    const withAction = { ...base, bid_details: { next_step: "Call", next_step_due: "2026-11-02" } } as unknown as typeof base;
    expect(buildCardUpdates(withAction, values({ nextStep: "Call", nextStepDue: "2026-11-02" }))).toEqual({});
    expect(buildCardUpdates(withAction, values({ nextStep: "  Visit  ", nextStepDue: "2026-11-02" }))).toEqual({ next_step: "Visit" });
    expect(buildCardUpdates(withAction, values({}))).toEqual({ next_step: null, next_step_due: null });
    expect(buildCardUpdates(base, values({ nextStepDue: "2027-01-05" }))).toEqual({ next_step_due: "2027-01-05" });
  });

  it("never sends the closing date", () => {
    const old = { ...base, expected_close_date: "2026-12-31" } as unknown as typeof base;
    expect(buildCardUpdates(old, values({}))).not.toHaveProperty("expected_close_date");
  });
});
