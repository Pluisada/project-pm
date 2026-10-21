import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanySelectPage } from "@/components/CompanySelectPage";

const companies = [
  { slug: "salcomp", name: "Salcomp", role: "super_admin" },
  { slug: "lity", name: "Lity", role: "sales_member" },
  { slug: "valor-log", name: "Valor Log", role: "visitor" },
  { slug: "elsewhere", name: "Elsewhere", role: "admin" },
];

describe("CompanySelectPage", () => {
  it("lists each company the server returned with the person's role there", () => {
    render(<CompanySelectPage companies={companies} onSelect={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Choose a company" })).toBeInTheDocument();
    const items = screen.getAllByRole("button").filter((b) => b.textContent !== "Sign out");
    expect(items.map((b) => b.textContent)).toEqual([
      "SalcompAdministrator", "LitySales Member", "Valor LogVisitor (read-only)", "ElsewhereCompany Admin",
    ]);
  });

  it("reports the chosen slug and nothing else", async () => {
    const onSelect = vi.fn();
    render(<CompanySelectPage companies={companies} onSelect={onSelect} onLogout={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Lity/ }));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("lity");
  });

  it("shows decorative logos only for known companies", () => {
    const { container } = render(<CompanySelectPage companies={companies} onSelect={vi.fn()} onLogout={vi.fn()} />);
    const logos = [...container.querySelectorAll("img")];
    expect(logos.map((i) => i.getAttribute("src"))).toEqual(["/brand/salcomp-logo.png", "/brand/lity-logo.webp", "/brand/valorlog-wordmark.png"]);
    for (const img of logos) expect(img).toHaveAttribute("alt", "");
  });

  it("explains an account with no company instead of listing anything", () => {
    render(<CompanySelectPage companies={[]} onSelect={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText(/no active company membership/)).toBeInTheDocument();
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Sign out"]);
  });

  it("shows a notice and a problem when given them, and signs out on request", async () => {
    const onLogout = vi.fn();
    render(<CompanySelectPage companies={companies} onSelect={vi.fn()} onLogout={onLogout} notice="Account changed." problem="No access." />);
    expect(screen.getByRole("status")).toHaveTextContent("Account changed.");
    expect(screen.getByRole("alert")).toHaveTextContent("No access.");
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });
});
