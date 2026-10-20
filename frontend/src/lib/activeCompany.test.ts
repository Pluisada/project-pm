import { afterEach, describe, expect, it } from "vitest";
import {
  CompanyScopeError,
  captureScope,
  getActiveCompany,
  resetActiveCompanyForTests,
  runInCompanyScope,
  setActiveCompany,
  subscribeActiveCompany,
} from "@/lib/activeCompany";
import { isAbortError } from "@/lib/api";

afterEach(() => resetActiveCompanyForTests());

describe("active company", () => {
  it("is in memory only and starts empty", () => {
    localStorage.setItem("pm_central_company", "salcomp");
    expect(getActiveCompany()).toBeNull();
  });

  it("notifies subscribers once per real change", () => {
    let calls = 0;
    const off = subscribeActiveCompany(() => { calls += 1; });
    setActiveCompany("lity");
    setActiveCompany("lity");
    setActiveCompany("salcomp");
    off();
    setActiveCompany(null);
    expect(calls).toBe(2);
  });

  it("captures the company at the start and ends the scope when the company changes", () => {
    setActiveCompany("lity");
    const scope = captureScope("lity");
    expect(scope.companySlug).toBe("lity");
    expect(scope.isCurrent()).toBe(true);
    expect(scope.signal.aborted).toBe(false);
    setActiveCompany("salcomp");
    expect(scope.isCurrent()).toBe(false);
    expect(scope.signal.aborted).toBe(true);
    expect(() => scope.assertCurrent()).toThrow(CompanyScopeError);
  });

  it("refuses to start work for a company that is not the active one", () => {
    setActiveCompany("salcomp");
    expect(() => captureScope("lity")).toThrow(CompanyScopeError);
  });

  it("treats its refusal as an aborted request, not as a failure to show", () => {
    expect(isAbortError(new CompanyScopeError("lity"))).toBe(true);
  });

  it("does not end a scope when the same company is registered again", () => {
    setActiveCompany("lity");
    const scope = captureScope("lity");
    setActiveCompany("lity");
    expect(scope.isCurrent()).toBe(true);
  });

  it("with no active company a scope keeps the slug its caller gave", () => {
    const scope = captureScope("lity");
    expect(scope.isCurrent()).toBe(true);
    setActiveCompany("salcomp");
    expect(scope.isCurrent()).toBe(true); // it never belonged to a registered company
  });

  it("keeps one company for every step of a multi-step operation", async () => {
    setActiveCompany("lity");
    const used: string[] = [];
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const run = runInCompanyScope("lity", async (scope) => {
      used.push(scope.companySlug);
      await gate; // the person switches company between the two steps
      used.push(scope.companySlug);
      return "done";
    });
    setActiveCompany("salcomp");
    release();
    await expect(run).rejects.toBeInstanceOf(CompanyScopeError);
    expect(used).toEqual(["lity", "lity"]);
  });

  it("returns the result when the company did not change", async () => {
    setActiveCompany("lity");
    await expect(runInCompanyScope("lity", async () => 42)).resolves.toBe(42);
  });
});
