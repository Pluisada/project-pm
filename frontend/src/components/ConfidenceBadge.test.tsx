import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConfidenceBadge } from "@/components/ConfidenceBadge";

describe("ConfidenceBadge", () => {
  it.each([["LOW"], ["MEDIUM"], ["HIGH"]] as const)("always carries the %s text, not only a colour", (level) => {
    render(<ConfidenceBadge level={level} />);
    expect(screen.getByTestId("confidence-badge")).toHaveTextContent(level);
  });

  it("renders nothing for null unless asked, then a neutral Not assigned", () => {
    const { container, rerender } = render(<ConfidenceBadge level={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ConfidenceBadge level={null} showUnassigned />);
    expect(screen.getByTestId("confidence-badge")).toHaveTextContent("Not assigned");
    expect(screen.getByTestId("confidence-badge")).toHaveAttribute("data-level", "none");
  });
});

// jsdom has no layout or computed theme, so readability is checked on the declared colours: WCAG contrast of text on
// its background, in the light block of globals.css (the dark values arrive with the theme).
const css = readFileSync(join(__dirname, "../app/globals.css"), "utf8");
const block = (start: string) => {
  const from = css.indexOf(start);
  return css.slice(from, css.indexOf("}", from));
};
const light = block(":root {");

const channel = (v: number) => {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * channel(n >> 16) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
};
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const value = (scope: string, name: string) => {
  const match = scope.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`${name} missing`);
  return match[1];
};

describe("probability level colours", () => {
  it("light theme: text on background is readable for every level", () => {
    const scope = light;
    for (const level of ["low", "medium", "high"]) {
      expect(contrast(value(scope, `--conf-${level}-ink`), value(scope, `--conf-${level}-bg`))).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("uses red for LOW, yellow for MEDIUM and green for HIGH", () => {
    const hue = (hex: string) => {
      const n = parseInt(hex.slice(1), 16);
      const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255];
      return { r, g, b };
    };
    for (const scope of [light]) {
      const low = hue(value(scope, "--conf-low-dot"));
      const medium = hue(value(scope, "--conf-medium-dot"));
      const high = hue(value(scope, "--conf-high-dot"));
      expect(low.r).toBeGreaterThan(low.g + 60);
      expect(medium.r).toBeGreaterThan(medium.b + 100);
      expect(medium.g).toBeGreaterThan(medium.b + 100);
      expect(high.g).toBeGreaterThan(high.r + 40);
    }
  });
});
