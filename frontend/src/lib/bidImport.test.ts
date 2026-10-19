import { describe, expect, it } from "vitest";
import type { ImportPreviewRow } from "@/lib/api";
import { clearChoice, decisionsKey, matchesFilter, toDecisions, updateChoice } from "@/lib/bidImport";

const row = (over: Partial<ImportPreviewRow>): ImportPreviewRow =>
  ({
    row_number: 2, action: "create", errors: [], warnings: [], reasons: [], required_decisions: [], ...over,
  }) as ImportPreviewRow;

describe("toDecisions", () => {
  it("sends only rows with an explicit choice, sorted by row", () => {
    const decisions = toDecisions({
      9: { confirm_update: true },
      3: { target_stage: "RFI" },
      5: { confirm_move: false },
    });
    expect(decisions).toEqual([
      { row_number: 3, target_stage: "RFI" },
      { row_number: 9, confirm_update: true },
    ]);
  });

  it("never sends false flags", () => {
    expect(toDecisions({ 4: { skip: true, confirm_update: false } })).toEqual([{ row_number: 4, skip: true }]);
  });
});

describe("updateChoice", () => {
  it("skip excludes the other choices", () => {
    const next = updateChoice({ 4: { target_stage: "RFI", confirm_update: true } }, 4, { skip: true });
    expect(next[4]).toEqual({ skip: true });
  });

  it("a stage replaces accepting the proposal, and vice versa", () => {
    let choices = updateChoice({}, 4, { confirm_proposed_stage: true });
    choices = updateChoice(choices, 4, { target_stage: "Proposal" });
    expect(toDecisions(choices)).toEqual([{ row_number: 4, target_stage: "Proposal" }]);
    choices = updateChoice(choices, 4, { confirm_proposed_stage: true });
    expect(toDecisions(choices)).toEqual([{ row_number: 4, confirm_proposed_stage: true }]);
  });

  it("choosing a stage lifts a skip", () => {
    const choices = updateChoice({ 4: { skip: true } }, 4, { target_stage: "RFI" });
    expect(toDecisions(choices)).toEqual([{ row_number: 4, target_stage: "RFI" }]);
  });

  it("clearChoice drops the row", () => {
    expect(decisionsKey(clearChoice({ 4: { skip: true } }, 4))).toBe("[]");
  });
});

describe("matchesFilter", () => {
  it("filters by pending decisions, errors, and action", () => {
    const pending = row({ required_decisions: ["confirm_update"], action: "review" });
    expect(matchesFilter(pending, "pending")).toBe(true);
    expect(matchesFilter(pending, "create")).toBe(false);
    expect(matchesFilter(row({ errors: [{ severity: "error" } as never] }), "errors")).toBe(true);
    expect(matchesFilter(row({}), "all")).toBe(true);
  });
});
