import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { SESSION_EXPIRED_EVENT } from "@/lib/auth";
import { ProtectedRoute } from "@/components/ProtectedRoute";

const api = vi.hoisted(() => ({ listBoards: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }));
vi.mock("@/components/KanbanWithSidebar", () => ({ KanbanWithSidebar: () => <div>board</div> }));

describe("ProtectedRoute session expiry", () => {
  beforeEach(() => {
    localStorage.clear();
    api.listBoards.mockReset();
    api.listBoards.mockResolvedValue([{ id: 1 }]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ needs_setup: false }))));
  });

  it("returns to login with a notice when the session expires mid-use", async () => {
    localStorage.setItem("pm_auth_token", "t");
    localStorage.setItem("pm_auth_user", "rep");
    localStorage.setItem("pm_auth_role", "member");
    render(<ProtectedRoute />);
    await screen.findByText("board");

    act(() => {
      localStorage.clear();
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    });

    expect(await screen.findByText("Your session expired. Sign in again.")).toBeInTheDocument();
    expect(screen.queryByText("board")).not.toBeInTheDocument();
  });

  it("shows login when the first board load 401s on a stale token", async () => {
    localStorage.setItem("pm_auth_token", "stale");
    localStorage.setItem("pm_auth_user", "rep");
    localStorage.setItem("pm_auth_role", "member");
    api.listBoards.mockImplementation(async () => {
      localStorage.clear();
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
      throw { status_code: 401, detail: "Invalid token" };
    });
    render(<ProtectedRoute />);
    await waitFor(() => expect(screen.getByText("Your session expired. Sign in again.")).toBeInTheDocument());
    expect(api.listBoards).toHaveBeenCalledTimes(1);
  });
});
