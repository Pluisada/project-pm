"use client";

import { useEffect, useRef, useState } from "react";
import type { UserRole } from "@/lib/auth";

export type AppView = "board" | "dashboard" | "reports" | "manage-users" | "import";

const SECTIONS: { view: AppView; label: string; adminOnly?: boolean }[] = [
  { view: "board", label: "Board" },
  { view: "dashboard", label: "Dashboard" },
  { view: "reports", label: "Reports" },
  { view: "manage-users", label: "Manage Users", adminOnly: true },
  { view: "import", label: "Import Bids", adminOnly: true },
];

interface AppNavProps {
  view: AppView;
  role: UserRole | null;
  username: string;
  onNavigate: (view: AppView) => void;
  onLogout: () => void;
}

const linkClass = (active: boolean) =>
  `whitespace-nowrap rounded-md px-2 py-1.5 lg:px-3 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary-blue)] ${
    active
      ? "bg-[var(--surface)] text-[var(--primary-blue)] shadow-[inset_0_-2px_0_var(--primary-blue)]"
      : "text-[var(--gray-text)] hover:bg-[var(--surface)] hover:text-[var(--navy-dark)]"
  }`;

export const AppNav = ({ view, role, username, onNavigate, onLogout }: AppNavProps) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sections = SECTIONS.filter((s) => !s.adminOnly || role === "admin");

  useEffect(() => {
    if (!menuOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  const go = (next: AppView) => {
    setMenuOpen(false);
    onNavigate(next);
  };

  const renderLinks = (className: string) =>
    sections.map((s) => (
      <button
        key={s.view}
        type="button"
        onClick={() => go(s.view)}
        aria-current={view === s.view ? "page" : undefined}
        className={`${className} ${linkClass(view === s.view)}`}
      >
        {s.label}
      </button>
    ));

  return (
    <header className="relative z-40 border-b border-[var(--stroke)] bg-white">
      <div className="flex h-12 items-center gap-3 px-4">
        <span className="whitespace-nowrap text-sm font-bold text-[var(--navy-dark)]">
          <span className="mr-2 inline-block h-2 w-2 rounded-full bg-[var(--accent-yellow)] align-middle" />
          Salcomp Sales
        </span>

        <nav aria-label="Main" className="ml-2 hidden items-center gap-1 md:flex lg:ml-4">
          {renderLinks("")}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          {/* The board portals its Undo control here; empty on other screens. */}
          <div id="nav-undo-slot" className="flex min-w-0 items-center gap-2" />
          <span className="hidden max-w-[12rem] truncate text-sm text-[var(--gray-text)] lg:inline" title={username}>
            Welcome, <span className="font-semibold text-[var(--navy-dark)]">{username}</span>
          </span>
          <button
            type="button"
            onClick={onLogout}
            title={`Signed in as ${username}`}
            className="rounded-md border border-[var(--stroke)] px-3 py-1.5 text-sm font-semibold text-[var(--navy-dark)] transition hover:border-[var(--primary-blue)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary-blue)]"
          >
            Logout
          </button>
          <button
            ref={menuButtonRef}
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            className="rounded-md border border-[var(--stroke)] px-3 py-1.5 text-sm font-semibold text-[var(--navy-dark)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary-blue)] md:hidden"
          >
            {menuOpen ? "✕" : "☰"}
          </button>
        </div>
      </div>

      {menuOpen && (
        <nav
          id="mobile-nav"
          aria-label="Main"
          className="absolute inset-x-0 top-full flex flex-col gap-1 border-b border-[var(--stroke)] bg-white p-2 shadow-[var(--shadow)] md:hidden"
        >
          <p className="px-3 py-1 text-xs text-[var(--gray-text)]">Welcome, {username}</p>
          {renderLinks("w-full text-left")}
        </nav>
      )}
    </header>
  );
};
