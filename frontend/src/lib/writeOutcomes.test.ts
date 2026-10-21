import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetActiveCompanyForTests, setActiveCompany } from "@/lib/activeCompany";
import {
  COMPANY_HEADER,
  confirmCompanyCardsImport,
  createCompanyBoard,
  inviteCompanyMember,
  listCompanyBoards,
  moveCompanyCard,
  previewCompanyCardsImport,
} from "@/lib/api";
import {
  clearWriteOutcomes,
  describeWrite,
  dismissWriteOutcome,
  getWriteOutcomes,
  isTrackedWrite,
  resetWriteOutcomesForTests,
  trackWrite,
} from "@/lib/writeOutcomes";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });

describe("describeWrite", () => {
  it.each([
    ["PUT", "/api/companies/lity/boards/3/cards/7/position", "Move card"],
    ["PUT", "/api/companies/lity/boards/3/cards/7", "Save card"],
    ["DELETE", "/api/companies/lity/boards/3/cards/7", "Delete card"],
    ["POST", "/api/companies/lity/boards/3/cards", "Create card"],
    ["POST", "/api/companies/lity/boards", "Create board"],
    ["POST", "/api/companies/lity/boards/3/columns", "Column change"],
    ["DELETE", "/api/companies/lity/boards/3/columns/9", "Column change"],
    ["POST", "/api/companies/lity/boards/3/undo", "Undo"],
    ["POST", "/api/companies/lity/boards/3/excel/import/confirm", "Cards import"],
    ["POST", "/api/central/companies/lity/invitations", "Member change"],
    ["PUT", "/api/central/companies/lity/members/4/role", "Member change"],
    ["POST", "/api/companies/lity/something-new", "Change"],
  ])("%s %s is %s", (method, endpoint, action) => {
    expect(describeWrite(method, endpoint)).toBe(action);
  });
});

describe("isTrackedWrite", () => {
  it.each([
    ["POST", "/api/companies/lity/boards/3/excel/import/confirm", true],
    ["POST", "/api/companies/lity/boards/3/imports", true],
    ["POST", "/api/companies/lity/imports/9/revert", true],
    ["POST", "/api/companies/lity/boards/3/cards", true],
    ["PUT", "/api/companies/lity/boards/3/cards/7/position", true],
    ["DELETE", "/api/companies/lity/boards/3/cards/7", true],
    ["POST", "/api/central/companies/lity/invitations", true],
    ["POST", "/api/companies/lity/boards/3/excel/import/preview", false],
    ["POST", "/api/companies/lity/boards/3/excel/import/preview?x=1", false],
    ["POST", "/api/companies/lity/boards/3/imports/preview", false],
    ["GET", "/api/companies/lity/boards/3", false],
    ["HEAD", "/api/companies/lity/boards/3", false],
  ])("%s %s is tracked: %s", (method, endpoint, tracked) => {
    expect(isTrackedWrite(method, endpoint)).toBe(tracked);
  });

  it("does not exclude a write merely because it is a POST or its path mentions preview elsewhere", () => {
    expect(isTrackedWrite("POST", "/api/companies/lity/boards/3/cards/previews")).toBe(true);
    expect(isTrackedWrite("POST", "/api/companies/lity/boards/preview/cards")).toBe(true);
  });
});

describe("recorded outcomes", () => {
  beforeEach(() => resetWriteOutcomesForTests());
  afterEach(() => {
    resetWriteOutcomesForTests();
    resetActiveCompanyForTests();
  });

  it("records nothing when the write settles while its own company is still active", () => {
    setActiveCompany("lity");
    trackWrite("lity", "PUT", "/api/companies/lity/boards/3/cards/7/position").saved();
    expect(getWriteOutcomes()).toEqual([]);
  });

  it("records saved, failed and unknown outcomes of a write that settles after the company changed", () => {
    setActiveCompany("lity");
    const a = trackWrite("lity", "PUT", "/api/companies/lity/boards/3/cards/7/position");
    const b = trackWrite("lity", "DELETE", "/api/companies/lity/boards/3/cards/8");
    const c = trackWrite("lity", "POST", "/api/companies/lity/boards/3/cards");
    setActiveCompany("salcomp");
    a.saved();
    b.failed("Access denied");
    c.unknown();
    expect(getWriteOutcomes().map(({ companySlug, action, result, detail }) => ({ companySlug, action, result, detail }))).toEqual([
      { companySlug: "lity", action: "Move card", result: "saved", detail: undefined },
      { companySlug: "lity", action: "Delete card", result: "failed", detail: "Access denied" },
      { companySlug: "lity", action: "Create card", result: "unknown", detail: undefined },
    ]);
  });

  it("settles once: a later report for the same write changes nothing", () => {
    setActiveCompany("lity");
    const write = trackWrite("lity", "POST", "/api/companies/lity/boards");
    setActiveCompany(null);
    write.saved();
    write.unknown();
    write.failed("x");
    expect(getWriteOutcomes()).toHaveLength(1);
  });

  it("can be dismissed, and is forgotten with the session", () => {
    setActiveCompany("lity");
    const first = trackWrite("lity", "POST", "/api/companies/lity/boards");
    const second = trackWrite("lity", "POST", "/api/companies/lity/boards/3/cards");
    setActiveCompany("salcomp");
    first.saved();
    dismissWriteOutcome(getWriteOutcomes()[0].id);
    expect(getWriteOutcomes()).toEqual([]);
    clearWriteOutcomes(); // sign-out or another account
    second.saved(); // answers after that must never reach whoever signs in next
    expect(getWriteOutcomes()).toEqual([]);
  });
});

