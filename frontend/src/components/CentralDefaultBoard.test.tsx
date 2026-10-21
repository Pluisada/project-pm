import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralApp } from "@/components/CentralApp";
import { resetActiveCompanyForTests } from "@/lib/activeCompany";
import { resetWriteOutcomesForTests } from "@/lib/writeOutcomes";

// The "Create default board" action of a company with no board: explicit, single-flight, company-scoped.

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const caps = (over = {}) => ({ readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: true, canManageBoards: true, canImport: true, ...over });
const board = (id: number, title = "Sales Pipeline") => ({ id, user_id: 1, title, description: null, created_at: "", updated_at: "" });
const detail = (id: number, title: string) => ({ ...board(id, title), columns: [
  { id: 30, board_id: id, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "", cards: [] }] });

type Post = { release: (r: Response | Error) => void };

function setup(opts: { role?: string; me?: Record<string, ReturnType<typeof caps>>; lists?: Record<string, ReturnType<typeof board>[]> } = {}) {
  const lists: Record<string, ReturnType<typeof board>[]> = opts.lists ?? { salcomp: [] };
  const posts: { url: string; post: Post }[] = [];
  const calls: { url: string; method: string }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? "GET";
    calls.push({ url: u, method });
    if (u.endsWith("/api/central/me")) {
      return json(200, { email: "a@b.com", is_global_admin: false,
        companies: Object.keys(lists).map((slug) => ({ slug, name: slug[0].toUpperCase() + slug.slice(1), role: opts.role ?? "super_admin" })) });
    }
    const m = u.match(/\/api\/companies\/([^/]+)(\/.*)?$/);
    if (!m) return json(404, { detail: "not mocked" });
    const [, slug, rest = ""] = m;
    if (rest === "/me") return json(200, { user_id: 7, company_slug: slug, role: opts.role ?? "super_admin", capabilities: (opts.me ?? {})[slug] ?? caps() });
    if (rest === "/boards" && method === "GET") return json(200, lists[slug]);
    if (rest === "/boards/default" && method === "POST") {
      return new Promise<Response>((resolve, reject) => {
        posts.push({ url: u, post: { release: (r) => (r instanceof Error ? reject(r) : resolve(r)) } });
      });
    }
    const d = rest.match(/^\/boards\/(\d+)$/);
    if (d && method === "GET") {
      const b = lists[slug].find((x) => x.id === Number(d[1]));
      return b ? json(200, detail(b.id, b.title)) : json(404, { detail: "Board not found" });
    }
    if (rest.endsWith("/undo")) return json(200, { available: false, description: null });
    return json(404, { detail: "not mocked" });
  });
  vi.stubGlobal("fetch", fetchMock);
  const creates = () => calls.filter((c) => c.method === "POST" && /\/boards\/default$/.test(c.url));
  return { lists, posts, calls, creates };
}

const signedIn = () => {
  localStorage.setItem("pm_auth_token", "tok");
  localStorage.setItem("pm_auth_user", "a@b.com");
};
const pickCompany = async (name: string) => userEvent.click(await screen.findByRole("button", { name: new RegExp(name) }));
const createButton = () => screen.findByRole("button", { name: "Create default board" });

