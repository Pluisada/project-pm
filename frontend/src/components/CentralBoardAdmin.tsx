"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createCompanyBoard,
  createCompanyColumn,
  deleteCompanyColumn,
  getCompanyBoard,
  getCompanyMe,
  getErrorMessage,
  isAbortError,
  ROLE_SUPER_ADMIN,
  updateCompanyColumn,
  type ApiError,
  type BoardDetail,
  type BoardResponse,
  type ColumnResponse,
  type CompanyMe,
  type StageKind,
} from "@/lib/api";

const STAGE_LABELS: Record<StageKind, string> = { open: "Open", won: "Won", lost: "Lost" };
const STAGE_KINDS: StageKind[] = ["open", "won", "lost"];

const fieldClass =
  "mt-1 w-full rounded-lg border border-[var(--stroke)] bg-white px-3 py-2 text-sm outline-none focus:border-[var(--primary-blue)] disabled:opacity-50";
const labelClass = "block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]";
const primaryButton =
  "rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "rounded-lg border border-[var(--stroke)] px-3 py-1.5 text-xs font-semibold text-[var(--primary-blue)] transition hover:border-[var(--primary-blue)] disabled:cursor-not-allowed disabled:opacity-50";

type Notice = { kind: "ok" | "error" | "uncertain"; text: string };

/**
 * Plain-language text for a failed request; never the raw body of a server error. `refreshed` says whether the
 * data was re-read after the failure: true (it was), false (it was tried and failed), null (nothing was re-read).
 * The text never claims a reload that did not complete.
 */
function failureText(err: unknown, what: string, conflict?: string, refreshed: boolean | null = null): string {
  const code = (err as Partial<ApiError>).status_code;
  const check =
    refreshed === true ? " The data was reloaded; check it before trying again."
    : refreshed === false ? " It could not be reloaded either; reload the page and check before trying again."
    : " Check the data before trying again.";
  if (code === 0) return `Network error - ${what} may not have been saved.${check}`;
  if (code === 401) return "Your session expired.";
  if (code === 403) return `You are not allowed to ${what}.`;
  if (code === 404) return `That item was not found - it may already have been removed.${refreshed === true ? " The data was reloaded." : ""}`;
  if (code === 409) return conflict ?? getErrorMessage(err, `Could not ${what}: the data changed.`);
  if (code === 422) return "The values were not accepted. Titles need 1 to 255 characters.";
  if (code === 503) return "The service is temporarily unavailable. Try again shortly.";
  if (code != null && code >= 500) return `Server error - ${what} may not have been saved.${check}`;
  return `Could not ${what}.`;
}

/**
 * Board and column administration of one company. Shown only when the backend's capabilities for the caller
 * (GET .../me) allow board management; the backend re-checks every request. Deleting a column is a separate,
 * narrower right (Super Admin only) that has no capability flag, so it is derived from the role the backend
 * resolved, never from canManageBoards. Nothing is applied optimistically: after each request the board is
 * re-read and that answer is what is shown. The company-side boards list and board view are told through
 * `onBoardCreated` and `onColumnsChanged`.
 */
