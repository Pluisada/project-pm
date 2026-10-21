"use client";

import { useEffect, useRef, useState } from "react";
import { createDefaultCompanyBoard, getCompanyMe, type ApiError, type DefaultBoardResponse } from "@/lib/api";

/**
 * The explicit "Create default board" action of a company with no board. Shown only when the backend's capabilities
 * for the caller (GET .../me) allow board management; the backend re-checks the request. Nothing is created on mount
 * or on a read. One request at a time, bound to the company captured when it began: the parent keys this component by
 * company, so a switch unmounts it and its answer is dropped. After a failure of unknown outcome (network, 5xx,
 * including database_busy) the board list is read again before another attempt is invited.
 */
export function CentralDefaultBoard({
  companySlug,
  onCreated,
  onRefreshBoards,
}: {
  companySlug: string;
  onCreated: (board: DefaultBoardResponse) => void;
  onRefreshBoards: () => Promise<boolean>;
}) {
  const [allowed, setAllowed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "error" | "uncertain"; text: string } | null>(null);
  const busyRef = useRef(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // A failed read leaves the action hidden (never an assumed privilege).
  useEffect(() => {
    const controller = new AbortController();
    getCompanyMe(companySlug, controller.signal).then(
      (me) => {
        if (!controller.signal.aborted) setAllowed(me.capabilities.readOnly === false && me.capabilities.canManageBoards === true);
      },
      () => {}
    );
    return () => controller.abort();
  }, [companySlug]);

  if (!allowed) return null;

  const create = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setNotice(null);
    const slug = companySlug; // captured now: the result belongs to this company only
    try {
      const board = await createDefaultCompanyBoard(slug);
      if (alive.current) onCreated(board);
    } catch (err) {
      const code = (err as Partial<ApiError>).status_code;
      if (code === 401 || !alive.current) return;
      if (code === 403) {
        setNotice({ kind: "error", text: "You are not allowed to create the board." });
      } else if (code === 0 || (code != null && code >= 500)) {
        const refreshed = await onRefreshBoards();
        if (!alive.current) return;
        const busyText = code === 503 ? "The service is busy." : code === 0 ? "Network error." : "Server error.";
        setNotice({
          kind: "uncertain",
          text: `${busyText} The board may not have been created. ${
            refreshed ? "The board list was reloaded; check it before trying again." : "The board list could not be reloaded either; reload the page and check before trying again."
          }`,
        });
      } else {
        setNotice({ kind: "error", text: "Could not create the default board." });
      }
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  };

  return (
    <div className="mt-4">
      <button type="button" onClick={() => void create()} disabled={busy} aria-busy={busy}
        className="rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
        {busy ? "Creating..." : "Create default board"}
      </button>
      {notice && (
        <p role={notice.kind === "error" ? "alert" : "status"} data-testid="default-board-notice"
          className={`mt-3 text-sm font-medium ${notice.kind === "error" ? "text-red-800" : "text-amber-900"}`}>
          {notice.text}
        </p>
      )}
    </div>
  );
}
