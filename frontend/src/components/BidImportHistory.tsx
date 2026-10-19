"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import {
  getBidImport,
  getErrorMessage,
  listBidImports,
  type ImportBatch,
  type ImportBatchDetail,
} from "@/lib/api";
import { tdClass, thClass } from "@/components/ReportBlocks";
import { BidImportRevertDialog } from "@/components/BidImportRevertDialog";

const PAGE = 50;

const STATUS_STYLE: Record<string, string> = {
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  reverted: "bg-amber-100 text-amber-900",
  no_commercial_changes: "bg-slate-100 text-slate-700",
};

/** What the badge says: reverted and no-change batches get their own state instead of "completed". */
const displayState = (b: ImportBatch): { key: string; label: string } => {
  if (b.revert?.reverted) return { key: "reverted", label: "Reverted" };
  if (b.status === "completed" && b.revert?.no_commercial_changes) return { key: "no_commercial_changes", label: "No commercial changes" };
  return { key: b.status, label: b.status };
};

const StateBadge = ({ batch }: { batch: ImportBatch }) => {
  const { key, label } = displayState(batch);
  return (
    <span className={clsx("rounded-full px-2 py-0.5 text-xs font-semibold", STATUS_STYLE[key] ?? "bg-gray-100 text-gray-700")}>{label}</span>
  );
};

const RevertSummary = ({ batch }: { batch: ImportBatch }) => {
  const r = batch.revert;
  if (!r?.reverted) return null;
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-amber-900">
      <p className="font-semibold">
        {r.outcome === "no_commercial_changes" ? "Reverted (no commercial changes)" : "Reverted"} by {r.reverted_by_username ?? "an administrator"} on {fmt(r.reverted_at)}
      </p>
      <p className="text-xs">
        {r.cards_deleted ?? 0} cards deleted, {r.cards_restored ?? 0} restored, {r.moves_reverted ?? 0} moves undone, {r.already_deleted ?? 0} already deleted.
      </p>
      <p className="text-xs">This cannot be undone with the Kanban Undo. To apply these changes again, import the file again.</p>
    </div>
  );
};

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "-");

