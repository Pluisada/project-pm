"use client";

import { useState } from "react";
import clsx from "clsx";
import type { DecisionNeeded, ImportIssue, ImportPreviewRow } from "@/lib/api";
import {
  ROW_FILTERS,
  clearChoice,
  matchesFilter,
  updateChoice,
  type Choices,
  type RowChoice,
  type RowFilter,
} from "@/lib/bidImport";
import { thClass, tdClass, selectClass } from "@/components/ReportBlocks";

const PAGE_SIZE = 100;

const DECISION_LABEL: Record<DecisionNeeded, string> = {
  fix_source_row: "Fix the row in the file, or skip it",
  confirm_stage: "Confirm the stage",
  choose_stage: "Choose a stage",
  resolve_duplicate: "Duplicate: skip all but one",
  confirm_update: "Confirm the update",
  confirm_move: "Confirm the stage move",
};

const ACTION_STYLE: Record<string, string> = {
  create: "bg-green-100 text-green-800",
  update: "bg-blue-100 text-blue-800",
  ignore: "bg-gray-100 text-gray-700",
  review: "bg-yellow-100 text-yellow-800",
};

const Issues = ({ issues, tone }: { issues: ImportIssue[]; tone: "error" | "warning" | "info" }) =>
  issues.length === 0 ? null : (
    <ul
      className={clsx(
        "list-disc pl-5 text-xs",
        tone === "error" && "text-red-700",
        tone === "warning" && "text-yellow-800",
        tone === "info" && "text-[var(--gray-text)]"
      )}
    >
      {issues.map((issue, i) => (
        <li key={`${issue.code}-${i}`}>{issue.message}</li>
      ))}
    </ul>
  );

/** Reasons not already listed as an error or warning of the row. */
const otherReasons = (row: ImportPreviewRow): ImportIssue[] => {
  const shown = new Set([...row.errors, ...row.warnings].map((i) => `${i.code}|${i.message}`));
  return row.reasons.filter((i) => !shown.has(`${i.code}|${i.message}`));
};

const checkboxLabel = "flex items-center gap-2 text-xs text-[var(--navy-dark)]";

interface DecisionControlsProps {
  row: ImportPreviewRow;
  choice: RowChoice;
  stages: string[];
  disabled: boolean;
  onChange: (change: RowChoice) => void;
}

const DecisionControls = ({ row, choice, stages, disabled, onChange }: DecisionControlsProps) => {
  const needs = (d: DecisionNeeded) => row.required_decisions.includes(d);
  const proposed = row.classification.proposed?.title;
  return (
    <div className="flex flex-col gap-2">
      {(needs("confirm_stage") || needs("choose_stage")) && (
        <>
          {needs("confirm_stage") && proposed && (
            <label className={checkboxLabel}>
              <input
                type="checkbox"
                checked={!!choice.confirm_proposed_stage}
                disabled={disabled}
                onChange={(e) => onChange({ confirm_proposed_stage: e.target.checked })}
              />
              Accept proposed stage: {proposed}
            </label>
          )}
          <select
            aria-label={`Stage for row ${row.row_number}`}
            className={selectClass}
            value={choice.target_stage ?? ""}
            disabled={disabled}
            onChange={(e) => onChange({ target_stage: e.target.value || undefined })}
          >
            <option value="">{needs("choose_stage") ? "Choose a stage..." : "Or choose another stage..."}</option>
            {stages.map((title) => (
              <option key={title} value={title}>
                {title}
              </option>
            ))}
          </select>
        </>
      )}
      {needs("confirm_update") && (
        <label className={checkboxLabel}>
          <input
            type="checkbox"
            checked={!!choice.confirm_update}
            disabled={disabled}
            onChange={(e) => onChange({ confirm_update: e.target.checked })}
          />
          Update card #{row.existing_card_id}
        </label>
      )}
      {needs("confirm_move") && row.stage_change && (
        <label className={checkboxLabel}>
          <input
            type="checkbox"
            checked={!!choice.confirm_move}
            disabled={disabled}
            onChange={(e) => onChange({ confirm_move: e.target.checked })}
          />
          Move from {row.stage_change.from} to {row.stage_change.to}
        </label>
      )}
      <label className={checkboxLabel}>
        <input
          type="checkbox"
          checked={!!choice.skip}
          disabled={disabled}
          onChange={(e) => onChange({ skip: e.target.checked })}
        />
        Skip this row
      </label>
    </div>
  );
};

interface BidImportRowsProps {
  rows: ImportPreviewRow[];
  stages: string[];
  choices: Choices;
  disabled: boolean;
  onChoices: (choices: Choices) => void;
}

