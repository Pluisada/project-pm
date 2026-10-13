/**
 * Building blocks shared by the Reports page and the Executive Dashboard.
 */
import clsx from "clsx";
import { formatTotals } from "@/lib/format";

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
    {note && <p className="mt-2 text-xs text-[var(--gray-text)]">{note}</p>}
  </div>
);

export const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <section className="rounded-3xl border border-[var(--stroke)] bg-white p-6 shadow-[var(--shadow)]">
    <h2 className="font-display text-lg font-semibold text-[var(--navy-dark)]">{title}</h2>
    <div className="mt-4">{children}</div>
  </section>
);

export type StageBar = { column_id: number; title: string; count: number; totals: Record<string, number> };

/**
 * Lead count per stage as horizontal bars (one series, so no legend - the
 * section title names it). Value totals are labels beside each bar, since
 * they're per currency and can't share one length scale.
 */
export const StageBars = ({ stages }: { stages: StageBar[] }) => {
  const max = Math.max(1, ...stages.map((stage) => stage.count));
  return (
    <ul className="space-y-3">
      {stages.map((stage) => (
        <li
          key={stage.column_id}
          className="grid grid-cols-[110px_1fr] items-center gap-3 sm:grid-cols-[130px_1fr_minmax(140px,auto)]"
          title={`${stage.title}: ${stage.count} lead${stage.count === 1 ? "" : "s"}, ${formatTotals(stage.totals).join(", ")}`}
        >
          <span className="text-sm font-semibold text-[var(--navy-dark)]">{stage.title}</span>
          <div className="flex items-center gap-2">
            <div className="h-5 flex-1">
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
          <div className="col-span-2 text-xs text-[var(--gray-text)] sm:col-span-1">
            {formatTotals(stage.totals).join(" / ")}
          </div>
        </li>
      ))}
    </ul>
  );
};
