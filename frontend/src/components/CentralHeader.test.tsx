import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralHeader } from "@/components/CentralHeader";

const props = (over = {}) => ({
  company: { slug: "lity", name: "Lity", role: "super_admin" },
  email: "a@b.com",
  canSwitch: true,
  onSwitch: vi.fn(),
  theme: "system" as const,
  onThemeChange: vi.fn(),
  onSignOut: vi.fn(),
  ...over,
});

describe("CentralHeader", () => {
  it("shows the company's own logo, named by the company, and who is signed in", () => {
    render(<CentralHeader {...props()} />);
    expect(screen.getByRole("img", { name: "Lity" })).toHaveAttribute("src", "/brand/lity-logo.webp");
    expect(screen.getByText("a@b.com")).toBeInTheDocument();
  });

  it("falls back to the company name for a company without a logo", () => {
    render(<CentralHeader {...props({ company: { slug: "elsewhere", name: "Elsewhere", role: "visitor" } })} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("Elsewhere")).toBeInTheDocument();
  });

  it("offers Switch company only when there is another company to switch to", async () => {
    const first = props();
    const { rerender } = render(<CentralHeader {...first} />);
    await userEvent.click(screen.getByRole("button", { name: "Switch company" }));
    expect(first.onSwitch).toHaveBeenCalledTimes(1);
    rerender(<CentralHeader {...props({ canSwitch: false })} />);
    expect(screen.queryByRole("button", { name: "Switch company" })).not.toBeInTheDocument();
  });

  it("offers only the controls that exist: theme, switch and sign-out, no workspace links", async () => {
    const p = props();
    render(<CentralHeader {...p} />);
    expect(screen.queryAllByRole("link")).toEqual([]);
    expect(screen.queryByText(/Reports|Dashboard|Products|Import/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dark" }));
    expect(p.onThemeChange).toHaveBeenCalledWith("dark");
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(p.onSignOut).toHaveBeenCalledTimes(1);
  });

  it("marks the current theme", () => {
    render(<CentralHeader {...props({ theme: "light" })} />);
    expect(screen.getByRole("button", { name: "Light" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Dark" })).toHaveAttribute("aria-pressed", "false");
  });
});
