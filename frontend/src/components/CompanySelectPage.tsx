"use client";

import type { ReactNode } from "react";
import type { CentralCompany } from "@/lib/api";
import { brandFor } from "@/lib/branding";

const ROLE_LABEL: Record<string, string> = {
  super_admin: "Administrator",
  admin: "Company Admin",
  sales_member: "Sales Member",
  visitor: "Visitor (read-only)",
};

interface CompanySelectPageProps {
  companies: CentralCompany[];
  onSelect: (slug: string) => void;
  onLogout: () => void;
  /** A status message for the person (for example, the account changed in another tab). */
  notice?: string | null;
  /** A problem to show above the list (for example, access to a company was withdrawn). */
  problem?: string | null;
  /** Extra content under the heading (for example, what became of a change made in a company just left). */
  children?: ReactNode;
}

/**
 * Shown after central sign-in until a company is chosen, and again when the person switches company. It only lists
 * what the server returned for this account and reports the choice; the server decides what each request may do.
 */
export const CompanySelectPage = ({ companies, onSelect, onLogout, notice, problem, children }: CompanySelectPageProps) => (
  <main className="flex min-h-screen items-center justify-center px-6">
    <div className="w-full max-w-md rounded-[24px] border border-[var(--stroke)] bg-[var(--surface-strong)]/80 p-8 shadow-[var(--shadow)]">
      <h1 className="font-display text-2xl font-semibold text-[var(--navy-dark)]">Choose a company</h1>
      {notice && <p role="status" className="mt-4 text-sm font-medium text-amber-900">{notice}</p>}
      {problem && <p role="alert" className="mt-4 text-sm font-medium text-red-800">{problem}</p>}
      {children && <div className="mt-4 empty:hidden">{children}</div>}
      {companies.length === 0 ? (
        <p className="mt-4 text-sm text-[var(--gray-text)]">
          Your account has no active company membership. Ask an administrator to invite you.
        </p>
      ) : (
        <ul className="mt-6 flex flex-col gap-3">
          {companies.map((company) => {
            const brand = brandFor(company.slug);
            return (
              <li key={company.slug}>
                <button
                  type="button"
                  onClick={() => onSelect(company.slug)}
                  className="flex w-full items-center justify-between rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-4 py-3 text-left transition hover:border-[var(--primary-blue)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary-blue)]"
                >
                  <span className="flex items-center gap-3">
                    {brand ? (
                      // Decorative: the name is the label. This screen is neutral, so the logo sits on white in any scheme.
                      <span className="flex h-8 w-20 items-center justify-center rounded bg-white px-1">
                        {/* eslint-disable-next-line @next/next/no-img-element -- static export, local asset */}
                        <img src={brand.logo.src} width={brand.logo.width} height={brand.logo.height} alt="" className="max-h-5 w-auto max-w-full" />
                      </span>
                    ) : null}
                    <span className="font-semibold text-[var(--navy-dark)]">{company.name}</span>
                  </span>
                  <span className="text-xs text-[var(--gray-text)]">{ROLE_LABEL[company.role] ?? company.role}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <button type="button" onClick={onLogout} className="mt-6 text-sm font-semibold text-[var(--primary-blue)] hover:underline">
        Sign out
      </button>
    </div>
  </main>
);
