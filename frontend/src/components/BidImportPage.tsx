"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import {
  confirmBidImport,
  getBoard,
  getErrorMessage,
  previewBidImport,
  type ImportPreview,
  type ImportResult,
} from "@/lib/api";
import { decisionsKey, formatCents, toDecisions, type Choices } from "@/lib/bidImport";
import { BidImportHistory } from "@/components/BidImportHistory";
import { BidImportRows } from "@/components/BidImportRows";
import { Section, StatTile } from "@/components/ReportBlocks";

type BidImportPageProps = {
  boardId: number;
};

const buttonClass =
  "rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] transition hover:border-[var(--primary-blue)] disabled:opacity-50";
const primaryClass =
  "rounded-full bg-[var(--secondary-purple)] px-6 py-3 text-xs font-semibold uppercase tracking-wide text-white transition hover:brightness-110 disabled:opacity-50";

const Alert = ({ children }: { children: React.ReactNode }) => (
  <div className="rounded-lg border border-red-300 bg-red-50 p-3" role="alert">
    <p className="text-sm font-medium text-red-800">{children}</p>
  </div>
);

export const BidImportPage = ({ boardId }: BidImportPageProps) => {
  const [tab, setTab] = useState<"new" | "history">("new");
  const [stages, setStages] = useState<string[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [choices, setChoices] = useState<Choices>({});
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  // The decisions the current preview (and its token) was built with.
  const [previewedKey, setPreviewedKey] = useState("[]");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState<"preview" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getBoard(boardId)
      .then((board) => setStages([...board.columns].sort((a, b) => a.position - b.position).map((c) => c.title)))
      .catch((err: unknown) => setError(getErrorMessage(err, "Failed to load the board's stages")));
  }, [boardId]);

  const reset = () => {
    setFile(null);
    setChoices({});
    setPreview(null);
    setPreviewedKey("[]");
    setAcknowledged(false);
    if (fileInput.current) fileInput.current.value = "";
  };

  const handleFile = (selected: File | null) => {
    reset();
    setError(null);
    setResult(null);
    setFile(selected);
  };

  const runPreview = async () => {
    if (!file) return;
    setBusy("preview");
    setError(null);
    setAcknowledged(false);
    try {
      const key = decisionsKey(choices);
      setPreview(await previewBidImport(boardId, file, toDecisions(choices)));
      setPreviewedKey(key);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to preview the file"));
    } finally {
      setBusy(null);
    }
  };

  const runConfirm = async () => {
    if (!file || !preview?.preview_token) return;
    setBusy("confirm");
    setError(null);
    try {
      const outcome = await confirmBidImport(boardId, file, toDecisions(choices), preview.preview_token);
      setResult(outcome);
      setHistoryKey((k) => k + 1);
      reset();
    } catch (err) {
      // A stale or refused preview can't be retried: the admin must preview again.
      setError(getErrorMessage(err, "The import failed"));
      setPreview(null);
      setAcknowledged(false);
      setHistoryKey((k) => k + 1);
    } finally {
      setBusy(null);
    }
  };

  const stale = preview !== null && decisionsKey(choices) !== previewedKey;
  const pending = preview ? preview.rows.filter((r) => r.required_decisions.length > 0).length : 0;
  const canConfirm = !!preview?.confirmable && !!preview.preview_token && !stale && busy === null && acknowledged;
  const summary = preview?.summary;

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-6 px-4 py-5 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-[var(--navy-dark)]">Import Bids</h1>
          <p className="mt-2 text-sm text-[var(--gray-text)]">
            Upload the Bids report, review what would change, then confirm. Nothing is written until you confirm.
          </p>
        </div>
      </div>

      <div className="flex gap-2" role="tablist">
        {(["new", "history"] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={clsx(
              "rounded-full border px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] transition",
              tab === value
                ? "border-[var(--primary-blue)] bg-[var(--primary-blue)] text-white"
                : "border-[var(--stroke)] text-[var(--navy-dark)] hover:border-[var(--primary-blue)]"
            )}
          >
            {value === "new" ? "New import" : "History"}
          </button>
        ))}
      </div>

      {tab === "history" ? (
        <Section title="Import history">
          <BidImportHistory boardId={boardId} refreshKey={historyKey} />
        </Section>
      ) : (
        <>
          {result && (
            <div className="rounded-2xl border border-green-300 bg-green-50 p-4 text-sm text-green-900" role="status">
              <p className="font-semibold">
                {result.batch ? `Import #${result.batch.id} completed.` : "Nothing to import: every row is unchanged. No batch was recorded."}
              </p>
              <p>
                {result.created_card_ids.length} created, {result.updated_card_ids.length} updated (
                {result.moved_card_ids.length} moved), {result.skipped} skipped, {result.review} left in review.
              </p>
            </div>
          )}

          <Section title="1. Choose the file">
            <div className="flex flex-wrap items-center gap-3">
              <input
                ref={fileInput}
                type="file"
                accept=".xlsx"
                aria-label="Bids report (.xlsx)"
                disabled={busy !== null}
                onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
                className="text-sm"
              />
              {!preview && (
                <button type="button" onClick={runPreview} disabled={!file || busy !== null} className={primaryClass}>
                  {busy === "preview" ? "Reading..." : "Preview"}
                </button>
              )}
            </div>
          </Section>

          {error && <Alert>{error}</Alert>}

          {preview && summary && (
            <>
              <Section title="2. Summary">
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  <StatTile label="Rows" lines={[String(summary.total_rows)]} note={`${summary.valid_rows} valid`} />
                  <StatTile label="New cards" lines={[String(summary.proposed_creates)]} />
                  <StatTile
                    label="Updates"
                    lines={[String(summary.proposed_updates)]}
                    note={`${summary.unconfirmed_updates} unconfirmed`}
                  />
                  <StatTile
                    label="Needs decision"
                    lines={[String(pending)]}
                    note={`${summary.rows_with_errors} with errors, ${summary.rows_with_warnings} with warnings`}
                  />
                </div>
                <p className="mt-3 text-xs text-[var(--gray-text)]">
                  {summary.unchanged} unchanged, {summary.ignored} ignored, {summary.internal_duplicate_rows} duplicate
                  row(s) in the file.
                </p>
                {Object.keys(summary.by_target_stage).length > 0 && (
                  <p className="mt-1 text-xs text-[var(--gray-text)]">
                    By stage:{" "}
                    {Object.entries(summary.by_target_stage)
                      .map(([stage, count]) => `${stage} ${count}`)
                      .join(", ")}
                  </p>
                )}
                {Object.entries(summary.revenue_cents_by_currency).map(([currency, totals]) => (
                  <p key={currency} className="mt-1 text-xs text-[var(--gray-text)]">
                    Revenue {currency}: {formatCents(totals.current_year, currency)} this year,{" "}
                    {formatCents(totals.next_year, currency)} next year
                  </p>
                ))}
                {preview.sheet && (
                  <p className="mt-1 text-xs text-[var(--gray-text)]">
                    Sheet &quot;{preview.sheet.name}&quot;
                    {preview.sheet.missing_optional_columns.length > 0 &&
                      `; missing optional columns: ${preview.sheet.missing_optional_columns.join(", ")}`}
                    {preview.sheet.ignored_columns.length > 0 &&
                      `; ignored columns: ${preview.sheet.ignored_columns.join(", ")}`}
                  </p>
                )}
                {preview.file_warnings.length > 0 && (
                  <ul className="mt-2 list-disc pl-5 text-xs text-yellow-800">
                    {preview.file_warnings.map((w, i) => (
                      <li key={`${w.code}-${i}`}>{w.message}</li>
                    ))}
                  </ul>
                )}
                {preview.notice && <p className="mt-2 text-xs text-[var(--gray-text)]">{preview.notice}</p>}
              </Section>

              <Section title="3. Rows and decisions">
                <BidImportRows
                  rows={preview.rows}
                  stages={stages}
                  choices={choices}
                  disabled={busy !== null}
                  onChoices={setChoices}
                />
              </Section>

              <Section title="4. Confirm">
                {!preview.confirmable && (
                  <Alert>
                    This preview can&apos;t be confirmed: a decision doesn&apos;t fit its row. Clear it, then update the
                    preview.
                  </Alert>
                )}
                {stale && (
                  <p className="mb-3 text-sm font-medium text-yellow-800" role="status">
                    Your decisions changed since this preview. Update the preview before confirming.
                  </p>
                )}
                {pending > 0 && (
                  <p className="mb-3 text-sm text-[var(--gray-text)]">
                    {pending} row(s) still need a decision; they are not imported and stay in review.
                  </p>
                )}
                <label className="mb-4 flex items-center gap-2 text-sm text-[var(--navy-dark)]">
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    disabled={busy !== null || stale || !preview.confirmable}
                    onChange={(e) => setAcknowledged(e.target.checked)}
                  />
                  I reviewed this preview and want to apply it to the board.
                </label>
                <div className="flex flex-wrap items-center gap-3">
                  <button type="button" onClick={runPreview} disabled={busy !== null} className={buttonClass}>
                    {busy === "preview" ? "Reading..." : "Update preview"}
                  </button>
                  <button type="button" onClick={runConfirm} disabled={!canConfirm} className={primaryClass}>
                    {busy === "confirm" ? "Importing..." : "Confirm import"}
                  </button>
                  <span className="text-xs text-[var(--gray-text)]">
                    The preview is valid for {preview.expires_in_minutes} minutes.
                  </span>
                </div>
              </Section>
            </>
          )}
        </>
      )}
    </div>
  );
};
