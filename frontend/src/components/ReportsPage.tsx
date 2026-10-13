"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import {
  getBoardReport,
  getErrorMessage,
  getLeadOptions,
  type BoardReport,
  type LeadOptions,
  type ReportGroup,
} from "@/lib/api";
import { formatDate, formatMoney, formatTotals } from "@/lib/format";
import { Section, StageBars, StatTile, Totals, selectClass, tdClass, thClass } from "@/components/ReportBlocks";

type ReportsPageProps = {
  boardId: number;
  onBack: () => void;
};

const GroupTable = ({ label, groups }: { label: string; groups: ReportGroup[] }) =>
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
            <tr key={group.key} className="border-b border-[var(--stroke)] last:border-0">
              <td className={clsx(tdClass, "font-semibold")}>{group.key}</td>
              <td className={tdClass}>{group.open_count}</td>
              <td className={tdClass}><Totals totals={group.open_totals} /></td>
              <td className={tdClass}>{group.won_count}</td>
              <td className={tdClass}><Totals totals={group.won_totals} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

export const ReportsPage = ({ boardId, onBack }: ReportsPageProps) => {
  const [options, setOptions] = useState<LeadOptions | null>(null);
  const [report, setReport] = useState<BoardReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [salespersonId, setSalespersonId] = useState("");
  const [productLine, setProductLine] = useState("");

  useEffect(() => {
    getLeadOptions()
      .then(setOptions)
      .catch((err: unknown) => setError(getErrorMessage(err, "Failed to load filters")));
  }, []);

  useEffect(() => {
    getBoardReport(boardId, {
      salespersonId: salespersonId ? Number(salespersonId) : undefined,
      productLine: productLine || undefined,
    })
      .then((data) => {
        setReport(data);
        setError(null);
      })
      .catch((err: unknown) => setError(getErrorMessage(err, "Failed to load report")));
  }, [boardId, salespersonId, productLine]);

  const kpis = report?.kpis;

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-6 px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.35em] text-[var(--gray-text)]">
            Salcomp Sales Pipeline
          </p>
          <h1 className="mt-3 font-display text-4xl font-semibold text-[var(--navy-dark)]">Reports</h1>
          <p className="mt-2 text-sm text-[var(--gray-text)]">
            Values are totaled per currency, without conversion.
          </p>
        </div>
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] transition hover:border-[var(--primary-blue)]"
        >
          Back to Board
        </button>
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
      </div>

      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-5 py-3 text-sm font-medium text-red-700">
          {error}
        </div>
      )}

      {!report || !kpis ? (
        !error && <p className="text-sm text-[var(--gray-text)]">Loading report...</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5" data-testid="report-kpis">
            <StatTile label="Open leads" lines={[String(kpis.open_count)]} />
            <StatTile label="Open pipeline" lines={formatTotals(kpis.open_totals, true)} />
            <StatTile label="Won" lines={[String(kpis.won_count), ...formatTotals(kpis.won_totals, true)]} />
            <StatTile label="Lost" lines={[String(kpis.lost_count)]} />
            <StatTile
              label="Win rate"
              lines={[kpis.win_rate === null ? "-" : `${Math.round(kpis.win_rate * 100)}%`]}
            />
          </div>

          <Section title="Leads by stage">
            <StageBars stages={report.stages} />
          </Section>

          <Section title="Closing in the next 30 days or overdue">
            {report.closing_soon.length === 0 ? (
              <p className="text-sm text-[var(--gray-text)]">No open leads closing soon.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-[var(--stroke)]">
                      <th className={thClass}>Close date</th>
                      <th className={thClass}>Lead</th>
                      <th className={thClass}>Stage</th>
                      <th className={thClass}>Salesperson</th>
                      <th className={thClass}>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.closing_soon.map((lead) => (
                      <tr key={lead.card_id} className="border-b border-[var(--stroke)] last:border-0">
                        <td className={clsx(tdClass, lead.overdue && "font-semibold text-red-700")}>
                          {formatDate(lead.expected_close_date)}
                          {lead.overdue && " (overdue)"}
                        </td>
                        <td className={tdClass}>
                          <div className="font-semibold">{lead.title}</div>
                          {lead.company && <div className="text-xs text-[var(--gray-text)]">{lead.company}</div>}
                        </td>
                        <td className={tdClass}>{lead.stage}</td>
                        <td className={tdClass}>{lead.salesperson_name ?? "Unassigned"}</td>
                        <td className={tdClass}>
                          {lead.deal_value != null && lead.currency ? formatMoney(lead.deal_value, lead.currency) : "-"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          <Section title="By salesperson">
            <GroupTable label="Salesperson" groups={report.by_salesperson} />
          </Section>
          <div className="grid gap-6 lg:grid-cols-2">
            <Section title="By product line">
              <GroupTable label="Product line" groups={report.by_product_line} />
            </Section>
            <Section title="By lead source">
              <GroupTable label="Source" groups={report.by_source} />
            </Section>
          </div>
        </>
      )}
    </div>
  );
};
