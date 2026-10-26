import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetActiveCompanyForTests, setActiveCompany } from "@/lib/activeCompany";
import {
  COMPANY_HEADER,
  centralForgotPassword,
  downloadCompanyExcel,
  getCentralMe,
  getCompanyBoard,
  inviteCompanyMember,
  isAbortError,
  listCompanyBoards,
  listCompanyMembers,
  moveCompanyCard,
} from "@/lib/api";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const board = (id: number) => ({ id, user_id: 1, title: "B", created_at: "", updated_at: "", columns: [] });
type Call = [string, RequestInit];

describe("company-scoped requests", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    resetActiveCompanyForTests();
  });

  const stub = (handler: (url: string, init: RequestInit) => Promise<Response>) => {
    const fetchMock = vi.fn(handler);
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };

  it("sends the company of its own URL in X-Company-Slug and still sends the token", async () => {
    setActiveCompany("lity");
    const fetchMock = stub(async () => json(200, board(3)));
    await getCompanyBoard("lity", 3);
    const [url, init] = fetchMock.mock.calls[0] as unknown as Call;
    expect(url).toMatch(/\/api\/companies\/lity\/boards\/3$/);
    const headers = init.headers as Record<string, string>;
    expect(headers[COMPANY_HEADER]).toBe("lity");
    expect(headers.Authorization).toBe("Bearer tok");
  });

  it("does not read the stored company when a request starts", async () => {
    setActiveCompany("lity");
    const read = vi.spyOn(Storage.prototype, "getItem");
    stub(async () => json(200, board(3)));
    await getCompanyBoard("lity", 3);
    expect(read.mock.calls.filter(([key]) => String(key).includes("company"))).toEqual([]);
    read.mockRestore();
  });

  it("keeps its destination when the stored company changes after it started", async () => {
    setActiveCompany("lity");
    let release: (r: Response) => void = () => {};
    const fetchMock = stub(() => new Promise<Response>((resolve) => { release = resolve; }));
    const pending = getCompanyBoard("lity", 3);
    localStorage.setItem("pm_central_company", "salcomp");
    release(json(200, board(3)));
    await pending;
    expect(String((fetchMock.mock.calls[0] as unknown as Call)[0])).toContain("/api/companies/lity/");
  });

  it("is not redirected by another tab's storage event", async () => {
    setActiveCompany("lity");
    const fetchMock = stub(async () => json(200, board(3)));
    localStorage.setItem("pm_central_company", "salcomp");
    window.dispatchEvent(new StorageEvent("storage", { key: "pm_central_company", newValue: "salcomp", oldValue: "lity" }));
    await getCompanyBoard("lity", 3);
    await listCompanyBoards("lity");
    for (const [url, init] of fetchMock.mock.calls as unknown as Call[]) {
      expect(String(url)).toContain("/api/companies/lity/");
      expect((init.headers as Record<string, string>)[COMPANY_HEADER]).toBe("lity");
    }
  });

  it("refuses to send a request for a company that is no longer active", async () => {
    setActiveCompany("lity");
    const fetchMock = stub(async () => json(200, board(3)));
    setActiveCompany("salcomp"); // the person switched before this older call ran
    const error = await getCompanyBoard("lity", 3).catch((e) => e);
    expect(isAbortError(error)).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aborts a request under way when the active company changes", async () => {
    setActiveCompany("lity");
    let seen: AbortSignal | null | undefined;
    stub((_url, init) => new Promise<Response>((_resolve, reject) => {
      seen = init.signal;
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const pending = getCompanyBoard("lity", 3);
    await vi.waitFor(() => expect(seen).toBeDefined());
    setActiveCompany("salcomp");
    const error = await pending.catch((e) => e);
    expect(seen?.aborted).toBe(true);
    expect(isAbortError(error)).toBe(true);
  });

  it("still honors the caller's own AbortSignal", async () => {
    setActiveCompany("lity");
    let seen: AbortSignal | null | undefined;
    stub((_url, init) => new Promise<Response>((_resolve, reject) => {
      seen = init.signal;
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const controller = new AbortController();
    const pending = getCompanyBoard("lity", 3, controller.signal);
    await vi.waitFor(() => expect(seen).toBeDefined());
    controller.abort();
    expect(isAbortError(await pending.catch((e) => e))).toBe(true);
  });

  it("passes the company header on binary downloads too, and aborts them on a switch", async () => {
    setActiveCompany("lity");
    let seen: RequestInit | undefined;
    stub((_url, init) => new Promise<Response>((_resolve, reject) => {
      seen = init;
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const pending = downloadCompanyExcel("lity", 3, "export");
    await vi.waitFor(() => expect(seen).toBeDefined());
    expect((seen?.headers as Record<string, string>)[COMPANY_HEADER]).toBe("lity");
    setActiveCompany("salcomp");
    expect(isAbortError(await pending.catch((e) => e))).toBe(true);
  });

  it("leaves legacy and central-account calls untouched", async () => {
    const { listBoards, getCentralMe } = await import("@/lib/api");
    setActiveCompany("lity");
    const fetchMock = stub(async (url) => json(200, String(url).endsWith("/me") ? { email: "a@b.c", is_global_admin: false, companies: [] } : []));
    await listBoards();
    await getCentralMe();
    for (const [, init] of fetchMock.mock.calls as unknown as Call[]) {
      expect((init.headers as Record<string, string>)[COMPANY_HEADER]).toBeUndefined();
    }
    setActiveCompany("salcomp"); // a switch does not abort calls that are not company calls
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("company administration requests (/api/central/companies/{slug}/...)", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    resetActiveCompanyForTests();
  });
  const stub = (handler: (url: string, init: RequestInit) => Promise<Response>) => {
    const fetchMock = vi.fn(handler);
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };

  it("sends the company of its own URL in X-Company-Slug and the token", async () => {
    setActiveCompany("lity");
    const fetchMock = stub(async () => json(200, []));
    await listCompanyMembers("lity");
    const [url, init] = fetchMock.mock.calls[0] as unknown as Call;
    expect(String(url)).toMatch(/\/api\/central\/companies\/lity\/members$/);
    const headers = init.headers as Record<string, string>;
    expect(headers[COMPANY_HEADER]).toBe("lity");
    expect(headers.Authorization).toBe("Bearer tok");
  });

  it("refuses to send an administration request for a company that is no longer active", async () => {
    setActiveCompany("lity");
    const fetchMock = stub(async () => json(202, {}));
    setActiveCompany("salcomp");
    expect(isAbortError(await listCompanyMembers("lity").catch((e) => e))).toBe(true);
    expect(isAbortError(await inviteCompanyMember("lity", "x@example.com", "visitor").catch((e) => e))).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aborts a read under way when the active company changes", async () => {
    setActiveCompany("lity");
    let seen: AbortSignal | null | undefined;
    stub((_url, init) => new Promise<Response>((_resolve, reject) => {
      seen = init.signal;
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const pending = listCompanyMembers("lity");
    await vi.waitFor(() => expect(seen).toBeDefined());
    setActiveCompany("salcomp");
    expect(isAbortError(await pending.catch((e) => e))).toBe(true);
  });

  it("leaves company-less central endpoints alone: no header, no abort on a switch", async () => {
    setActiveCompany("lity");
    const fetchMock = stub(async (url) => json(200, String(url).endsWith("/me") ? { email: "a@b.c", is_global_admin: false, companies: [] } : {}));
    await getCentralMe();
    await centralForgotPassword("a@b.c");
    setActiveCompany("salcomp");
    for (const [, init] of fetchMock.mock.calls as unknown as Call[]) {
      expect((init.headers as Record<string, string>)[COMPANY_HEADER]).toBeUndefined();
    }
  });
});

describe("a write is never aborted by a company switch", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    resetActiveCompanyForTests();
  });

  it("lets a write in flight finish under the company it started in, so its outcome is known", async () => {
    setActiveCompany("lity");
    let release: (r: Response) => void = () => {};
    let sent: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => {
      sent = init.signal;
      return new Promise<Response>((resolve) => { release = resolve; });
    }));
    const pending = inviteCompanyMember("lity", "x@example.com", "visitor");
    await vi.waitFor(() => expect(sent).toBeDefined());
    setActiveCompany("salcomp"); // the person switched while the invitation was being sent
    expect(sent?.aborted).toBe(false);
    release(json(202, { email_sent: true, message: "Invitation sent." }));
    await expect(pending).resolves.toEqual({ email_sent: true, message: "Invitation sent." });
  });

  it("reports a failed write as the real error, not as a cancellation", async () => {
    setActiveCompany("lity");
    vi.stubGlobal("fetch", vi.fn(async () => json(409, { detail: "The membership changed; retry" })));
    const error = await moveCompanyCard("lity", 3, 7, 11, 0).catch((e) => e);
    expect(isAbortError(error)).toBe(false);
    expect(error).toMatchObject({ status_code: 409, detail: "The membership changed; retry" });
  });

  it("still lets the caller abort its own write", async () => {
    setActiveCompany("lity");
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    const controller = new AbortController();
    const pending = moveCompanyCard("lity", 3, 7, 11, 0, controller.signal);
    controller.abort();
    expect(isAbortError(await pending.catch((e) => e))).toBe(true);
  });
});
