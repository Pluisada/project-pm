"use client";

import type { CentralCompany } from "@/lib/api";
import { brandFor } from "@/lib/branding";
import type { ThemePreference } from "@/lib/theme";
import { ThemeToggle } from "@/components/ThemeToggle";
import { NavSlot } from "@/components/NavSlot";

export type CentralView = "board" | "users" | "database";

interface CentralHeaderProps {
  company: CentralCompany;
  email: string;
  /** More than one company is available to this account. */
  canSwitch: boolean;
  onSwitch: () => void;
  theme: ThemePreference;
  onThemeChange: (next: ThemePreference) => void;
  onSignOut: () => void;
  /** Sections of the company the person may open (the backend still decides every request). Omitted: no navigation. */
  nav?: { current: CentralView; items: { id: CentralView; label: string }[]; onSelect: (view: CentralView) => void };
  /** The Board section's own controls: places for Undo and "Manage boards and columns" (rendered into them by the
   *  board), and the AI Assistant toggle (omitted for a role that cannot use it). */
  boardTools?: { ai?: { open: boolean; onToggle: () => void } };
}

/**
 * The bar above a company's pages: its identity, an explicit way to change company, the sections the person may open
 * (Board, Users, Database; only those offered by the caller), the theme, who is signed in and sign-out.
 */
export const CentralHeader = ({ company, email, canSwitch, onSwitch, theme, onThemeChange, onSignOut, nav, boardTools }: CentralHeaderProps) => {
  const brand = brandFor(company.slug);
  const button =
    "rounded-md border border-[var(--nav-line)] px-3 py-1.5 text-sm font-semibold text-[var(--nav-ink)] transition hover:border-[var(--nav-brand)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--nav-brand)]";
  return (
    <header
      data-testid="central-header"
      className="relative z-40 border-b border-[var(--nav-line)] bg-[var(--nav-bg)] shadow-[inset_0_3px_0_var(--nav-top)]"
    >
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3">
        {brand ? (
          // The logo sits on a --logo-bg chip so a dark-on-light logo stays legible on a dark header.
          <span className="inline-flex shrink-0 items-center rounded-md bg-[var(--logo-bg)] px-1.5 py-1">
            {/* eslint-disable-next-line @next/next/no-img-element -- static export, local asset */}
            <img src={brand.logo.src} width={brand.logo.width} height={brand.logo.height} alt={company.name} className="h-5 w-auto" />
          </span>
        ) : (
          <span className="whitespace-nowrap text-sm font-bold text-[var(--nav-ink)]">{company.name}</span>
        )}
        {canSwitch && (
          <button type="button" onClick={onSwitch} className={button}>
            Switch company
          </button>
        )}
        {nav && nav.items.length > 1 && (
          <nav aria-label="Sections" className="flex flex-wrap items-center gap-2">
            {nav.items.map((item) => (
              <button key={item.id} type="button" onClick={() => nav.onSelect(item.id)}
                aria-current={nav.current === item.id ? "page" : undefined}
                className={`${button} ${nav.current === item.id ? "border-[var(--nav-brand)] bg-[var(--nav-line)]" : ""}`}>
                {item.label}
              </button>
            ))}
          </nav>
        )}
        {boardTools && (
          <div className="flex flex-wrap items-center gap-2" data-testid="board-tools">
            <NavSlot id="central-undo-slot" />
            <NavSlot id="central-boards-slot" />
            {boardTools.ai && (
              <button type="button" onClick={boardTools.ai.onToggle} aria-expanded={boardTools.ai.open} aria-controls="central-ai-panel"
                className={`${button} ${boardTools.ai.open ? "border-[var(--nav-brand)] bg-[var(--nav-line)]" : ""}`}>
                AI Assistant
              </button>
            )}
          </div>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <ThemeToggle value={theme} onChange={onThemeChange} />
          <span className="max-w-[14rem] truncate text-sm text-[var(--nav-muted)]" title={email}>{email}</span>
          <button type="button" onClick={onSignOut} className={button}>
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
};
