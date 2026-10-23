import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralApp } from "@/components/CentralApp";
import { resetActiveCompanyForTests } from "@/lib/activeCompany";

// The company's sections: the board page holds only the board (no invitation form, no database downloads); Users and
// Database are their own screens, reached from the top navigation, offered only to the roles that may use them.

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const board = { id: 3, user_id: 1, title: "Pipeline", created_at: "", updated_at: "" };
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: true, canManageBoards: true, canImport: true, canExport: true, canManageProducts: true };

function backend(companies: { slug: string; name: string; role: string }[], boards: unknown[] = [board], ai?: (body: unknown) => Response) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith("/ai") && init?.method === "POST") return (ai ?? (() => json(500)))(JSON.parse(String(init.body)));
    if (url.endsWith("/undo")) return json(200, { available: true, description: 'Undo edit to "Lead 1"' });
    if (url.endsWith("/api/central/me")) return json(200, { email: "a@b.com", is_global_admin: false, companies });
    const m = url.match(/\/api\/companies\/([^/]+)\/(me|boards(?:\/(\d+))?)$/);
    if (m && m[2] === "me") return json(200, { user_id: 1, company_slug: m[1], role: "x", capabilities: caps });
    if (m && m[3]) return json(200, { ...board, id: Number(m[3]), columns: [] });
    if (m) return json(200, boards);
    if (/\/api\/central\/companies\/[^/]+\/(members|invitations)$/.test(url)) return json(200, []);
    if (/\/api\/companies\/[^/]+\/products$/.test(url)) return json(200, { initialized: true, products: [{ id: 1, name: "Chargers", card_count: 2 }] });
    return json(404);
  }));
  return calls;
}

const enter = async (name: string) => userEvent.click(await screen.findByRole("button", { name: new RegExp(name) }));
const nav = () => screen.queryByRole("navigation", { name: "Sections" });

