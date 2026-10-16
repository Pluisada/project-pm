"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import {
  getRevertPreview,
  revertBidImport,
  type ApiError,
  type RevertBlocker,
  type RevertPlan,
  type RevertResult,
} from "@/lib/api";
import { tdClass, thClass } from "@/components/ReportBlocks";

const FINGERPRINT = /^[0-9a-f]{64}$/;

const BLOCKER_TEXT: Record<string, { reason: string; guidance: string }> = {
  field_modified_since: {
    reason: "The field was changed after the import.",
    guidance: "Restore the imported value on the card, or accept that this import cannot be reverted.",
  },
  moved_since: {
    reason: "The card was moved to another column after the import.",
    guidance: "Move the card back to the column the import left it in.",
  },
  edited_since: {
    reason: "The card was edited by a user after the import.",
    guidance: "Edits made after an import cannot be discarded automatically.",
  },
  activity_since: {
    reason: "The card has activity recorded after the import.",
    guidance: "Cards with later activity cannot be deleted by a revert.",
  },
  stage_activity_since: {
    reason: "The card changed stage after the import.",
    guidance: "Cards with later stage history cannot be deleted by a revert.",
  },
  source_ref_modified_since: {
    reason: "The card's source reference changed after the import.",
    guidance: "The card no longer matches what the import created.",
  },
  bid_details_modified_after_import: {
    reason: "The Bid details changed after the import.",
    guidance: "The Bid details no longer match what the import wrote.",
  },
  bid_details_missing: {
    reason: "The Bid details recorded by the import are missing.",
    guidance: "The card no longer matches what the import wrote.",
  },
  source_ref_missing: {
    reason: "The card lost its source reference.",
    guidance: "The card no longer matches what the import wrote.",
  },
  origin_column_missing: {
    reason: "The column the card came from no longer exists.",
    guidance: "The card cannot be moved back automatically.",
  },
  missing_without_trace: {
    reason: "The card is gone and the system has no record of its deletion.",
    guidance: "Check the board before reverting.",
  },
  later_batch_decision_on_card: {
    reason: "A later import made a decision about this card.",
    guidance: "Revert the later import first.",
  },
};

const blockerText = (code: string) => ({
  reason: BLOCKER_TEXT[code]?.reason ?? `Conflict: ${code}.`,
  guidance: BLOCKER_TEXT[code]?.guidance ?? "Resolve the conflict, then review the preview again.",
});

const fieldLabel = (field: string | null) => (field ? field.replace(/^(card|bid_details|source_ref)\./, "").replace(/_/g, " ") : "-");

const showValue = (value: RevertBlocker["imported"]) => (value === null || value === undefined || value === "" ? "(empty)" : String(value));

const cardLabel = (id: number | null) => (id === null ? "-" : `Card #${id}`);

type Problem = { message: string; detail?: string; retry?: "preview" | "confirm"; unconfirmed?: boolean; stale?: boolean };

/** Plain-language account of a failed call, saying what is and is not known about the batch. */
function describe(err: unknown, phase: "preview" | "confirm"): Problem {
  const e = (err ?? {}) as Partial<ApiError>;
  const status = e.status_code ?? 0;
  const code = e.code;
  if (status === 0) {
    return phase === "preview"
      ? { message: "Could not reach the server, so the revert preview was not loaded. Nothing was changed.", retry: "preview" }
      : {
          message: "Could not reach the server. The revert may or may not have been applied.",
          detail: "Check the import history before trying again.",
          unconfirmed: true,
        };
  }
  if (status === 503) {
    return {
      message: "The database is busy. Nothing was reverted.",
      detail: "Try again in a few seconds.",
      retry: phase,
    };
  }
  if (status === 409 && code === "plan_changed") {
    return {
      message: "The data changed since this preview. Nothing was reverted.",
      detail: "Review the updated preview before confirming.",
      stale: true,
    };
  }
  if (status === 409 && code === "revert_blocked") {
    return { message: "The revert is blocked by conflicts. Nothing was changed.", stale: true };
  }
  if (status === 409 && code === "already_reverted") {
    return { message: "This import was already reverted." };
  }
  if (status === 409 && code === "not_revertible") {
    return { message: "This import cannot be reverted." };
  }
  if (status === 404) return { message: "This import no longer exists." };
  if (status === 500 && code === "revert_outcome_unconfirmed") {
    return {
      message: "The result of the revert could not be confirmed.",
      detail: "Check the import history before trying again.",
      unconfirmed: true,
    };
  }
  if (status === 500) {
    return phase === "preview"
      ? { message: "The revert preview failed. Nothing was changed.", retry: "preview" }
      : { message: "The revert failed. Nothing was changed.", retry: "confirm" };
  }
  return { message: typeof e.detail === "string" ? e.detail : "The request failed.", retry: phase };
}

