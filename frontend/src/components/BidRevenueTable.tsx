"use client";

import { type BidRevenueMeasure } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { Section, tdClass, thClass } from "@/components/ReportBlocks";

type Row = { title: string; count: number; bid_revenue?: BidRevenueMeasure[] };

/** Bid report revenue by stage. Own currency (no conversion), never added to Deal Value, empty years not counted as 0. */
export function BidRevenueTable({ rows, testId }: { rows: Row[]; testId: string }) {
  const has = rows.some((row) => (row.bid_revenue ?? []).length > 0);
  return (
    <Section title="Bid revenue by stage">
      {!has ? (
        <p className="text-sm text-[var(--gray-text)]">No Bid revenue informed.</p>
      ) : (
        <>
          <p className="mb-3 text-sm text-[var(--gray-text)]">
            Annual revenue from the Bid report, in the report&apos;s own currency. It is separate from Deal Value and never added
            to it; leads with no revenue informed for a year are not counted for that year.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full" data-testid={testId}>
              <thead>
                <tr className="border-b border-[var(--stroke)]">
                  <th className={thClass}>Stage</th>
                  <th className={thClass}>Leads</th>
                  <th className={thClass}>Current year</th>
                  <th className={thClass}>Next year</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.title} className="border-b border-[var(--stroke)] last:border-0">
                    <td className={tdClass}>{row.title}</td>
                    <td className={tdClass}>{row.count}</td>
                    <td className={tdClass}>
                      {(row.bid_revenue ?? []).filter((m) => m.current_count > 0).map((m) => (
                        <div key={m.currency}>
                          {formatMoney(m.current_cents / 100, m.currency)} <span className="text-xs text-[var(--gray-text)]">({m.current_count})</span>
                        </div>
                      ))}
                      {!(row.bid_revenue ?? []).some((m) => m.current_count > 0) && "-"}
                    </td>
                    <td className={tdClass}>
                      {(row.bid_revenue ?? []).filter((m) => m.next_count > 0).map((m) => (
                        <div key={m.currency}>
                          {formatMoney(m.next_cents / 100, m.currency)} <span className="text-xs text-[var(--gray-text)]">({m.next_count})</span>
                        </div>
                      ))}
                      {!(row.bid_revenue ?? []).some((m) => m.next_count > 0) && "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  );
}
