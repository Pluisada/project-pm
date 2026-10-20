import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralApp } from "@/components/CentralApp";
import { getActiveCompany, resetActiveCompanyForTests } from "@/lib/activeCompany";
import { listCompanyBoards } from "@/lib/api";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const board = { id: 3, user_id: 1, title: "Pipeline", created_at: "", updated_at: "" };


// Board detail and /me of a company: the Kanban read added on top of the board list.
function companyRead(url: string): Response | null {
  const m = String(url).match(/\/api\/companies\/([^/]+)\/(me|boards\/(\d+))$/);
  if (!m) return null;
  if (m[2] === "me") {
    return json(200, { user_id: 1, company_slug: m[1], role: "super_admin", capabilities: { readOnly: false } });
  }
  return json(200, { ...board, id: Number(m[3]), columns: [] });
}

function route(handlers: Record<string, () => Response>) {
  const fetchMock = vi.fn(async (url: string) => {
    const read = companyRead(url);
    if (read) return read;
    if (/\/api\/central\/companies\/[^/]+\/(members|invitations)$/.test(String(url))) return json(200, []); // member panel
    const hit = Object.keys(handlers).find((p) => String(url).includes(p));
    return hit ? handlers[hit]() : json(404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const me = (companies: { slug: string; name: string; role: string }[]) =>
  json(200, { email: "a@b.com", is_global_admin: false, companies });

describe("CentralApp", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    resetActiveCompanyForTests();
  });

  it("registers the chosen company in memory, sends its slug on every request and forgets it on sign-out", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    const fetchMock = route({
      "/api/central/me": () => me([
        { slug: "salcomp", name: "Salcomp", role: "super_admin" },
        { slug: "lity", name: "Lity", role: "super_admin" },
      ]),
      "/api/companies/lity/boards": () => json(200, [board]),
      "/api/central/logout": () => json(204),
    });
    render(<CentralApp />);
    await userEvent.selectOptions(await screen.findByLabelText("Company"), "lity");
    await screen.findByRole("button", { name: "Export Current Database" });
    expect(getActiveCompany()).toBe("lity");
    const companyCalls = fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/companies/"));
    expect(companyCalls.length).toBeGreaterThan(0);
    for (const [, init] of companyCalls as unknown as [string, RequestInit][]) {
      expect((init.headers as Record<string, string>)["X-Company-Slug"]).toBe("lity");
    }
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(getActiveCompany()).toBeNull();
  });

  it("switching company ends the old company's requests and never sends a later one to it", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    const fetchMock = route({
      "/api/central/me": () => me([
        { slug: "salcomp", name: "Salcomp", role: "super_admin" },
        { slug: "lity", name: "Lity", role: "super_admin" },
      ]),
      "/api/companies/salcomp/boards": () => json(200, [board]),
      "/api/companies/lity/boards": () => json(200, [board]),
    });
    render(<CentralApp />);
    const select = await screen.findByLabelText("Company");
    await userEvent.selectOptions(select, "salcomp");
    await screen.findByRole("button", { name: "Export Current Database" });
    fetchMock.mockClear();
    await userEvent.selectOptions(select, "lity");
    await waitFor(() => expect(getActiveCompany()).toBe("lity"));
    await screen.findByRole("button", { name: "Export Current Database" });
    const urls = fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes("/api/companies/"));
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.every((u) => u.includes("/api/companies/lity/"))).toBe(true);
  });

  it("shows the landing page and LOGIN button first, then reveals the sign-in form", async () => {
    const fetchMock = route({});
    render(<CentralApp />);
    expect(await screen.findByRole("button", { name: "LOGIN" })).toBeInTheDocument();
    expect(screen.getByAltText(/three racehorses/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "LOGIN" }));
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Forgot my password?" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["?central_reset_token=abc", "reset"],
    ["?central_invite_token=abc", "invite"],
  ])("bypasses the landing page for %s without calling the server", async (search) => {
    const fetchMock = route({});
    window.history.replaceState(null, "", `/${search}`);
    render(<CentralApp />);
    await screen.findAllByLabelText(/password/i);
    expect(screen.queryByRole("button", { name: "LOGIN" })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  describe("an authentication change made in another tab", () => {
    // Synthetic storage events in jsdom prove this tab's logic, not real multi-tab browser behavior.
    const two = [
      { slug: "salcomp", name: "Salcomp", role: "super_admin" },
      { slug: "lity", name: "Lity", role: "super_admin" },
    ];
    let account: { email: string; companies: typeof two };
    const signedInOnLity = async () => {
      account = { email: "a@b.com", companies: two };
      localStorage.setItem("pm_auth_token", "tok");
      localStorage.setItem("pm_auth_user", "a@b.com");
      const fetchMock = route({
        "/api/central/me": () => json(200, { ...account, is_global_admin: false }),
        "/api/companies/salcomp/boards": () => json(200, [board]),
        "/api/companies/lity/boards": () => json(200, [board]),
      });
      render(<CentralApp />);
      await userEvent.selectOptions(await screen.findByLabelText("Company"), "lity");
      await screen.findByRole("button", { name: "Export Current Database" });
      fetchMock.mockClear();
      return fetchMock;
    };
    const tokenChanged = (newValue: string | null) => {
      if (newValue === null) localStorage.removeItem("pm_auth_token");
      else localStorage.setItem("pm_auth_token", newValue);
      act(() => {
        window.dispatchEvent(new StorageEvent("storage", { key: "pm_auth_token", oldValue: "tok", newValue }));
      });
    };
    const calls = (fetchMock: ReturnType<typeof route>, part: string) =>
      fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes(part));

    it("keeps the same account and company after asking the server who the new token belongs to", async () => {
      const fetchMock = await signedInOnLity();
      tokenChanged("tok2");
      await screen.findByRole("button", { name: "Export Current Database" });
      expect(getActiveCompany()).toBe("lity");
      expect(calls(fetchMock, "/api/central/me")).toHaveLength(1);
      expect(screen.queryByText(/account signed in on this browser changed/)).not.toBeInTheDocument();
      expect(calls(fetchMock, "/api/companies/salcomp/")).toEqual([]);
    });

    it("drops the previous account's company and data when the token belongs to someone else", async () => {
      const fetchMock = await signedInOnLity();
      account = { email: "c@d.com", companies: two };
      tokenChanged("tok2");
      expect(await screen.findByText(/account signed in on this browser changed to c@d.com/)).toBeInTheDocument();
      expect(screen.getByText("c@d.com", { selector: "p" })).toBeInTheDocument();
      expect(screen.queryByText("a@b.com")).not.toBeInTheDocument();
      expect(getActiveCompany()).toBeNull();
      expect(screen.getByLabelText("Company")).toHaveValue("");
      expect(screen.queryByRole("button", { name: "Export Current Database" })).not.toBeInTheDocument();
      // Nothing was requested for any company with the new token while the old account's company was shown.
      expect(calls(fetchMock, "/api/companies/")).toEqual([]);
    });

    it("returns to sign-in, with no company, when the other tab signed out", async () => {
      const fetchMock = await signedInOnLity();
      tokenChanged(null);
      expect(await screen.findByText(/You signed out in another tab/)).toBeInTheDocument();
      expect(screen.getByLabelText("Email")).toBeInTheDocument();
      expect(getActiveCompany()).toBeNull();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it("cancels a read still under way for the old account without showing an error", async () => {
      localStorage.setItem("pm_auth_token", "tok");
      localStorage.setItem("pm_auth_user", "a@b.com");
      let hungSignal: AbortSignal | null | undefined;
      vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
        const u = String(url);
        if (u.endsWith("/api/central/me")) return json(200, { email: "a@b.com", is_global_admin: false, companies: [{ slug: "lity", name: "Lity", role: "super_admin" }] });
        if (u.endsWith("/api/companies/lity/boards")) return json(200, [board]);
        if (/\/api\/companies\/lity\/boards\/3$/.test(u)) {
          hungSignal = init?.signal;
          return new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          });
        }
        return json(404);
      }));
      render(<CentralApp />);
      await vi.waitFor(() => expect(hungSignal).toBeDefined());
      expect(hungSignal?.aborted).toBe(false);
      tokenChanged(null);
      expect(hungSignal?.aborted).toBe(true);
      await screen.findByText(/You signed out in another tab/);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("ignores events that do not change the token, other keys, and a tab that is not signed in", async () => {
      const fetchMock = await signedInOnLity();
      act(() => {
        window.dispatchEvent(new StorageEvent("storage", { key: "pm_auth_token", oldValue: "tok", newValue: "tok" }));
        // Another tab remembering a different company is only a hint: it never moves this tab.
        window.dispatchEvent(new StorageEvent("storage", { key: "pm_central_company", oldValue: "lity", newValue: "salcomp" }));
      });
      expect(getActiveCompany()).toBe("lity");
      expect(fetchMock).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
    });

    const authOf = (call: unknown) =>
      ((call as [string, RequestInit])[1].headers as Record<string, string>).Authorization;
    const lastAuthFor = (fetchMock: ReturnType<typeof route>, part: string) => {
      const hit = fetchMock.mock.calls.filter(([u]) => String(u).includes(part));
      return authOf(hit[hit.length - 1]);
    };

    it("keeps sending this tab's own token until the new one has been verified, then switches to it", async () => {
      const fetchMock = await signedInOnLity();
      // Another tab replaced the token, and this tab has not processed the storage event yet.
      localStorage.setItem("pm_auth_token", "tok2");
      await listCompanyBoards("lity");
      expect(lastAuthFor(fetchMock, "/api/companies/lity/boards")).toBe("Bearer tok");
      // The event arrives: the server is asked about the new token (and only that call carries it) ...
      act(() => {
        window.dispatchEvent(new StorageEvent("storage", { key: "pm_auth_token", oldValue: "tok", newValue: "tok2" }));
      });
      await screen.findByRole("button", { name: "Export Current Database" });
      expect(lastAuthFor(fetchMock, "/api/central/me")).toBe("Bearer tok2");
      // ... and once it is verified, this tab runs it.
      await listCompanyBoards("lity");
      expect(lastAuthFor(fetchMock, "/api/companies/lity/boards")).toBe("Bearer tok2");
    });

    it("never sends the new token under the old account while it is being verified", async () => {
      const fetchMock = await signedInOnLity();
      localStorage.setItem("pm_auth_token", "tok2");
      account = { email: "c@d.com", companies: two };
      act(() => {
        window.dispatchEvent(new StorageEvent("storage", { key: "pm_auth_token", oldValue: "tok", newValue: "tok2" }));
      });
      await screen.findByText(/changed to c@d.com/);
      // Every request made for the old account, before and during the check, used the old token; the only
      // request with the new token is the one that asks who it belongs to.
      const withNew = fetchMock.mock.calls.filter((c) => authOf(c) === "Bearer tok2").map(([u]) => String(u));
      expect(withNew.every((u) => u.endsWith("/api/central/me"))).toBe(true);
      expect(fetchMock.mock.calls.filter((c) => authOf(c) === "Bearer tok").every(([u]) => !String(u).endsWith("/api/central/me"))).toBe(true);
    });

    it("signs out this tab's own session, not the one another tab has stored", async () => {
      const fetchMock = await signedInOnLity();
      localStorage.setItem("pm_auth_token", "tok2"); // another tab signed in; no event processed yet
      localStorage.setItem("pm_auth_user", "c@d.com");
      await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
      expect(lastAuthFor(fetchMock, "/api/central/logout")).toBe("Bearer tok");
      expect(localStorage.getItem("pm_auth_token")).toBe("tok2"); // the other tab's session is left alone
    });

    it("does nothing in a tab that is on the sign-in screen", async () => {
      const fetchMock = route({});
      render(<CentralApp />);
      await screen.findByRole("button", { name: "LOGIN" });
      tokenChanged("tok2");
      expect(fetchMock).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "LOGIN" })).toBeInTheDocument();
    });
  });

  it("signs in, loads the company and shows both buttons to a super admin", async () => {
    route({
      "/api/central/login": () => json(200, { access_token: "tok", token_type: "bearer" }),
      "/api/central/me": () => me([{ slug: "salcomp", name: "Salcomp", role: "super_admin" }]),
      "/api/companies/salcomp/boards": () => json(200, [board]),
    });
    render(<CentralApp />);
    await userEvent.click(await screen.findByRole("button", { name: "LOGIN" }));
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.com");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign In" }));
    expect(await screen.findByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Import Template" })).toBeInTheDocument();
  });

  it("shows an error and no session for bad credentials", async () => {
    route({ "/api/central/login": () => json(401, { detail: "Invalid credentials" }) });
    render(<CentralApp />);
    await userEvent.click(await screen.findByRole("button", { name: "LOGIN" }));
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.com");
    await userEvent.type(screen.getByLabelText("Password"), "bad");
    await userEvent.click(screen.getByRole("button", { name: "Sign In" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(localStorage.getItem("pm_auth_token")).toBeNull();
  });

  it("hides the buttons for a sales member and a visitor", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    route({
      "/api/central/me": () => me([{ slug: "salcomp", name: "Salcomp", role: "sales_member" }]),
      "/api/companies/salcomp/boards": () => json(200, [board]),
    });
    render(<CentralApp />);
    await screen.findByLabelText("Company");
    await waitFor(() => expect(screen.getByLabelText("Company")).toHaveValue("salcomp"));
    expect(screen.queryByRole("button", { name: /Export|Template/ })).not.toBeInTheDocument();
  });

  it("does not pick a company the backend did not list, even if remembered", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    localStorage.setItem("pm_central_company", "other");
    const fetchMock = route({
      "/api/central/me": () =>
        me([
          { slug: "salcomp", name: "Salcomp", role: "super_admin" },
          { slug: "acme", name: "Acme", role: "visitor" },
        ]),
    });
    render(<CentralApp />);
    expect(await screen.findByLabelText("Company")).toHaveValue("");
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/companies/"))).toBe(false);
  });

  it("drops the company and re-reads access when the backend answers 403", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    route({
      "/api/central/me": () => me([{ slug: "salcomp", name: "Salcomp", role: "super_admin" }]),
      "/api/companies/salcomp/boards": () => json(403, { detail: "Access denied" }),
    });
    render(<CentralApp />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You no longer have access to that company.");
    expect(screen.queryByRole("button", { name: "Export Current Database" })).not.toBeInTheDocument();
  });

  it("does not loop on requests when the only company keeps answering 403", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    const fetchMock = route({
      "/api/central/me": () => me([{ slug: "salcomp", name: "Salcomp", role: "super_admin" }]),
      "/api/companies/salcomp/boards": () => json(403, { detail: "Access denied" }),
    });
    render(<CentralApp />);
    await screen.findByRole("alert");
    await new Promise((r) => setTimeout(r, 100));
    const count = (part: string) => fetchMock.mock.calls.filter(([u]) => String(u).includes(part)).length;
    expect(count("/boards")).toBe(1);
    expect(count("/api/central/me")).toBe(2);
    expect(localStorage.getItem("pm_auth_token")).toBe("tok");
    expect(screen.getByLabelText("Company")).toHaveValue("");
  });

  it("offers a retry, not the login form, when /me fails with a network error", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    let up = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (String(url).includes("/api/central/me")) {
        if (!up) throw new TypeError("down");
        return me([{ slug: "salcomp", name: "Salcomp", role: "sales_member" }]);
      }
      return json(200, [board]);
    }));
    render(<CentralApp />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Network error");
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(localStorage.getItem("pm_auth_token")).toBe("tok");
    up = true;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Company")).toHaveValue("salcomp");
  });

  it("switches company: new downloads target the new company and the old message is dropped", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    const fetchMock = route({
      "/api/central/me": () =>
        me([
          { slug: "salcomp", name: "Salcomp", role: "super_admin" },
          { slug: "acme", name: "Acme", role: "sales_member" },
        ]),
      "/api/companies/salcomp/boards/3/excel/export": () => json(500, { detail: "boom" }),
      "/api/companies/salcomp/boards": () => json(200, [board]),
      "/api/companies/acme/boards": () => json(200, [{ ...board, id: 9 }]),
    });
    render(<CentralApp />);
    await userEvent.selectOptions(await screen.findByLabelText("Company"), "salcomp");
    await userEvent.click(await screen.findByRole("button", { name: "Export Current Database" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // the button is usable again after the failure
    expect(screen.getByRole("button", { name: "Export Current Database" })).toBeEnabled();

    await userEvent.selectOptions(screen.getByLabelText("Company"), "acme");
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/companies/acme/boards"))).toBe(true));
    expect(screen.queryByRole("button", { name: /Export|Template/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Company"), "salcomp");
    expect(await screen.findByRole("button", { name: "Export Current Database" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("returns to sign-in with a notice when the session expires", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    route({
      "/api/central/me": () => me([{ slug: "salcomp", name: "Salcomp", role: "super_admin" }]),
      "/api/companies/salcomp/boards": () => json(401, { detail: "Invalid or expired token" }),
    });
    render(<CentralApp />);
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent("Your session expired");
  });

  describe("board loading failures", () => {
    const login = () => {
      localStorage.setItem("pm_auth_token", "tok");
      localStorage.setItem("pm_auth_user", "a@b.com");
    };
    const count = (fetchMock: ReturnType<typeof vi.fn>, part: string) =>
      fetchMock.mock.calls.filter(([u]) => String(u).includes(part) && !/\/boards\/\d+(\/undo)?$/.test(String(u))).length;

    it("offers one explicit retry after a network or server failure, then loads the boards", async () => {
      login();
      let up = false;
      const fetchMock = vi.fn(async (url: string) => {
        const u = String(url);
        if (u.includes("/api/central/me")) return me([{ slug: "salcomp", name: "Salcomp", role: "super_admin" }]);
        if (companyRead(u)) return companyRead(u)!;
        if (!up) return json(500, { detail: "Server exploded" });
        return json(200, [board]);
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<CentralApp />);
      expect(await screen.findByRole("alert")).toHaveTextContent("Server exploded");
      // no automatic loop
      await new Promise((r) => setTimeout(r, 100));
      expect(count(fetchMock, "/boards")).toBe(1);
      up = true;
      const retry = screen.getByRole("button", { name: "Try again" });
      await userEvent.dblClick(retry);
      expect(await screen.findByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
      expect(count(fetchMock, "/boards")).toBe(2);
      expect(count(fetchMock, "/api/central/me")).toBe(1);
    });

    it("offers the retry for a network error too", async () => {
      login();
      let up = false;
      vi.stubGlobal("fetch", vi.fn(async (url: string) => {
        if (String(url).includes("/api/central/me")) return me([{ slug: "salcomp", name: "Salcomp", role: "super_admin" }]);
        if (companyRead(url)) return companyRead(url)!;
        if (!up) throw new TypeError("down");
        return json(200, [board]);
      }));
      render(<CentralApp />);
      expect(await screen.findByRole("alert")).toHaveTextContent("Network error");
      up = true;
      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
    });

    it("does not offer a retry for 403, which keeps its own handling", async () => {
      login();
      route({
        "/api/central/me": () => me([{ slug: "salcomp", name: "Salcomp", role: "super_admin" }]),
        "/api/companies/salcomp/boards": () => json(403, { detail: "Access denied" }),
      });
      render(<CentralApp />);
      expect(await screen.findByRole("alert")).toHaveTextContent("You no longer have access");
      expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    });

    it("ignores a stale response and a stale retry button when the company changes mid-request", async () => {
      login();
      let releaseSalcomp!: (r: Response) => void;
      const fetchMock = vi.fn((url: string) => {
        const u = String(url);
        if (u.includes("/api/central/me")) {
          return Promise.resolve(me([
            { slug: "salcomp", name: "Salcomp", role: "super_admin" },
            { slug: "acme", name: "Acme", role: "super_admin" },
          ]));
        }
        if (companyRead(u)) return Promise.resolve(companyRead(u)!);
        if (u.includes("/companies/salcomp/boards")) return new Promise<Response>((r) => (releaseSalcomp = r));
        return Promise.resolve(json(200, [{ ...board, id: 9, title: "Acme board" }]));
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<CentralApp />);
      await userEvent.selectOptions(await screen.findByLabelText("Company"), "salcomp");
      await waitFor(() => expect(count(fetchMock, "/companies/salcomp/boards")).toBe(1));
      await userEvent.selectOptions(screen.getByLabelText("Company"), "acme");
      expect(await screen.findByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
      // the abandoned request fails late: nothing about it may surface
      await act(async () => releaseSalcomp(json(500, { detail: "late failure" })));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
      expect(screen.getByLabelText("Company")).toHaveValue("acme");
    });

    it("retries the currently selected company, not the one that failed", async () => {
      login();
      let acmeUp = false;
      const fetchMock = vi.fn(async (url: string) => {
        const u = String(url);
        if (u.includes("/api/central/me")) {
          return me([
            { slug: "salcomp", name: "Salcomp", role: "super_admin" },
            { slug: "acme", name: "Acme", role: "super_admin" },
          ]);
        }
        if (companyRead(u)) return companyRead(u)!;
        if (u.includes("/companies/salcomp/boards")) return json(500, { detail: "salcomp down" });
        return acmeUp ? json(200, [{ ...board, id: 9 }]) : json(500, { detail: "acme down" });
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<CentralApp />);
      await userEvent.selectOptions(await screen.findByLabelText("Company"), "salcomp");
      expect(await screen.findByRole("alert")).toHaveTextContent("salcomp down");
      await userEvent.selectOptions(screen.getByLabelText("Company"), "acme");
      expect(await screen.findByRole("alert")).toHaveTextContent("acme down");
      acmeUp = true;
      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
      expect(count(fetchMock, "/companies/salcomp/boards")).toBe(1);
      expect(count(fetchMock, "/companies/acme/boards")).toBe(2);
    });
  });

  describe("board administration inside the app", () => {
    const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
      canAssignSalesperson: true, canManageBoards: true, canImport: false };
    const columnOf = (boardId: number, title: string) => ({ id: boardId * 10, board_id: boardId, title, position: 0, stage_kind: "open", created_at: "", updated_at: "", cards: [] });

    function adminRig(opts: { createStatus?: number | "network"; listAfterFailure?: unknown[] } = {}) {
      localStorage.setItem("pm_auth_token", "tok");
      localStorage.setItem("pm_auth_user", "a@b.com");
      let boards = [board];
      let columnTitle = "1st Contact";
      const log: { method: string; url: string }[] = [];
      const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
        const u = String(url);
        const method = init?.method ?? "GET";
        log.push({ method, url: u });
        if (u.includes("/api/central/me")) return me([{ slug: "acme", name: "Acme", role: "super_admin" }]);
        if (u.endsWith("/api/companies/acme/me")) return json(200, { user_id: 1, company_slug: "acme", role: "super_admin", capabilities: caps });
        if (u.endsWith("/undo")) return json(404);
        if (method === "POST" && u.endsWith("/boards")) {
          const body = JSON.parse(String(init?.body));
          if (opts.createStatus === "network") { boards = [...boards, { ...board, id: 8, title: body.title }]; throw new TypeError("down"); }
          if (opts.createStatus) return json(opts.createStatus, { detail: "x" });
          const created = { ...board, id: 9, title: body.title };
          boards = [...boards, created];
          return json(201, created);
        }
        if (method === "POST" && u.endsWith("/columns")) { columnTitle = "Added"; return json(201, columnOf(3, "Added")); }
        if (method === "GET" && u.endsWith("/api/companies/acme/boards")) return json(200, boards);
        const m = u.match(/\/api\/companies\/acme\/boards\/(\d+)$/);
        if (m && method === "GET") return json(200, { ...board, id: Number(m[1]), title: `Board ${m[1]}`, columns: [columnOf(Number(m[1]), Number(m[1]) === 3 ? columnTitle : "Fresh column")] });
        return json(404);
      });
      vi.stubGlobal("fetch", fetchMock);
      return { log, count: (method: string, suffix: string) => log.filter((c) => c.method === method && c.url.endsWith(suffix)).length };
    }

    it("selects a newly created board and shows it", async () => {
      const rig = adminRig();
      render(<CentralApp />);
      await userEvent.click(await screen.findByRole("button", { name: "Manage boards and columns" }));
      await userEvent.type(await screen.findByLabelText("New board title"), "Second");
      await userEvent.click(screen.getByRole("button", { name: "Create board" }));
      expect((await screen.findAllByText("Fresh column")).length).toBeGreaterThan(0);
      expect(screen.getByLabelText("Board")).toHaveValue("9");
      expect(rig.count("GET", "/boards/9")).toBeGreaterThan(0);
    });

    it.each([["network" as const], [502]])("re-reads the board list after a %s failure on board creation", async (failure) => {
      const rig = adminRig({ createStatus: failure });
      render(<CentralApp />);
      await userEvent.click(await screen.findByRole("button", { name: "Manage boards and columns" }));
      const listReads = () => rig.count("GET", "/api/companies/acme/boards");
      const before = listReads();
      await userEvent.type(await screen.findByLabelText("New board title"), "Maybe");
      await userEvent.click(screen.getByRole("button", { name: "Create board" }));
      await waitFor(() => expect(listReads()).toBe(before + 1));
      expect(rig.count("POST", "/boards")).toBe(1);
      const notice = await screen.findByTestId("admin-notice");
      expect(notice).toHaveTextContent("The data was reloaded");
    });

    it("refreshes the board after a column change without remounting it", async () => {
      const rig = adminRig();
      render(<CentralApp />);
      await userEvent.click(await screen.findByRole("button", { name: "Manage boards and columns" }));
      await screen.findAllByText("1st Contact");
      const identityReads = () => rig.count("GET", "/api/companies/acme/me");
      const before = identityReads();
      await userEvent.type(await screen.findByLabelText("New column title"), "Added");
      await userEvent.click(screen.getByRole("button", { name: "Add column" }));
      await waitFor(() => expect(screen.getAllByText("Added").length).toBeGreaterThan(0));
      expect(identityReads()).toBe(before); // the board view was refreshed in place, not remounted
    });
  });
});
