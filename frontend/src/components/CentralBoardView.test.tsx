import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralBoardView } from "@/components/CentralBoardView";
import { getCompanyBoard, getCompanyMe } from "@/lib/api";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: false, canMoveBack: false, canDeleteCards: false,
  canAssignSalesperson: false, canManageBoards: false, canImport: false };
const meOf = (slug: string, readOnly = false) => ({ user_id: 7, company_slug: slug, role: "sales_member", capabilities: { ...caps, readOnly } });
const boardOf = (id: number, cardTitle: string) => ({
  id, user_id: 1, title: `Board ${id}`, created_at: "", updated_at: "",
  columns: [{ id: id * 10, board_id: id, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "",
    cards: [{ id: id * 100, column_id: id * 10, title: cardTitle, position: 0, company: "ACME", salesperson_name: "Ana" }] }],
});

const urlOf = (input: unknown) => String(input);

describe("central read layer", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("builds company-scoped URLs, sends the bearer token and passes the abort signal", async () => {
    const fetchMock = vi.fn(async (url: string) =>
      json(200, urlOf(url).endsWith("/me") ? meOf("a b") : boardOf(3, "x")));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await getCompanyMe("a b", controller.signal);
    await getCompanyBoard("a b", 3, controller.signal);
    const [[meUrl, meInit], [boardUrl, boardInit]] = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(meUrl).toMatch(/\/api\/companies\/a%20b\/me$/);
    expect(boardUrl).toMatch(/\/api\/companies\/a%20b\/boards\/3$/);
    for (const init of [meInit, boardInit]) {
      expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
      // The request carries a live signal (the caller's, combined with the active-company signal); that it follows
      // an abort while the request is under way is covered in lib/companyScope.test.ts and lib/central.test.ts.
      expect(init.signal).toBeInstanceOf(AbortSignal);
      expect(init.signal?.aborted).toBe(false);
    }
  });

  it("rejects an answer that belongs to another company or board", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      json(200, urlOf(url).endsWith("/me") ? meOf("other") : boardOf(9, "x"))));
    await expect(getCompanyMe("acme")).rejects.toMatchObject({ status_code: 0 });
    await expect(getCompanyBoard("acme", 3)).rejects.toMatchObject({ status_code: 0 });
  });
});

describe("CentralBoardView", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("shows the columns and cards of the selected company only, using central endpoints only", async () => {
    const fetchMock = vi.fn(async (url: string) =>
      json(200, urlOf(url).endsWith("/me") ? meOf("acme") : boardOf(3, "Lead A")));
    vi.stubGlobal("fetch", fetchMock);
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    expect(await screen.findByText("Lead A")).toBeInTheDocument();
    expect(screen.getByText("1 card")).toBeInTheDocument();
    // Nothing else was fetched or written: the two reads and the writer's undo status read. The undo status is
    // requested only once /me has made the viewer a writer, which can be after the card is on screen, so wait for it.
    await waitFor(() => expect(fetchMock.mock.calls).toHaveLength(3));
    for (const [url] of fetchMock.mock.calls) expect(urlOf(url)).toContain("/api/companies/acme/");
  });

  it("shows View only when the backend says read-only", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      json(200, urlOf(url).endsWith("/me") ? meOf("acme", true) : boardOf(3, "Lead A"))));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    expect(await screen.findByText("View only")).toBeInTheDocument();
  });

  it("never shows a late answer from the previous company after a switch", async () => {
    let releaseOld: () => void = () => {};
    const gate = new Promise<void>((resolve) => { releaseOld = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const isOld = urlOf(url).includes("/companies/old/");
      if (isOld) {
        await gate; // answers only after the switch, ignoring the abort like a slow network would
        return json(200, urlOf(url).endsWith("/me") ? meOf("old") : boardOf(1, "OLD CARD"));
      }
      void init;
      return json(200, urlOf(url).endsWith("/me") ? meOf("new") : boardOf(2, "NEW CARD"));
    }));
    const { rerender } = render(<CentralBoardView companySlug="old" boardId={1} />);
    rerender(<CentralBoardView companySlug="new" boardId={2} />);
    expect(await screen.findByText("NEW CARD")).toBeInTheDocument();
    releaseOld();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("OLD CARD")).not.toBeInTheDocument();
    expect(screen.getByText("NEW CARD")).toBeInTheDocument();
  });

  it("hides the old cards while the new company is loading", async () => {
    let releaseNew: () => void = () => {};
    const gate = new Promise<void>((resolve) => { releaseNew = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (urlOf(url).includes("/companies/new/")) await gate;
      const slug = urlOf(url).includes("/companies/new/") ? "new" : "old";
      return json(200, urlOf(url).endsWith("/me") ? meOf(slug) : boardOf(slug === "new" ? 2 : 1, slug === "new" ? "NEW CARD" : "OLD CARD"));
    }));
    const { rerender } = render(<CentralBoardView companySlug="old" boardId={1} />);
    expect(await screen.findByText("OLD CARD")).toBeInTheDocument();
    rerender(<CentralBoardView companySlug="new" boardId={2} />);
    expect(screen.queryByText("OLD CARD")).not.toBeInTheDocument();
    releaseNew();
    expect(await screen.findByText("NEW CARD")).toBeInTheDocument();
  });

  it("distinguishes network, 401 and 403 errors, and retries only the retryable ones", async () => {
    let mode: "net" | 401 | 403 | "ok" = "net";
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (mode === "net") throw new TypeError("failed");
      if (mode === 401) return json(401, { detail: "no" });
      if (mode === 403) return json(403, { detail: "no" });
      return json(200, urlOf(url).endsWith("/me") ? meOf("acme") : boardOf(3, "Lead A"));
    }));
    const { unmount } = render(<CentralBoardView companySlug="acme" boardId={3} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Network error");
    mode = "ok";
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Lead A")).toBeInTheDocument();
    unmount();

    mode = 403;
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("do not have access");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("explains a 401 as an expired session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "expired" })));
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("session expired"));
  });
});

