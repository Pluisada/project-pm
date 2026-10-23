import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralMembers } from "@/components/CentralMembers";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const member = (id: number, email: string, role = "sales_member", is_active = true) => ({
  user_id: id, email, role, is_active, user_is_active: true, email_verified: true, joined_at: "2026-10-01T00:00:00",
});

function rig(initial = [member(2, "ana@example.com")]) {
  let members = initial;
  const calls: { method: string; url: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const path = String(url);
    calls.push({ method, url: path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path.endsWith("/members") && method === "GET") return json(200, members);
    if (path.endsWith("/invitations") && method === "GET") return json(200, []);
    if (path.endsWith("/invitations") && method === "POST") return json(202, { message: "Invitation sent." });
    if (path.endsWith("/deactivate")) { members = members.map((m) => ({ ...m, is_active: false })); return new Response(null, { status: 204 }); }
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

  it("invites with the chosen company and role, and never offers the reserved Admin role", async () => {
    const calls = rig();
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await screen.findByText("ana@example.com");
    const options = Array.from(screen.getByLabelText("Role").querySelectorAll("option")).map((o) => o.value);
    expect(options).toEqual(["sales_member", "visitor"]);
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
    await userEvent.click(screen.getByRole("button", { name: "Deactivate ana@example.com" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/members/2/deactivate"))).toBe(true));
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

  it("still never offers Admin on the invitation form (an invitation cannot carry it)", async () => {
    rig();
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    await screen.findByText("ana@example.com");
    const options = Array.from(screen.getByLabelText("Role").querySelectorAll("option")).map((o) => o.value);
    expect(options).toEqual(["sales_member", "visitor"]);
  });

  it("shows the refusal when the backend says 403", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(403, { detail: "Access denied" })));
    render(<CentralMembers companySlug="lity" companyName="Lity" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not allowed");
  });
});