describe("CentralApp sections", () => {
  beforeEach(() => {
    localStorage.setItem("pm_auth_token", "tok");
    localStorage.setItem("pm_auth_user", "a@b.com");
    Element.prototype.scrollIntoView = vi.fn(); // jsdom does not implement it; the chat calls it on new messages
  });
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); resetActiveCompanyForTests(); });

  it("keeps the invitation form and the database downloads off the board page", async () => {
    backend([{ slug: "lity", name: "Lity", role: "super_admin" }]);
    render(<CentralApp />);
    expect(await screen.findByTestId("central-board")).toBeInTheDocument();
    expect(screen.queryByText(/Users of/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export Current Database" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download Import Template" })).not.toBeInTheDocument();
  });

  it("opens Users from the top navigation for the Super Admin, with the invitation form, and returns to the board", async () => {
    backend([{ slug: "lity", name: "Lity", role: "super_admin" }]);
    render(<CentralApp />);
    await screen.findByTestId("central-board");
    await userEvent.click(within(nav()!).getByRole("button", { name: "Users" }));
    expect(await screen.findByRole("heading", { name: "Users of Lity" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Users" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByTestId("central-board")).not.toBeInTheDocument();
    await userEvent.click(within(nav()!).getByRole("button", { name: "Board" }));
    expect(await screen.findByTestId("central-board")).toBeInTheDocument();
    expect(screen.queryByText(/Users of/)).not.toBeInTheDocument();
  });

  it("opens Products from the top navigation for the Super Admin, shows the catalog and returns to the board", async () => {
    backend([{ slug: "lity", name: "Lity", role: "super_admin" }]);
    render(<CentralApp />);
    await screen.findByTestId("central-board");
    await userEvent.click(within(nav()!).getByRole("button", { name: "Products" }));
    const screenEl = await screen.findByTestId("central-products-screen");
    expect(await within(screenEl).findByText("Chargers")).toBeInTheDocument();
    expect(within(screenEl).getByRole("button", { name: "Add product" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Products" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByTestId("central-board")).not.toBeInTheDocument();
    await userEvent.click(within(nav()!).getByRole("button", { name: "Board" }));
    expect(await screen.findByTestId("central-board")).toBeInTheDocument();
  });

  it("offers a company Admin no Products section and never requests the catalog", async () => {
    const calls = backend([{ slug: "lity", name: "Lity", role: "admin" }]);
    render(<CentralApp />);
    await screen.findByTestId("central-board");
    expect(within(nav()!).queryByRole("button", { name: "Products" })).not.toBeInTheDocument();
    expect(calls.some((u) => u.endsWith("/products"))).toBe(false);
  });

  it("opens Database from the top navigation with both existing actions, and not the board", async () => {
    backend([{ slug: "lity", name: "Lity", role: "super_admin" }]);
    render(<CentralApp />);
    await screen.findByTestId("central-board");
    await userEvent.click(within(nav()!).getByRole("button", { name: "Database" }));
    const screenEl = await screen.findByTestId("central-database-screen");
    expect(within(screenEl).getByRole("button", { name: "Export Current Database" })).toBeEnabled();
    expect(within(screenEl).getByRole("button", { name: "Download Import Template" })).toBeEnabled();
    expect(screen.queryByTestId("central-board")).not.toBeInTheDocument();
  });

  it("offers a company Admin the Database section but not Users", async () => {
    backend([{ slug: "lity", name: "Lity", role: "admin" }]);
    render(<CentralApp />);
    await screen.findByTestId("central-board");
    expect(within(nav()!).queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    await userEvent.click(within(nav()!).getByRole("button", { name: "Database" }));
    expect(await screen.findByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
  });

  it.each(["sales_member", "visitor"])("offers a %s no Users, Products or Database section at all", async (role) => {
    const calls = backend([{ slug: "lity", name: "Lity", role }]);
    render(<CentralApp />);
    await screen.findByTestId("central-board");
    // Board, Reports and KPIs are reads every role has; the management sections are not offered.
    expect(within(nav()!).getAllByRole("button").map((b) => b.textContent)).toEqual(["Board", "Reports", "KPIs"]);
    expect(screen.queryByRole("button", { name: /^(Users|Products|Database)$/ })).not.toBeInTheDocument();
    expect(calls.some((u) => /members|invitations|excel/.test(u))).toBe(false);
  });

  it("never opens a section for a company the role does not have it in (switching starts on the board)", async () => {
    backend([{ slug: "lity", name: "Lity", role: "super_admin" }, { slug: "valor-log", name: "Valor Log", role: "sales_member" }]);
    render(<CentralApp />);
    await enter("Lity");
    await userEvent.click(await screen.findByRole("button", { name: "Users" }));
    await screen.findByRole("heading", { name: "Users of Lity" });
    await userEvent.click(screen.getByRole("button", { name: "Switch company" }));
    await enter("Valor Log");
    expect(await screen.findByTestId("central-board")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Users of/)).not.toBeInTheDocument();
  });

  it("keeps Switch company working from a section and returns to the picker", async () => {
    backend([{ slug: "lity", name: "Lity", role: "super_admin" }, { slug: "salcomp", name: "Salcomp", role: "super_admin" }]);
    render(<CentralApp />);
    await enter("Lity");
    await userEvent.click(await screen.findByRole("button", { name: "Database" }));
    await userEvent.click(screen.getByRole("button", { name: "Switch company" }));
    expect(await screen.findByRole("heading", { name: "Choose a company" })).toBeInTheDocument();
    await enter("Salcomp");
    expect(await screen.findByTestId("central-board")).toBeInTheDocument(); // a new company always opens on its board
    expect(screen.queryByTestId("central-database-screen")).not.toBeInTheDocument();
  });

  it("says a company without a board has nothing to download yet", async () => {
    backend([{ slug: "lity", name: "Lity", role: "super_admin" }], []);
    render(<CentralApp />);
    await screen.findByTestId("central-empty-workspace");
    await userEvent.click(await screen.findByRole("button", { name: "Database" }));
    await waitFor(() => expect(screen.getByTestId("central-database-screen")).toHaveTextContent("has no board yet"));
    expect(screen.queryByRole("button", { name: "Export Current Database" })).not.toBeInTheDocument();
  });

  describe("controls in the top navigation and on the Database screen", () => {
    const header = () => screen.getByTestId("central-header");

    it("puts Undo in the top navigation, not above the columns, with its description as the tooltip", async () => {
      backend([{ slug: "lity", name: "Lity", role: "super_admin" }]);
      render(<CentralApp />);
      const undo = await screen.findByTestId("undo-button");
      expect(within(header()).getByTestId("undo-button")).toBe(undo);
      expect(within(screen.getByTestId("central-board")).queryByTestId("undo-button")).not.toBeInTheDocument();
      expect(undo).toHaveAttribute("title", 'Undo edit to "Lead 1"');
      expect(undo).toBeEnabled();
    });

    it("puts Manage boards and columns in the top navigation and opens its panel above the board", async () => {
      backend([{ slug: "lity", name: "Lity", role: "super_admin" }]);
      render(<CentralApp />);
      await screen.findByTestId("central-board");
      const toggle = await within(header()).findByRole("button", { name: "Manage boards and columns" });
      expect(screen.queryByRole("button", { name: "Hide board administration" })).not.toBeInTheDocument();
      expect(within(screen.getByTestId("central-board")).queryByRole("button", { name: /Manage boards/ })).not.toBeInTheDocument();
      await userEvent.click(toggle);
      expect(within(header()).getByRole("button", { name: "Hide board administration" })).toHaveAttribute("aria-expanded", "true");
      const panel = await screen.findByTestId("board-admin");
      const boardEl = screen.getByTestId("central-board");
      expect(panel.compareDocumentPosition(boardEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // the panel comes first
    });

    it("moves Import Cards from Excel to the Database screen, next to the two downloads", async () => {
      backend([{ slug: "lity", name: "Lity", role: "super_admin" }]);
      render(<CentralApp />);
      await screen.findByTestId("central-board");
      expect(screen.queryByRole("heading", { name: "Import Cards from Excel" })).not.toBeInTheDocument();
      await userEvent.click(within(nav()!).getByRole("button", { name: "Database" }));
      const database = await screen.findByTestId("central-database-screen");
      expect(await within(database).findByRole("heading", { name: "Import Cards from Excel" })).toBeInTheDocument();
      expect(within(database).getByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
      expect(within(database).getByRole("button", { name: "Download Import Template" })).toBeInTheDocument();
    });

    it("offers the AI Assistant button to a writer, opens the company assistant, shows refused actions and closes it", async () => {
      const ask = vi.fn<(body: unknown) => Response>(() => json(200, {
        response: "Done what I could.",
        actions_applied: { successful: [{ type: "update" }], failed: [{ action: {}, code: "card_version_conflict", card_id: 1, reason: "The card was changed after the assistant was shown it, so this action was not applied" }] },
      }));
      const calls = backend([{ slug: "lity", name: "Lity", role: "super_admin" }], [board], ask);
      render(<CentralApp />);
      await screen.findByTestId("central-board");
      expect(screen.queryByRole("complementary", { name: "AI Assistant" })).not.toBeInTheDocument();
      await userEvent.click(within(header()).getByRole("button", { name: "AI Assistant" }));
      const panel = await screen.findByRole("complementary", { name: "AI Assistant" });
      expect(within(header()).getByRole("button", { name: "AI Assistant" })).toHaveAttribute("aria-expanded", "true");
      expect(header()).toBeVisible(); // the navigation is not covered by the panel (the panel sits beside the board, below the header)
      expect(header().contains(panel)).toBe(false);
      await userEvent.type(within(panel).getByPlaceholderText(/Ask me to organize/), "tidy up");
      await userEvent.click(within(panel).getByRole("button", { name: "Send" }));
      expect(await within(panel).findByText(/Done what I could\./)).toHaveTextContent("changed after the assistant was shown it");
      expect(ask).toHaveBeenCalledWith({ message: "tidy up", history: [] });
      expect(calls.some((u) => /\/api\/companies\/lity\/boards\/3\/ai$/.test(u))).toBe(true); // the company endpoint, never the legacy one
      expect(calls.some((u) => /^\/api\/boards\//.test(u) || /\/api\/boards\/3\/ai/.test(u))).toBe(false);
      await userEvent.click(within(panel).getByRole("button", { name: "Close AI Assistant" }));
      expect(screen.queryByRole("complementary", { name: "AI Assistant" })).not.toBeInTheDocument();
      expect(screen.getByTestId("central-board")).toBeInTheDocument(); // the board is still usable
    });

    it("does not offer the AI Assistant to a Visitor", async () => {
      backend([{ slug: "lity", name: "Lity", role: "visitor" }]);
      render(<CentralApp />);
      await screen.findByTestId("central-board");
      expect(screen.queryByRole("button", { name: "AI Assistant" })).not.toBeInTheDocument();
    });
  });
});
