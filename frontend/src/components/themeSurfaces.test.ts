import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Inside a company identity the dark theme darkens --surface-strong and lightens --ink. A hard-coded white surface
// next to token-colored text then becomes unreadable (it was seen on the Members panel and every form field), so
// the pages of the central shell take their surfaces from the tokens.
const dir = __dirname;
const files = readdirSync(dir).filter((f) => /^(Central.*|ExcelDownloadButtons|CompanySelectPage)\.tsx$/.test(f));

describe("central pages use theme tokens for their surfaces", () => {
  it("finds the pages it is meant to guard", () => {
    expect(files).toEqual(expect.arrayContaining(["CentralMembers.tsx", "CentralCardForm.tsx", "CentralBoardAdmin.tsx", "ExcelDownloadButtons.tsx"]));
  });

  it.each(files)("%s has no hard-coded white surface", (file) => {
    const source = readFileSync(path.join(dir, file), "utf8");
    // The decorative logo chip on the company picker is white on purpose (logos are dark on light).
    const allowed = file === "CompanySelectPage.tsx" ? source.replace("rounded bg-white px-1", "") : source;
    expect(allowed).not.toMatch(/\bbg-white\b/);
  });
});
