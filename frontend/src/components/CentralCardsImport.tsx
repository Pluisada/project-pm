"use client";

import { useEffect, useRef, useState } from "react";
import {
  confirmCompanyCardsImport,
  getCompanyMe,
  getErrorMessage,
  isAbortError,
  previewCompanyCardsImport,
  type ApiError,
  type CardsImportDiff,
  type CardsImportIssue,
  type CardsImportPreview,
  type CardsImportResult,
  type CardsImportRow,
  type CardsImportSummary,
  type CompanyMe,
} from "@/lib/api";

interface CentralCardsImportProps {
  companySlug: string;
  boardId: number;
  /** Called after an import was applied, so the board is read again. */
  onImported?: () => void;
}

const ROWS_SHOWN = 50;
const ACTION_LABEL: Record<CardsImportRow["action"], string> = {
  create: "Create",
  update: "Update",
  unchanged: "No change",
  invalid: "Error",
};

type Phase = "idle" | "previewing" | "preview" | "confirming" | "uncertain" | "done";
type Notice = { ok: boolean; text: string };

const appliedAt = (iso: string) => new Date(iso.endsWith("Z") ? iso : `${iso}Z`).toLocaleString();
const countsText = (s: Pick<CardsImportSummary, "created" | "updated" | "unchanged">) =>
  `${s.created} created, ${s.updated} updated, ${s.unchanged} unchanged`;

const FIELD_LABEL: Record<string, string> = {
  title: "Title", company: "Company", contact: "Contact", details: "Details", deal_value: "Deal value (USD)",
  deal_value_next_year: "Next-year value (USD)", confidence_level: "Probability", expected_close_date: "Close date",
  product_line: "Product line", lead_source: "Lead source", salesperson_id: "Salesperson", stage: "Stage",
};
const SHOWN_MAX = 120;
const LEDGER_INCOMPATIBLE =
  "The import ledger of this company has an unexpected structure. Nothing was imported. An administrator must investigate the ledger configuration before importing; do not retry.";

/** A missing value reads as empty (or unassigned for the salesperson), never as a blank that could be missed. */
const shown = (field: string, value: string | null) => {
  if (value === null || value === "") return field === "salesperson_id" ? "(unassigned)" : "(empty)";
  return value.length > SHOWN_MAX ? `${value.slice(0, SHOWN_MAX)}...` : value;
};
const diffText = (d: CardsImportDiff) => `${shown(d.field, d.old)} -> ${shown(d.field, d.new)}`;

const where = (issue: CardsImportIssue) =>
  `${issue.row ? `Row ${issue.row}` : "File"}${issue.column ? `, ${issue.column}` : ""}`;

/**
 * What the person may be told for a failed confirmation. `uncertain` means the outcome is unknown (the response
 * was lost, or the server failed without saying nothing was changed): the screen then offers only a re-check.
 */
function confirmFailure(error: unknown): { uncertain: boolean; stale: boolean; text: string } {
  const e = error as Partial<ApiError> | null;
  const status = e?.status_code;
  const definite = e?.data?.applied === false;
  const fallback = getErrorMessage(error, "The import failed.");
  if (e?.code === "import_not_initialized") {
    return { uncertain: false, stale: true, text: "The Excel import is not set up for this company yet. Nothing was imported." };
  }
  if (e?.code === "import_ledger_incompatible") return { uncertain: false, stale: true, text: LEDGER_INCOMPATIBLE };
  if (status === 401) return { uncertain: false, stale: false, text: "Your session expired. Sign in again; nothing was imported." };
  if (status === 403) return { uncertain: false, stale: false, text: "You do not have permission to import cards. Nothing was imported." };
  if (e?.code === "already_imported") {
    const s = e.data?.import_summary as CardsImportSummary | undefined;
    return {
      uncertain: false, stale: true,
      text: s ? `This workbook was already imported on ${appliedAt(s.applied_at)} (${countsText(s)}) and cannot be applied again. Nothing was changed now.`
        : "This workbook was already imported. Nothing was changed now.",
    };
  }
  if (e?.code === "no_changes") return { uncertain: false, stale: true, text: "The workbook changes nothing on this board. Nothing was changed." };
  if (e?.code === "preview_stale") return { uncertain: false, stale: true, text: `${fallback}. Nothing was imported.` };
  if (definite) return { uncertain: false, stale: false, text: `${fallback}. Nothing was changed; you may confirm again.` };
  if (status !== undefined && status >= 400 && status < 500) return { uncertain: false, stale: false, text: `${fallback}. Nothing was imported.` };
  return {
    uncertain: true, stale: false,
    text: status === 0
      ? "The connection was lost before the result arrived. The import may or may not have been applied."
      : "The server did not confirm the result. The import may or may not have been applied.",
  };
}

