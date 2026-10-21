"use client";

import type { CentralCompany } from "@/lib/api";
import { dismissWriteOutcome, useWriteOutcomes, type WriteOutcome } from "@/lib/writeOutcomes";

const message = (outcome: WriteOutcome, company: string): string => {
  switch (outcome.result) {
    case "saved":
      return `"${outcome.action}" in ${company} finished saving after you left it. Open ${company} to see the result.`;
    case "failed":
      return `"${outcome.action}" in ${company} was refused by the server${outcome.detail ? `: ${outcome.detail.replace(/\.$/, "")}.` : "."}`;
    default:
      return `"${outcome.action}" in ${company} may or may not have been saved: no confirmation arrived before you left. Check ${company} before repeating it.`;
  }
};

/**
 * What happened to changes the person made in a company they have since left. Each is shown once and can be
 * dismissed. It only reports what the server answered; it never claims a write succeeded without that answer.
 */
export const WriteOutcomeNotices = ({ companies }: { companies: CentralCompany[] }) => {
  const outcomes = useWriteOutcomes();
  if (outcomes.length === 0) return null;
  return (
    <div data-testid="write-outcomes" className="flex flex-col gap-2">
      {outcomes.map((outcome) => {
        const name = companies.find((c) => c.slug === outcome.companySlug)?.name ?? outcome.companySlug;
        return (
          <div key={outcome.id} role={outcome.result === "saved" ? "status" : "alert"}
            className={`flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm font-medium ${
              outcome.result === "saved" ? "border-green-300 text-green-900" : "border-amber-300 text-amber-900"
            }`}>
            <span>{message(outcome, name)}</span>
            <button type="button" onClick={() => dismissWriteOutcome(outcome.id)} className="shrink-0 font-semibold underline">
              Dismiss
            </button>
          </div>
        );
      })}
    </div>
  );
};