describe("writes made through the API client", () => {
  beforeEach(() => {
    resetWriteOutcomesForTests();
    localStorage.setItem("pm_auth_token", "tok");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    resetActiveCompanyForTests();
    resetWriteOutcomesForTests();
  });

  const held = () => {
    let release: (r: Response | Error) => void = () => {};
    const fetchMock = vi.fn(
      () => new Promise<Response>((resolve, reject) => { release = (r) => (r instanceof Error ? reject(r) : resolve(r)); })
    );
    vi.stubGlobal("fetch", fetchMock);
    return { fetchMock, release: (r: Response | Error) => release(r) };
  };

  it("reports a write that finished after the person left its company, and does not abort it", async () => {
    setActiveCompany("lity");
    const { fetchMock, release } = held();
    const pending = createCompanyBoard("lity", { title: "Pipeline" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    setActiveCompany("salcomp");
    expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].signal?.aborted).toBe(false);
    release(json(201, { id: 5, title: "Pipeline" }));
    await pending;
    expect(getWriteOutcomes()).toMatchObject([{ companySlug: "lity", action: "Create board", result: "saved" }]);
  });

  it("reports a write the server refused as not saved, with the server's reason", async () => {
    setActiveCompany("lity");
    const { fetchMock, release } = held();
    const pending = moveCompanyCard("lity", 3, 7, 11, 0).catch((e) => e);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    setActiveCompany("salcomp");
    release(json(403, { detail: "Access denied" }));
    expect(await pending).toMatchObject({ status_code: 403 });
    expect(getWriteOutcomes()).toMatchObject([{ action: "Move card", result: "failed", detail: "Access denied" }]);
  });

  it.each([
    ["a network error", () => new TypeError("down")],
    ["a server error", () => json(500, { detail: "sqlite3.OperationalError /var/db/x.db" })],
  ])("reports %s as an unknown outcome without repeating the server's text", async (_label, outcome) => {
    setActiveCompany("lity");
    const { fetchMock, release } = held();
    const pending = inviteCompanyMember("lity", "x@example.com", "visitor").catch((e) => e);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    setActiveCompany("salcomp");
    release(outcome());
    await pending;
    expect(getWriteOutcomes()).toMatchObject([{ action: "Member change", result: "unknown" }]);
    expect(JSON.stringify(getWriteOutcomes())).not.toMatch(/sqlite|\/var/);
  });

  it("records nothing for a write that finishes in its own company, for reads, or for one refused before sending", async () => {
    setActiveCompany("lity");
    const { fetchMock, release } = held();
    const own = createCompanyBoard("lity", { title: "A" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    release(json(201, { id: 1, title: "A" }));
    await own;
    expect(getWriteOutcomes()).toEqual([]);

    vi.stubGlobal("fetch", vi.fn(async () => json(200, [])));
    await listCompanyBoards("lity");
    setActiveCompany("salcomp");
    await listCompanyBoards("lity").catch(() => undefined); // refused: lity is no longer active
    await createCompanyBoard("lity", { title: "B" }).catch(() => undefined); // refused before it is sent
    expect(getWriteOutcomes()).toEqual([]);
  });

  it("never reports a Cards import preview that settles after the company changed, whatever its answer", async () => {
    const file = new File(["x"], "cards.xlsx");
    for (const outcome of [() => json(200, { plan_digest: "d" }), () => json(422, { detail: "bad file" }), () => new TypeError("down"), () => json(500, {})]) {
      setActiveCompany("lity");
      const { fetchMock, release } = held();
      const pending = previewCompanyCardsImport("lity", 3, file).catch((e) => e);
      await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
      setActiveCompany("salcomp");
      release(outcome());
      await pending;
      expect(getWriteOutcomes()).toEqual([]);
      resetActiveCompanyForTests();
    }
  });

  it("still scopes a preview: company header sent, refused when the company is no longer active", async () => {
    const file = new File(["x"], "cards.xlsx");
    setActiveCompany("lity");
    const fetchMock = vi.fn(async () => json(200, { plan_digest: "d" }));
    vi.stubGlobal("fetch", fetchMock);
    await previewCompanyCardsImport("lity", 3, file);
    expect(((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].headers as Record<string, string>)[COMPANY_HEADER]).toBe("lity");
    setActiveCompany("salcomp");
    const refused = await previewCompanyCardsImport("lity", 3, file).catch((e) => e);
    expect(refused).toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the real import confirmation tracked: saved, refused and unknown after a switch", async () => {
    const file = new File(["x"], "cards.xlsx");
    const cases: [() => Response | Error, string][] = [
      [() => json(201, { created: 1 }), "saved"],
      [() => json(409, { detail: "already_imported" }), "failed"],
      [() => new TypeError("down"), "unknown"],
    ];
    for (const [outcome, result] of cases) {
      resetWriteOutcomesForTests();
      setActiveCompany("lity");
      const { fetchMock, release } = held();
      const pending = confirmCompanyCardsImport("lity", 3, file, "digest").catch((e) => e);
      await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
      setActiveCompany("salcomp");
      release(outcome());
      await pending;
      expect(getWriteOutcomes()).toMatchObject([{ companySlug: "lity", action: "Cards import", result }]);
      resetActiveCompanyForTests();
    }
  });

  it("sends the company header on the write it follows", async () => {
    setActiveCompany("lity");
    const fetchMock = vi.fn(async () => json(201, { id: 1, title: "A" }));
    vi.stubGlobal("fetch", fetchMock);
    await createCompanyBoard("lity", { title: "A" });
    expect(((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].headers as Record<string, string>)[COMPANY_HEADER]).toBe("lity");
  });
});
