import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralApp } from "@/components/CentralApp";
import { CentralForgotScreen, CentralInviteScreen, CentralResetScreen, readCentralLink } from "@/components/CentralLinkScreens";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const STRONG = "Str0ng!Pass";

function stubFetch(handler: (url: string, init?: RequestInit) => Response) {
  const mock = vi.fn(async (url: string, init?: RequestInit) => handler(String(url), init));
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("readCentralLink", () => {
  it("reads an invitation, a reset, or nothing; an invitation wins", () => {
    expect(readCentralLink("?central_invite_token=abc")).toEqual({ kind: "invite", token: "abc" });
    expect(readCentralLink("?central_reset_token=xyz")).toEqual({ kind: "reset", token: "xyz" });
    expect(readCentralLink("?central_invite_token=a&central_reset_token=b")).toEqual({ kind: "invite", token: "a" });
    expect(readCentralLink("?signup_token=legacy")).toBeNull();
    expect(readCentralLink("")).toBeNull();
  });
});

describe("central link screens", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("accepts an invitation with a new password and never sends a stored session", async () => {
    localStorage.setItem("pm_auth_token", "stale");
    const mock = stubFetch(() => json(200, { message: "ok" }));
    const done = vi.fn();
    render(<CentralInviteScreen token="tok123" onDone={done} />);
    await userEvent.type(screen.getByLabelText("Password"), STRONG);
    await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
    await userEvent.click(screen.getByRole("button", { name: "Activate account" }));
    expect(done).toHaveBeenCalledWith(expect.stringContaining("account is ready"));
    const [url, init] = mock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/central/invitations/accept");
    expect(JSON.parse(String(init.body))).toEqual({ token: "tok123", password: STRONG });
    expect(init.headers).not.toHaveProperty("Authorization");
  });

  it("keeps the submit disabled until the passwords match", async () => {
    stubFetch(() => json(200));
    render(<CentralInviteScreen token="t" onDone={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Password"), STRONG);
    await userEvent.type(screen.getByLabelText("Confirm password"), "other");
    expect(screen.getByRole("button", { name: "Activate account" })).toBeDisabled();
    expect(screen.getByText("The passwords do not match.")).toBeInTheDocument();
  });

  it.each([
    [400, { detail: "This invitation is invalid or has expired" }],
  ])("explains an invalid, expired, used or revoked invitation (%s) without echoing the token", async (status, body) => {
    stubFetch(() => json(status, body));
    const done = vi.fn();
    render(<CentralInviteScreen token="secret-token" onDone={done} />);
    await userEvent.type(screen.getByLabelText("Password"), STRONG);
    await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
    await userEvent.click(screen.getByRole("button", { name: "Activate account" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("invalid, expired or was already used");
    expect(document.body.textContent).not.toContain("secret-token");
    expect(done).not.toHaveBeenCalled();
  });

  it("points a person who already has an account to the existing-account form", async () => {
    stubFetch(() => json(400, { detail: "This invitation is invalid or has expired" }));
    render(<CentralInviteScreen token="secret-token" onDone={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Password"), STRONG);
    await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
    await userEvent.click(screen.getByRole("button", { name: "Activate account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent('choose "I already have an account"');
  });

  it("shows the password policy message of a weak password", async () => {
    stubFetch(() => json(422, { detail: "Password does not meet the requirements" }));
    render(<CentralInviteScreen token="t" onDone={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Password"), "weak");
    await userEvent.type(screen.getByLabelText("Confirm password"), "weak");
    await userEvent.click(screen.getByRole("button", { name: "Activate account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Password does not meet the requirements");
  });

  it("lets an existing account join with its own email and password", async () => {
    const mock = stubFetch(() => json(200, { message: "joined" }));
    const done = vi.fn();
    render(<CentralInviteScreen token="tok" onDone={done} />);
    await userEvent.click(screen.getByRole("button", { name: "I already have an account" }));
    await userEvent.type(screen.getByLabelText("Email"), "ana@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "Whatever1!");
    await userEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(done).toHaveBeenCalledWith(expect.stringContaining("joined"));
    expect(String(mock.mock.calls[0][0])).toContain("/api/central/invitations/join");
  });

  it("a wrong password on join is a plain message and does not fire the session-expired event", async () => {
    stubFetch(() => json(401, { detail: "Invalid credentials" }));
    const expired = vi.fn();
    window.addEventListener("pm:session-expired", expired);
    render(<CentralInviteScreen token="tok" onDone={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "I already have an account" }));
    await userEvent.type(screen.getByLabelText("Email"), "ana@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    window.removeEventListener("pm:session-expired", expired);
    expect(expired).not.toHaveBeenCalled();
  });

  it("resets the password and reports an expired link", async () => {
    stubFetch(() => json(400, { detail: "This link is invalid or has expired" }));
    render(<CentralResetScreen token="r" onDone={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("New password"), STRONG);
    await userEvent.type(screen.getByLabelText("Confirm password"), STRONG);
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
  });

  it("forgot-password shows the same neutral answer", async () => {
    stubFetch(() => json(200, { message: "If the account exists, a link to choose a new password has been sent." }));
    render(<CentralForgotScreen onBack={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Email"), "nobody@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("If the account exists");
  });
});

describe("CentralApp with a one-time link in the address bar", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("shows the invitation screen, removes the token from the URL and never stores it", async () => {
    window.history.replaceState(null, "", "/?central_invite_token=abc123");
    stubFetch(() => json(404));
    render(<CentralApp />);
    expect(await screen.findByRole("heading", { name: "Accept your invitation" })).toBeInTheDocument();
    expect(window.location.search).toBe("");
    expect(JSON.stringify({ ...localStorage })).not.toContain("abc123");
  });

  it("shows the reset screen for a reset link", async () => {
    window.history.replaceState(null, "", "/?central_reset_token=zzz");
    stubFetch(() => json(404));
    render(<CentralApp />);
    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("the sign-in page offers Forgot my password", async () => {
    stubFetch(() => json(404));
    render(<CentralApp />);
    await userEvent.click(await screen.findByRole("button", { name: "LOGIN" }));
    await userEvent.click(await screen.findByRole("button", { name: "Forgot my password?" }));
    expect(await screen.findByRole("heading", { name: "Forgot my password" })).toBeInTheDocument();
  });
});
