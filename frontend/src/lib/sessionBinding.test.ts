import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bindSessionToken,
  centralLogin,
  centralLogout,
  clearAuth,
  getAuthHeader,
  getSessionToken,
  isBoundSessionToken,
  login,
  readStoredToken,
} from "@/lib/auth";
import { getCentralMe, listCompanyBoards } from "@/lib/api";
import { resetActiveCompanyForTests } from "@/lib/activeCompany";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
type Call = [string, RequestInit];
const authOf = (call: unknown) => ((call as Call)[1].headers as Record<string, string> | undefined)?.Authorization;

// "Another tab replaced the token" is simulated by writing the shared storage directly and dispatching nothing: it is
// the state of a tab whose `storage` event has not been processed yet. jsdom does not prove real multi-tab timing.
describe("the session token a tab runs", () => {
  beforeEach(() => bindSessionToken(null));
  afterEach(() => {
    vi.unstubAllGlobals();
    bindSessionToken(null);
    resetActiveCompanyForTests();
    localStorage.clear();
  });

  it("keeps sending the bound token when another tab replaces the stored one", () => {
    localStorage.setItem("pm_auth_token", "A");
    bindSessionToken("A");
    localStorage.setItem("pm_auth_token", "B");
    expect(getAuthHeader()).toEqual({ Authorization: "Bearer A" });
    expect(getSessionToken()).toBe("A");
    expect(readStoredToken()).toBe("B");
    expect(isBoundSessionToken("A")).toBe(true);
    expect(isBoundSessionToken("B")).toBe(false);
    expect(isBoundSessionToken(null)).toBe(false);
  });

  it("uses the bound token on real requests too", async () => {
    localStorage.setItem("pm_auth_token", "A");
    bindSessionToken("A");
    localStorage.setItem("pm_auth_token", "B");
    const fetchMock = vi.fn(async () => json(200, []));
    vi.stubGlobal("fetch", fetchMock);
    await listCompanyBoards("lity");
    expect(authOf(fetchMock.mock.calls[0])).toBe("Bearer A");
  });

  it("asks the server about a token this tab has not adopted without adopting it", async () => {
    localStorage.setItem("pm_auth_token", "A");
    bindSessionToken("A");
    const fetchMock = vi.fn(async () => json(200, { email: "b@x.y", is_global_admin: false, companies: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await getCentralMe("B");
    expect(authOf(fetchMock.mock.calls[0])).toBe("Bearer B");
    expect(getSessionToken()).toBe("A");
  });

  it("legacy mode, which binds nothing, keeps following storage", async () => {
    const fetchMock = vi.fn(async () => json(200, { access_token: "L1", token_type: "bearer", username: "u", role: "admin" }));
    vi.stubGlobal("fetch", fetchMock);
    await login({ username: "u", password: "pw" });
    expect(getAuthHeader()).toEqual({ Authorization: "Bearer L1" });
    expect(isBoundSessionToken("L1")).toBe(false);
    localStorage.setItem("pm_auth_token", "L2");
    expect(getAuthHeader()).toEqual({ Authorization: "Bearer L2" });
    clearAuth();
    expect(getAuthHeader()).toEqual({});
    expect(localStorage.getItem("pm_auth_token")).toBeNull();
  });

  it("central sign-in binds the token it just stored", async () => {
    localStorage.setItem("pm_auth_token", "old");
    bindSessionToken("old");
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { access_token: "NEW", token_type: "bearer" })));
    await centralLogin("a@b.c", "pw");
    expect(getAuthHeader()).toEqual({ Authorization: "Bearer NEW" });
    expect(localStorage.getItem("pm_auth_token")).toBe("NEW");
    localStorage.setItem("pm_auth_token", "other");
    expect(getAuthHeader()).toEqual({ Authorization: "Bearer NEW" });
  });

  it("clearing this tab's own session removes it from storage", () => {
    localStorage.setItem("pm_auth_token", "A");
    localStorage.setItem("pm_auth_user", "a@b.c");
    bindSessionToken("A");
    clearAuth();
    expect(localStorage.getItem("pm_auth_token")).toBeNull();
    expect(localStorage.getItem("pm_auth_user")).toBeNull();
    expect(getAuthHeader()).toEqual({});
  });

  it("clearing a session another tab has replaced leaves the other tab's session in storage", () => {
    localStorage.setItem("pm_auth_token", "A");
    bindSessionToken("A");
    localStorage.setItem("pm_auth_token", "B");
    localStorage.setItem("pm_auth_user", "b@x.y");
    clearAuth(); // e.g. A's session expired in this tab
    expect(localStorage.getItem("pm_auth_token")).toBe("B");
    expect(localStorage.getItem("pm_auth_user")).toBe("b@x.y");
    expect(getSessionToken()).toBe("B"); // nothing bound any more: the tab reads storage until it adopts a session
  });

  it("signs out this tab's session, not the stored one", async () => {
    localStorage.setItem("pm_auth_token", "A");
    bindSessionToken("A");
    localStorage.setItem("pm_auth_token", "B");
    const fetchMock = vi.fn(async () => json(204));
    vi.stubGlobal("fetch", fetchMock);
    await centralLogout();
    expect(String((fetchMock.mock.calls[0] as unknown as Call)[0])).toMatch(/\/api\/central\/logout$/);
    expect(authOf(fetchMock.mock.calls[0])).toBe("Bearer A");
    expect(localStorage.getItem("pm_auth_token")).toBe("B");
  });

  it("with nothing replaced, logout still clears storage as before", async () => {
    localStorage.setItem("pm_auth_token", "A");
    bindSessionToken("A");
    vi.stubGlobal("fetch", vi.fn(async () => json(204)));
    await centralLogout();
    expect(localStorage.getItem("pm_auth_token")).toBeNull();
  });
});
