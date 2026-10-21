import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { BRAND_ATTRIBUTE, brandFor, useCompanyBranding } from "@/lib/branding";

const attr = () => document.documentElement.getAttribute(BRAND_ATTRIBUTE);

describe("brandFor", () => {
  it.each([
    ["salcomp", "Salcomp", "/brand/salcomp-logo.png"],
    ["valor-log", "Valor-Log", "/brand/valorlog-wordmark.png"],
    ["lity", "Lity", "/brand/lity-logo.webp"],
  ])("%s has its own name and logo", (slug, name, src) => {
    expect(brandFor(slug)).toMatchObject({ slug, name, logo: { src } });
  });

  it.each([null, undefined, "", "other", "Salcomp", "__proto__", "constructor", "toString"])(
    "%j has no brand (neutral fallback)",
    (slug) => {
      expect(brandFor(slug)).toBeNull();
    }
  );

  it("never shares a logo between companies", () => {
    const logos = ["salcomp", "valor-log", "lity"].map((s) => brandFor(s)!.logo.src);
    expect(new Set(logos).size).toBe(3);
  });
});

describe("useCompanyBranding", () => {
  afterEach(() => document.documentElement.removeAttribute(BRAND_ATTRIBUTE));

  it("applies the company, follows a switch and leaves nothing behind", () => {
    const { rerender, unmount } = renderHook(({ slug }: { slug: string | null }) => useCompanyBranding(slug), {
      initialProps: { slug: "lity" as string | null },
    });
    expect(attr()).toBe("lity");
    rerender({ slug: "valor-log" });
    expect(attr()).toBe("valor-log");
    rerender({ slug: "salcomp" });
    expect(attr()).toBe("salcomp");
    rerender({ slug: null });
    expect(attr()).toBeNull();
    rerender({ slug: "lity" });
    unmount();
    expect(attr()).toBeNull();
  });

  it("an unknown company clears the previous identity instead of keeping it or guessing", () => {
    const { rerender } = renderHook(({ slug }: { slug: string | null }) => useCompanyBranding(slug), {
      initialProps: { slug: "salcomp" as string | null },
    });
    rerender({ slug: "not-a-company" });
    expect(attr()).toBeNull();
  });
});

// Contrast of the palettes in globals.css, light and dark (WCAG 2 relative luminance).
const css = readFileSync(path.resolve(__dirname, "../app/globals.css"), "utf8");
const darkStart = css.indexOf(':root[data-company][data-theme="dark"]');
const darkEnd = css.indexOf(':root[data-company] ::selection');
const lightCss = css.slice(0, darkStart);
const darkCss = css.slice(darkStart, darkEnd);

const declarations = (source: string, selector: string): Record<string, string> => {
  const block = source.match(new RegExp(`${selector.replace(/[[\]"]/g, "\\$&")}\\s*\\{([^}]*)\\}`));
  const out: Record<string, string> = {};
  for (const m of (block?.[1] ?? "").matchAll(/(--[a-z-]+):\s*([^;]+);/g)) out[m[1]] = m[2].replace(/\/\*.*?\*\//g, "").trim();
  return out;
};

const tokens = (slug: string, mode: "light" | "dark") => {
  const merged = {
    ...declarations(lightCss, ":root"),
    ...declarations(lightCss, `:root[data-company="${slug}"]`),
    ...(mode === "dark" ? declarations(darkCss, `:root[data-company="${slug}"][data-theme="dark"]`) : {}),
  };
  const resolve = (name: string, depth = 0): string => {
    const value = merged[name];
    const ref = value?.match(/^var\((--[a-z-]+)\)$/);
    return ref && depth < 5 ? resolve(ref[1], depth + 1) : value;
  };
  return resolve;
};

const luminance = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

describe.each(["salcomp", "lity", "valor-log"])("%s palette contrast", (slug) => {
  describe.each(["light", "dark"] as const)("%s", (mode) => {
    const t = tokens(slug, mode);
    const pairs: [string, string, string, number][] = [
      ["text on page", "--ink", "--canvas", 7],
      ["text on surface", "--ink", "--panel", 7],
      ["secondary text on page", "--muted", "--canvas", 4.5],
      ["secondary text on surface", "--muted", "--panel", 4.5],
      ["brand text and focus on page", "--brand", "--canvas", 4.5],
      ["brand text and focus on surface", "--brand", "--panel", 4.5],
      ["white label on filled button", "#ffffff", "--brand-secondary", 4.5],
      ["badge text on accent", "--on-accent", "--accent", 4.5],
      ["navigation text", "--nav-ink", "--nav-bg", 4.5],
      ["navigation secondary text", "--nav-muted", "--nav-bg", 4.5],
      ["navigation active link", "--nav-brand", "--nav-hover", 4.5],
      ["navigation focus ring", "--nav-brand", "--nav-bg", 3],
    ];
    it.each(pairs)("%s", (_label, fg, bg, min) => {
      const f = fg.startsWith("#") ? fg : t(fg);
      expect(f, fg).toMatch(/^#[0-9a-f]{6}$/);
      expect(t(bg), bg).toMatch(/^#[0-9a-f]{6}$/);
      expect(contrast(f, t(bg))).toBeGreaterThanOrEqual(min);
    });
  });

  it("changes between light and dark instead of reusing one palette", () => {
    expect(tokens(slug, "dark")("--canvas")).not.toBe(tokens(slug, "light")("--canvas"));
    expect(luminance(tokens(slug, "dark")("--canvas"))).toBeLessThan(0.05);
  });
});

it("the three companies do not share a brand, canvas or logo-independent identity", () => {
  const brands = ["salcomp", "lity", "valor-log"].map((s) => tokens(s, "light")("--brand"));
  expect(new Set(brands).size).toBe(3);
  const navs = ["salcomp", "lity", "valor-log"].map((s) => tokens(s, "light")("--nav-bg"));
  expect(tokens("valor-log", "light")("--nav-bg")).not.toBe(tokens("salcomp", "light")("--nav-bg"));
  expect(navs.length).toBe(3);
});
