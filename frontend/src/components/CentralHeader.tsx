"use client";

import type { CentralCompany } from "@/lib/api";
import { brandFor } from "@/lib/branding";
import type { ThemePreference } from "@/lib/theme";
import { ThemeToggle } from "@/components/ThemeToggle";

interface CentralHeaderProps {
  company: CentralCompany;
  email: string;
  /** More than one company is available to this account. */
  canSwitch: boolean;
  onSwitch: () => void;
  theme: ThemePreference;
  onThemeChange: (next: ThemePreference) => void;
  onSignOut: () => void;
}

/**
 * The bar above a company's pages: its identity, an explicit way to change company, the theme, who is signed in and
 * sign-out. It links to nothing else: pages that do not exist in central mode yet are not offered.
 */
export const CentralHeader = ({ company, email, canSwitch, onSwitch, theme, onThemeChange, onSignOut }: CentralHeaderProps) => {
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
