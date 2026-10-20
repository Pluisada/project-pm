import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SESSION_EXPIRED_EVENT, centralLogin, centralLogout, getAuthMode, getAuthState } from "@/lib/auth";
import {
  createCompanyBoard, createCompanyColumn, deleteCompanyColumn, downloadCompanyExcel, getCentralMe, isAbortError,
  safeExcelFilename, updateCompanyColumn,
} from "@/lib/api";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const xlsx = (disposition: string | null) =>
  new Response(new Blob(["PK"]), {
    status: 200,
    headers: disposition ? { "Content-Disposition": disposition } : {},
  });

describe("central auth", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("detects the mode and rejects anything that is not exactly legacy or central", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, { auth: "central" })));
    expect(await getAuthMode()).toBe("central");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, { auth: "legacy" })));
    expect(await getAuthMode()).toBe("legacy");
    for (const bad of [json(404), json(500), json(200, { auth: "other" }), json(200, {}), json(200, null), new Response("<html>", { status: 200 })]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(bad));
      await expect(getAuthMode()).rejects.toThrow();
    }
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("down")));
    await expect(getAuthMode()).rejects.toThrow();
  });

  it("logs in against /api/central/login and stores the token under the project's key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(200, { access_token: "tok", token_type: "bearer" }));
    vi.stubGlobal("fetch", fetchMock);
    await centralLogin("a@b.com", "pw");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/central/login");
    expect(JSON.parse(init.body)).toEqual({ email: "a@b.com", password: "pw" });
    expect(getAuthState()).toMatchObject({ isAuthenticated: true, token: "tok", username: "a@b.com", role: null });
  });

  it("stores nothing on a failed login", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(401, { detail: "Invalid credentials" })));
    await expect(centralLogin("a@b.com", "bad")).rejects.toThrow("Invalid email or password");
    expect(getAuthState().isAuthenticated).toBe(false);
  });

  it("sends the bearer token to /me and signals expiry on a 401", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    const fetchMock = vi.fn().mockResolvedValue(json(200, { email: "a@b.com", is_global_admin: true, companies: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await getCentralMe();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/central/me");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer tok");

    const expired = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(401, { detail: "Invalid or expired token" })));
    await expect(getCentralMe()).rejects.toMatchObject({ status_code: 401 });
    window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
    expect(expired).toHaveBeenCalledTimes(1);
    expect(getAuthState().token).toBeNull();
  });

  it("logs out through /api/central/logout and clears the session", async () => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await centralLogout();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/central/logout");
    expect(getAuthState().isAuthenticated).toBe(false);
  });
});

describe("Excel downloads", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    ["export", "/boards/7/excel/export"],
    ["template", "/boards/7/excel/template"],
  ] as const)("downloads the %s file with the bearer token and the server's safe name", async (kind, path) => {
    const fetchMock = vi.fn().mockResolvedValue(xlsx('attachment; filename="cards-salcomp-board-7-20261019.xlsx"'));
    vi.stubGlobal("fetch", fetchMock);
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    });
    const name = await downloadCompanyExcel("salcomp", 7, kind);
    expect(fetchMock.mock.calls[0][0]).toContain(`/api/companies/salcomp${path}`);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer tok");
    expect(name).toBe("cards-salcomp-board-7-20261019.xlsx");
    expect(clicked).toEqual([name]);
  });

  it("passes the abort signal to fetch and saves nothing when aborted mid-request", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn((_url: string, init: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const pending = downloadCompanyExcel("salcomp", 7, "export", controller.signal);
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
    controller.abort();
    const error = await pending.catch((e) => e);
    expect(isAbortError(error)).toBe(true);
    expect(click).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("saves nothing if the signal aborts after the bytes arrived", async () => {
    const controller = new AbortController();
    vi.stubGlobal("fetch", vi.fn(async () => {
      controller.abort();
      return xlsx('attachment; filename="a.xlsx"');
    }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const error = await downloadCompanyExcel("salcomp", 7, "export", controller.signal).catch((e) => e);
    expect(isAbortError(error)).toBe(true);
    expect(click).not.toHaveBeenCalled();
  });

  it("never lets an unsafe server filename through", () => {
    expect(safeExcelFilename('attachment; filename="../../evil.exe"', "export")).toBe("cards-export.xlsx");
    expect(safeExcelFilename('attachment; filename="a b.xlsx"', "export")).toBe("cards-export.xlsx");
    expect(safeExcelFilename(null, "template")).toBe("cards-import-template.xlsx");
  });

  it("surfaces 403 without ending the session, and 401 as expiry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(403, { detail: "Access denied" })));
    await expect(downloadCompanyExcel("salcomp", 7, "export")).rejects.toMatchObject({ status_code: 403 });
    expect(getAuthState().token).toBe("tok");

    const expired = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(401)));
    await expect(downloadCompanyExcel("salcomp", 7, "export")).rejects.toMatchObject({ status_code: 401 });
    window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("never saves an error response or a network failure as a file", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(500, { detail: "boom" })));
    await expect(downloadCompanyExcel("salcomp", 7, "export")).rejects.toMatchObject({ status_code: 500 });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("down")));
    await expect(downloadCompanyExcel("salcomp", 7, "export")).rejects.toMatchObject({ status_code: 0 });
    expect(click).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});

