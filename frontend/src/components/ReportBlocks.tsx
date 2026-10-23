/**
 * Building blocks shared by the Reports page and the Executive Dashboard.
 */
import clsx from "clsx";
import { annualLabels, annualLines, formatTotals, type AnnualMeasure } from "@/lib/format";

export const selectClass =
  "rounded-lg border border-[var(--stroke)] bg-white px-3 py-2 text-sm text-[var(--navy-dark)] outline-none focus:border-[var(--primary-blue)]";
export const thClass =
  "px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]";
export const tdClass = "px-3 py-2 align-top text-sm text-[var(--navy-dark)]";

/** Per-currency totals, one line each (never summed across currencies). */
export const Totals = ({ totals }: { totals: Record<string, number> }) => (
  <>
    {formatTotals(totals).map((line) => (
      <div key={line}>{line}</div>
    ))}
  </>
);

/** A headline number. `note` adds a small caption (comparison, caveat). */
export const StatTile = ({ label, lines, note }: { label: string; lines: string[]; note?: string }) => (
  <div className="rounded-2xl border border-[var(--stroke)] bg-white px-5 py-4">
    <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">{label}</p>
    {lines.map((line, i) => (
      <p
        key={line}
        className={clsx(
          "font-display font-semibold text-[var(--navy-dark)]",
          i === 0 ? "mt-2 text-2xl" : "text-base"
        )}
      >
        {line}
      </p>
    ))}
    {note && <p className="mt-2 whitespace-pre-line text-xs text-[var(--gray-text)]">{note}</p>}
  </div>
);

/**
 * Deal Value in its three measures (USD): the two-year total in bold
 * and, smaller, the two annual amounts it is made of ("-" = never entered).
 */
export const AnnualTotals = ({ measure, years, compact }: { measure: AnnualMeasure; years?: number[]; compact?: boolean }) => {
  const lines = annualLines(measure, years, compact);
  if (lines.length === 0) return <>-</>;
  return (
    <>
      {lines.map((line) => (
        <div key={line.currency} className="mb-1 last:mb-0" data-testid="annual-totals">
          <div className="font-semibold">{line.total}</div>
          <div className="text-xs text-[var(--gray-text)]">{line.detail}</div>
        </div>
      ))}
    </>
  );
};

/** One-line key to AnnualTotals, shown once per page; flags amounts that span several years. */
export const AnnualLegend = ({ years }: { years?: number[] }) => {
  const labels = annualLabels(years);
  return (
    <p className="text-xs text-[var(--gray-text)]" data-testid="annual-legend">
      Deal Value is shown as the {labels.total} (bold) and, below it, the {labels.current} and {labels.next} amounts it
      adds up. All amounts are in USD.
      {!labels.single && years && years.length > 1 && (
        <> Amounts here were entered for different years ({years.join(", ")}), so the year columns mix them.</>
      )}
    </p>
  );
};

export const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <section className="rounded-3xl border border-[var(--stroke)] bg-white p-6 shadow-[var(--shadow)]">
    <h2 className="font-display text-lg font-semibold text-[var(--navy-dark)]">{title}</h2>
    <div className="mt-4">{children}</div>
  </section>
);

export type StageBar = {
  column_id: number;
  title: string;
  count: number;
  totals: Record<string, number>;
  /** When present, the stage shows the three Deal Value measures instead of `totals` alone. */
  next_totals?: Record<string, number>;
  sum_totals?: Record<string, number>;
};

/**
 * Lead count per stage as horizontal bars (one series, so no legend - the
 * section title names it). Value totals are labels beside each bar, since
 * they're per currency and can't share one length scale.
 */
export const StageBars = ({ stages, years }: { stages: StageBar[]; years?: number[] }) => {
  const max = Math.max(1, ...stages.map((stage) => stage.count));
  return (
    <ul className="space-y-3">
      {stages.map((stage) => (
        <li
          key={stage.column_id}
          className="grid grid-cols-[minmax(96px,130px)_1fr] items-center gap-x-3 gap-y-1 md:grid-cols-[minmax(110px,160px)_minmax(80px,1fr)_minmax(14rem,auto)]"
          title={`${stage.title}: ${stage.count} lead${stage.count === 1 ? "" : "s"}, ${
            stage.sum_totals
              ? annualLines({ current: stage.totals, next: stage.next_totals, sum: stage.sum_totals }, years)
                  .map((line) => `${line.total} (${line.detail})`)
                  .join(", ") || "-"
              : formatTotals(stage.totals).join(", ")
          }`}
        >
          <span className="min-w-0 break-words text-sm font-semibold text-[var(--navy-dark)]" data-testid="stage-label">{stage.title}</span>
          <div className="flex min-w-0 items-center gap-2">
            <div className="h-5 min-w-0 flex-1">
              {stage.count > 0 && (
                <div
                  className="h-5 rounded-r bg-[var(--primary-blue)]"
                  style={{ width: `${(stage.count / max) * 100}%` }}
                />
              )}
            </div>
            <span className="w-6 text-right text-sm font-semibold text-[var(--navy-dark)]">
              {stage.count}
            </span>
          </div>
          <div className="col-span-2 min-w-0 break-words text-xs text-[var(--gray-text)] md:col-span-1" data-testid="stage-summary">
            {stage.sum_totals ? (
              <AnnualTotals measure={{ current: stage.totals, next: stage.next_totals, sum: stage.sum_totals }} years={years} compact />
            ) : (
              formatTotals(stage.totals).join(" / ")
            )}
          </div>
        </li>
      ))}
    </ul>
  );
};
