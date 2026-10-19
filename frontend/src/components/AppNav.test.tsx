import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppNav } from "@/components/AppNav";

const setup = (role: "admin" | "member", view: "board" | "reports" = "board") => {
  const onNavigate = vi.fn();
  const onLogout = vi.fn();
  render(<AppNav view={view} role={role} username="ana" onNavigate={onNavigate} onLogout={onLogout} />);
  return { onNavigate, onLogout };
};

describe("AppNav", () => {
  it("shows admin-only sections to admins only", () => {
    setup("member");
    expect(screen.queryByRole("button", { name: "Manage Users" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Import Bids" })).toBeNull();
    expect(screen.getByRole("button", { name: "Reports" })).toBeInTheDocument();
  });

  it("lists every section for an admin and marks the active one", () => {
    setup("admin", "reports");
    for (const name of ["Board", "Dashboard", "Reports", "Manage Users", "Import Bids"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Reports" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Board" })).not.toHaveAttribute("aria-current");
  });

  it("navigates and logs out", async () => {
    const { onNavigate, onLogout } = setup("admin");
    await userEvent.click(screen.getByRole("button", { name: "Dashboard" }));
    expect(onNavigate).toHaveBeenCalledWith("dashboard");
    await userEvent.click(screen.getByRole("button", { name: "Logout" }));
    expect(onLogout).toHaveBeenCalled();
  });

  it("mobile menu toggles with aria-expanded, closes on selection and Escape", async () => {
    const { onNavigate } = setup("admin");
    const toggle = screen.getByRole("button", { name: "Open menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    const menu = document.getElementById("mobile-nav")!;
    expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(within(menu).getByRole("button", { name: "Import Bids" }));
    expect(onNavigate).toHaveBeenCalledWith("import");
    expect(document.getElementById("mobile-nav")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Open menu" }));
    await userEvent.keyboard("{Escape}");
    expect(document.getElementById("mobile-nav")).toBeNull();
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveFocus();
  });
});