export const CentralBoardAdmin = ({
  companySlug,
  boards,
  boardId,
  onSelectBoard,
  onBoardCreated,
  onColumnsChanged,
  onRefreshBoards,
}: {
  companySlug: string;
  boards: BoardResponse[];
  boardId: number | null;
  onSelectBoard: (id: number) => void;
  onBoardCreated: (board: BoardResponse) => void;
  onColumnsChanged: () => void;
  /** Re-reads the company's board list; resolves false when it could not be read. */
  onRefreshBoards: () => Promise<boolean>;
}) => {
  const [me, setMe] = useState<CompanyMe | null>(null);
  const [open, setOpen] = useState(false);
  const [board, setBoard] = useState<{ id: number; data: BoardDetail } | null>(null);
  const [loadError, setLoadError] = useState<{ id: number; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  // Both belong to one board (`board`) and are ignored once another board is selected.
  const [editingRaw, setEditing] = useState<{ board: number | null; id: number; title: string; kind: StageKind } | null>(null);
  const [confirmRaw, setConfirmDelete] = useState<{ board: number | null; id: number; title: string } | null>(null);
  const editing = editingRaw && editingRaw.board === boardId ? editingRaw : null;
  const confirmDelete = confirmRaw && confirmRaw.board === boardId ? confirmRaw : null;
  const [newColumn, setNewColumn] = useState({ title: "", kind: "open" as StageKind });
  const [newBoard, setNewBoard] = useState({ title: "", description: "" });
  const [attempt, setAttempt] = useState(0);
  const boardRef = useRef(boardId);
  const loadSeq = useRef(0);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const deleteTrigger = useRef<HTMLElement | null>(null);
  const editInput = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const newColumnInput = useRef<HTMLInputElement>(null);
  const [refocus, setRefocus] = useState<{ n: number; to: "list" | "trigger" }>({ n: 0, to: "list" });

  useEffect(() => {
    boardRef.current = boardId;
  }, [boardId]);

  // Capabilities: a failed read leaves `me` null, which shows nothing (never an assumed privilege).
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

  const caps = me?.capabilities;
  const allowed = !!caps && caps.readOnly === false && caps.canManageBoards === true;
  // Column deletion is Super Admin only in the backend; canManageBoards (also true for a company Admin) says nothing about it.
  const canDeleteColumns = allowed && me?.role === ROLE_SUPER_ADMIN;

  /** Re-reads the selected board; resolves false when it could not be read. */
  const reload = useCallback(async (): Promise<boolean> => {
    if (boardId === null) return true;
    const seq = ++loadSeq.current;
    try {
      const data = await getCompanyBoard(companySlug, boardId);
      if (seq !== loadSeq.current) return true;
      setBoard({ id: boardId, data });
      setLoadError(null);
      return true;
    } catch (err) {
      if (seq !== loadSeq.current || isAbortError(err)) return true;
      setLoadError({ id: boardId, text: failureText(err, "load the columns") });
      return false;
    }
  }, [companySlug, boardId]);

  useEffect(() => {
    if (!open || !allowed || boardId === null) return;
    const seq = ++loadSeq.current;
    getCompanyBoard(companySlug, boardId).then(
      (data) => {
        if (seq !== loadSeq.current) return;
        setBoard({ id: boardId, data });
        setLoadError(null);
      },
      (err) => {
        if (seq !== loadSeq.current || isAbortError(err)) return;
        setLoadError({ id: boardId, text: failureText(err, "load the columns") });
      }
    );
  }, [companySlug, open, allowed, boardId, attempt]);

  useEffect(() => {
    if (confirmDelete) cancelRef.current?.focus();
  }, [confirmDelete]);
  // Focus after a delete attempt, once the controls are enabled again. A deleted column's trigger no longer
  // exists, so focus goes to the column list (or the new-column field); after a failure it returns to the trigger.
  useEffect(() => {
    if (refocus.n === 0) return;
    if (refocus.to === "trigger") deleteTrigger.current?.focus();
    else (listRef.current ?? newColumnInput.current)?.focus();
  }, [refocus]);
  useEffect(() => {
    if (editing) editInput.current?.focus();
  }, [editing?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!allowed) return null;

  /** One request at a time: runs `request`, then re-reads the board and reports. Returns true on success. */
  const run = async (
    what: string,
    request: () => Promise<unknown>,
    okText: string,
    opts: { columns?: boolean; conflict?: string } = {}
  ): Promise<boolean> => {
    if (busyRef.current) return false;
    const startBoard = boardRef.current;
    busyRef.current = true;
    setBusy(true);
    setNotice(null);
    try {
      await request();
      if (boardRef.current !== startBoard && opts.columns !== false) return true;
      if (opts.columns !== false) onColumnsChanged();
      const reloaded = await reload();
      setNotice(reloaded
        ? { kind: "ok", text: okText }
        : { kind: "uncertain", text: `${okText} The columns could not be reloaded; reload the page to see the current state.` });
      return true;
    } catch (err) {
      const code = (err as Partial<ApiError>).status_code;
      const uncertain = code === 0 || (code != null && code >= 500);
      // Whatever failed, the screen must show what the server has, not what was attempted. A board creation of
      // unknown outcome re-reads the board list; the message is written only once the answer is known.
      let refreshed: boolean | null = null;
      if (opts.columns === false) {
        if (uncertain) refreshed = await onRefreshBoards();
      } else if (code !== 401 && code !== 403 && code !== 422) {
        refreshed = await reload();
      }
      setNotice({ kind: uncertain ? "uncertain" : "error", text: failureText(err, what, opts.conflict, refreshed) });
      return false;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const columns: ColumnResponse[] =
    board && board.id === boardId ? [...board.data.columns].sort((a, b) => a.position - b.position || a.id - b.id) : [];
  const selectedTitle = boards.find((b) => b.id === boardId)?.title ?? "";
  const shownLoadError = loadError && loadError.id === boardId ? loadError.text : null;

  const addBoard = async (e: React.FormEvent) => {
    e.preventDefault();
    const title = newBoard.title.trim();
    if (!title) return;
    const description = newBoard.description.trim();
    let created: BoardResponse | null = null;
    const ok = await run(
      "create the board",
      async () => {
        created = await createCompanyBoard(companySlug, description ? { title, description } : { title });
      },
      `Created board "${title}".`,
      { columns: false }
    );
    if (ok && created) {
      setNewBoard({ title: "", description: "" });
      onBoardCreated(created);
    }
  };

  const addColumn = async (e: React.FormEvent) => {
    e.preventDefault();
    const title = newColumn.title.trim();
    if (!title || boardId === null) return;
    // An explicit index past the end puts the column last; the backend default (0) would put it first.
    const ok = await run(
      "create the column",
      () => createCompanyColumn(companySlug, boardId, { title, position: columns.length, stage_kind: newColumn.kind }),
      `Created column "${title}".`
    );
    if (ok) setNewColumn({ title: "", kind: "open" });
  };

  const saveEdit = async (column: ColumnResponse) => {
    if (!editing || boardId === null) return;
    const title = editing.title.trim();
    if (!title) return;
    const updates: { title?: string; stage_kind?: StageKind } = {};
    if (title !== column.title) updates.title = title;
    if (editing.kind !== column.stage_kind) updates.stage_kind = editing.kind;
    if (Object.keys(updates).length === 0) {
      setEditing(null);
      return;
    }
    const ok = await run("edit the column", () => updateCompanyColumn(companySlug, boardId, column.id, updates), `Saved "${title}".`);
    if (ok) setEditing(null);
  };

  const moveColumn = (column: ColumnResponse, to: number) => {
    if (boardId === null) return;
    void run("move the column", () => updateCompanyColumn(companySlug, boardId, column.id, { position: to }), `Moved "${column.title}".`);
  };

  const removeColumn = async (target: { id: number; title: string }) => {
    if (boardId === null || !canDeleteColumns) return;
    const ok = await run(
      "delete columns",
      () => deleteCompanyColumn(companySlug, boardId, target.id),
      `Deleted column "${target.title}".`,
      { conflict: `"${target.title}" contains cards and cannot be deleted. Move the cards to another column first.` }
    );
    setConfirmDelete(null);
    setRefocus((cur) => ({ n: cur.n + 1, to: ok ? "list" : "trigger" }));
  };

  return (
    <section aria-label="Board administration" data-testid="board-admin">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className={secondaryButton}>
        {open ? "Hide board administration" : "Manage boards and columns"}
      </button>
      {open && (
        <div className="mt-4 rounded-[14px] border border-[var(--stroke)] bg-white/70 p-5" aria-busy={busy}>
          {notice && (
            <p role={notice.kind === "ok" ? "status" : "alert"} data-testid="admin-notice"
              className={`mb-4 text-sm font-medium ${notice.kind === "ok" ? "text-green-800" : "text-red-800"}`}>
              {notice.text}
            </p>
          )}

          <h3 className="font-display text-lg font-semibold text-[var(--navy-dark)]">Boards</h3>
          {boards.length === 0 ? (
            <p className="mt-2 text-sm text-[var(--gray-text)]">This company has no boards yet. Create the first one below.</p>
          ) : (
            <div className="mt-2">
              <label htmlFor="admin-board" className={labelClass}>Board to administer</label>
              <select id="admin-board" value={boardId ?? ""} disabled={busy}
                onChange={(e) => onSelectBoard(Number(e.target.value))} className={fieldClass}>
                {boards.map((b) => (
                  <option key={b.id} value={b.id}>{b.title}</option>
                ))}
              </select>
            </div>
          )}
          <form onSubmit={addBoard} className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div>
              <label htmlFor="new-board-title" className={labelClass}>New board title</label>
              <input id="new-board-title" value={newBoard.title} maxLength={255} disabled={busy}
                onChange={(e) => setNewBoard({ ...newBoard, title: e.target.value })} className={fieldClass} />
            </div>
            <div>
              <label htmlFor="new-board-description" className={labelClass}>Description (optional)</label>
              <input id="new-board-description" value={newBoard.description} maxLength={1000} disabled={busy}
                onChange={(e) => setNewBoard({ ...newBoard, description: e.target.value })} className={fieldClass} />
            </div>
            <button type="submit" disabled={busy || !newBoard.title.trim()} className={primaryButton}>Create board</button>
          </form>

          {boardId !== null && (
            <>
              <h3 className="mt-8 font-display text-lg font-semibold text-[var(--navy-dark)]">Columns of {selectedTitle}</h3>
              {shownLoadError && (
                <div>
                  <p role="alert" className="mt-2 text-sm font-medium text-red-800">{shownLoadError}</p>
                  <button type="button" onClick={() => setAttempt((n) => n + 1)} className={`${secondaryButton} mt-2`}>Try again</button>
                </div>
              )}
              {!shownLoadError && !(board && board.id === boardId) && (
                <p role="status" className="mt-2 text-sm text-[var(--gray-text)]">Loading columns...</p>
              )}
              {!shownLoadError && board && board.id === boardId && columns.length === 0 && (
                <p className="mt-2 text-sm text-[var(--gray-text)]">This board has no columns yet.</p>
              )}
              {columns.length > 0 && (
                <ol ref={listRef} tabIndex={-1} className="mt-3 space-y-2 outline-none" aria-label="Columns in order">
                  {columns.map((column, index) => {
                    const cardCount = column.cards?.length ?? 0;
                    const isEditing = editing?.id === column.id;
                    return (
                      <li key={column.id} data-testid={`admin-column-${column.id}`}
                        className="rounded-lg border border-[var(--stroke)] bg-white px-3 py-2">
                        {isEditing ? (
                          <form onSubmit={(e) => { e.preventDefault(); void saveEdit(column); }}
                            onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }}
                            className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
                            <div>
                              <label htmlFor={`edit-title-${column.id}`} className={labelClass}>Column title</label>
                              <input id={`edit-title-${column.id}`} ref={editInput} value={editing.title} maxLength={255} disabled={busy}
                                onChange={(e) => setEditing({ ...editing, title: e.target.value })} className={fieldClass} />
                            </div>
                            <div>
                              <label htmlFor={`edit-kind-${column.id}`} className={labelClass}>Stage kind</label>
                              <select id={`edit-kind-${column.id}`} value={editing.kind} disabled={busy}
                                onChange={(e) => setEditing({ ...editing, kind: e.target.value as StageKind })} className={fieldClass}>
                                {STAGE_KINDS.map((k) => <option key={k} value={k}>{STAGE_LABELS[k]}</option>)}
                              </select>
                            </div>
                            <div className="flex gap-2">
                              <button type="submit" disabled={busy || !editing.title.trim()} className={primaryButton}>Save</button>
                              <button type="button" disabled={busy} onClick={() => setEditing(null)} className={secondaryButton}>Cancel</button>
                            </div>
                          </form>
                        ) : (
                          <div className="flex flex-wrap items-center gap-3">
                            <span className="min-w-0 flex-1">
                              <span className="font-semibold text-[var(--navy-dark)]">{column.title}</span>
                              <span className="ml-2 text-xs text-[var(--gray-text)]">
                                {STAGE_LABELS[column.stage_kind]} - {cardCount} {cardCount === 1 ? "card" : "cards"}
                              </span>
                            </span>
                            <button type="button" disabled={busy || index === 0} onClick={() => moveColumn(column, index - 1)}
                              aria-label={`Move ${column.title} up`} className={secondaryButton}>Move up</button>
                            <button type="button" disabled={busy || index === columns.length - 1} onClick={() => moveColumn(column, index + 1)}
                              aria-label={`Move ${column.title} down`} className={secondaryButton}>Move down</button>
                            <button type="button" disabled={busy}
                              onClick={() => setEditing({ board: boardId, id: column.id, title: column.title, kind: column.stage_kind })}
                              aria-label={`Edit column ${column.title}`} className={secondaryButton}>Edit</button>
                            {canDeleteColumns && (
                              <button type="button" disabled={busy}
                                onClick={(e) => { deleteTrigger.current = e.currentTarget; setConfirmDelete({ board: boardId, id: column.id, title: column.title }); }}
                                aria-label={`Delete column ${column.title}`}
                                className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-semibold text-red-800 transition hover:bg-red-50 disabled:opacity-50">
                                Delete
                              </button>
                            )}
                          </div>
                        )}
                        {confirmDelete?.id === column.id && (
                          <div role="alertdialog" aria-modal="true" aria-labelledby="column-delete-title" aria-describedby="column-delete-body"
                            data-testid="column-delete-confirm"
                            onKeyDown={(e) => {
              if (e.key === "Escape" && !busy) { setConfirmDelete(null); deleteTrigger.current?.focus(); }
              if (e.key === "Tab") {
                // Keep Tab inside the dialog while it is open.
                const stops = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
                if (stops.length === 0) { e.preventDefault(); return; }
                const at = stops.indexOf(document.activeElement as HTMLButtonElement);
                const next = e.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at === stops.length - 1 ? 0 : at + 1);
                e.preventDefault();
                stops[next].focus();
              }
            }}
                            className="mt-2 flex flex-wrap items-center gap-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2">
                            <div className="min-w-0 flex-1">
                              <p id="column-delete-title" className="text-sm font-semibold text-red-900">Delete the column &quot;{confirmDelete.title}&quot;?</p>
                              <p id="column-delete-body" className="text-xs text-red-900">
                                The column must be empty: if it still holds cards the deletion is refused and nothing is removed.
                              </p>
                            </div>
                            <button type="button" disabled={busy} onClick={() => void removeColumn(confirmDelete)}
                              className="rounded-lg bg-red-700 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60">
                              {busy ? "Deleting..." : "Confirm delete"}
                            </button>
                            <button type="button" ref={cancelRef} disabled={busy}
                              onClick={() => { setConfirmDelete(null); deleteTrigger.current?.focus(); }}
                              className="rounded-lg border border-[var(--stroke)] px-3 py-1.5 text-xs font-semibold text-[var(--navy-dark)] disabled:opacity-60">
                              Cancel
                            </button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ol>
              )}
              <form onSubmit={addColumn} className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
                <div>
                  <label htmlFor="new-column-title" className={labelClass}>New column title</label>
                  <input id="new-column-title" ref={newColumnInput} value={newColumn.title} maxLength={255} disabled={busy}
                    onChange={(e) => setNewColumn({ ...newColumn, title: e.target.value })} className={fieldClass} />
                </div>
                <div>
                  <label htmlFor="new-column-kind" className={labelClass}>New column stage kind</label>
                  <select id="new-column-kind" value={newColumn.kind} disabled={busy}
                    onChange={(e) => setNewColumn({ ...newColumn, kind: e.target.value as StageKind })} className={fieldClass}>
                    {STAGE_KINDS.map((k) => <option key={k} value={k}>{STAGE_LABELS[k]}</option>)}
                  </select>
                </div>
                <button type="submit" disabled={busy || !newColumn.title.trim() || !(board && board.id === boardId)} className={primaryButton}>
                  Add column
                </button>
              </form>
            </>
          )}
        </div>
      )}
    </section>
  );
};
