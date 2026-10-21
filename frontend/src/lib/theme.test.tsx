import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { THEME_KEY, readThemePreference, useThemePreference } from "@/lib/theme";

const theme = () => document.documentElement.getAttribute("data-theme");

// jsdom has no matchMedia: a controllable one.
const mockOs = (dark: boolean) => {
  const listeners = new Set<() => void>();
  const media = {
    matches: dark,
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  };
  vi.stubGlobal("matchMedia", () => media);
  return (next: boolean) => {
    media.matches = next;
    listeners.forEach((fn) => fn());
  };
};

describe("theme preference", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });
  afterEach(() => vi.unstubAllGlobals());

  it("defaults to system and follows the OS, including live changes", () => {
    const setOs = mockOs(true);
    const { result } = renderHook(() => useThemePreference());
    expect(result.current[0]).toBe("system");
    expect(theme()).toBe("dark");
    act(() => setOs(false));
    expect(theme()).toBe("light");
  });

  it("an explicit choice wins over the OS, is saved, and stops following it", () => {
    const setOs = mockOs(true);
    const { result } = renderHook(() => useThemePreference());
    act(() => result.current[1]("light"));
    expect(theme()).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    act(() => setOs(false));
    act(() => setOs(true));
    expect(theme()).toBe("light");
    act(() => result.current[1]("dark"));
    expect(theme()).toBe("dark");
  });

  it("restores the saved choice on the next visit", () => {
    mockOs(false);
    localStorage.setItem(THEME_KEY, "dark");
    const { result } = renderHook(() => useThemePreference());
    expect(result.current[0]).toBe("dark");
    expect(theme()).toBe("dark");
  });

  it("treats an unknown stored value as system", () => {
    localStorage.setItem(THEME_KEY, "purple");
    expect(readThemePreference()).toBe("system");
  });

  it("still works when storage is blocked", () => {
    mockOs(false);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = renderHook(() => useThemePreference());
    expect(result.current[0]).toBe("system");
    act(() => result.current[1]("dark"));
    expect(theme()).toBe("dark");
    vi.restoreAllMocks();
  });
});