describe("company board and column administration client", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  const stub = (response: Response | (() => Promise<Response>)) => {
    const fetchMock = vi.fn(async () => (typeof response === "function" ? response() : response.clone()));
    vi.stubGlobal("fetch", fetchMock);
    return () => {
      const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
      return { url, init, body: init.body ? JSON.parse(String(init.body)) : undefined, headers: init.headers as Record<string, string> };
    };
  };

  it("createCompanyBoard POSTs the payload to the company's boards and returns the board", async () => {
    const sent = stub(json(201, { id: 9, title: "New" }));
    await expect(createCompanyBoard("a b", { title: "New" })).resolves.toMatchObject({ id: 9 });
    const call = sent();
    expect(call.url).toMatch(/\/api\/companies\/a%20b\/boards$/);
    expect(call.init.method).toBe("POST");
    expect(call.body).toEqual({ title: "New" });
    expect(call.headers.Authorization).toBe("Bearer tok");
  });

  it("createCompanyColumn POSTs title, position and stage_kind", async () => {
    const sent = stub(json(201, { id: 5, title: "Q" }));
    await expect(createCompanyColumn("acme", 3, { title: "Q", position: 2, stage_kind: "won" })).resolves.toMatchObject({ id: 5 });
    const call = sent();
    expect(call.url).toMatch(/\/api\/companies\/acme\/boards\/3\/columns$/);
    expect(call.init.method).toBe("POST");
    expect(call.body).toEqual({ title: "Q", position: 2, stage_kind: "won" });
  });

  it("updateCompanyColumn PUTs only the fields given", async () => {
    const sent = stub(json(200, { id: 5, title: "R" }));
    await updateCompanyColumn("acme", 3, 5, { position: 0 });
    const call = sent();
    expect(call.url).toMatch(/\/api\/companies\/acme\/boards\/3\/columns\/5$/);
    expect(call.init.method).toBe("PUT");
    expect(call.body).toEqual({ position: 0 });
  });

  it("deleteCompanyColumn DELETEs the column and resolves on 204", async () => {
    const sent = stub(new Response(null, { status: 204 }));
    await expect(deleteCompanyColumn("acme", 3, 5)).resolves.toBeUndefined();
    const call = sent();
    expect(call.url).toMatch(/\/api\/companies\/acme\/boards\/3\/columns\/5$/);
    expect(call.init.method).toBe("DELETE");
    expect(call.init.body).toBeUndefined();
  });

  it("none of them uses a legacy /api/boards route", async () => {
    const fetchMock = vi.fn(async () => json(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    await createCompanyBoard("acme", { title: "x" });
    await createCompanyColumn("acme", 1, { title: "x", position: 0, stage_kind: "open" });
    await updateCompanyColumn("acme", 1, 2, { title: "y" });
    await deleteCompanyColumn("acme", 1, 2);
    for (const [url] of fetchMock.mock.calls as unknown as [string][]) expect(url).toContain("/api/companies/acme/");
  });

  it.each([401, 403, 404, 409, 422, 503])("propagates HTTP %i as an ApiError with the status", async (status) => {
    stub(json(status, { detail: "nope" }));
    await expect(updateCompanyColumn("acme", 1, 2, { title: "y" })).rejects.toMatchObject({ status_code: status, detail: "nope" });
    await expect(deleteCompanyColumn("acme", 1, 2)).rejects.toMatchObject({ status_code: status });
    await expect(createCompanyBoard("acme", { title: "x" })).rejects.toMatchObject({ status_code: status });
    await expect(createCompanyColumn("acme", 1, { title: "x", position: 0, stage_kind: "open" })).rejects.toMatchObject({ status_code: status });
  });

  it("reports a network failure as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("down"); }));
    await expect(deleteCompanyColumn("acme", 1, 2)).rejects.toMatchObject({ status_code: 0 });
    await expect(createCompanyBoard("acme", { title: "x" })).rejects.toMatchObject({ status_code: 0 });
  });
});
