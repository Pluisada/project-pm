import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralApp } from "@/components/CentralApp";
import { getActiveCompany, resetActiveCompanyForTests } from "@/lib/activeCompany";
import { resetWriteOutcomesForTests } from "@/lib/writeOutcomes";

// The Kanban workspace as the central shell shows it: the company's board, reads and writes through
// /api/companies/{slug}/..., with the two companies deliberately using the SAME board and card ids.

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
type Held = { match: RegExp; method: string; release: (r: Response | Error) => void; signal?: AbortSignal | null };

const caps = (over = {}) => ({ readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: true, canManageBoards: true, canImport: true, ...over });
const card = (id: number, columnId: number, title: string) => ({ id, column_id: columnId, title, position: 0, company: "ACME",
  contact: null, deal_value: null, deal_value_next_year: 0, deal_year: 2026, salesperson_id: 7, salesperson_name: "Ana", details: null });
const boardDetail = (slug: string, id: number, title: string, cardTitle: string) => ({
  id, user_id: 1, title, created_at: "", updated_at: "",
  columns: [
    { id: 30, board_id: id, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "", cards: [card(100, 30, cardTitle)] },
    { id: 31, board_id: id, title: "Award", position: 1, stage_kind: "won", created_at: "", updated_at: "", cards: [] },
  ],
});

interface World {
  boards: Record<string, { id: number; title: string; cardTitle: string }[]>;
  role?: string;
  me?: Record<string, ReturnType<typeof caps>>;
  undo?: { available: boolean; description: string | null };
}

function setup(world: World) {
  const holds: Held[] = [];
  const calls: { url: string; method: string; headers: Record<string, string> }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? "GET";
    calls.push({ url: u, method, headers: (init?.headers ?? {}) as Record<string, string> });
    const hold = holds.find((h) => h.method === method && h.match.test(u));
    if (hold) {
      holds.splice(holds.indexOf(hold), 1);
      return new Promise<Response>((resolve, reject) => {
        hold.signal = init?.signal;
        hold.release = (r) => (r instanceof Error ? reject(r) : resolve(r));
        holds.push(hold); // keep it addressable for the test
      }) as Promise<Response>;
    }
    if (/\/api\/central\/companies\/[^/]+\/(members|invitations)$/.test(u)) return json(200, []);
    if (u.endsWith("/api/central/me")) {
      return json(200, { email: "a@b.com", is_global_admin: false,
        companies: Object.keys(world.boards).map((slug) => ({ slug, name: slug[0].toUpperCase() + slug.slice(1), role: world.role ?? "super_admin" })) });
    }
    const m = u.match(/\/api\/companies\/([^/]+)(\/.*)?$/);
    if (m) {
      const [, slug, rest = ""] = m;
      const boards = world.boards[slug] ?? [];
      if (rest === "/me") return json(200, { user_id: 7, company_slug: slug, role: world.role ?? "super_admin", capabilities: (world.me ?? {})[slug] ?? caps() });
      if (rest === "/boards" && method === "GET") return json(200, boards.map((b) => ({ id: b.id, user_id: 1, title: b.title, created_at: "", updated_at: "" })));
      const detail = rest.match(/^\/boards\/(\d+)$/);
      if (detail && method === "GET") {
        const b = boards.find((x) => x.id === Number(detail[1]));
        return b ? json(200, boardDetail(slug, b.id, b.title, b.cardTitle)) : json(404, { detail: "Board not found" });
      }
      if (rest.endsWith("/undo") && method === "GET") return json(200, world.undo ?? { available: false, description: null });
    }
    return json(404, { detail: "not mocked" });
  });
  vi.stubGlobal("fetch", fetchMock);
  const hold = (method: string, match: RegExp): Held => {
    const h: Held = { match, method, release: () => {} };
    holds.push(h);
    return h;
  };
  return { fetchMock, calls, hold };
}

const pickCompany = async (name: string) => userEvent.click(await screen.findByRole("button", { name: new RegExp(name) }));
const switchCompany = async (name: string) => {
  await userEvent.click(await screen.findByRole("button", { name: "Switch company" }));
  await pickCompany(name);
};
const signedIn = () => {
  localStorage.setItem("pm_auth_token", "tok");
  localStorage.setItem("pm_auth_user", "a@b.com");
};
const sameIds = {
  salcomp: [{ id: 3, title: "Salcomp pipeline", cardTitle: "Salcomp lead" }, { id: 4, title: "Salcomp second", cardTitle: "Salcomp other" }],
  lity: [{ id: 3, title: "Lity pipeline", cardTitle: "Lity lead" }, { id: 4, title: "Lity second", cardTitle: "Lity other" }],
};