export const CentralCardsImport = ({ companySlug, boardId, onImported }: CentralCardsImportProps) => {
  const [me, setMe] = useState<CompanyMe | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [preview, setPreview] = useState<CardsImportPreview | null>(null);
  const [result, setResult] = useState<CardsImportResult | CardsImportSummary | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [showAll, setShowAll] = useState(false);
  const mounted = useRef(true);
  const busy = useRef(false); // one request at a time; also stops a double click from sending twice
  const inFlight = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const resultHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      inFlight.current?.abort(); // only a preview is abortable; a confirmation is never cancelled half way
    };
  }, []);

  // Capabilities only decide visibility; the backend refuses everyone else. A failed read shows nothing.
  useEffect(() => {
    const controller = new AbortController();
    getCompanyMe(companySlug, controller.signal).then(
      (answer) => {
        if (!controller.signal.aborted) setMe(answer);
      },
      () => {}
    );
    return () => controller.abort();
  }, [companySlug]);

  useEffect(() => {
    if (phase === "preview" || phase === "done" || phase === "uncertain") resultHeading.current?.focus();
  }, [phase]);

  const caps = me?.capabilities;
  if (!caps || caps.readOnly !== false || caps.canImport !== true) return null;

  const reset = () => {
    setFile(null);
    setPreview(null);
    setResult(null);
    setNotice(null);
    setShowAll(false);
    setPhase("idle");
    if (fileInput.current) fileInput.current.value = "";
  };

  const choose = (picked: File | null) => {
    if (busy.current) return;
    setFile(picked);
    setPreview(null);
    setResult(null);
    setNotice(null);
    setShowAll(false);
    setPhase("idle");
  };

  const runPreview = async (recheck = false) => {
    if (!file || busy.current) return;
    busy.current = true;
    const controller = new AbortController();
    inFlight.current = controller;
    setNotice(null);
    setPhase("previewing");
    try {
      const body = await previewCompanyCardsImport(companySlug, boardId, file, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      // The ledger record is the only proof of an application; what the preview would change now proves nothing.
      const recorded = body.already_imported ?? body.previous_import;
      if (recheck && recorded) {
        setResult(recorded);
        setPreview(null);
        setNotice({ ok: true, text: `The import was applied on ${appliedAt(recorded.applied_at)}.` });
        setPhase("done");
        onImported?.();
        return;
      }
      setPreview(body);
      setNotice(recheck ? { ok: true, text: "The server has no record of this workbook being applied on this board. Review the preview below before confirming again." } : null);
      setPhase("preview");
    } catch (error) {
      if (!mounted.current || isAbortError(error)) return;
      const status = (error as Partial<ApiError>).status_code;
      setPreview(null);
      const notInitialized = (error as Partial<ApiError>).code === "import_not_initialized";
      const ledgerBad = (error as Partial<ApiError>).code === "import_ledger_incompatible";
      setNotice({
        ok: false,
        text: notInitialized ? "The Excel import is not set up for this company yet. Nothing was imported."
          : ledgerBad ? LEDGER_INCOMPATIBLE
          : status === 401 ? "Your session expired. Sign in again."
          : status === 403 ? "You do not have permission to import cards."
          : status === 0 ? "Network error - check your connection and try again."
          : getErrorMessage(error, "The preview failed. Try again."),
      });
      setPhase(recheck && !notInitialized && !ledgerBad ? "uncertain" : "idle");
    } finally {
      busy.current = false;
      if (inFlight.current === controller) inFlight.current = null;
    }
  };

  const runConfirm = async () => {
    if (!file || !preview || !preview.confirmable || busy.current) return;
    busy.current = true;
    setNotice(null);
    setPhase("confirming");
    try {
      const done = await confirmCompanyCardsImport(companySlug, boardId, file, preview.plan_digest);
      if (!mounted.current) {
        onImported?.();
        return;
      }
      setResult(done);
      setPreview(null);
      setPhase("done");
      onImported?.();
    } catch (error) {
      if (!mounted.current) return;
      const failure = confirmFailure(error);
      setNotice({ ok: false, text: failure.text });
      if (failure.uncertain) {
        setPhase("uncertain"); // no confirm button here: only a re-check, which reads and never writes
      } else if (failure.stale) {
        setPreview(null); // the preview no longer describes the board: ask for a new one, never send it again
        setPhase("idle");
      } else {
        setPhase("preview");
      }
    } finally {
      busy.current = false;
    }
  };

  const issues = preview
    ? [...preview.file_warnings, ...preview.rows.flatMap((r) => r.errors), ...preview.rows.flatMap((r) => r.warnings)]
    : [];
  const errors = preview ? preview.rows.flatMap((r) => r.errors) : [];
  const warnings = preview ? [...preview.file_warnings, ...preview.rows.flatMap((r) => r.warnings)] : [];
  const changes = preview ? preview.rows.filter((r) => r.action === "create" || r.action === "update") : [];
  const visible = showAll ? changes : changes.slice(0, ROWS_SHOWN);
  const working = phase === "previewing" || phase === "confirming";

  return (
    <section aria-labelledby="cards-import-title" className="rounded-[24px] border border-[var(--stroke)] bg-[var(--surface-strong)]/80 p-6 shadow-[var(--shadow)]">
      <h2 id="cards-import-title" className="font-display text-xl font-semibold text-[var(--navy-dark)]">Import Cards from Excel</h2>
      <p className="mt-2 text-sm text-[var(--gray-text)]">
        Upload a Cards workbook (the export or the template). A row with an empty card_id creates a card; a row with the
        exported card_id updates that card of this board. Nothing is saved until you confirm, and a workbook with any error
        is not imported at all.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label htmlFor="cards-import-file" className="text-sm font-semibold text-[var(--navy-dark)]">Excel workbook (.xlsx)</label>
        <input
          ref={fileInput}
          id="cards-import-file"
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          disabled={working || phase === "uncertain"}
          onChange={(e) => choose(e.target.files?.[0] ?? null)}
          className="text-sm"
        />
        <button
          type="button"
          onClick={() => runPreview()}
          disabled={!file || working || phase === "uncertain" || phase === "done"}
          className="rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-4 py-2 text-sm font-semibold text-[var(--navy-dark)] transition hover:bg-[var(--surface)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {phase === "previewing" ? "Checking..." : "Preview import"}
        </button>
      </div>

      {notice && (
        <p role={notice.ok ? "status" : "alert"} className={`mt-4 text-sm font-medium ${notice.ok ? "text-green-800" : "text-red-800"}`}>
          {notice.text}
        </p>
      )}

      {phase === "uncertain" && (
        <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4">
          <h3 ref={resultHeading} tabIndex={-1} className="text-sm font-semibold text-amber-900">Result unknown - do not import again yet</h3>
          <p className="mt-1 text-sm text-amber-900">
            Check the result first. This only reads: it asks the server whether this workbook was already applied.
          </p>
          <div className="mt-3 flex gap-3">
            <button type="button" onClick={() => runPreview(true)} disabled={working}
              className="rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
              Check result
            </button>
            <button type="button" onClick={reset}
              className="rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-4 py-2 text-sm font-semibold text-[var(--navy-dark)]">
              Start over
            </button>
          </div>
        </div>
      )}

      {phase === "done" && result && (
        <div className="mt-4" role="region" aria-label="Import result">
          <h3 ref={resultHeading} tabIndex={-1} className="text-sm font-semibold text-[var(--navy-dark)]">Import result</h3>
          <p className="mt-1 text-sm">{countsText(result)}.</p>
          <button type="button" onClick={reset}
            className="mt-3 rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-4 py-2 text-sm font-semibold text-[var(--navy-dark)]">
            Import another file
          </button>
        </div>
      )}

      {(phase === "preview" || phase === "confirming") && preview && (
        <div className="mt-4" role="region" aria-label="Import preview">
          <h3 ref={resultHeading} tabIndex={-1} className="text-sm font-semibold text-[var(--navy-dark)]">Preview of {preview.filename}</h3>
          <dl className="mt-2 grid grid-cols-2 gap-2 text-sm sm:grid-cols-5">
            <div><dt className="text-[var(--gray-text)]">New cards</dt><dd className="font-semibold">{preview.summary.creates}</dd></div>
            <div><dt className="text-[var(--gray-text)]">Updates</dt><dd className="font-semibold">{preview.summary.updates}</dd></div>
            <div><dt className="text-[var(--gray-text)]">No change</dt><dd className="font-semibold">{preview.summary.unchanged}</dd></div>
            <div><dt className="text-[var(--gray-text)]">Errors</dt><dd className="font-semibold">{preview.summary.invalid}</dd></div>
            <div><dt className="text-[var(--gray-text)]">Warnings</dt><dd className="font-semibold">{preview.summary.warnings}</dd></div>
          </dl>

          {errors.length > 0 && (
            <div className="mt-3">
              <h4 className="text-sm font-semibold text-red-800">Errors - fix these in the workbook and preview again</h4>
              <ul className="mt-1 list-disc pl-5 text-sm text-red-800">
                {errors.slice(0, showAll ? undefined : ROWS_SHOWN).map((issue, i) => (
                  <li key={`e${i}`}>{where(issue)}: {issue.message}</li>
                ))}
              </ul>
            </div>
          )}
          {warnings.length > 0 && (
            <div className="mt-3">
              <h4 className="text-sm font-semibold text-amber-900">Warnings</h4>
              <ul className="mt-1 list-disc pl-5 text-sm text-amber-900">
                {warnings.slice(0, showAll ? undefined : ROWS_SHOWN).map((issue, i) => (
                  <li key={`w${i}`}>{where(issue)}: {issue.message}</li>
                ))}
              </ul>
            </div>
          )}

          {preview.blocked_reason === "already_imported" && preview.already_imported && (
            <p role="alert" className="mt-3 text-sm font-medium text-red-800">
              This workbook was already imported on {appliedAt(preview.already_imported.applied_at)} ({countsText(preview.already_imported)}),
              import #{preview.already_imported.import_id}. It cannot be confirmed again, even if its cards were changed or
              deleted since. To import similar data, change a value in the workbook.
            </p>
          )}
          {preview.blocked_reason === "no_changes" && (
            <p role="status" className="mt-3 text-sm font-medium text-[var(--navy-dark)]">
              The workbook changes nothing on this board.
              {preview.previous_import ? ` This content was applied on ${appliedAt(preview.previous_import.applied_at)}.` : ""}
            </p>
          )}

          {changes.length > 0 && (
            <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Cards this import would create or update</caption>
              <thead>
                <tr className="text-[var(--gray-text)]">
                  <th scope="col" className="py-1 pr-3">Row</th>
                  <th scope="col" className="py-1 pr-3">Operation</th>
                  <th scope="col" className="py-1 pr-3">Card</th>
                  <th scope="col" className="py-1 pr-3">Stage</th>
                  <th scope="col" className="py-1">Changes</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => (
                  <tr key={r.row} className="border-t border-[var(--stroke)]">
                    <td className="py-1 pr-3">{r.row}</td>
                    <td className="py-1 pr-3 font-semibold">{ACTION_LABEL[r.action]}</td>
                    <td className="py-1 pr-3">{r.title}{r.card_id ? ` (#${r.card_id})` : ""}</td>
                    <td className="py-1 pr-3">{r.stage}</td>
                    <td className="py-1 break-words">
                      {r.action === "update" ? (
                        <ul className="space-y-0.5">
                          {r.diffs.map((d) => (
                            <li key={d.field}><span className="font-semibold">{FIELD_LABEL[d.field] ?? d.field}:</span> {diffText(d)}</li>
                          ))}
                        </ul>
                      ) : "new card"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
          {(changes.length > ROWS_SHOWN || issues.length > ROWS_SHOWN) && !showAll && (
            <button type="button" onClick={() => setShowAll(true)} className="mt-2 text-sm font-semibold underline">Show everything</button>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={runConfirm}
              disabled={!preview.confirmable || phase === "confirming"}
              className="rounded-lg bg-[var(--secondary-purple)] px-5 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {phase === "confirming"
                ? "Importing..."
                : `Confirm import (${preview.summary.creates} new, ${preview.summary.updates} updated)`}
            </button>
            <button type="button" onClick={reset} disabled={phase === "confirming"}
              className="rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-4 py-2 text-sm font-semibold text-[var(--navy-dark)] disabled:opacity-50">
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
};
