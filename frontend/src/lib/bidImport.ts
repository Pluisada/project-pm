/**
 * Client-side state of the Bid import screen. Only turns the admin's choices
 * into the decisions the backend asks for; which rows need what, and whether
 * a decision fits, is always decided by the backend's preview.
 */
import type { ImportDecision, ImportPreviewRow } from "@/lib/api";

export type RowChoice = Omit<ImportDecision, "row_number">;
export type Choices = Record<number, RowChoice>;

export type RowFilter = "all" | "pending" | "errors" | "warnings" | "create" | "update" | "ignore";

export const ROW_FILTERS: { value: RowFilter; label: string }[] = [
  { value: "pending", label: "Needs decision" },
  { value: "errors", label: "Errors" },
  { value: "warnings", label: "Warnings" },
  { value: "create", label: "New" },
  { value: "update", label: "Updates" },
  { value: "ignore", label: "Ignored" },
  { value: "all", label: "All" },
];

const hasChoice = (choice: RowChoice): boolean =>
  !!(choice.target_stage || choice.confirm_proposed_stage || choice.skip || choice.confirm_update || choice.confirm_move);

/** The decisions to send: one per row that has at least one explicit choice. */
export function toDecisions(choices: Choices): ImportDecision[] {
  return Object.entries(choices)
    .filter(([, choice]) => hasChoice(choice))
    .map(([row, choice]) => ({
      row_number: Number(row),
      ...(choice.target_stage ? { target_stage: choice.target_stage } : {}),
      ...(choice.confirm_proposed_stage ? { confirm_proposed_stage: true } : {}),
      ...(choice.skip ? { skip: true } : {}),
      ...(choice.confirm_update ? { confirm_update: true } : {}),
      ...(choice.confirm_move ? { confirm_move: true } : {}),
    }))
    .sort((a, b) => a.row_number - b.row_number);
}

/** Stable text of the decisions, to tell whether a preview is still current. */
export const decisionsKey = (choices: Choices): string => JSON.stringify(toDecisions(choices));

/**
 * Apply one change to a row's choice. Skipping excludes every other choice,
 * and a chosen stage replaces accepting the proposed one (and vice versa).
 */
export function updateChoice(choices: Choices, row: number, change: RowChoice): Choices {
  const next: RowChoice = { ...choices[row], ...change };
  if (change.skip) return { ...choices, [row]: { skip: true } };
  if (change.target_stage) next.confirm_proposed_stage = false;
  if (change.confirm_proposed_stage) next.target_stage = undefined;
  if (change.target_stage || change.confirm_proposed_stage) next.skip = false;
  return { ...choices, [row]: next };
}

export function clearChoice(choices: Choices, row: number): Choices {
  const { [row]: _removed, ...rest } = choices;
  void _removed;
  return rest;
}

export function matchesFilter(row: ImportPreviewRow, filter: RowFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "pending":
      return row.required_decisions.length > 0;
    case "errors":
      return row.errors.length > 0 || row.reasons.some((r) => r.severity === "error");
    case "warnings":
      return row.warnings.length > 0;
    default:
      return row.action === filter;
  }
}

/** Cents as a currency amount. */
export function formatCents(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
}