describe("create default board", () => {
  beforeEach(() => {
    localStorage.clear();
    resetActiveCompanyForTests();
    resetWriteOutcomesForTests();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("is offered to a caller whose capabilities allow board management, and not on mount", async () => {
    signedIn();
    const { creates } = setup();
    render(<CentralApp />);
    expect(await createButton()).toBeEnabled();
    await new Promise((r) => setTimeout(r, 30));
    expect(creates()).toEqual([]);
  });

  it.each([
    ["a Sales Member", "sales_member", caps({ canManageBoards: false })],
    ["a read-only Visitor", "visitor", caps({ readOnly: true, canManageBoards: false })],
    ["a super_admin role string without the capability", "super_admin", caps({ canManageBoards: false })],
  ])("is absent for %s", async (_label, role, c) => {
    signedIn();
    const { creates } = setup({ role, me: { salcomp: c } });
    render(<CentralApp />);
    await screen.findByTestId("central-empty-workspace");
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("button", { name: "Create default board" })).not.toBeInTheDocument();
    expect(creates()).toEqual([]);
  });

  it("sends one company-scoped POST, then selects and opens the returned board", async () => {
    signedIn();
    const { lists, posts, creates } = setup({ lists: { salcomp: [] } });
    render(<CentralApp />);
    await userEvent.click(await createButton());
    expect(creates()).toEqual([{ url: expect.stringMatching(/\/api\/companies\/salcomp\/boards\/default$/), method: "POST" }]);
    lists.salcomp.push(board(5));
    await act(async () => posts[0].post.release(json(201, { ...board(5), created: true })));
    expect(await screen.findByTestId("central-board")).toBeInTheDocument();
    expect(screen.queryByTestId("central-empty-workspace")).not.toBeInTheDocument();
  });

  it("handles an existing board (200, created=false) without duplicating it", async () => {
    signedIn();
    const { lists, posts } = setup({ lists: { salcomp: [] } });
    render(<CentralApp />);
    await userEvent.click(await createButton());
    lists.salcomp.push(board(2, "Existing"));
    await act(async () => posts[0].post.release(json(200, { ...board(2, "Existing"), created: false })));
    expect(await screen.findByTestId("central-board")).toBeInTheDocument();
    expect(screen.getByText(/already had a board/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Board")).not.toBeInTheDocument(); // one board, not two entries
  });

  it("sends a single request for a double click while one is pending", async () => {
    signedIn();
    const { posts, creates } = setup();
    render(<CentralApp />);
    const button = await createButton();
    await userEvent.dblClick(button);
    await userEvent.click(button);
    expect(creates()).toHaveLength(1);
    expect(button).toBeDisabled();
    await act(async () => posts[0].post.release(json(503, { detail: { code: "database_busy" } })));
  });

  it("explains a 403 as an authorization failure", async () => {
    signedIn();
    const { posts } = setup();
    render(<CentralApp />);
    await userEvent.click(await createButton());
    await act(async () => posts[0].post.release(json(403, { detail: "Access denied" })));
    expect(await screen.findByTestId("default-board-notice")).toHaveTextContent("not allowed to create the board");
    expect(screen.getByRole("button", { name: "Create default board" })).toBeEnabled();
  });

  it.each([
    ["database_busy (503)", () => json(503, { detail: { code: "database_busy", message: "busy" } })],
    ["a server error (500)", () => json(500, { detail: "boom" })],
    ["a network failure", () => new TypeError("Failed to fetch")],
  ])("re-reads the board list after %s before inviting a retry", async (_label, failure) => {
    signedIn();
    const { posts, calls } = setup();
    render(<CentralApp />);
    await userEvent.click(await createButton());
    const listsBefore = calls.filter((c) => c.method === "GET" && /\/salcomp\/boards$/.test(c.url)).length;
    await act(async () => posts[0].post.release(failure()));
    expect(await screen.findByTestId("default-board-notice")).toHaveTextContent("board list was reloaded; check it before trying again");
    expect(calls.filter((c) => c.method === "GET" && /\/salcomp\/boards$/.test(c.url)).length).toBe(listsBefore + 1);
    expect(screen.getByRole("button", { name: "Create default board" })).toBeEnabled();
  });

  it("never applies a pending result to the company the person switched to", async () => {
    signedIn();
    const { posts } = setup({ lists: { salcomp: [], lity: [] } });
    render(<CentralApp />);
    await pickCompany("Salcomp");
    await userEvent.click(await createButton());
    await userEvent.click(await screen.findByRole("button", { name: "Switch company" }));
    await pickCompany("Lity");
    await screen.findByTestId("central-empty-workspace");
    await act(async () => posts[0].post.release(json(201, { ...board(5), created: true })));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByTestId("central-empty-workspace")).toBeInTheDocument();
    expect(screen.queryByTestId("central-board")).not.toBeInTheDocument();
    expect(await createButton()).toBeEnabled();
  });

  it("does not create anything on a remount (refresh) of the empty state", async () => {
    signedIn();
    const { creates } = setup();
    const first = render(<CentralApp />);
    await createButton();
    first.unmount();
    render(<CentralApp />);
    await createButton();
    await waitFor(() => expect(creates()).toEqual([]));
  });
});