const BatchDetail = ({ batchId, refreshKey, onClose }: { batchId: number; refreshKey: number; onClose: () => void }) => {
  const [detail, setDetail] = useState<ImportBatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBidImport(batchId)
      .then(setDetail)
      .catch((err: unknown) => setError(getErrorMessage(err, "Failed to load the import")));
  }, [batchId, refreshKey]);

  return (
    <div className="rounded-2xl border border-[var(--stroke)] bg-white p-5" aria-label="Import details">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-display text-lg font-semibold text-[var(--navy-dark)]">Import #{batchId}</h3>
        <button type="button" onClick={onClose} className="text-xs font-semibold text-[var(--gray-text)]">
          Close
        </button>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!detail && !error && <p className="text-sm text-[var(--gray-text)]">Loading...</p>}
      {detail && (
        <div className="flex flex-col gap-3 text-sm text-[var(--navy-dark)]">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
            <div><dt className="text-xs text-[var(--gray-text)]">File</dt><dd>{detail.filename}</dd></div>
            <div><dt className="text-xs text-[var(--gray-text)]">Status</dt><dd><StateBadge batch={detail} /></dd></div>
            <div><dt className="text-xs text-[var(--gray-text)]">By</dt><dd>{detail.username ?? "-"}</dd></div>
            <div><dt className="text-xs text-[var(--gray-text)]">Started</dt><dd>{fmt(detail.created_at)}</dd></div>
            <div><dt className="text-xs text-[var(--gray-text)]">Finished</dt><dd>{fmt(detail.completed_at)}</dd></div>
            <div><dt className="text-xs text-[var(--gray-text)]">Card changes</dt><dd>{detail.card_changes_count}</dd></div>
            <div><dt className="text-xs text-[var(--gray-text)]">Created</dt><dd>{detail.created_count}</dd></div>
            <div><dt className="text-xs text-[var(--gray-text)]">Updated</dt><dd>{detail.updated_count}</dd></div>
            <div><dt className="text-xs text-[var(--gray-text)]">Skipped / in review</dt><dd>{detail.skipped_count} / {detail.review_count}</dd></div>
          </dl>
          <p className="break-all text-xs text-[var(--gray-text)]">SHA-256: {detail.file_sha256}</p>
          {detail.status === "failed" && (
            <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-red-800">
              <p className="font-semibold">Failed{detail.failure_kind ? `: ${detail.failure_kind}` : ""}</p>
              {detail.failure_reason && <p className="text-xs">{detail.failure_reason}</p>}
              <p className="text-xs">Nothing was written to the board.</p>
            </div>
          )}
          <RevertSummary batch={detail} />
          {detail.mapping && (
            <details>
              <summary className="cursor-pointer text-xs font-semibold text-[var(--primary-blue)]">
                Mapping used
              </summary>
              <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-[var(--surface)] p-3 text-xs">
                {JSON.stringify(detail.mapping, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
};

/** Batches of this board (completed and failed), newest first. `refreshKey` reloads after an import. */
export const BidImportHistory = ({ boardId, refreshKey }: { boardId: number; refreshKey: number }) => {
  const [items, setItems] = useState<ImportBatch[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [reverting, setReverting] = useState<ImportBatch | null>(null);
  const [changes, setChanges] = useState(0);

  // Reload from the first page when the parent signals a refresh or a batch was reverted.
  const reloadKey = `${refreshKey}:${changes}`;
  const [seenKey, setSeenKey] = useState(reloadKey);
  if (seenKey !== reloadKey) {
    setSeenKey(reloadKey);
    setLoading(true);
    setError(null);
  }

  const fetchPage = useCallback(
    (offset: number, isCurrent: () => boolean = () => true) =>
      listBidImports(boardId, PAGE, offset)
        .then((page) => {
          if (!isCurrent()) return;
          setItems((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
          setTotal(page.total);
        })
        .catch((err: unknown) => {
          if (isCurrent()) setError(getErrorMessage(err, "Failed to load the import history"));
        })
        .finally(() => {
          if (isCurrent()) setLoading(false);
        }),
    [boardId]
  );

  useEffect(() => {
    let cancelled = false;
    fetchPage(0, () => !cancelled);
    return () => {
      cancelled = true;
    };
  }, [fetchPage, reloadKey]);

  const loadMore = (offset: number) => {
    setLoading(true);
    setError(null);
    fetchPage(offset);
  };

  return (
    <div className="flex flex-col gap-4">
      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3" role="alert">
          <p className="text-sm font-medium text-red-800">{error}</p>
        </div>
      )}
      {selected !== null && <BatchDetail batchId={selected} refreshKey={changes} onClose={() => setSelected(null)} />}
      {items.length === 0 && !loading && !error ? (
        <p className="text-sm text-[var(--gray-text)]">No imports yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[var(--stroke)]">
                <th className={thClass}>#</th>
                <th className={thClass}>Date</th>
                <th className={thClass}>File</th>
                <th className={thClass}>By</th>
                <th className={thClass}>Status</th>
                <th className={thClass}>Created</th>
                <th className={thClass}>Updated</th>
                <th className={thClass}>Skipped</th>
                <th className={thClass}>Review</th>
                <th className={thClass} />
              </tr>
            </thead>
            <tbody>
              {items.map((b) => (
                <tr key={b.id} className="border-b border-[var(--stroke)] last:border-0">
                  <td className={tdClass}>{b.id}</td>
                  <td className={tdClass}>{fmt(b.created_at)}</td>
                  <td className={tdClass}>{b.filename}</td>
                  <td className={tdClass}>{b.username ?? "-"}</td>
                  <td className={tdClass}>
                    <StateBadge batch={b} />
                    {b.revert?.reverted && (
                      <div className="text-xs text-[var(--gray-text)]">
                        by {b.revert.reverted_by_username ?? "-"}, {fmt(b.revert.reverted_at)}
                      </div>
                    )}
                    {b.failure_kind && <div className="text-xs text-red-700">{b.failure_kind}</div>}
                  </td>
                  <td className={tdClass}>{b.created_count}</td>
                  <td className={tdClass}>{b.updated_count}</td>
                  <td className={tdClass}>{b.skipped_count}</td>
                  <td className={tdClass}>{b.review_count}</td>
                  <td className={tdClass}>
                    <button type="button" onClick={() => setSelected(b.id)} className="text-xs font-semibold text-[var(--primary-blue)]">
                      Details
                    </button>
                    {b.revert?.revertible && !b.revert.reverted && (
                      <button
                        type="button"
                        onClick={() => setReverting(b)}
                        className="ml-3 text-xs font-semibold text-red-700"
                        aria-label={`Revert import #${b.id}`}
                      >
                        Revert import…
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {reverting && (
        <BidImportRevertDialog
          batchId={reverting.id}
          filename={reverting.filename}
          onClose={() => setReverting(null)}
          onChanged={() => setChanges((n) => n + 1)}
        />
      )}
      {loading && <p className="text-sm text-[var(--gray-text)]">Loading...</p>}
      {!loading && items.length < total && (
        <button
          type="button"
          onClick={() => loadMore(items.length)}
          className="self-start rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)]"
        >
          Load more ({total - items.length} left)
        </button>
      )}
    </div>
  );
};
