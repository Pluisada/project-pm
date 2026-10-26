import type { LeadGeneration } from "@/lib/api";
import { Section } from "@/components/ReportBlocks";

const percent = (share: number) => `${(share * 100).toFixed(1)}%`;

const formatDay = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/**
 * Pareto of this year's leads by the user who created them: one bar per person (length = share of the busiest
 * person's count), with count, share of the total and the running cumulative share. The backend does the ranking.
 */
export const LeadGenerationPareto = ({ data }: { data: LeadGeneration }) => {
  const max = Math.max(1, ...data.rows.map((row) => row.count));
  return (
    <Section title="Lead Generation Pareto">
      <p className="-mt-2 mb-4 text-sm text-[var(--gray-text)]" data-testid="pareto-period">
        Leads created {formatDay(data.period_start)} to {formatDay(data.period_end)} (UTC, current calendar year; not affected by the Period filter), by who created them
      </p>
      {data.rows.length === 0 ? (
        <p className="text-sm text-[var(--gray-text)]" data-testid="pareto-empty">No leads with a recorded creator were generated in this period.</p>
      ) : (
        <ol className="space-y-3" data-testid="pareto-rows">
          {data.rows.map((row) => (
            <li
              key={row.salesperson_id}
              data-testid="pareto-row"
              className="grid grid-cols-[minmax(96px,150px)_1fr] items-center gap-x-3 gap-y-1 md:grid-cols-[minmax(110px,170px)_minmax(80px,1fr)_minmax(13rem,auto)]"
            >
              <span className="min-w-0 break-words text-sm font-semibold text-[var(--navy-dark)]" data-testid="pareto-name">{row.name}</span>
              <div className="h-5 min-w-0">
                <div className="h-5 rounded-r bg-[var(--primary-blue)]" style={{ width: `${(row.count / max) * 100}%` }} />
              </div>
              <span className="col-span-2 text-xs text-[var(--gray-text)] md:col-span-1 md:text-sm" data-testid="pareto-figures">
                <span className="font-semibold text-[var(--navy-dark)]">{row.count}</span> {row.count === 1 ? "lead" : "leads"} · {percent(row.share)} · cumulative {percent(row.cumulative_share)}
              </span>
            </li>
          ))}
        </ol>
      )}
      <p className="mt-4 text-xs text-[var(--gray-text)]" data-testid="pareto-total">
        {data.total} lead{data.total === 1 ? "" : "s"} ranked
        {data.total > 0 && "; percentages are of this ranked total"}.
        {data.unattributed_count > 0 &&
          ` ${data.unattributed_count} lead${data.unattributed_count === 1 ? "" : "s"} with no recorded creator ${
            data.unattributed_count === 1 ? "is" : "are"
          } excluded from the ranking and from the percentages.`}
      </p>
      <p className="mt-1 text-xs text-[var(--gray-text)]" data-testid="pareto-filter-note">
        Each lead is credited to the person who originally created it, not to the current owner of the card.
      </p>
    </Section>
  );
};
