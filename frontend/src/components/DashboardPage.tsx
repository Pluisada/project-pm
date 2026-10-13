"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import {
  getErrorMessage,
  getKpiAging,
  getKpiConversion,
  getKpiPipeline,
  getKpiStageTime,
  getKpiSummary,
  type KpiAging,
  type KpiConversion,
  type KpiCoverage,
  type KpiDuration,
  type KpiPeriodPreset,
  type KpiPeriodQuery,
  type KpiPipeline,
  type KpiRate,
  type KpiStageTime,
  type KpiSummary,
  type OutcomeFlow,
  type PeriodKpis,
} from "@/lib/api";
import {
  formatDateTime,
  formatDays,
  formatPeriodDates,
  formatMoney,
  formatRate,
  moneyTotalsToNumbers,
} from "@/lib/format";
import { Section, StageBars, StatTile, Totals, selectClass, tdClass, thClass } from "@/components/ReportBlocks";

type DashboardPageProps = {
  boardId: number;
  onBack: () => void;
};

const PRESETS: { value: KpiPeriodPreset; label: string }[] = [
  { value: "last_30_days", label: "Last 30 days" },
  { value: "last_90_days", label: "Last 90 days" },
  { value: "year_to_date", label: "Year to date" },
  { value: "custom", label: "Custom range" },
];

// Period-based ("flow") data re-fetches with the filters; the stock views
// (pipeline, aging) describe the board now and load once.
// `key` identifies the filters a response was fetched for: data is shown only
// while it matches the current filters, never a previous period's numbers.
type FlowData = { key: string; summary: KpiSummary; conversion: KpiConversion; stageTime: KpiStageTime };
type StockData = { aging: KpiAging; pipeline: KpiPipeline };

const rateLine = (rate: KpiRate) =>
  rate.value === null ? "-" : `${formatRate(rate.value)} (${rate.numerator}/${rate.denominator})`;

const durationLine = (d: KpiDuration, lowerBound = false) =>
  d.count === 0 ? "-" : `${formatDays(d.median_days, lowerBound)} median, ${formatDays(d.mean_days, lowerBound)} mean`;

/**
 * How to show a previous-period figure. A period that ended before tracking
 * started has no data - that is "not tracked", never 0.
 */
const previousNote = (previous: PeriodKpis | undefined, value: (p: PeriodKpis) => string): string | undefined => {
  if (!previous) return undefined;
  const started = previous.coverage.tracking_started_at;
  if (!started || previous.period.end <= started) return "Previous period: not tracked";
  return `Previous: ${value(previous)}${previous.coverage.status === "partial" ? " (partial history)" : ""}`;
};

const CoverageBanner = ({ coverage }: { coverage: KpiCoverage }) => {
  const since = coverage.tracking_started_at ? formatDateTime(coverage.tracking_started_at) : null;
  const { tone, label, text } = {
    complete: {
      tone: "border-green-200 bg-green-50 text-green-800",
      label: "Complete history",
      text: `Stage history covers this whole period (tracking since ${since}).`,
    },
    partial: {
      tone: "border-amber-200 bg-amber-50 text-amber-900",
      label: "Partial history",
      text: `Stage tracking started on ${since}, after this period began. Earlier activity in the period was not recorded, so counts cover only what happened since then, and durations of cards already in a stage at that time are lower bounds.`,
    },
    no_history: {
      tone: "border-[var(--stroke)] bg-[var(--surface)] text-[var(--gray-text)]",
      label: "No history",
      text: "No stage changes have been recorded on this board yet.",
    },
  }[coverage.status];
  return (
    <div className={clsx("rounded-2xl border px-5 py-3 text-sm", tone)} role="status" data-testid="coverage">
      <span className="font-semibold">{label}.</span> {text}
    </div>
  );
};

/** Closing value (recorded at close), what's unknown, and today's value - never mixed. */
const OutcomeRow = ({
  label,
  flow,
  previous,
  previousTracked,
}: {
  label: string;
  flow: OutcomeFlow;
  previous?: OutcomeFlow;
  previousTracked?: boolean;
}) => (
  <tr className="border-b border-[var(--stroke)] last:border-0" data-testid={`closing-${label.toLowerCase()}`}>
    <td className={clsx(tdClass, "font-semibold")}>{label}</td>
    <td className={tdClass}>{flow.opportunities}</td>
    <td className={tdClass}>
      {flow.opportunities === 0 ? "-" : <Totals totals={moneyTotalsToNumbers(flow.values)} />}
    </td>
    <td className={tdClass}>{flow.without_value}</td>
    <td className={clsx(tdClass, flow.value_unavailable > 0 && "font-semibold text-amber-800")}>
      {flow.value_unavailable}
    </td>
    <td className={clsx(tdClass, "text-[var(--gray-text)]")}>
      {flow.opportunities === 0 ? "-" : <Totals totals={moneyTotalsToNumbers(flow.current_values)} />}
    </td>
    {previous && (
      <td className={clsx(tdClass, "text-[var(--gray-text)]")}>
        {previousTracked ? (
          <>
            {previous.opportunities} {previous.opportunities > 0 && <Totals totals={moneyTotalsToNumbers(previous.values)} />}
          </>
        ) : (
          "Not tracked"
        )}
      </td>
    )}
  </tr>
);

