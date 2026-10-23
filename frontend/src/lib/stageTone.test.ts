import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { OPEN_TONES, stageTone, stageToneStyle } from "@/lib/stageTone";

describe("stageTone", () => {
  it("walks blue, cyan, violet, amber through the open stages and never uses a title", () => {
    expect([0, 1, 2, 3].map((i) => stageTone("open", i))).toEqual(["blue", "cyan", "violet", "amber"]);
  });

  it("gives won green and lost red, whatever their position", () => {
    expect(stageTone("won", 0)).toBe("green");
    expect(stageTone("lost", 3)).toBe("red");
  });

  it("gives open stages beyond the palette the neutral slate and never goes below the first", () => {
    expect(stageTone("open", 4)).toBe("slate");
    expect(stageTone("open", 9)).toBe("slate");
    expect(stageTone("open", -1)).toBe("blue");
  });

  it("keeps the six seeded stages distinct", () => {
    const tones = [stageTone("open", 0), stageTone("open", 1), stageTone("open", 2), stageTone("open", 3), stageTone("won", 0), stageTone("lost", 0)];
    expect(new Set(tones).size).toBe(6);
  });

  it("never uses green or red for an open stage, so RFI and Award (won) differ", () => {
    for (let i = 0; i < 8; i++) expect(["green", "red"]).not.toContain(stageTone("open", i));
    expect(stageTone("open", 2)).not.toBe(stageTone("won", 0));
  });

  it("styles a column from the stage variables", () => {
    expect(stageToneStyle("green").backgroundColor).toBe("var(--stage-green-bg)");
    expect(stageToneStyle("green").borderTopColor).toBe("var(--stage-green-accent)");
  });

  it("has a background and an accent variable in the stylesheet for every tone", () => {
    const css = readFileSync("src/app/globals.css", "utf8");
    for (const tone of [...OPEN_TONES, "slate", "green", "red"]) {
      expect(css).toContain(`--stage-${tone}-bg:`);
      expect(css).toContain(`--stage-${tone}-accent:`);
    }
  });
});
