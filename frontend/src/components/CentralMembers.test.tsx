import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralMembers } from "@/components/CentralMembers";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const member = (id: number, email: string, role = "sales_member", is_active = true) => ({
  user_id: id, email, role, is_active, user_is_active: true, email_verified: true, joined_at: "2026-10-01T00:00:00",
});

const pendingInv = [{ invitation_id: 7, email: "wait@example.com", role: "sales_member", expires_at: "2026-11-01T00:00:00" }];

function rig(initial = [member(2, "ana@example.com")], opts: { pending?: typeof pendingInv; resend?: () => Response; invite?: () => Response } = {}) {
  let members = initial;
  const calls: { method: string; url: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const path = String(url);
    calls.push({ method, url: path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path.endsWith("/members") && method === "GET") return json(200, members);
    if (path.endsWith("/invitations") && method === "GET") return json(200, opts.pending ?? []);
    if (path.endsWith("/resend") && method === "POST") return opts.resend ? opts.resend() : json(200, { message: "Invitation email sent." });
    if (path.endsWith("/invitations") && method === "POST") return opts.invite ? opts.invite() : json(202, { email_sent: true, message: "Invitation sent." });
    if (path.endsWith("/deactivate")) { members = members.map((m) => ({ ...m, is_active: false })); return new Response(null, { status: 204 }); }
    if (path.endsWith("/cancel")) { members = members.map((m) => ({ ...m, is_active: false, user_is_active: false })); return new Response(null, { status: 204 }); }
    if (path.endsWith("/role")) return new Response(null, { status: 204 });
    return json(404);
  }));
  return calls;
}

