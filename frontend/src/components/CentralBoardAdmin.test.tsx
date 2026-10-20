import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralBoardAdmin } from "@/components/CentralBoardAdmin";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const base = { readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: true, canManageBoards: true, canImport: false };
type Caps = Partial<typeof base>;
type Col = { id: number; board_id: number; title: string; position: number; stage_kind: string; created_at: string; updated_at: string; cards: { id: number }[] };
const col = (id: number, title: string, position: number, cards = 0, kind = "open"): Col => ({
  id, board_id: 3, title, position, stage_kind: kind, created_at: "", updated_at: "", cards: Array.from({ length: cards }, (_, i) => ({ id: id * 100 + i })),
});
const board = { id: 3, user_id: 1, title: "Pipeline", created_at: "", updated_at: "" };

type Call = { method: string; url: string; body?: unknown };
type Hooks = { put?: (c: Call) => Response | Promise<Response>; post?: (c: Call) => Response | Promise<Response>; del?: (c: Call) => Response | Promise<Response>; me?: () => Response };

function setup(opts: { role?: string; caps?: Caps; columns?: Col[]; boards?: typeof board[]; hooks?: Hooks; refresh?: () => Promise<boolean> } = {}) {
  const calls: Call[] = [];
  let columns = opts.columns ?? [col(30, "1st Contact", 0), col(31, "Proposal", 1), col(32, "Award", 2, 0, "won")];
  const hooks = opts.hooks ?? {};
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const call = { method, url, body };
    calls.push(call);
    if (url.endsWith("/me")) {
      return hooks.me ? hooks.me() : json(200, { user_id: 7, company_slug: "acme", role: opts.role ?? "super_admin", capabilities: { ...base, ...opts.caps } });
    }
    if (method === "GET") return json(200, { ...board, columns });
    if (method === "PUT") return hooks.put ? hooks.put(call) : json(200, columns[0]);
    if (method === "POST") return hooks.post ? hooks.post(call) : json(201, columns[0]);
    if (method === "DELETE") return hooks.del ? hooks.del(call) : new Response(null, { status: 204 });
    return json(404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const callbacks = { onSelectBoard: vi.fn(), onBoardCreated: vi.fn(), onColumnsChanged: vi.fn(), onRefreshBoards: vi.fn(opts.refresh ?? (async () => true)) };
  const view = render(<CentralBoardAdmin companySlug="acme" boards={opts.boards ?? [board]} boardId={(opts.boards ?? [board])[0]?.id ?? null} {...callbacks} />);
  return { calls, callbacks, setColumns: (next: Col[]) => { columns = next; }, ...view };
}
const writes = (calls: Call[], method: string) => calls.filter((c) => c.method === method);
const openPanel = async () => userEvent.click(await screen.findByRole("button", { name: "Manage boards and columns" }));
const names = () => within(screen.getByRole("list", { name: "Columns in order" })).getAllByRole("listitem").map((li) => within(li).getAllByText(/./)[0].textContent);

describe("CentralBoardAdmin", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  describe("who sees it", () => {
    it("shows the entry point to a super admin", async () => {
      setup();
      expect(await screen.findByRole("button", { name: "Manage boards and columns" })).toBeInTheDocument();
    });

    it.each([
      ["sales_member", { canManageBoards: false }],
      ["visitor", { readOnly: true, canManageBoards: false, canEditOwnCards: false }],
      ["super_admin", { readOnly: true }],
      ["admin", { canManageBoards: undefined as unknown as boolean }],
    ])("shows nothing to %s without board management", async (role, caps) => {
      const { calls } = setup({ role, caps });
      await waitFor(() => expect(calls.some((c) => c.url.endsWith("/me"))).toBe(true));
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.queryByRole("button", { name: /Manage boards/ })).toBeNull();
    });

    it("grants nothing when the identity request fails", async () => {
      const { calls } = setup({ hooks: { me: () => json(500, { detail: "boom" }) } });
      await waitFor(() => expect(calls.length).toBeGreaterThan(0));
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.queryByRole("button", { name: /Manage boards/ })).toBeNull();
      expect(calls.every((c) => c.method === "GET")).toBe(true);
    });
  });

  describe("boards", () => {
    it("shows an empty-list message and creates the first board", async () => {
      const created = { ...board, id: 9, title: "Fresh" };
      const { calls, callbacks } = setup({ boards: [], hooks: { post: () => json(201, created) } });
      await openPanel();
      expect(screen.getByText(/no boards yet/i)).toBeInTheDocument();
      await userEvent.type(screen.getByLabelText("New board title"), "Fresh");
      await userEvent.click(screen.getByRole("button", { name: "Create board" }));
      await waitFor(() => expect(callbacks.onBoardCreated).toHaveBeenCalledWith(created));
      expect(writes(calls, "POST")[0]).toMatchObject({ url: expect.stringContaining("/api/companies/acme/boards"), body: { title: "Fresh" } });
      expect(await screen.findByRole("status")).toHaveTextContent('Created board "Fresh".');
    });

    it("lets the admin pick which board to administer and offers no board edit or delete", async () => {
      const { callbacks } = setup({ boards: [board, { ...board, id: 4, title: "Other" }] });
      await openPanel();
      await userEvent.selectOptions(screen.getByLabelText("Board to administer"), "4");
      expect(callbacks.onSelectBoard).toHaveBeenCalledWith(4);
      expect(screen.queryByRole("button", { name: /(delete|rename|edit) board/i })).toBeNull();
    });

    it("keeps the form and reports a failed board creation without exposing details", async () => {
      const { callbacks } = setup({ hooks: { post: () => json(500, { detail: "sqlite3.OperationalError /var/db/x.db" }) } });
      await openPanel();
      await userEvent.type(screen.getByLabelText("New board title"), "X");
      await userEvent.click(screen.getByRole("button", { name: "Create board" }));
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).not.toMatch(/sqlite|\/var/);
      expect(callbacks.onBoardCreated).not.toHaveBeenCalled();
      expect(screen.getByLabelText("New board title")).toHaveValue("X");
    });
  });

  describe("board creation of unknown outcome", () => {
    const fillAndCreate = async () => {
      await openPanel();
      await userEvent.type(screen.getByLabelText("New board title"), "X");
      await userEvent.click(screen.getByRole("button", { name: "Create board" }));
    };

    it("re-reads the board list after a network failure and says so only because it happened", async () => {
      const { callbacks, calls } = setup({ hooks: { post: () => { throw new TypeError("down"); } } });
      await fillAndCreate();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Network error");
      expect(alert).toHaveTextContent("The data was reloaded");
      expect(callbacks.onRefreshBoards).toHaveBeenCalledTimes(1);
      expect(callbacks.onBoardCreated).not.toHaveBeenCalled();
      expect(writes(calls, "POST")).toHaveLength(1); // never retried
      expect(screen.getByLabelText("New board title")).toHaveValue("X");
    });

    it("re-reads the board list after a 5xx", async () => {
      const { callbacks } = setup({ hooks: { post: () => json(502, { detail: "bad gateway" }) } });
      await fillAndCreate();
      expect(await screen.findByRole("alert")).toHaveTextContent("The data was reloaded");
      expect(callbacks.onRefreshBoards).toHaveBeenCalledTimes(1);
    });

    it("keeps the original error and does not claim a reload when the refresh fails", async () => {
      const { callbacks } = setup({ refresh: async () => false, hooks: { post: () => json(500) } });
      await fillAndCreate();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Server error - create the board may not have been saved.");
      expect(alert).toHaveTextContent("could not be reloaded either");
      expect(alert.textContent).not.toMatch(/data was reloaded/);
      expect(callbacks.onRefreshBoards).toHaveBeenCalledTimes(1);
    });

    it("does not refresh the list for a definite refusal (403, 422)", async () => {
      for (const status of [403, 422]) {
        const { callbacks, unmount } = setup({ hooks: { post: () => json(status) } });
        await fillAndCreate();
        await screen.findByRole("alert");
        expect(callbacks.onRefreshBoards).not.toHaveBeenCalled();
        unmount();
      }
    });
  });

  describe("columns", () => {
    it("renders the columns in the server's position order", async () => {
      setup({ columns: [col(31, "Proposal", 1), col(30, "1st Contact", 0), col(32, "Award", 2, 0, "won")] });
      await openPanel();
      await screen.findByText("Award");
      expect(names()).toEqual(["1st Contact", "Proposal", "Award"]);
    });

    it("shows loading, then an empty message for a board without columns", async () => {
      setup({ columns: [] });
      await openPanel();
      expect(await screen.findByText("This board has no columns yet.")).toBeInTheDocument();
    });

    it("reports a load failure and retries on request", async () => {
      let fail = true;
      const { calls } = setup();
      const original = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
      vi.stubGlobal("fetch", vi.fn(async (u: string, i?: RequestInit) => {
        if (fail && !String(u).endsWith("/me") && (i?.method ?? "GET") === "GET") return json(503, { detail: "x" });
        return original(u, i);
      }));
      await openPanel();
      expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
      fail = false;
      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByText("Proposal")).toBeInTheDocument();
      expect(calls.length).toBeGreaterThan(0);
    });

    it("creates a column at the end and shows the server's list afterwards", async () => {
      const { calls, setColumns, callbacks } = setup({
        hooks: { post: () => { setColumns([col(30, "1st Contact", 0), col(31, "Proposal", 1), col(32, "Award", 2, 0, "won"), col(33, "Lead Gen", 3)]); return json(201, col(33, "Lead Gen", 3)); } },
      });
      await openPanel();
      await screen.findByText("Proposal");
      await userEvent.type(screen.getByLabelText("New column title"), "Lead Gen");
      await userEvent.click(screen.getByRole("button", { name: "Add column" }));
      expect(await screen.findByText("Lead Gen")).toBeInTheDocument();
      expect(writes(calls, "POST")[0].body).toEqual({ title: "Lead Gen", position: 3, stage_kind: "open" });
      expect(callbacks.onColumnsChanged).toHaveBeenCalled();
      expect(screen.getByLabelText("New column title")).toHaveValue("");
    });

    it("edits a title and sends only what changed", async () => {
      const { calls, setColumns } = setup({
        hooks: { put: () => { setColumns([col(30, "Qualified", 0), col(31, "Proposal", 1), col(32, "Award", 2, 0, "won")]); return json(200, col(30, "Qualified", 0)); } },
      });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Edit column 1st Contact" }));
      const input = screen.getByLabelText("Column title");
      expect(input).toHaveFocus();
      await userEvent.clear(input);
      await userEvent.type(input, "Qualified");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(await screen.findByText("Qualified")).toBeInTheDocument();
      expect(writes(calls, "PUT")[0].body).toEqual({ title: "Qualified" });
    });

    it("reorders with the buttons and shows the order the server returns, not the requested one", async () => {
      const { calls, setColumns } = setup({
        hooks: { put: () => { setColumns([col(31, "Proposal", 0), col(30, "1st Contact", 1), col(32, "Award", 2, 0, "won")]); return json(200, col(30, "1st Contact", 1)); } },
      });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Move 1st Contact down" }));
      await waitFor(() => expect(names()).toEqual(["Proposal", "1st Contact", "Award"]));
      expect(writes(calls, "PUT")[0].body).toEqual({ position: 1 });
      expect(screen.getByRole("button", { name: "Move Proposal up" })).toBeDisabled();
    });

    it("leaves no stale state when a reorder fails, and re-reads the server", async () => {
      const { calls } = setup({ hooks: { put: () => json(500, { detail: "traceback ..." }) } });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Move 1st Contact down" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Server error");
      expect(names()).toEqual(["1st Contact", "Proposal", "Award"]);
      await waitFor(() => expect(calls.filter((c) => c.method === "GET" && !c.url.endsWith("/me")).length).toBeGreaterThan(1));
    });

    it("blocks a second submission while a request is pending", async () => {
      let release: (r: Response) => void = () => {};
      const { calls } = setup({ hooks: { put: () => new Promise<Response>((r) => { release = r; }) } });
      await openPanel();
      const down = await screen.findByRole("button", { name: "Move 1st Contact down" });
      await userEvent.click(down);
      await waitFor(() => expect(down).toBeDisabled());
      await userEvent.click(down);
      expect(writes(calls, "PUT")).toHaveLength(1);
      release(json(200, col(30, "1st Contact", 1)));
      await waitFor(() => expect(down).toBeEnabled());
    });

    it("shows a conflict message from the server for an invalid stage kind change", async () => {
      setup({ hooks: { put: () => json(409, { detail: 'This board already has a "won" stage' }) } });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Edit column Proposal" }));
      await userEvent.selectOptions(screen.getByLabelText("Stage kind"), "won");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(await screen.findByRole("alert")).toHaveTextContent('already has a "won" stage');
      expect(screen.getByLabelText("Column title")).toBeInTheDocument(); // the form stays open
    });
  });

  describe("column request failures", () => {
    const tryMove = async () => userEvent.click(await screen.findByRole("button", { name: "Move 1st Contact down" }));
    const reads = (calls: Call[]) => calls.filter((c) => c.method === "GET" && !c.url.endsWith("/me")).length;

    it.each([
      [401, "Your session expired.", false],
      [403, "not allowed to move the column", false],
      [404, "not found", true],
      [422, "values were not accepted", false],
      [503, "temporarily unavailable", true],
    ])("HTTP %i is explained in plain language (re-read: %s)", async (status, text, rereads) => {
      const { calls } = setup({ hooks: { put: () => json(status, { detail: "internal /var/x trace" }) } });
      await openPanel();
      await screen.findByText("Proposal");
      const before = reads(calls);
      await tryMove();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(text);
      expect(alert.textContent).not.toMatch(/internal|\/var/);
      expect(names()).toEqual(["1st Contact", "Proposal", "Award"]);
      if (rereads) await waitFor(() => expect(reads(calls)).toBeGreaterThan(before));
      else expect(reads(calls)).toBe(before);
    });

    it("a 5xx is reported as uncertain, re-reads, and is not retried", async () => {
      const { calls } = setup({ hooks: { put: () => json(500) } });
      await openPanel();
      await screen.findByText("Proposal");
      const before = reads(calls);
      await tryMove();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Server error");
      expect(alert).toHaveTextContent("The data was reloaded");
      expect(reads(calls)).toBeGreaterThan(before);
      expect(writes(calls, "PUT")).toHaveLength(1);
    });

    it("a network failure is reported as uncertain, re-reads, and is not retried", async () => {
      const { calls } = setup({ hooks: { put: () => { throw new TypeError("down"); } } });
      await openPanel();
      await screen.findByText("Proposal");
      const before = reads(calls);
      await tryMove();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Network error");
      expect(alert).toHaveTextContent("may not have been saved");
      expect(reads(calls)).toBeGreaterThan(before);
      expect(writes(calls, "PUT")).toHaveLength(1);
    });

    it("does not claim a reload when the re-read after a failure also fails", async () => {
      const { calls } = setup({ hooks: { put: () => json(500) } });
      await openPanel();
      await screen.findByText("Proposal");
      const original = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
      vi.stubGlobal("fetch", vi.fn(async (u: string, i?: RequestInit) =>
        (i?.method ?? "GET") === "GET" && !String(u).endsWith("/me") ? json(503) : original(u, i)));
      await tryMove();
      const alert = await screen.findByTestId("admin-notice");
      expect(alert).toHaveTextContent("could not be reloaded either");
      expect(alert.textContent).not.toMatch(/data was reloaded/);
      expect(calls.length).toBeGreaterThan(0);
    });
  });

  describe("who sees the panel", () => {
    it.each([
      ["visitor", { readOnly: true, canManageBoards: false }],
      ["sales_member", { canManageBoards: false }],
      ["admin", { readOnly: true, canManageBoards: true }],
      ["mystery_role", { canManageBoards: false }],
    ])("shows nothing to %s", async (role, caps) => {
      const { calls } = setup({ role, caps });
      await waitFor(() => expect(calls.some((c) => c.url.endsWith("/me"))).toBe(true));
      await new Promise((r) => setTimeout(r, 30));
      expect(screen.queryByTestId("board-admin")).toBeNull();
      expect(calls.filter((c) => c.method !== "GET" || !c.url.endsWith("/me"))).toHaveLength(0);
    });

    it("lets a company admin create and edit columns, with no delete control anywhere", async () => {
      const { calls } = setup({ role: "admin" });
      await openPanel();
      await userEvent.type(await screen.findByLabelText("New column title"), "Lead Gen");
      await userEvent.click(screen.getByRole("button", { name: "Add column" }));
      await waitFor(() => expect(writes(calls, "POST")).toHaveLength(1));
      await userEvent.click(await screen.findByRole("button", { name: "Edit column Proposal" }));
      await userEvent.clear(screen.getByLabelText("Column title"));
      await userEvent.type(screen.getByLabelText("Column title"), "Quote");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(writes(calls, "PUT")).toHaveLength(1));
      expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
    });
  });

  describe("column deletion", () => {
    it("is offered to a super admin only", async () => {
      setup();
      await openPanel();
      expect(await screen.findByRole("button", { name: "Delete column Proposal" })).toBeInTheDocument();
    });

    it("is not offered to a company admin even though canManageBoards is true", async () => {
      setup({ role: "admin", caps: { canManageBoards: true } });
      await openPanel();
      await screen.findByText("Proposal");
      expect(screen.getByRole("button", { name: "Add column" })).toBeInTheDocument(); // management itself is available
      expect(screen.queryByRole("button", { name: /^Delete column/ })).toBeNull();
    });

    it("asks for confirmation, names the column and explains it must be empty; cancel sends nothing", async () => {
      const { calls } = setup();
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Proposal" }));
      const dialog = screen.getByRole("alertdialog");
      expect(dialog).toHaveTextContent('Delete the column "Proposal"?');
      expect(dialog).toHaveTextContent(/must be empty/);
      expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
      await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(writes(calls, "DELETE")).toHaveLength(0);
      expect(screen.getByRole("button", { name: "Delete column Proposal" })).toHaveFocus();
    });

    it("removes the column after a confirmed delete and shows the refreshed order", async () => {
      const { calls, setColumns, callbacks } = setup({
        hooks: { del: () => { setColumns([col(30, "1st Contact", 0), col(32, "Award", 1, 0, "won")]); return new Response(null, { status: 204 }); } },
      });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Proposal" }));
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      await waitFor(() => expect(names()).toEqual(["1st Contact", "Award"]));
      expect(writes(calls, "DELETE")[0].url).toContain("/boards/3/columns/31");
      expect(screen.getByRole("status")).toHaveTextContent('Deleted column "Proposal".');
      expect(callbacks.onColumnsChanged).toHaveBeenCalled();
    });

    it("keeps the column on 409, explains why and reloads", async () => {
      const { calls } = setup({ hooks: { del: () => json(409, { detail: "Column cannot be deleted while it contains cards." }) } });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Proposal" }));
      const before = calls.filter((c) => c.method === "GET").length;
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent('"Proposal" contains cards and cannot be deleted. Move the cards to another column first.');
      expect(screen.getByText("Proposal")).toBeInTheDocument();
      expect(writes(calls, "DELETE")).toHaveLength(1); // never retried
      await waitFor(() => expect(calls.filter((c) => c.method === "GET").length).toBeGreaterThan(before));
    });

    it("keeps the column on 403", async () => {
      setup({ hooks: { del: () => json(403, { detail: "Access denied" }) } });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Proposal" }));
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("not allowed to delete columns");
      expect(names()).toEqual(["1st Contact", "Proposal", "Award"]);
    });

    it("sends a single request for a double click on confirm", async () => {
      let release: (r: Response) => void = () => {};
      const { calls } = setup({ hooks: { del: () => new Promise<Response>((r) => { release = r; }) } });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Proposal" }));
      const confirm = screen.getByRole("button", { name: "Confirm delete" });
      await userEvent.dblClick(confirm);
      expect(writes(calls, "DELETE")).toHaveLength(1);
      release(new Response(null, { status: 204 }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    });

    it("closes on Escape without deleting and returns focus to the trigger", async () => {
      const { calls } = setup();
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Proposal" }));
      expect(screen.getByRole("alertdialog")).toHaveAttribute("aria-modal", "true");
      await userEvent.keyboard("{Escape}");
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(writes(calls, "DELETE")).toHaveLength(0);
      expect(screen.getByRole("button", { name: "Delete column Proposal" })).toHaveFocus();
    });

    it("keeps Tab inside the dialog", async () => {
      setup();
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Proposal" }));
      const dialog = screen.getByRole("alertdialog");
      await userEvent.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
      await userEvent.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
      await userEvent.tab({ shift: true });
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    });

    it("after a successful delete focus moves to the column list, not to the removed button or the page", async () => {
      const { setColumns } = setup({
        hooks: { del: () => { setColumns([col(30, "1st Contact", 0), col(32, "Award", 1, 0, "won")]); return new Response(null, { status: 204 }); } },
      });
      await openPanel();
      const trigger = await screen.findByRole("button", { name: "Delete column Proposal" });
      await userEvent.click(trigger);
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      await waitFor(() => expect(names()).toEqual(["1st Contact", "Award"]));
      expect(trigger).not.toBeInTheDocument();
      await waitFor(() => expect(screen.getByRole("list", { name: "Columns in order" })).toHaveFocus());
    });

    it("after deleting the last column focus moves to the new-column field", async () => {
      const { setColumns } = setup({
        columns: [col(30, "Only", 0)],
        hooks: { del: () => { setColumns([]); return new Response(null, { status: 204 }); } },
      });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Only" }));
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      await waitFor(() => expect(screen.getByLabelText("New column title")).toHaveFocus());
    });

    it("keeps focus on the trigger after a failed delete", async () => {
      setup({ hooks: { del: () => json(409) } });
      await openPanel();
      await userEvent.click(await screen.findByRole("button", { name: "Delete column Proposal" }));
      await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
      await screen.findByRole("alert");
      expect(screen.getByRole("button", { name: "Delete column Proposal" })).toHaveFocus();
    });
  });
});