describe("the Kanban workspace inside the central shell", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    resetActiveCompanyForTests();
    resetWriteOutcomesForTests();
  });

  it("shows the chosen company's board with every request bound to that company", async () => {
    signedIn();
    const { calls } = setup({ boards: sameIds });
    render(<CentralApp />);
    await pickCompany("Lity");
    expect(await screen.findByText("Lity lead")).toBeInTheDocument();
    const board = screen.getByTestId("central-board");
    expect(within(board).getByText("1st Contact")).toBeInTheDocument();
    expect(within(board).getByText("Award")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Lity" })).toBeInTheDocument(); // header and branding stay
    const company = calls.filter((c) => c.url.includes("/api/companies/"));
    expect(company.length).toBeGreaterThan(0);
    for (const c of company) {
      expect(c.url).toMatch(/\/api\/companies\/lity\//);
      expect(c.headers["X-Company-Slug"]).toBe("lity");
    }
    expect(calls.some((c) => /\/api\/(boards|lead-options|products)/.test(c.url))).toBe(false); // nothing legacy
  });

  it("keeps the company picker until a company is chosen and mounts no workspace before that", async () => {
    signedIn();
    const { calls } = setup({ boards: sameIds });
    render(<CentralApp />);
    expect(await screen.findByRole("heading", { name: "Choose a company" })).toBeInTheDocument();
    expect(screen.queryByTestId("central-board")).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/api/companies/"))).toBe(false);
  });

  it("never reuses one company's board state for another, even with identical board and card ids", async () => {
    signedIn();
    setup({ boards: sameIds });
    render(<CentralApp />);
    await pickCompany("Salcomp");
    expect(await screen.findByText("Salcomp lead")).toBeInTheDocument();
    await switchCompany("Lity");
    expect(screen.queryByText("Salcomp lead")).not.toBeInTheDocument(); // not even while Lity loads
    expect(await screen.findByText("Lity lead")).toBeInTheDocument();
    expect(screen.queryByText("Salcomp lead")).not.toBeInTheDocument();
    await switchCompany("Salcomp");
    expect(await screen.findByText("Salcomp lead")).toBeInTheDocument();
    expect(screen.queryByText("Lity lead")).not.toBeInTheDocument();
  });

  it("does not carry a picked board over to the other company", async () => {
    signedIn();
    setup({ boards: sameIds });
    render(<CentralApp />);
    await pickCompany("Salcomp");
    await screen.findByText("Salcomp lead");
    await userEvent.selectOptions(screen.getByLabelText("Board"), "4");
    expect(await screen.findByText("Salcomp other")).toBeInTheDocument();
    await switchCompany("Lity");
    // Board 4 exists in Lity too, but it was never picked there: Lity opens on its own first board.
    expect(await screen.findByText("Lity lead")).toBeInTheDocument();
    expect(screen.queryByText("Lity other")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Board")).toHaveValue("3");
  });

  describe("a switch while reads are pending", () => {
    it("lets a late board answer from the previous company change nothing, and aborts that read", async () => {
      signedIn();
      const { hold } = setup({ boards: sameIds });
      const slow = hold("GET", /\/api\/companies\/salcomp\/boards\/3$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await waitFor(() => expect(slow.signal).toBeDefined());
      await switchCompany("Lity");
      expect(await screen.findByText("Lity lead")).toBeInTheDocument();
      expect(slow.signal?.aborted).toBe(true); // the read of the company just left is cancelled
      // Even a network that ignores the abort cannot put Salcomp's board on Lity's screen.
      await act(async () => slow.release(json(200, boardDetail("salcomp", 3, "Salcomp pipeline", "Salcomp LATE"))));
      expect(screen.queryByText("Salcomp LATE")).not.toBeInTheDocument();
      expect(screen.getByText("Lity lead")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("lets a late board LIST from the previous company neither show nor select anything", async () => {
      signedIn();
      const { hold } = setup({ boards: sameIds });
      const slow = hold("GET", /\/api\/companies\/salcomp\/boards$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await waitFor(() => expect(slow.signal).toBeDefined());
      await switchCompany("Lity");
      expect(await screen.findByText("Lity lead")).toBeInTheDocument();
      await act(async () => slow.release(json(200, [{ id: 4, user_id: 1, title: "Salcomp LATE LIST", created_at: "", updated_at: "" }])));
      expect(screen.queryByText("Salcomp LATE LIST")).not.toBeInTheDocument();
      expect(screen.getByLabelText("Board")).toHaveValue("3");
      expect(getActiveCompany()).toBe("lity");
    });

    it("never turns a late failure of the previous company into an error on the new one", async () => {
      signedIn();
      const { hold } = setup({ boards: sameIds });
      const slow = hold("GET", /\/api\/companies\/salcomp\/boards\/3$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await waitFor(() => expect(slow.signal).toBeDefined());
      await switchCompany("Lity");
      await screen.findByText("Lity lead");
      await act(async () => slow.release(json(500, { detail: "salcomp is down" })));
      expect(screen.queryByText(/salcomp is down/)).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  describe("a switch while a write is under way", () => {
    const openAdmin = async () => userEvent.click(await screen.findByRole("button", { name: "Manage boards and columns" }));
    const startBoardCreation = async (title: string) => {
      await openAdmin();
      await userEvent.type(screen.getByLabelText("New board title"), title);
      await userEvent.click(screen.getByRole("button", { name: "Create board" }));
    };

    it("lets the write finish, says so on the new company's screen, and selects nothing there", async () => {
      signedIn();
      const { hold, calls } = setup({ boards: sameIds });
      const create = hold("POST", /\/api\/companies\/salcomp\/boards$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await screen.findByText("Salcomp lead");
      await startBoardCreation("Fresh");
      await waitFor(() => expect(create.signal).toBeDefined());
      await switchCompany("Lity");
      expect(await screen.findByText("Lity lead")).toBeInTheDocument();
      expect(create.signal?.aborted).toBe(false); // a write is never aborted by a switch
      await act(async () => create.release(json(201, { id: 4, user_id: 1, title: "Fresh", created_at: "", updated_at: "" })));
      const notice = await screen.findByText(/"Create board" in Salcomp finished saving after you left it/);
      expect(notice).toBeInTheDocument();
      // The new board's id (4) means nothing in Lity: Lity still shows its own first board.
      expect(screen.getByText("Lity lead")).toBeInTheDocument();
      expect(screen.getByLabelText("Board")).toHaveValue("3");
      expect(calls.filter((c) => c.method === "POST").every((c) => /\/api\/companies\/salcomp\//.test(c.url))).toBe(true);
      await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      expect(screen.queryByText(/finished saving/)).not.toBeInTheDocument();
    });

    it("reports a refused write as not saved, with the server's reason", async () => {
      signedIn();
      const { hold } = setup({ boards: sameIds });
      const create = hold("POST", /\/api\/companies\/salcomp\/boards$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await screen.findByText("Salcomp lead");
      await startBoardCreation("Fresh");
      await waitFor(() => expect(create.signal).toBeDefined());
      await switchCompany("Lity");
      await screen.findByText("Lity lead");
      await act(async () => create.release(json(403, { detail: "Access denied" })));
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent('"Create board" in Salcomp was refused by the server: Access denied.');
      expect(alert).not.toHaveTextContent(/Nothing changed|was not saved/);
    });

    it("says the outcome is unknown after a network error, and tells the person to check before repeating", async () => {
      signedIn();
      const { hold } = setup({ boards: sameIds });
      const create = hold("POST", /\/api\/companies\/salcomp\/boards$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await screen.findByText("Salcomp lead");
      await startBoardCreation("Fresh");
      await waitFor(() => expect(create.signal).toBeDefined());
      await switchCompany("Lity");
      await screen.findByText("Lity lead");
      await act(async () => create.release(new TypeError("network down")));
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent('"Create board" in Salcomp may or may not have been saved');
      expect(alert).toHaveTextContent("Check Salcomp before repeating it");
    });

    it("shows the outcome on the company picker too, and forgets it at sign-out", async () => {
      signedIn();
      const { hold } = setup({ boards: sameIds });
      const create = hold("POST", /\/api\/companies\/salcomp\/boards$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await screen.findByText("Salcomp lead");
      await startBoardCreation("Fresh");
      await waitFor(() => expect(create.signal).toBeDefined());
      await userEvent.click(screen.getByRole("button", { name: "Switch company" })); // stay on the picker
      await screen.findByRole("heading", { name: "Choose a company" });
      await act(async () => create.release(json(201, { id: 4, user_id: 1, title: "Fresh", created_at: "", updated_at: "" })));
      expect(await screen.findByText(/"Create board" in Salcomp finished saving/)).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
      expect(screen.queryByText(/finished saving/)).not.toBeInTheDocument();
    });

    it("does not show a write of a signed-out session to whoever signs in next", async () => {
      signedIn();
      const { hold } = setup({ boards: sameIds });
      const create = hold("POST", /\/api\/companies\/salcomp\/boards$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await screen.findByText("Salcomp lead");
      await startBoardCreation("Fresh");
      await waitFor(() => expect(create.signal).toBeDefined());
      await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
      await act(async () => create.release(json(201, { id: 4, user_id: 1, title: "Fresh", created_at: "", updated_at: "" })));
      expect(screen.queryByText(/finished saving/)).not.toBeInTheDocument();
    });
  });

  describe("a multi-step operation (undo, then re-read the board)", () => {
    it("keeps its steps in the company it started in: after a switch the re-read is never sent anywhere", async () => {
      signedIn();
      const { hold, calls } = setup({ boards: sameIds, undo: { available: true, description: "Moved a card" } });
      const undo = hold("POST", /\/api\/companies\/salcomp\/boards\/3\/undo$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await userEvent.click(await screen.findByTestId("undo-button"));
      await waitFor(() => expect(undo.signal).toBeDefined());
      await switchCompany("Lity");
      expect(await screen.findByText("Lity lead")).toBeInTheDocument();
      const sentBefore = calls.length;
      await act(async () => undo.release(json(200, { undone: "Moved a card" })));
      const notice = await screen.findByText(/"Undo" in Salcomp finished saving after you left it/);
      expect(notice).toBeInTheDocument();
      // The second step (reading Salcomp's board again) belongs to Salcomp, which is no longer on screen: it is not sent,
      // not to Salcomp and above all not to Lity with Salcomp's ids.
      const later = calls.slice(sentBefore).filter((c) => c.method === "GET" && /\/api\/companies\//.test(c.url));
      expect(later.filter((c) => /salcomp/.test(c.url))).toEqual([]);
      expect(screen.getByText("Lity lead")).toBeInTheDocument();
    });

    it("keeps an undo that the server refused as newer work visible as an error, not as success", async () => {
      signedIn();
      const { hold } = setup({ boards: sameIds, undo: { available: true, description: "Moved a card" } });
      const undo = hold("POST", /\/api\/companies\/salcomp\/boards\/3\/undo$/);
      render(<CentralApp />);
      await pickCompany("Salcomp");
      await userEvent.click(await screen.findByTestId("undo-button"));
      await waitFor(() => expect(undo.signal).toBeDefined());
      await act(async () => undo.release(json(409, { detail: "The card was changed by someone else; it can no longer be undone." })));
      expect(await screen.findByTestId("undo-notice")).toHaveTextContent("can no longer be undone");
      expect(screen.getByTestId("undo-notice")).toHaveAttribute("role", "alert");
    });
  });

  describe("a company with no board", () => {
    it("shows an explicit empty state and creates nothing by itself", async () => {
      signedIn();
      const { calls } = setup({ boards: { salcomp: [], lity: sameIds.lity } });
      render(<CentralApp />);
      await pickCompany("Salcomp");
      const empty = await screen.findByTestId("central-empty-workspace");
      expect(empty).toHaveTextContent("No board yet");
      expect(empty).toHaveTextContent("Nothing is created automatically");
      expect(screen.queryByTestId("central-board")).not.toBeInTheDocument();
      await new Promise((r) => setTimeout(r, 30));
      expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
    });

    it("tells a Sales Member to ask an administrator, and offers no way to create one", async () => {
      signedIn();
      const { calls } = setup({ boards: { salcomp: [] }, role: "sales_member", me: { salcomp: caps({ canManageBoards: false, canEditAnyCard: false, canImport: false }) } });
      render(<CentralApp />);
      const empty = await screen.findByTestId("central-empty-workspace");
      expect(empty).toHaveTextContent("An administrator has to create one");
      expect(screen.queryByRole("button", { name: "Create board" })).not.toBeInTheDocument();
      expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
    });

    it("disappears once the company has a board", async () => {
      signedIn();
      setup({ boards: sameIds });
      render(<CentralApp />);
      await pickCompany("Lity");
      await screen.findByText("Lity lead");
      expect(screen.queryByTestId("central-empty-workspace")).not.toBeInTheDocument();
    });
  });
});
