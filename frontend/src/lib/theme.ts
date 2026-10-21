/**
 * Light / dark preference. Persisted per browser; "system" follows the operating system.
 * The resolved value is written to <html data-theme>; globals.css only reacts to that attribute
 * (and only inside a company identity, see lib/branding.ts).
 */
import { useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";

export const THEME_KEY = "pm_theme";

export function readThemePreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system"; // storage blocked, or no window during the static prerender
  }
}

export function useThemePreference(): [ThemePreference, (next: ThemePreference) => void] {
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference);

  useEffect(() => {
    const root = document.documentElement;
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const apply = () => root.setAttribute("data-theme", preference === "system" ? (media?.matches ? "dark" : "light") : preference);
    apply();
    if (preference !== "system" || !media) return;
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [preference]);

  const update = (next: ThemePreference) => {
    setPreference(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // the choice still applies for this visit
    }
  };
  return [preference, update];
}
