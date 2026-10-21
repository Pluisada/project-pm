"use client";

import type { ThemePreference } from "@/lib/theme";

const icon = (children: React.ReactNode) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
);

const OPTIONS: { value: ThemePreference; label: string; icon: React.ReactNode }[] = [
  {
    value: "light",
    label: "Light",
    icon: icon(
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </>
    ),
  },
  { value: "dark", label: "Dark", icon: icon(<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />) },
  {
    value: "system",
    label: "System",
    icon: icon(
      <>
        <rect x="3" y="4" width="18" height="12" rx="2" />
        <path d="M8 20h8M12 16v4" />
      </>
    ),
  },
];

interface ThemeToggleProps {
  value: ThemePreference;
  onChange: (next: ThemePreference) => void;
}

/** Light / Dark / System, drawn with the navigation tokens so it fits every company's header. */
export const ThemeToggle = ({ value, onChange }: ThemeToggleProps) => (
  <div role="group" aria-label="Theme" className="flex items-center rounded-md border border-[var(--nav-line)] p-0.5">
    {OPTIONS.map((option) => (
      <button
        key={option.value}
        type="button"
        title={option.label}
        aria-label={option.label}
        aria-pressed={value === option.value}
        onClick={() => onChange(option.value)}
        className={`rounded p-1.5 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--nav-brand)] ${
          value === option.value ? "bg-[var(--nav-hover)] text-[var(--nav-brand)]" : "text-[var(--nav-muted)] hover:text-[var(--nav-ink)]"
        }`}
      >
        {option.icon}
      </button>
    ))}
  </div>
);
