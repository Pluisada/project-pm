"use client";

import { useEffect, useRef, useState } from "react";
import { downloadCompanyExcel, getErrorMessage, isAbortError, ROLE_COMPANY_ADMIN, ROLE_SUPER_ADMIN, type ApiError, type ExcelDownload } from "@/lib/api";

interface ExcelDownloadButtonsProps {
  companySlug: string;
  boardId: number;
  /** The caller's role in this company as /api/central/me reported it. Only gates visibility; the backend decides. */
  role: string;
}

const LABELS: Record<ExcelDownload, string> = {
  export: "Export Current Database",
  template: "Download Import Template",
};

function failureMessage(error: unknown): string {
  const status = (error as Partial<ApiError> | null)?.status_code;
  if (status === 401) return "Your session expired. Sign in again.";
  if (status === 403) return "You do not have permission to download this file.";
  if (status === 0) return "Network error - check your connection and try again.";
  return getErrorMessage(error, "The download failed. Try again.");
}

export const ExcelDownloadButtons = ({ companySlug, boardId, role }: ExcelDownloadButtonsProps) => {
  const [busy, setBusy] = useState<ExcelDownload | null>(null);
  // The message remembers the context it belongs to, so it is never shown under another company or board.
  const [message, setMessage] = useState<{ context: string; ok: boolean; text: string } | null>(null);
  const context = `${companySlug}:${boardId}`;
  const inFlight = useRef<AbortController | null>(null);

  // Leaving the context (or unmounting) cancels the download in progress; it never restarts by itself.
  useEffect(() => {
    return () => {
      if (inFlight.current) {
        inFlight.current.abort();
        inFlight.current = null;
        setBusy(null);
      }
    };
  }, [companySlug, boardId]);

  if (role !== ROLE_SUPER_ADMIN && role !== ROLE_COMPANY_ADMIN) return null;

  const run = async (kind: ExcelDownload) => {
    if (inFlight.current) return;
    const controller = new AbortController();
    inFlight.current = controller;
    setBusy(kind);
    setMessage(null);
    try {
      const filename = await downloadCompanyExcel(companySlug, boardId, kind, controller.signal);
      if (!controller.signal.aborted) setMessage({ context, ok: true, text: `Downloaded ${filename}` });
    } catch (error) {
      // A cancelled download is an expected interruption: no message, only the busy state is released.
      if (!controller.signal.aborted && !isAbortError(error)) {
        setMessage({ context, ok: false, text: failureMessage(error) });
      }
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null;
        setBusy(null);
      }
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      {(Object.keys(LABELS) as ExcelDownload[]).map((kind) => (
        <button
          key={kind}
          type="button"
          onClick={() => run(kind)}
          disabled={busy !== null}
          className="rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-4 py-2 text-sm font-semibold text-[var(--navy-dark)] transition hover:bg-[var(--surface)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy === kind ? "Downloading..." : LABELS[kind]}
        </button>
      ))}
      {message && message.context === context && (
        <p role={message.ok ? "status" : "alert"} className={`text-sm ${message.ok ? "text-green-800" : "text-red-800"}`}>
          {message.text}
        </p>
      )}
    </div>
  );
};
