import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AuthModeGate } from "@/components/AuthModeGate";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

const mode = (auth: string) =>
  vi.fn(async (url: string) =>
    String(url).includes("/api/mode")
      ? new Response(JSON.stringify({ auth }), { status: 200 })
      : new Response(JSON.stringify({ needs_setup: false, setup_enabled: false }), { status: 200 })
  );

describe("AuthModeGate", () => {
  it("renders the legacy login in legacy mode", async () => {
    vi.stubGlobal("fetch", mode("legacy"));
    render(<AuthModeGate />);
    expect(await screen.findByLabelText("Username or email")).toBeInTheDocument();
  });

  it("renders the central login in central mode", async () => {
    vi.stubGlobal("fetch", mode("central"));
    render(<AuthModeGate />);
    await userEvent.click(await screen.findByRole("button", { name: "LOGIN" }));
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  });

  it.each([
    ["a network failure", () => Promise.reject(new TypeError("down"))],
    ["an HTTP error", () => Promise.resolve(new Response("{}", { status: 502 }))],
    ["an unexpected body", () => Promise.resolve(new Response(JSON.stringify({ auth: "x" }), { status: 200 }))],
  ])("shows an error, not the legacy login, after %s, and recovers on retry", async (_name, failure) => {
    const fetchMock = vi.fn(failure as () => Promise<Response>);
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthModeGate />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
    expect(screen.queryByLabelText("Username or email")).not.toBeInTheDocument();

    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ auth: "central" }), { status: 200 }));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await userEvent.click(await screen.findByRole("button", { name: "LOGIN" }));
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  describe("a one-time reset link opened in central mode", () => {
    const SYNTHETIC = "synthetic-test-token-not-real";
    const STRONG = "Str0ng!Passw0rd";

    it("shows the reset form, not Sign In, once the mode is known", async () => {
      window.history.replaceState(null, "", `/?central_reset_token=${SYNTHETIC}`);
      vi.stubGlobal("fetch", mode("central"));
      render(<AuthModeGate />);
      expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Sign In" })).not.toBeInTheDocument();
    });

    it("sends the captured token to the official endpoint, removes it from the URL and never logs or stores it", async () => {
      window.history.replaceState(null, "", `/?central_reset_token=${SYNTHETIC}`);
      const fetchMock = vi.fn(async (url: string, _init?: RequestInit) =>
        String(url).includes("/api/mode")
          ? new Response(JSON.stringify({ auth: "central" }), { status: 200 })
          : new Response("null", { status: 200 })
      );
      vi.stubGlobal("fetch", fetchMock);
      const logs = [vi.spyOn(console, "log"), vi.spyOn(console, "warn"), vi.spyOn(console, "error"), vi.spyOn(console, "info")];
      render(<AuthModeGate />);
      await userEvent.type(await screen.findByLabelText("New password"), STRONG);
      await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
      expect(window.location.search).toBe("");
      await userEvent.click(screen.getByRole("button", { name: "Save password" }));

      const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/central/password/reset"));
      expect(call).toBeDefined();
      expect(JSON.parse(String(call![1]!.body))).toEqual({ token: SYNTHETIC, new_password: STRONG });
      expect(await screen.findByRole("status")).toHaveTextContent("Password updated");
      expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(SYNTHETIC);
      for (const spy of logs) expect(JSON.stringify(spy.mock.calls)).not.toContain(SYNTHETIC);
    });

    const landingAfter = async () => {
      // Back on the landing page (LOGIN button), not on the sign-in form, and the link is gone from the address.
      expect(await screen.findByRole("button", { name: "LOGIN" })).toBeInTheDocument();
      expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Sign In" })).not.toBeInTheDocument();
      expect(window.location.search).toBe("");
    };

    it("after a successful reset the landing page shows, not the sign-in form, and a refresh does not reopen the reset", async () => {
      window.history.replaceState(null, "", `/?central_reset_token=${SYNTHETIC}`);
      vi.stubGlobal("fetch", vi.fn(async (url: string) =>
        String(url).includes("/api/mode")
          ? new Response(JSON.stringify({ auth: "central" }), { status: 200 })
          : new Response("null", { status: 200 })
      ));
      const { unmount } = render(<AuthModeGate />);
      await userEvent.type(await screen.findByLabelText("New password"), STRONG);
      await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
      await userEvent.click(screen.getByRole("button", { name: "Save password" }));
      await landingAfter();
      expect(screen.getByRole("status")).toHaveTextContent("Password updated");
      // The sign-in form opens only through the normal entry: the LOGIN button.
      await userEvent.click(screen.getByRole("button", { name: "LOGIN" }));
      expect(await screen.findByLabelText("Email")).toBeInTheDocument();
      // A refresh (a new page load at the cleaned address) starts at the landing page, never at the reset screen.
      unmount();
      render(<AuthModeGate />);
      expect(await screen.findByRole("button", { name: "LOGIN" })).toBeInTheDocument();
      expect(screen.queryByLabelText("New password")).not.toBeInTheDocument();
    });

    it("after accepting an invitation the landing page shows, not the sign-in form", async () => {
      window.history.replaceState(null, "", `/?central_invite_token=${SYNTHETIC}`);
      const fetchMock = vi.fn(async (url: string) =>
        String(url).includes("/api/mode")
          ? new Response(JSON.stringify({ auth: "central" }), { status: 200 })
          : new Response("null", { status: 200 })
      );
      vi.stubGlobal("fetch", fetchMock);
      render(<AuthModeGate />);
      await userEvent.type(await screen.findByLabelText("Password"), STRONG);
      await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
      await userEvent.click(screen.getByRole("button", { name: "Activate account" }));
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/central/invitations/accept"))).toBe(true);
      await landingAfter();
      expect(screen.getByRole("status")).toHaveTextContent("Your account is ready");
      expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(SYNTHETIC);
    });

    it("an invalid invitation keeps the invitation screen with the error and does not go to the landing page", async () => {
      window.history.replaceState(null, "", `/?central_invite_token=${SYNTHETIC}`);
      vi.stubGlobal("fetch", vi.fn(async (url: string) =>
        String(url).includes("/api/mode")
          ? new Response(JSON.stringify({ auth: "central" }), { status: 200 })
          : new Response(JSON.stringify({ detail: "x" }), { status: 400 })
      ));
      render(<AuthModeGate />);
      await userEvent.type(await screen.findByLabelText("Password"), STRONG);
      await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
      await userEvent.click(screen.getByRole("button", { name: "Activate account" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(/invalid|expired/i);
      expect(screen.queryByRole("button", { name: "LOGIN" })).not.toBeInTheDocument();
    });

    it("reports an invalid or expired token in plain language", async () => {
      window.history.replaceState(null, "", `/?central_reset_token=${SYNTHETIC}`);
      vi.stubGlobal("fetch", vi.fn(async (url: string) =>
        String(url).includes("/api/mode")
          ? new Response(JSON.stringify({ auth: "central" }), { status: 200 })
          : new Response(JSON.stringify({ detail: "x" }), { status: 400 })
      ));
      render(<AuthModeGate />);
      await userEvent.type(await screen.findByLabelText("New password"), STRONG);
      await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
      await userEvent.click(screen.getByRole("button", { name: "Save password" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
    });

    it("without a token the central mode still opens Sign In", async () => {
      vi.stubGlobal("fetch", mode("central"));
      render(<AuthModeGate />);
      expect(await screen.findByRole("button", { name: "LOGIN" })).toBeInTheDocument();
      await userEvent.click(await screen.findByRole("button", { name: "LOGIN" }));
      expect(await screen.findByRole("heading", { name: "Sign In" })).toBeInTheDocument();
    });
  });
});