describe("CentralBoardView kanban layout", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  const richBoard = {
    id: 3, user_id: 1, title: "Board 3", created_at: "", updated_at: "",
    columns: [
      { id: 31, board_id: 3, title: "Proposal", position: 1, stage_kind: "open", created_at: "", updated_at: "", cards: [
        { id: 2, column_id: 31, title: "Full lead", position: 0, company: "ACME", salesperson_name: "Ana", deal_value: 1000,
          deal_value_next_year: 500, deal_year: 2026, confidence_level: "HIGH", expected_close_date: "2026-12-31" },
      ] },
      { id: 30, board_id: 3, title: "1st Contact", position: 0, stage_kind: "open", created_at: "", updated_at: "", cards: [
        { id: 1, column_id: 30, title: "Bare lead", position: 0, company: null, salesperson_name: null, deal_value: null,
          confidence_level: null, expected_close_date: null },
      ] },
      { id: 32, board_id: 3, title: "Empty", position: 2, stage_kind: "won", created_at: "", updated_at: "" },
    ],
  };
  const stub = () => {
    const fetchMock = vi.fn(async (url: string) => json(200, urlOf(url).endsWith("/me") ? meOf("acme") : richBoard));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };

  it("orders columns, places cards in their columns and shows the available fields", async () => {
    stub();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Full lead");
    const columns = screen.getAllByTestId(/^central-column-/);
    expect(columns.map((c) => c.getAttribute("aria-label"))).toEqual(["1st Contact", "Proposal", "Empty"]);
    expect(within(columns[0]).getByText("Bare lead")).toBeInTheDocument();
    const full = within(columns[1]).getByTestId("central-card-2");
    expect(within(full).getByText("ACME")).toBeInTheDocument();
    expect(within(full).getByText("Ana")).toBeInTheDocument();
    expect(within(full).getByText("Deal value 2026")).toBeInTheDocument();
    expect(within(full).getByText(/1,000/)).toBeInTheDocument();
    expect(within(full).getByText("Next year 2027")).toBeInTheDocument();
    expect(within(full).getByText(/500/)).toBeInTheDocument();
    expect(within(full).getByTestId("confidence-badge")).toHaveAttribute("data-level", "HIGH");
    // The closing date is no longer part of the application: never shown on the compact card.
    expect(within(full).queryByText(/Close:/)).not.toBeInTheDocument();
    expect(full.textContent).not.toMatch(/Edit|History|Delete/);
    expect(within(columns[2]).getByText("0 cards")).toBeInTheDocument();
  });

  it("omits absent fields instead of inventing them", async () => {
    stub();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    const bare = await screen.findByTestId("central-card-1");
    expect(bare.textContent).toBe("Bare lead"); // the title only: every action lives in the details
    expect(within(bare).queryByTestId("confidence-badge")).not.toBeInTheDocument();
  });

  it("has no write controls, no drag handles and no legacy calls", async () => {
    const fetchMock = stub();
    render(<CentralBoardView companySlug="acme" boardId={3} />);
    await screen.findByText("Full lead");
    // Nothing else was fetched or written: the two reads and the writer's undo status read.
    expect(fetchMock.mock.calls).toHaveLength(3);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    for (const [url, init] of fetchMock.mock.calls as unknown as [string, RequestInit | undefined][]) {
      expect(urlOf(url)).toContain("/api/companies/acme/");
      expect(init?.method ?? "GET").toBe("GET");
    }
  });

  describe("refreshToken (structural change made elsewhere)", () => {
    it("re-reads the board in place without remounting, and keeps an open card form and what was typed", async () => {
      let board = boardOf(3, "Lead A");
      const fetchMock = vi.fn(async (url: string) => {
        if (urlOf(url).endsWith("/me")) return json(200, meOf("acme"));
        if (urlOf(url).endsWith("/undo")) return json(404);
        return json(200, board);
      });
      vi.stubGlobal("fetch", fetchMock);
      const { rerender } = render(<CentralBoardView companySlug="acme" boardId={3} refreshToken={0} />);
      await userEvent.click(await screen.findByRole("button", { name: "Add a card" }));
      await userEvent.type(screen.getByLabelText("Title"), "Half typed");
      const boardReads = () => fetchMock.mock.calls.filter(([u]) => urlOf(u).endsWith("/boards/3")).length;
      const meReads = () => fetchMock.mock.calls.filter(([u]) => urlOf(u).endsWith("/me")).length;
      expect(boardReads()).toBe(1);
      board = { ...board, columns: [{ ...board.columns[0], title: "Renamed" }] };
      rerender(<CentralBoardView companySlug="acme" boardId={3} refreshToken={1} />);
      expect(await screen.findByText("Renamed")).toBeInTheDocument();
      expect(boardReads()).toBe(2);
      expect(meReads()).toBe(1); // no remount: identity was not read again
      expect(screen.getByLabelText("Title")).toHaveValue("Half typed");
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.queryByText("Loading board...")).toBeNull();
    });

    it("does not read again when the token is unchanged", async () => {
      const fetchMock = vi.fn(async (url: string) =>
        urlOf(url).endsWith("/undo") ? json(404) : json(200, urlOf(url).endsWith("/me") ? meOf("acme") : boardOf(3, "Lead A")));
      vi.stubGlobal("fetch", fetchMock);
      const { rerender } = render(<CentralBoardView companySlug="acme" boardId={3} refreshToken={4} />);
      await screen.findByText("Lead A");
      const before = fetchMock.mock.calls.length;
      rerender(<CentralBoardView companySlug="acme" boardId={3} refreshToken={4} />);
      await new Promise((r) => setTimeout(r, 30));
      expect(fetchMock.mock.calls).toHaveLength(before);
    });

    it("falls back to the full load with a retry when the in-place read fails", async () => {
      let fail = false;
      vi.stubGlobal("fetch", vi.fn(async (url: string) => {
        if (urlOf(url).endsWith("/undo")) return json(404);
        if (urlOf(url).endsWith("/me")) return json(200, meOf("acme"));
        return fail ? json(503, { detail: "down" }) : json(200, boardOf(3, "Lead A"));
      }));
      const { rerender } = render(<CentralBoardView companySlug="acme" boardId={3} refreshToken={0} />);
      await screen.findByText("Lead A");
      fail = true;
      rerender(<CentralBoardView companySlug="acme" boardId={3} refreshToken={1} />);
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      fail = false;
      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByText("Lead A")).toBeInTheDocument();
    });
  });
});
