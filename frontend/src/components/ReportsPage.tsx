"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import {
  CONFIDENCE_LEVELS,
  getBoardReport,
  getCompanyLeadOptions,
  getErrorMessage,
  getLeadOptions,
  type BoardReport,
  type ConfidenceFilter,
  type LeadOptions,
  type ReportGroup,
} from "@/lib/api";
import { annualLines, formatTotals, type AnnualMeasure } from "@/lib/format";
import { AnnualLegend, AnnualTotals, Section, StageBars, StatTile, selectClass, tdClass, thClass } from "@/components/ReportBlocks";

type ReportsPageProps = {
  boardId: number;
  /** Central mode: read the report of this company; absent: the legacy single-database app. */
  companySlug?: string;
};

const GroupTable = ({ label, groups, years }: { label: string; groups: ReportGroup[]; years?: number[] }) =>
  groups.length === 0 ? (
    <p className="text-sm text-[var(--gray-text)]">No leads.</p>
  ) : (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-[var(--stroke)]">
            <th className={thClass}>{label}</th>
            <th className={thClass}>Open</th>
            <th className={thClass}>Open pipeline</th>
            <th className={thClass}>Won</th>
            <th className={thClass}>Won value</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <tr key={`${group.key}:${group.salesperson_id ?? ""}`} className="border-b border-[var(--stroke)] last:border-0">
              <td className={clsx(tdClass, "font-semibold")}>{group.key}</td>
              <td className={tdClass}>{group.open_count}</td>
              <td className={tdClass}>
                <AnnualTotals measure={{ current: group.open_totals, next: group.open_next_totals, sum: group.open_sum_totals }} years={years} compact />
              </td>
              <td className={tdClass}>{group.won_count}</td>
              <td className={tdClass}>
                <AnnualTotals measure={{ current: group.won_totals, next: group.won_next_totals, sum: group.won_sum_totals }} years={years} compact />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

// Headline tiles: the per-year amounts under the two-year total (USD).
const tileNote = (measure: AnnualMeasure, years?: number[]) => {
  const lines = annualLines(measure, years, true);
  return lines.length ? lines.map((line) => line.detail).join("\n") : undefined;
};

export const ReportsPage = ({ boardId, companySlug }: ReportsPageProps) => {
  const [options, setOptions] = useState<LeadOptions | null>(null);
  const [report, setReport] = useState<BoardReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [salespersonId, setSalespersonId] = useState("");
  const [productLine, setProductLine] = useState("");
  const [confidence, setConfidence] = useState<ConfidenceFilter | "">("");

  useEffect(() => {
    (companySlug ? getCompanyLeadOptions(companySlug) : getLeadOptions())
      .then(setOptions)
      .catch((err: unknown) => setError(getErrorMessage(err, "Failed to load filters")));
  }, [companySlug]);

  useEffect(() => {
    getBoardReport(boardId, {
      salespersonId: salespersonId ? Number(salespersonId) : undefined,
      productLine: productLine || undefined,
      confidenceLevel: confidence || undefined,
    }, companySlug)
      .then((data) => {
        setReport(data);
        setError(null);
      })
      .catch((err: unknown) => setError(getErrorMessage(err, "Failed to load report")));
  }, [boardId, companySlug, salespersonId, productLine, confidence]);

  const kpis = report?.kpis;

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-6 px-4 py-5 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-[var(--navy-dark)]">Reports</h1>
          <p className="mt-2 text-sm text-[var(--gray-text)]">
            All Deal Values are in USD.
          </p>
        </div>
      </div>

      {/* Filters: one row above the charts */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]">
          Salesperson
          <select
            value={salespersonId}
            onChange={(e) => setSalespersonId(e.target.value)}
            className={selectClass}
            aria-label="Filter by salesperson"
          >
            <option value="">All</option>
            {options?.salespeople.map((person) => (
              <option key={person.id} value={String(person.id)}>{person.username}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]">
          Product line
          <select
            value={productLine}
            onChange={(e) => setProductLine(e.target.value)}
            className={selectClass}
            aria-label="Filter by product line"
          >
            <option value="">All</option>
            {options?.product_lines.map((line) => (
              <option key={line} value={line}>{line}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]">
          Probability level
          <select
            value={confidence}
            onChange={(e) => setConfidence(e.target.value as ConfidenceFilter | "")}
            className={selectClass}
            aria-label="Filter by probability level"
          >
            <option value="">All</option>
            {CONFIDENCE_LEVELS.map((level) => (
              <option key={level} value={level}>{level}</option>
            ))}
            <option value="UNASSIGNED">Not assigned</option>
          </select>
        </label>
      </div>

      {error && (
        <div role="alert" className="rounded-2xl border border-red-200 bg-red-50 px-5 py-3 text-sm font-medium text-red-700">
          {error}
        </div>
      )}

      {!report || !kpis ? (
        !error && <p className="text-sm text-[var(--gray-text)]">Loading report...</p>
      ) : (
        <>
          <AnnualLegend years={report.deal_years} />

          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5" data-testid="report-kpis">
            <StatTile label="Open leads" lines={[String(kpis.open_count)]} />
            <StatTile
              label="Open pipeline"
              lines={formatTotals(kpis.open_sum_totals ?? kpis.open_totals, true)}
              note={tileNote({ current: kpis.open_totals, next: kpis.open_next_totals, sum: kpis.open_sum_totals }, report.deal_years)}
            />
            <StatTile
              label="Won"
              lines={[String(kpis.won_count), ...formatTotals(kpis.won_sum_totals ?? kpis.won_totals, true)]}
              note={tileNote({ current: kpis.won_totals, next: kpis.won_next_totals, sum: kpis.won_sum_totals }, report.deal_years)}
            />
            <StatTile label="Lost" lines={[String(kpis.lost_count)]} />
            <StatTile
              label="Win rate"
              lines={[kpis.win_rate === null ? "-" : `${Math.round(kpis.win_rate * 100)}%`]}
            />
          </div>

          <Section title="Leads by stage">
            <StageBars stages={report.stages} years={report.deal_years} />
          </Section>

          <Section title="By salesperson">
            <GroupTable label="Salesperson" groups={report.by_salesperson} years={report.deal_years} />
          </Section>
          <div className="grid gap-6 lg:grid-cols-2">
            <Section title="By product line">
              <GroupTable label="Product line" groups={report.by_product_line} years={report.deal_years} />
            </Section>
            <Section title="By lead source">
              <GroupTable label="Source" groups={report.by_source} years={report.deal_years} />
            </Section>
          </div>
          {report.by_confidence_level && (
            <Section title="By probability level">
              <GroupTable label="Probability level" groups={report.by_confidence_level} years={report.deal_years} />
            </Section>
          )}
        </>
      )}
    </div>
  );
};