describe("CentralMembers", () => {
  beforeEach(() => localStorage.setItem("pm_auth_token", "tok"));
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("lists the members of the company", async () => {
    rig();
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    expect(await screen.findByText("ana@example.com")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Role of ana@example.com" })).toHaveValue("sales_member");
  });

  it("invites with the chosen company and role, and never offers the Super Admin role", async () => {
    const calls = rig();
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await screen.findByText("ana@example.com");
    const options = Array.from(screen.getByLabelText("Role").querySelectorAll("option")).map((o) => o.value);
    expect(options).toEqual(["sales_member", "visitor", "admin"]);
    expect(options).not.toContain("super_admin");
    await userEvent.type(screen.getByLabelText("Invite by email"), "new@other.com");
    await userEvent.selectOptions(screen.getByLabelText("Role"), "visitor");
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toContain("/api/central/companies/lity/invitations");
    expect(post?.body).toEqual({ email: "new@other.com", role: "visitor" });
    expect(await screen.findByTestId("members-notice")).toHaveTextContent("Invitation sent to new@other.com");
    expect(document.body.textContent).not.toMatch(/central_invite_token/);
  });

  it("changes a role and deactivates a member", async () => {
    const calls = rig();
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Role of ana@example.com" }), "visitor");
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/members/2/role"))).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ role: "visitor" });
    await userEvent.click(screen.getByRole("button", { name: "Remove ana@example.com from Lity" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/members/2/deactivate"))).toBe(true));
  });

  it("shows a neutral label instead of the internal placeholder of a cancelled user, and ordinary emails as they are", async () => {
    const cancelled = { ...member(3, "cancelled-3@cancelled.invalid", "sales_member", false), user_is_active: false };
    rig([member(2, "ana@example.com"), cancelled]);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    expect(await screen.findByText("ana@example.com")).toBeInTheDocument();
    expect(screen.getByText("Cancelled user")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/cancelled\.invalid/);
    expect(screen.queryByRole("button", { name: /Cancel user cancelled/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Invite cancelled/ })).toBeNull();
  });

  it("does not treat lookalike domains as cancelled users", async () => {
    rig([member(2, "bo@cancelled.com"), member(3, "cy@notcancelled.invalid")]);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    expect(await screen.findByText("bo@cancelled.com")).toBeInTheDocument();
    expect(screen.getByText("cy@notcancelled.invalid")).toBeInTheDocument();
    expect(screen.queryByText("Cancelled user")).toBeNull();
  });

  it("cancels a user everywhere only after an explicit confirmation that names the consequences", async () => {
    const calls = rig();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    const button = await screen.findByRole("button", { name: "Cancel user ana@example.com" });
    await userEvent.click(button);
    expect(calls.some((c) => c.url.endsWith("/cancel"))).toBe(false);
    await userEvent.click(button);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/api/central/users/2/cancel"))).toBe(true));
    const text = String(confirm.mock.calls[0][0]);
    expect(text).toMatch(/every company/);
    expect(text).toMatch(/cannot be undone/);
    expect(text).toMatch(/password and sessions are removed/);
    expect(text).toMatch(/invitation or password-reset link stops working/);
    expect(text).toMatch(/automatically reversed/);
    expect(text).toMatch(/Cards, reports and history stay/);
    expect(text).toMatch(/Remove from company/);
    expect(await screen.findByText(/was cancelled in every company/)).toBeInTheDocument();
  });

  it("promotes a joined member to Admin after a confirmation, and does nothing if it is declined", async () => {
    const calls = rig();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    const select = await screen.findByRole("combobox", { name: "Role of ana@example.com" });
    expect(Array.from(select.querySelectorAll("option")).map((o) => o.value)).toEqual(["sales_member", "visitor", "admin"]);
    await userEvent.selectOptions(select, "admin");
    expect(calls.some((c) => c.method === "PUT")).toBe(false); // declined
    await userEvent.selectOptions(select, "admin");
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ role: "admin" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    confirm.mockRestore();
  });

  it("invites an Admin only after a confirmation, and sends nothing if it is declined", async () => {
    const calls = rig();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await screen.findByText("ana@example.com");
    await userEvent.type(screen.getByLabelText("Invite by email"), "boss@other.com");
    await userEvent.selectOptions(screen.getByLabelText("Role"), "admin");
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    expect(calls.some((c) => c.method === "POST")).toBe(false); // declined
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ email: "boss@other.com", role: "admin" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    confirm.mockRestore();
  });

  it("does not ask for a confirmation when inviting a Sales Member or a Visitor", async () => {
    const calls = rig();
    const confirm = vi.spyOn(window, "confirm");
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await screen.findByText("ana@example.com");
    await userEvent.type(screen.getByLabelText("Invite by email"), "plain@other.com");
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("shows the refusal when the backend says 403", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(403, { detail: "Access denied" })));
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not allowed");
  });

  it("resends a pending invitation after a confirmation, and sends nothing if it is declined", async () => {
    const calls = rig(undefined, { pending: pendingInv });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    const button = await screen.findByRole("button", { name: "Email a new link to wait@example.com" });
    await userEvent.click(button);
    expect(calls.some((c) => c.url.endsWith("/resend"))).toBe(false);
    await userEvent.click(button);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/companies/lity/invitations/7/resend"))).toBe(true));
    expect(await screen.findByTestId("members-notice")).toHaveTextContent("A new invitation email was sent to wait@example.com");
    expect(document.body.textContent).not.toMatch(/central_invite_token/);
    confirm.mockRestore();
  });

  it.each([
    [503, "Email delivery is not configured; the invitation was not sent"],
    [502, "The email could not be sent; try again later"],
  ])("reports a %i resend honestly, never as sent", async (code, detail) => {
    rig(undefined, { pending: pendingInv, resend: () => json(code, { detail }) });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await userEvent.click(await screen.findByRole("button", { name: "Email a new link to wait@example.com" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(detail);
    expect(screen.queryByText(/new invitation email was sent/)).toBeNull();
    vi.restoreAllMocks();
  });

  it("shows the refusal when a resend is forbidden", async () => {
    rig(undefined, { pending: pendingInv, resend: () => json(403, { detail: "Access denied" }) });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await userEvent.click(await screen.findByRole("button", { name: "Email a new link to wait@example.com" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not allowed");
    vi.restoreAllMocks();
  });

  it("says so when the invitation was created but the email was not sent", async () => {
    rig(undefined, { invite: () => json(202, { email_sent: false, message: "Invitation created, but email delivery is not configured, so nothing was sent." }) });
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await userEvent.type(await screen.findByLabelText("Invite by email"), "new@other.com");
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("nothing was sent");
    expect(notice).not.toHaveTextContent("was emailed");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("offers Invite again on an inactive member only, and sends the invitation with the previous role", async () => {
    const calls = rig([member(2, "gone@example.com", "visitor", false), member(3, "here@example.com")]);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    expect(screen.queryByRole("button", { name: "Invite here@example.com again" })).toBeNull();
    await userEvent.click(await screen.findByRole("button", { name: "Invite gone@example.com again" }));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toContain("/api/central/companies/lity/invitations");
    expect(post?.body).toEqual({ email: "gone@example.com", role: "visitor" });
    expect(await screen.findByTestId("members-notice")).toHaveTextContent("Invitation sent to gone@example.com");
  });

  it("does not offer Invite again when the identity itself is deactivated", async () => {
    rig([{ ...member(2, "off@example.com", "visitor", false), user_is_active: false }]);
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await screen.findByText("off@example.com");
    expect(screen.queryByRole("button", { name: /Invite off@example.com again/ })).toBeNull();
  });

  it("shows the backend refusal when a re-invitation is not allowed", async () => {
    rig([member(2, "gone@example.com", "visitor", false)], { invite: () => json(403, { detail: "Access denied" }) });
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await userEvent.click(await screen.findByRole("button", { name: "Invite gone@example.com again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not allowed");
  });
});