export const BidImportRows = ({ rows, stages, choices, disabled, onChoices }: BidImportRowsProps) => {
  const pendingCount = rows.filter((r) => r.required_decisions.length > 0).length;
  const [filter, setFilter] = useState<RowFilter>(pendingCount > 0 ? "pending" : "all");
  const [shown, setShown] = useState(PAGE_SIZE);
  const [open, setOpen] = useState<number | null>(null);

  const visible = rows.filter((row) => matchesFilter(row, filter));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Row filter">
        {ROW_FILTERS.map(({ value, label }) => (
          <button
            key={value}
            type="button"
            aria-pressed={filter === value}
            onClick={() => {
              setFilter(value);
              setShown(PAGE_SIZE);
            }}
            className={clsx(
              "rounded-full border px-3 py-1 text-xs font-semibold transition",
              filter === value
                ? "border-[var(--primary-blue)] bg-[var(--primary-blue)] text-white"
                : "border-[var(--stroke)] text-[var(--navy-dark)] hover:border-[var(--primary-blue)]"
            )}
          >
            {label} ({rows.filter((r) => matchesFilter(r, value)).length})
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <p className="text-sm text-[var(--gray-text)]">No rows in this filter.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[var(--stroke)]">
                <th className={thClass}>Row</th>
                <th className={thClass}>Customer / project</th>
                <th className={thClass}>Source</th>
                <th className={thClass}>Action</th>
                <th className={thClass}>Stage</th>
                <th className={thClass}>Details</th>
                <th className={thClass}>Decision</th>
              </tr>
            </thead>
            <tbody>
              {visible.slice(0, shown).map((row) => {
                const choice = choices[row.row_number] ?? {};
                const hasChoice = row.row_number in choices;
                const expanded = open === row.row_number;
                return (
                  <tr key={row.row_number} className="border-b border-[var(--stroke)] last:border-0" data-row={row.row_number}>
                    <td className={tdClass}>{row.row_number}</td>
                    <td className={tdClass}>
                      <div className="font-semibold">{row.customer ?? "-"}</div>
                      <div className="text-xs text-[var(--gray-text)]">{row.project_name ?? row.code ?? ""}</div>
                    </td>
                    <td className={tdClass}>
                      <div>{row.source_status ?? "-"}</div>
                      <div className="text-xs text-[var(--gray-text)]">{row.source_stage ?? ""}</div>
                    </td>
                    <td className={tdClass}>
                      <span className={clsx("rounded-full px-2 py-0.5 text-xs font-semibold", ACTION_STYLE[row.action])}>
                        {row.action}
                      </span>
                    </td>
                    <td className={tdClass}>
                      {row.target_stage ?? row.classification.proposed?.title ?? "-"}
                      <div className="text-xs text-[var(--gray-text)]">{row.classification.status.replace(/_/g, " ")}</div>
                    </td>
                    <td className={tdClass}>
                      <Issues issues={row.errors} tone="error" />
                      <Issues issues={row.warnings} tone="warning" />
                      <Issues issues={otherReasons(row)} tone="info" />
                      {row.duplicate_rows.length > 0 && (
                        <p className="text-xs text-yellow-800">Same key as row(s) {row.duplicate_rows.join(", ")}</p>
                      )}
                      {row.changed_fields.length > 0 && (
                        <button
                          type="button"
                          className="mt-1 text-xs font-semibold text-[var(--primary-blue)]"
                          onClick={() => setOpen(expanded ? null : row.row_number)}
                        >
                          {expanded ? "Hide" : "Show"} {row.changed_fields.length} change(s)
                        </button>
                      )}
                      {expanded && (
                        <ul className="mt-1 text-xs">
                          {row.changed_fields.map((c) => (
                            <li key={c.field}>
                              <span className="font-semibold">{c.field}</span>: {String(c.current ?? "-")} {"->"}{" "}
                              {String(c.incoming ?? "-")}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td className={tdClass}>
                      {row.required_decisions.length > 0 ? (
                        <>
                          <p className="mb-2 text-xs text-[var(--gray-text)]">
                            {row.required_decisions.map((d) => DECISION_LABEL[d] ?? d).join("; ")}
                          </p>
                          <DecisionControls
                            row={row}
                            choice={choice}
                            stages={stages}
                            disabled={disabled}
                            onChange={(change) => onChoices(updateChoice(choices, row.row_number, change))}
                          />
                        </>
                      ) : (
                        <span className="text-xs text-[var(--gray-text)]">-</span>
                      )}
                      {hasChoice && (
                        <button
                          type="button"
                          disabled={disabled}
                          className="mt-2 text-xs font-semibold text-red-700 disabled:opacity-50"
                          onClick={() => onChoices(clearChoice(choices, row.row_number))}
                        >
                          Clear decision
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {visible.length > shown && (
        <button
          type="button"
          onClick={() => setShown((n) => n + PAGE_SIZE)}
          className="self-start rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)]"
        >
          Show more ({visible.length - shown} left)
        </button>
      )}
    </div>
  );
};
