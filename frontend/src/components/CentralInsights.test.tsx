import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralApp } from "@/components/CentralApp";
import { resetActiveCompanyForTests } from "@/lib/activeCompany";

// Reports and KPIs in central mode: the existing pages, pointed at the active company's endpoints. The pages' own
// calculations are covered by ReportsPage.test.tsx and DashboardPage.test.tsx; here: who sees them and what they get.

vi.mock("@/components/ReportsPage", () => ({
  ReportsPage: ({ boardId, companySlug }: { boardId: number; companySlug?: string }) => (
    <div data-testid="reports-screen">reports {companySlug}/{boardId}</div>
  ),
}));
vi.mock("@/components/DashboardPage", () => ({
  DashboardPage: ({ boardId, companySlug }: { boardId: number; companySlug?: string }) => (
    <div data-testid="kpis-screen">kpis {companySlug}/{boardId}</div>
  ),
}));

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const board = { id: 3, user_id: 1, title: "Pipeline", created_at: "", updated_at: "" };
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: true, canManageBoards: true, canImport: true, canExport: true, canManageProducts: true };

function backend(role: string, boards: unknown[] = [board]) {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = String(input);
    if (url.endsWith("/undo")) return json(200, { available: false, description: null });
    if (url.endsWith("/api/central/me")) return json(200, { email: "a@b.com", is_global_admin: false, companies: [{ slug: "lity", name: "Lity", role }] });
    const m = url.match(/\/api\/companies\/([^/]+)\/(me|boards(?:\/(\d+))?)$/);
    if (m && m[2] === "me") return json(200, { user_id: 1, company_slug: m[1], role, capabilities: caps });
    if (m && m[3]) return json(200, { ...board, id: Number(m[3]), columns: [] });
    if (m) return json(200, boards);
    return json(404);
  }));
}

const nav = () => screen.getByRole("navigation", { name: "Sections" });

describe("CentralApp Reports and KPIs", () => {
  beforeEach(() => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); resetActiveCompanyForTests(); });

  it.each(["super_admin", "admin", "sales_member", "visitor"])("offers %s Reports and KPIs, each opening its own screen for the active company and board", async (role) => {
    backend(role);
    render(<CentralApp />);
    await screen.findByTestId("central-board");
    await userEvent.click(within(nav()).getByRole("button", { name: "Reports" }));
    expect(await screen.findByTestId("reports-screen")).toHaveTextContent("reports lity/3");
    expect(screen.getByRole("button", { name: "Reports" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByTestId("central-board")).not.toBeInTheDocument();
    expect(screen.queryByTestId("kpis-screen")).not.toBeInTheDocument();
    await userEvent.click(within(nav()).getByRole("button", { name: "KPIs" }));
    expect(await screen.findByTestId("kpis-screen")).toHaveTextContent("kpis lity/3");
    expect(screen.queryByTestId("reports-screen")).not.toBeInTheDocument();
    await userEvent.click(within(nav()).getByRole("button", { name: "Board" }));
    expect(await screen.findByTestId("central-board")).toBeInTheDocument();
  });

  it("does not grant the management sections through Reports and KPIs", async () => {
    backend("visitor");
    render(<CentralApp />);
    await screen.findByTestId("central-board");
    expect(within(nav()).queryByRole("button", { name: /^(Users|Products|Database)$/ })).not.toBeInTheDocument();
  });

  it("offers neither when the company has no board to report on", async () => {
    backend("super_admin", []);
    render(<CentralApp />);
    await screen.findByTestId("central-empty-workspace");
    expect(screen.queryByRole("button", { name: /^(Reports|KPIs)$/ })).not.toBeInTheDocument();
  });
});