export const DashboardPage = ({ boardId, onBack }: DashboardPageProps) => {
  const [preset, setPreset] = useState<KpiPeriodPreset>("last_30_days");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [compare, setCompare] = useState(true);
  const [flow, setFlow] = useState<FlowData | null>(null);
  const [stock, setStock] = useState<StockData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const customIncomplete = preset === "custom" && (!start || !end);
  const filterKey = JSON.stringify([preset, start, end, compare]);

  useEffect(() => {
    Promise.all([getKpiAging(boardId), getKpiPipeline(boardId)])
      .then(([aging, pipeline]) => setStock({ aging, pipeline }))
      .catch((err: unknown) => setError(getErrorMessage(err, "Failed to load the pipeline")));
  }, [boardId]);

  useEffect(() => {
    if (customIncomplete) return;
    // Ignore a response that arrives after the filters have changed again.
    let current = true;
    const query: KpiPeriodQuery = preset === "custom" ? { period: preset, start, end } : { period: preset };
    Promise.all([
      getKpiSummary(boardId, query, compare),
      getKpiConversion(boardId, query),
      getKpiStageTime(boardId, query),
    ])
      .then(([summary, conversion, stageTime]) => {
        if (!current) return;
        setFlow({ key: filterKey, summary, conversion, stageTime });
        setError(null);
      })
      .catch((err: unknown) => {
        if (!current) return;
        setFlow(null);
        setError(getErrorMessage(err, "Failed to load KPIs"));
      });
    return () => {
      current = false;
    };
  }, [boardId, preset, start, end, compare, customIncomplete, filterKey]);

  // Only data fetched for the current filters - nothing while a custom range
  // is incomplete or a new period is loading.
  const current = flow && flow.key === filterKey && !customIncomplete ? flow : null;
  const kpis = current?.summary.current;
  const previous = current?.summary.previous ?? undefined;
  const previousTracked =
    !!previous?.coverage.tracking_started_at && previous.period.end > previous.coverage.tracking_started_at;
  const openStages = stock?.pipeline.stages.filter((stage) => stage.stage_kind === "open") ?? [];
  const valueUnavailable = kpis ? kpis.won.value_unavailable + kpis.lost.value_unavailable : 0;
  const partialStays = current?.stageTime.stages.reduce((n, s) => n + s.partial.count, 0) ?? 0;
  const baselineAges = stock?.aging.opportunities.filter((row) => row.history !== "observed").length ?? 0;

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-6 px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.35em] text-[var(--gray-text)]">
            Salcomp Sales Pipeline
          </p>
          <h1 className="mt-3 font-display text-4xl font-semibold text-[var(--navy-dark)]">
            Executive Dashboard
          </h1>
          <p className="mt-2 text-sm text-[var(--gray-text)]">
            Built only from recorded stage history. Values are per currency, without conversion.
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

      {/* Filters: one row above everything they affect */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]">
          Period
          <select
            value={preset}
            onChange={(e) => setPreset(e.target.value as KpiPeriodPreset)}
            className={selectClass}
            aria-label="Period"
          >
            {PRESETS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </label>
        {preset === "custom" && (
          <>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={selectClass} aria-label="Start date" />
            <span className="text-sm text-[var(--gray-text)]">to</span>
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={selectClass} aria-label="End date" />
          </>
        )}
        <label className="flex items-center gap-2 text-sm text-[var(--navy-dark)]">
          <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />
          Compare with previous period
        </label>
      </div>

      {customIncomplete && (
        <p className="text-sm text-[var(--gray-text)]">Choose a start and an end date (both included).</p>
      )}
      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-5 py-3 text-sm font-medium text-red-700" role="alert">
          {error}
        </div>
      )}

      {kpis && current && (
        <>
          <CoverageBanner coverage={kpis.coverage} />
          <p className="text-xs text-[var(--gray-text)]" data-testid="period-dates">
            {formatPeriodDates(kpis.period)} (UTC dates, both included)
            {previous && <>; compared with {formatPeriodDates(previous.period)}</>}
          </p>

          {/* Headline numbers: period flow, plus the open pipeline as of now */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-6" data-testid="dashboard-kpis">
            <StatTile
              label="Won"
              lines={[String(kpis.won.opportunities)]}
              note={previousNote(previous, (p) => String(p.won.opportunities)) ?? `${kpis.won.events} won transitions`}
            />
            <StatTile
              label="Lost"
              lines={[String(kpis.lost.opportunities)]}
              note={previousNote(previous, (p) => String(p.lost.opportunities)) ?? `${kpis.lost.events} lost transitions`}
            />
            <StatTile
              label="Win rate"
              lines={[formatRate(kpis.win_rate_closed.value)]}
              note={
                kpis.win_rate_closed.value === null
                  ? kpis.win_rate_closed.unavailable_reason ?? undefined
                  : [`${kpis.win_rate_closed.numerator} of ${kpis.win_rate_closed.denominator} closed`, previousNote(previous, (p) => formatRate(p.win_rate_closed.value))].filter(Boolean).join("; ")
              }
            />
            <StatTile
              label="Sales cycle (won)"
              lines={[formatDays(kpis.velocity.won.median_days)]}
              note={
                kpis.velocity.won.count === 0
                  ? "No deal with an observed creation was won in this period"
                  : [`Median of ${kpis.velocity.won.count}; mean ${formatDays(kpis.velocity.won.mean_days)}`, previousNote(previous, (p) => formatDays(p.velocity.won.median_days))].filter(Boolean).join("; ")
              }
            />
            <StatTile
              label="New opportunities"
              lines={[String(kpis.cohort.size)]}
              note={previousNote(previous, (p) => String(p.cohort.size)) ?? "Created in this period"}
            />
            <StatTile
              label="Open now"
              lines={[stock ? String(stock.aging.opportunities.length) : "-"]}
              note="Open opportunities today (not a period figure)"
            />
          </div>

          <Section title="Closing values by outcome">
            <p className="mb-3 text-xs text-[var(--gray-text)]">
              Closing value is the deal value recorded when the card was won or lost. A deal reopened and closed
              again counts once, at its latest closing value. Today&apos;s value is shown only for comparison and
              never replaces a closing value that wasn&apos;t recorded.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-[var(--stroke)]">
                    <th className={thClass}>Outcome</th>
                    <th className={thClass}>Deals</th>
                    <th className={thClass}>Closing value</th>
                    <th className={thClass}>Recorded without value</th>
                    <th className={thClass}>Value unavailable</th>
                    <th className={thClass}>Value today (comparison)</th>
                    {previous && <th className={thClass}>Previous period</th>}
                  </tr>
                </thead>
                <tbody>
                  <OutcomeRow label="Won" flow={kpis.won} previous={previous?.won} previousTracked={previousTracked} />
                  <OutcomeRow label="Lost" flow={kpis.lost} previous={previous?.lost} previousTracked={previousTracked} />
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Conversion">
            <div className="grid gap-4 sm:grid-cols-3">
              <StatTile
                label="Win rate (closed in period)"
                lines={[rateLine(current.conversion.win_rate_closed)]}
                note={current.conversion.win_rate_closed.unavailable_reason ?? "Each deal's last outcome in the period"}
              />
              <StatTile
                label="Cohort win rate"
                lines={[rateLine(current.conversion.cohort.win_rate)]}
                note={current.conversion.cohort.win_rate.unavailable_reason ?? `First outcome of ${current.conversion.cohort.size} new deals; ${current.conversion.cohort.still_open} still open`}
              />
              <StatTile
                label="Cohort win rate (decided)"
                lines={[rateLine(current.conversion.cohort.closed_win_rate)]}
                note={current.conversion.cohort.closed_win_rate.unavailable_reason ?? `${current.conversion.cohort.won_first} won, ${current.conversion.cohort.lost_first} lost first`}
              />
            </div>
            <div className="mt-6 overflow-x-auto">
              <table className="w-full" data-testid="advancement">
                <thead>
                  <tr className="border-b border-[var(--stroke)]">
                    <th className={thClass}>Open stage</th>
                    <th className={thClass}>Entered</th>
                    <th className={thClass}>Advanced</th>
                    <th className={thClass}>Lost</th>
                    <th className={thClass}>Other exit</th>
                    <th className={thClass}>Still there</th>
                    <th className={thClass}>Advance rate</th>
                  </tr>
                </thead>
                <tbody>
                  {current.conversion.advancement.map((row) => (
                    <tr key={row.column_id} className="border-b border-[var(--stroke)] last:border-0">
                      <td className={clsx(tdClass, "font-semibold")}>{row.title}</td>
                      <td className={tdClass}>{row.entered}</td>
                      <td className={tdClass}>{row.advanced}</td>
                      <td className={tdClass}>{row.lost}</td>
                      <td className={tdClass}>{row.other_exit}</td>
                      <td className={tdClass}>{row.still_in_stage}</td>
                      <td className={tdClass} title={row.rate.unavailable_reason ?? undefined}>
                        {formatRate(row.rate.value)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-[var(--gray-text)]">
                Counts distinct deals with an observed entry into the stage in the period; cards that were already
                in a stage when tracking started are not entries.
              </p>
            </div>
          </Section>

          <Section title="Time in stage">
            <div className="overflow-x-auto">
              <table className="w-full" data-testid="stage-time">
                <thead>
                  <tr className="border-b border-[var(--stroke)]">
                    <th className={thClass}>Stage</th>
                    <th className={thClass}>Completed stays</th>
                    <th className={thClass}>Since tracking start (lower bounds)</th>
                    <th className={thClass}>In stage now</th>
                  </tr>
                </thead>
                <tbody>
                  {current.stageTime.stages.map((stage) => (
                    <tr key={stage.column_id} className="border-b border-[var(--stroke)] last:border-0">
                      <td className={clsx(tdClass, "font-semibold")}>{stage.title}</td>
                      <td className={tdClass}>
                        {stage.complete.count} {stage.complete.count > 0 && <span className="text-[var(--gray-text)]">({durationLine(stage.complete)})</span>}
                      </td>
                      <td className={tdClass}>
                        {stage.partial.count} {stage.partial.count > 0 && <span className="text-[var(--gray-text)]">({durationLine(stage.partial, true)})</span>}
                      </td>
                      <td className={tdClass}>
                        {stage.current.count}{" "}
                        {stage.current.count > 0 && (
                          <span className="text-[var(--gray-text)]">
                            ({durationLine(stage.current, stage.current.baseline_entries > 0)}
                            {stage.current.baseline_entries > 0 && `; ${stage.current.baseline_entries} since tracking start`})
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      )}

      {stock && (
        <>
          <Section title="Open pipeline by stage (now)">
            {openStages.length === 0 ? (
              <p className="text-sm text-[var(--gray-text)]">No open stages.</p>
            ) : (
              <StageBars
                stages={openStages.map((s) => ({ column_id: s.column_id, title: s.title, count: s.count, totals: moneyTotalsToNumbers(s.values) }))}
              />
            )}
          </Section>

          <Section title="Aging of open opportunities (now)">
            {stock.aging.opportunities.length === 0 ? (
              <p className="text-sm text-[var(--gray-text)]">No open opportunities.</p>
            ) : (
              <div className="max-h-[420px] overflow-auto">
                <table className="w-full" data-testid="aging">
                  <thead>
                    <tr className="border-b border-[var(--stroke)]">
                      <th className={thClass}>Opportunity</th>
                      <th className={thClass}>Stage</th>
                      <th className={thClass}>In stage for</th>
                      <th className={thClass}>Value today</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stock.aging.opportunities.map((row) => (
                      <tr key={row.card_id} className="border-b border-[var(--stroke)] last:border-0">
                        <td className={clsx(tdClass, "font-semibold")}>{row.title}</td>
                        <td className={tdClass}>{row.column_title}</td>
                        <td className={tdClass}>
                          {row.history === "none"
                            ? "Unknown (no history)"
                            : formatDays(row.age_days, row.history === "since_tracking_start")}
                        </td>
                        <td className={tdClass}>
                          {row.deal_value !== null && row.currency ? formatMoney(row.deal_value, row.currency) : "-"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </>
      )}

      {kpis && current && stock && (
        <Section title="Data quality">
          <ul className="space-y-1 text-sm text-[var(--navy-dark)]" data-testid="data-quality">
            <li>
              Stage tracking started:{" "}
              {kpis.coverage.tracking_started_at ? formatDateTime(kpis.coverage.tracking_started_at) : "not yet"}
            </li>
            <li>Closed deals in the period whose closing value is unavailable (recorded before value tracking): {valueUnavailable}</li>
            <li>Stays in the period measured only since tracking started (lower bounds): {partialStays}</li>
            <li>Open opportunities whose age is known only since tracking started: {baselineAges}</li>
            <li>
              Deals won or lost in the period without an observed creation (excluded from the sales cycle):{" "}
              {kpis.velocity.excluded_unobserved_creation}
            </li>
          </ul>
        </Section>
      )}

      {!current && !error && !customIncomplete && <p className="text-sm text-[var(--gray-text)]">Loading KPIs...</p>}
    </div>
  );
};
