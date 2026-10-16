import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SESSION_EXPIRED_EVENT } from "@/lib/auth";
import { listBoards } from "@/lib/api";

const respond = (status: number, body: unknown = {}) =>
  vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));

describe("apiCall 401 handling", () => {
  const expired = vi.fn();

  beforeEach(() => {
    localStorage.clear();
    expired.mockClear();
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
  });
  afterEach(() => {
    window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
    vi.unstubAllGlobals();
  });

  it("clears the stored session and signals expiry on a 401 with a token", async () => {
    localStorage.setItem("pm_auth_token", "stale");
    localStorage.setItem("pm_auth_user", "rep");
    localStorage.setItem("pm_auth_role", "member");
    vi.stubGlobal("fetch", respond(401, { detail: "Invalid token" }));

    await expect(listBoards()).rejects.toMatchObject({ status_code: 401, detail: "Invalid token" });

    expect(localStorage.getItem("pm_auth_token")).toBeNull();
    expect(localStorage.getItem("pm_auth_user")).toBeNull();
    expect(localStorage.getItem("pm_auth_role")).toBeNull();
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("does not signal expiry for a 401 on a request without a token", async () => {
    vi.stubGlobal("fetch", respond(401));
    await expect(listBoards()).rejects.toMatchObject({ status_code: 401 });
    expect(expired).not.toHaveBeenCalled();
  });

  it("keeps the session on other errors (403, 409, 500)", async () => {
    localStorage.setItem("pm_auth_token", "good");
    localStorage.setItem("pm_auth_user", "rep");
    for (const status of [403, 409, 500]) {
      vi.stubGlobal("fetch", respond(status));
      await expect(listBoards()).rejects.toMatchObject({ status_code: status });
    }
    expect(localStorage.getItem("pm_auth_token")).toBe("good");
    expect(expired).not.toHaveBeenCalled();
  });
});