const Stat = ({ label, value }: { label: string; value: number }) => (
  <div className="rounded-lg border border-[var(--stroke)] px-3 py-2">
    <dt className="text-xs text-[var(--gray-text)]">{label}</dt>
    <dd className="text-lg font-semibold text-[var(--navy-dark)]">{value}</dd>
  </div>
);

const BlockerTable = ({ blockers }: { blockers: RevertBlocker[] }) => (
  <div className="overflow-x-auto">
    <table className="w-full" aria-label="Conflicts">
      <thead>
        <tr className="border-b border-[var(--stroke)]">
          <th className={thClass}>Card</th>
          <th className={thClass}>Field</th>
          <th className={thClass}>Imported value</th>
          <th className={thClass}>Current value</th>
          <th className={thClass}>Reason</th>
          <th className={thClass}>What to do</th>
        </tr>
      </thead>
      <tbody>
        {blockers.map((b, i) => {
          const text = blockerText(b.code);
          return (
            <tr key={`${b.code}-${b.card_id}-${b.field}-${i}`} className="border-b border-[var(--stroke)] align-top last:border-0">
              <td className={tdClass}>{cardLabel(b.card_id)}</td>
              <td className={tdClass}>{fieldLabel(b.field)}</td>
              <td className={tdClass}>{b.field && b.field !== "source_ref" ? showValue(b.imported) : "-"}</td>
              <td className={tdClass}>{b.field && b.field !== "source_ref" ? showValue(b.current) : "-"}</td>
              <td className={tdClass}>
                {text.reason}
                {b.batch_ids.length > 0 && <span className="block text-xs text-[var(--gray-text)]">Import #{b.batch_ids.join(", #")}</span>}
              </td>
              <td className={tdClass}>{text.guidance}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  </div>
);

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <section className="flex flex-col gap-1">
    <h4 className="text-xs font-semibold uppercase tracking-[0.15em] text-[var(--gray-text)]">{title}</h4>
    {children}
  </section>
);

const PlanDetails = ({ plan }: { plan: RevertPlan }) => {
  const ops = plan.items.flatMap((item) => item.operations.map((op) => ({ op, key: item.source_key })));
  const deletes = ops.filter(({ op }) => op.kind === "delete_card");
  const restores = ops.filter(({ op }) => op.kind === "restore_fields");
  const moves = ops.filter(({ op }) => op.kind === "move_card");
  const gone = plan.items.filter((item) => item.classification === "already_deleted");
  const { counts } = plan;
  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Cards to delete" value={counts.cards_to_delete} />
        <Stat label="Cards to restore" value={counts.cards_to_restore} />
        <Stat label="Moves to undo" value={counts.moves_to_revert} />
        <Stat label="Already deleted" value={counts.already_deleted} />
      </dl>
      {deletes.length > 0 && (
        <Section title="Cards that will be deleted">
          <ul className="text-sm text-[var(--navy-dark)]">
            {deletes.map(({ op, key }) => (
              <li key={`d${op.card_id}`}>{cardLabel(op.card_id)} <span className="text-xs text-[var(--gray-text)]">({key})</span></li>
            ))}
          </ul>
        </Section>
      )}
      {restores.length > 0 && (
        <Section title="Fields that will be restored">
          <ul className="text-sm text-[var(--navy-dark)]">
            {restores.map(({ op, key }) => (
              <li key={`r${op.card_id}`}>
                {cardLabel(op.card_id)} <span className="text-xs text-[var(--gray-text)]">({key})</span>: {(op.fields ?? []).map(fieldLabel).join(", ")}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {moves.length > 0 && (
        <Section title="Moves that will be undone">
          <ul className="text-sm text-[var(--navy-dark)]">
            {moves.map(({ op, key }) => (
              <li key={`m${op.card_id}`}>
                {cardLabel(op.card_id)} <span className="text-xs text-[var(--gray-text)]">({key})</span> returns to its original column
              </li>
            ))}
          </ul>
        </Section>
      )}
      {gone.length > 0 && (
        <Section title="Already deleted">
          <ul className="text-sm text-[var(--navy-dark)]">
            {gone.map((item) => (
              <li key={`g${item.item_id}`}>{cardLabel(item.card_id)} <span className="text-xs text-[var(--gray-text)]">({item.source_key})</span> was already deleted; nothing to do</li>
            ))}
          </ul>
        </Section>
      )}
      {plan.dependent_batches.length > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" role="note">
          <p className="font-semibold">Later imports touched these cards</p>
          <ul>
            {plan.dependent_batches.map((d) => (
              <li key={d.batch_id}>
                Revert import #{d.batch_id} first ({d.card_ids.length} {d.card_ids.length === 1 ? "card" : "cards"}).
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

type Props = {
  batchId: number;
  filename: string;
  onClose: () => void;
  /** Called once the server confirmed the revert (or showed it already happened), so the history can reload. */
  onChanged: () => void;
};

export const BidImportRevertDialog = ({ batchId, filename, onClose, onChanged }: Props) => {
  const [plan, setPlan] = useState<RevertPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<RevertResult | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    getRevertPreview(batchId)
      .then((p) => {
        if (cancelled) return;
        setPlan(p);
        setProblem((prev) => (prev?.stale ? prev : null));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPlan(null);
        setProblem(describe(err, "preview"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [batchId, reloadKey]);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  const reload = useCallback(() => {
    setLoading(true);
    setReloadKey((k) => k + 1);
  }, []);

  const reloadFresh = () => {
    setProblem(null);
    reload();
  };

  const unconfirmed = !!problem?.unconfirmed;
  const canConfirm =
    !!plan && plan.status === "ready" && plan.revertible && FINGERPRINT.test(plan.plan_fingerprint) && !submitting && !unconfirmed && !result;

  const confirm = async () => {
    if (!canConfirm || !plan) return;
    setSubmitting(true);
    setProblem(null);
    try {
      const done = await revertBidImport(batchId, plan.plan_fingerprint);
      setResult(done);
      onChanged();
    } catch (err) {
      const next = describe(err, "confirm");
      setProblem(next);
      // The server is the authority: after a refusal, re-read the state instead of guessing.
      if (next.stale) reload();
      if (!next.stale && !next.retry && !next.unconfirmed) {
        setPlan(null); // already reverted, not revertible or gone: nothing left to confirm
        onChanged();
      }
    } finally {
      setSubmitting(false);
    }
  };

  const blocked = !!plan && plan.status === "blocked";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="revert-title"
        aria-busy={loading || submitting}
        className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-[24px] border border-[var(--stroke)] bg-white shadow-[var(--shadow)] outline-none"
      >
        <div className="flex items-start justify-between gap-4 border-b border-[var(--stroke)] px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">Revert import</p>
            <h3 id="revert-title" className="mt-1 font-display text-lg font-semibold text-[var(--navy-dark)]">
              Import #{batchId} <span className="text-sm font-normal text-[var(--gray-text)]">{filename}</span>
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--stroke)] text-sm font-semibold text-[var(--gray-text)] disabled:opacity-50"
          >
            X
          </button>
        </div>

        <div className="flex flex-col gap-4 overflow-y-auto px-6 py-4">
          {!result && (
            <p className="rounded-lg border border-[var(--stroke)] bg-[var(--surface)] p-3 text-sm text-[var(--navy-dark)]">
              Reverting cannot be undone with the Kanban Undo. To apply these changes again, import the file again.
            </p>
          )}

          {loading && !result && <p className="text-sm text-[var(--gray-text)]">Loading the revert plan...</p>}

          {problem && (
            <div
              role="alert"
              className={clsx("rounded-lg border p-3 text-sm", problem.unconfirmed ? "border-amber-300 bg-amber-50 text-amber-900" : "border-red-300 bg-red-50 text-red-800")}
            >
              <p className="font-semibold">{problem.message}</p>
              {problem.detail && <p className="text-xs">{problem.detail}</p>}
              {problem.retry && (
                <button
                  type="button"
                  onClick={problem.retry === "preview" ? reloadFresh : confirm}
                  disabled={submitting}
                  className="mt-2 rounded-lg border border-current px-3 py-1 text-xs font-semibold disabled:opacity-50"
                >
                  {problem.retry === "preview" ? "Reload preview" : "Try again"}
                </button>
              )}
              {problem.unconfirmed && (
                <button
                  type="button"
                  onClick={() => {
                    onChanged();
                    onClose();
                  }}
                  className="mt-2 rounded-lg border border-current px-3 py-1 text-xs font-semibold"
                >
                  Check import history
                </button>
              )}
            </div>
          )}

          {result && (
            <div role="status" className="rounded-lg border border-green-300 bg-green-50 p-3 text-sm text-green-900">
              <p className="font-semibold">
                {result.no_commercial_changes ? "Import marked as reverted. It had no commercial changes to undo." : "Import reverted."}
              </p>
              <p className="text-xs">
                {result.counts.cards_deleted} deleted, {result.counts.cards_restored} restored, {result.counts.moves_reverted} moves undone,{" "}
                {result.counts.already_deleted} already deleted.
              </p>
              <p className="mt-1 text-xs">This cannot be undone with the Kanban Undo. To apply these changes again, import the file again.</p>
            </div>
          )}

          {plan && !result && (
            <>
              {plan.status === "already_reverted" && <p className="text-sm text-[var(--navy-dark)]">This import was already reverted.</p>}
              {plan.status === "not_revertible" && <p className="text-sm text-[var(--navy-dark)]">This import cannot be reverted.</p>}
              {plan.no_commercial_changes && plan.status === "ready" && (
                <p className="text-sm text-[var(--navy-dark)]">This import made no commercial changes. Confirming only marks it as reverted.</p>
              )}
              {(plan.status === "ready" || blocked) && <PlanDetails plan={plan} />}
              {blocked && (
                <div className="flex flex-col gap-2">
                  <p className="text-sm font-semibold text-red-800" role="alert">
                    {plan.blockers.length} {plan.blockers.length === 1 ? "conflict blocks" : "conflicts block"} this revert. Nothing will change until they are resolved.
                  </p>
                  <BlockerTable blockers={plan.blockers} />
                </div>
              )}
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-[var(--stroke)] px-6 py-3">
          {result ? (
            <button type="button" onClick={onClose} className="rounded-lg bg-[var(--primary-blue)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-white">
              Done
            </button>
          ) : (
            <>
              {blocked && (
                <button type="button" onClick={reload} disabled={loading || submitting} className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] disabled:opacity-50">
                  Review again
                </button>
              )}
              <button type="button" onClick={onClose} disabled={submitting} className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] disabled:opacity-50">
                Cancel
              </button>
              <button
                type="button"
                onClick={confirm}
                disabled={!canConfirm}
                className="rounded-lg bg-red-700 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-white disabled:opacity-50"
              >
                {submitting ? "Reverting..." : "Revert import"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
