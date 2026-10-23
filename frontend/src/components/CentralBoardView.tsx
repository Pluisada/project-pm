"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  closestCenter,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { CardDetailsModal } from "@/components/CardDetailsModal";
import { CentralCardForm, type CardFormTarget } from "@/components/CentralCardForm";
import { CentralCardHistory } from "@/components/CentralCardHistory";
import { CentralReviewDialog, reviewLabel, useCompanyReviews } from "@/components/CentralReviews";
import { InNavSlot } from "@/components/NavSlot";
import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { stageTone, stageToneStyle } from "@/lib/stageTone";
import { sortByDealValue } from "@/lib/cardOrder";
import { applyMove, cardDndId, columnDndId, currentIndex, planMove } from "@/lib/centralMove";
import { DEAL_CURRENCY, formatMoney } from "@/lib/format";
import {
  getCompanyBoard,
  getCompanyMe,
  deleteCompanyCard,
  getCompanyUndoStatus,
  CARD_CONFLICT_TEXT,
  getErrorMessage,
  isAbortError,
  isCardVersionConflict,
  moveCompanyCard,
  undoCompanyChange,
  type ApiError,
  type BoardDetail,
  type CardResponse,
  type ColumnResponse,
  type CompanyMe,
  type ReviewItem,
  type UndoStatus,
} from "@/lib/api";

type Loaded = { key: string; data: { board: BoardDetail; me: CompanyMe } } | { key: string; error: ApiError };

/** Tells the failure kinds apart: network (0), session (401), no access (403), anything else. */
function describeFailure(err: Partial<ApiError>): { text: string; retryable: boolean } {
  switch (err.status_code) {
    case 0:
      return { text: "Network error - check your connection.", retryable: true };
    case 401:
      return { text: "Your session expired.", retryable: false };
    case 403:
      return { text: "You do not have access to this company.", retryable: false };
    default:
      return { text: getErrorMessage(err, "Could not load the board."), retryable: true };
  }
}

// The pointer decides when it is over something; otherwise the nearest center (a card dragged into the gap
// between columns still lands somewhere sensible).
const pointerFirst: CollisionDetection = (args) => {
  const underPointer = pointerWithin(args);
  return underPointer.length > 0 ? underPointer : closestCenter(args);
};

const CentralCard = ({ card, onOpen, movable, overlay, reviews, onOpenReview }: { card: CardResponse; onOpen?: () => void; movable?: boolean; overlay?: boolean; reviews?: ReviewItem[]; onOpenReview?: (item: ReviewItem) => void }) => {
  const sortable = useSortable({ id: cardDndId(card.id), disabled: { draggable: !movable || overlay } });
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = sortable;
  const nextYear = card.deal_value_next_year;
  const showNextYear = nextYear != null && nextYear !== 0;
  // The summary is the compact card; opening it shows the details and, from there, every action (Edit, History, Delete).
  // Dragging lives only on the handle, so a click on the summary can never pick the card up or move it.
  const summary = (
    <>
      <span className="line-clamp-2 block break-words font-display text-sm font-semibold leading-5 text-[var(--navy-dark)]">{card.title}</span>
      {card.company && <span className="mt-0.5 block truncate text-xs text-[var(--gray-text)]">{card.company}</span>}
      {card.salesperson_name && <span className="mt-1 block truncate text-xs text-[var(--gray-text)]">{card.salesperson_name}</span>}
      {(card.deal_value != null || showNextYear) && (
        <span className="mt-1.5 block space-y-0.5 text-xs">
          {card.deal_value != null && (
            <span className="flex justify-between gap-2">
              <span className="text-[var(--gray-text)]">Deal value{card.deal_year ? ` ${card.deal_year}` : ""}</span>
              <span className="font-semibold text-[var(--navy-dark)]">{formatMoney(card.deal_value, DEAL_CURRENCY)}</span>
            </span>
          )}
          {showNextYear && (
            <span className="flex justify-between gap-2">
              <span className="text-[var(--gray-text)]">Next year{card.deal_year ? ` ${card.deal_year + 1}` : ""}</span>
              <span className="font-semibold text-[var(--navy-dark)]">{formatMoney(nextYear, DEAL_CURRENCY)}</span>
            </span>
          )}
        </span>
      )}
      {card.confidence_level && <span className="mt-1.5 block"><ConfidenceBadge level={card.confidence_level} /></span>}
    </>
  );
  return (
    <li ref={overlay ? undefined : setNodeRef} data-testid={`central-card-${card.id}`} data-title={card.title}
      style={overlay ? undefined : { transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.4 : 1 }}
      className="relative rounded-xl border border-[var(--stroke)] bg-[var(--surface-strong)] px-3 py-2.5 shadow-[0_1px_2px_rgba(3,33,71,0.06)]">
      {movable && !overlay && (
        <button type="button" ref={setActivatorNodeRef} {...attributes} {...listeners}
          aria-label={`Move ${card.title}`} data-testid={`central-handle-${card.id}`}
          className="absolute right-1.5 top-1.5 cursor-grab touch-none rounded p-1 text-[var(--gray-text)] hover:text-[var(--navy-dark)] focus-visible:outline focus-visible:outline-2">
          <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <circle cx="3" cy="2" r="1" /><circle cx="9" cy="2" r="1" /><circle cx="3" cy="6" r="1" /><circle cx="9" cy="6" r="1" />
            <circle cx="3" cy="10" r="1" /><circle cx="9" cy="10" r="1" />
          </svg>
        </button>
      )}
      {reviews && reviews.length > 0 && (
        // One small marker per pending review obligation, always in the same corner; it opens that obligation only.
        <div className="mb-1 flex flex-wrap gap-1">
          {reviews.map((item) => (
            <button key={item.id} type="button" data-testid={`review-marker-${item.id}`} onClick={() => onOpenReview?.(item)}
              aria-label={`${reviewLabel(item)}: ${card.title}`} title={reviewLabel(item)}
              className="inline-flex items-center gap-1 rounded-full bg-amber-200 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-950 hover:bg-amber-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-900">
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-amber-800" />
              Review
            </button>
          ))}
        </div>
      )}
      {onOpen && !overlay ? (
        <button type="button" onClick={onOpen} aria-label={`Open details for ${card.title}`} aria-haspopup="dialog" data-testid={`central-open-${card.id}`}
          className={`block w-full rounded-lg text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary-blue)] ${movable ? "pr-5" : ""}`}>
          {summary}
        </button>
      ) : (
        <div className={movable ? "pr-5" : undefined}>{summary}</div>
      )}
    </li>
  );
};

/** A column body that accepts drops (also when empty) and highlights while a card is over it. */
const DroppableList = ({ columnId, cardIds, children }: { columnId: number; cardIds: string[]; children: ReactNode }) => {
  const { setNodeRef, isOver } = useDroppable({ id: columnDndId(columnId) });
  return (
    <SortableContext items={cardIds} strategy={verticalListSortingStrategy}>
      <ul ref={setNodeRef} data-testid={`central-list-${columnId}`} data-over={isOver ? "true" : undefined}
        className={`mt-3 flex min-h-[48px] flex-1 flex-col gap-3 rounded-lg ${isOver ? "ring-2 ring-[var(--primary-blue)]" : ""}`}>
        {children}
      </ul>
    </SortableContext>
  );
};

/**
 * View of one company board. The result is stored with the company, board and attempt it was
 * requested for and only shown while that is still what is selected, so a late answer for another company
 * can never be displayed; the request itself is aborted when the selection changes. Cards can be created,
 * edited and moved (reordered or sent to another column); what each person may do comes from the capabilities
 * of GET .../me and the card's salesperson_id, and the backend stays the authority.
 *
 * Moves are optimistic and one at a time. The move is applied at once; if the server refuses it, the card goes
 * back where it was and the reason is shown; if the outcome is unknown (network), the card goes back and the
 * board is re-read. A move is never retried, and an answer that arrives after the selection changed is ignored.
 * `refreshToken` changes after a structural change made elsewhere (column administration): the board is then
 * re-read in place, without remounting, so an open form, history or drag keeps its state.
 * Every write carries the card's version as the person first saw it (when the form opened, the drag started or the
 * delete was requested; a board re-read in between never replaces it). A 409 card_version_conflict changes nothing, is
 * never retried with the latest version, and the board is re-read so the person sees what the other person did.
 */
export const CentralBoardView = ({ companySlug, boardId, refreshToken = 0 }: { companySlug: string; boardId: number; refreshToken?: number }) => {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Loaded | null>(null);
  const key = `${companySlug}:${boardId}:${attempt}`;
  // The open form belongs to one company/board/attempt; a form from another selection is never shown.
  const [form, setForm] = useState<{ key: string; target: CardFormTarget } | null>(null);
  // The open history belongs to one company/board/attempt, like the form.
  const [historyOf, setHistoryOf] = useState<{ key: string; cardId: number } | null>(null);
  // The card whose details are open (one selection, like the form and the history); its content is read from the board.
  const [detailsOf, setDetailsOf] = useState<{ key: string; cardId: number } | null>(null);
  const [activeCardId, setActiveCardId] = useState<number | null>(null);
  // The reason being collected for a move into the lost stage (one selection, like the form and the history).
  const [lossPrompt, setLossPrompt] = useState<{ key: string; cardId: number; title: string; target: { columnId: number; index: number }; version?: number; reason: string; error: string | null } | null>(null);
  // The review obligation open in its dialog, and the outcome of the last clearing; each belongs to one selection.
  const [reviewOpen, setReviewOpen] = useState<{ key: string; item: ReviewItem } | null>(null);
  const [reviewNotice, setReviewNotice] = useState<{ key: string; text: string } | null>(null);
  // Both belong to one selection and are only shown while it is still the current one.
  const [moveErrorFor, setMoveErrorFor] = useState<{ key: string; text: string } | null>(null);
  const [movingFor, setMovingFor] = useState<string | null>(null);
  const moving = movingFor === key;
  const moveError = moveErrorFor?.key === key ? moveErrorFor.text : null;
  // Undo state, each piece tied to one selection like the move state above.
  const [undoStatusFor, setUndoStatusFor] = useState<{ key: string; status: UndoStatus } | null>(null);
  const [undoingFor, setUndoingFor] = useState<string | null>(null);
  const [undoNoticeFor, setUndoNoticeFor] = useState<{ key: string; kind: "ok" | "error" | "uncertain"; text: string } | null>(null);
  const [undoTick, setUndoTick] = useState(0);
  // Deletion: the card awaiting confirmation, the one in flight and the outcome, each tied to one selection.
  const [confirmDelete, setConfirmDelete] = useState<{ key: string; id: number; title: string; version?: number } | null>(null);
  const [deletingFor, setDeletingFor] = useState<string | null>(null);
  const [deleteNoticeFor, setDeleteNoticeFor] = useState<{ key: string; kind: "ok" | "error" | "uncertain"; text: string } | null>(null);
  const deleteKey = useRef<string | null>(null);
  const deleting = deletingFor === key;
  const deleteNotice = deleteNoticeFor?.key === key ? deleteNoticeFor : null;
  const pendingDelete = confirmDelete?.key === key ? confirmDelete : null;
  const undoing = undoingFor === key;
  const undoNotice = undoNoticeFor?.key === key ? undoNoticeFor : null;
  const undoStatus = undoStatusFor?.key === key ? undoStatusFor.status : null;
  // The selection the page is on now and the one the in-flight move / undo belongs to (null: none in flight).
  const keyRef = useRef(key);
  const movingKey = useRef<string | null>(null);
  const undoKey = useRef<string | null>(null);
  const reloadSeq = useRef(0);
  // The version of the card being dragged, taken when the drag starts: a board re-read during the drag never replaces it.
  const dragVersion = useRef<{ cardId: number; version?: number } | null>(null);
  const undoSeq = useRef(0);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  useEffect(() => {
    keyRef.current = key;
    movingKey.current = null; // a move still in flight for the previous selection no longer blocks this one
    undoKey.current = null;
    deleteKey.current = null;
  }, [key]);

  // Pending reviews of the caller's own role: read from the backend whenever its capabilities say the caller reviews.
  const canReviewNow = !!result && result.key === key && "data" in result && !!result.data.me.capabilities.canReview && !result.data.me.capabilities.readOnly;
  const reviews = useCompanyReviews(companySlug, boardId, canReviewNow, undoTick);
  // Undo is offered to writers only (the endpoint refuses a Visitor); this is the loaded, writable state of this selection.
  const writable = !!result && result.key === key && "data" in result && !result.data.me.capabilities.readOnly;
  useEffect(() => {
    if (!writable) return;
    const controller = new AbortController();
    const seq = ++undoSeq.current;
    getCompanyUndoStatus(companySlug, boardId, controller.signal).then(
      (status) => {
        if (!controller.signal.aborted && seq === undoSeq.current) setUndoStatusFor({ key, status });
      },
      () => {} // unknown: the button stays hidden until the next successful read
    );
    return () => controller.abort();
  }, [companySlug, boardId, key, writable, undoTick]);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getCompanyMe(companySlug, controller.signal),
      getCompanyBoard(companySlug, boardId, controller.signal),
    ]).then(
      ([me, board]) => {
        if (!controller.signal.aborted) setResult({ key, data: { board, me } });
      },
      (err: ApiError) => {
        if (!controller.signal.aborted && !isAbortError(err)) setResult({ key, error: err });
      }
    );
    return () => controller.abort();
  }, [companySlug, boardId, key]);

  /** Re-reads the board in place (no loading screen, so an open form keeps what was typed). */
  const reloadBoard = (): Promise<boolean> => {
    const seq = ++reloadSeq.current;
    return getCompanyBoard(companySlug, boardId, new AbortController().signal).then(
      (board) => {
        if (seq !== reloadSeq.current) return true; // a newer read is in flight and decides what is shown
        setResult((cur) => (cur && cur.key === key && "data" in cur ? { key, data: { ...cur.data, board } } : cur));
        return true;
      },
      () => false // the board on screen stays as it was
    );
  };

  // A structural change elsewhere: re-read in place. If that read fails, fall back to the full load, which shows a
  // retry instead of silently keeping columns that no longer match the server.
  const seenToken = useRef(refreshToken);
  useEffect(() => {
    if (refreshToken === seenToken.current) return;
    seenToken.current = refreshToken;
    void reloadBoard().then((ok) => {
      if (!ok) setAttempt((n) => n + 1);
    });
  }, [refreshToken]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveCard = (saved: CardResponse) => {
    setResult((cur) => {
      if (!cur || cur.key !== key || !("data" in cur)) return cur;
      const { board } = cur.data;
      const columns = board.columns.map((column) => {
        const cards = column.cards ?? [];
        if (cards.some((c) => c.id === saved.id)) return { ...column, cards: cards.map((c) => (c.id === saved.id ? saved : c)) };
        return column.id === saved.column_id ? { ...column, cards: [...cards, saved] } : column;
      });
      return { key, data: { ...cur.data, board: { ...board, columns } } };
    });
    setForm(null);
    setUndoTick((n) => n + 1);
  };

  const replaceColumns = (update: (columns: ColumnResponse[]) => ColumnResponse[], forKey: string) =>
    setResult((cur) => {
      if (!cur || cur.key !== forKey || !("data" in cur)) return cur;
      return { key: forKey, data: { ...cur.data, board: { ...cur.data.board, columns: update(cur.data.board.columns) } } };
    });

  /** Returns true when the server confirmed the move. A move into the lost stage (`lossReason` given) is never shown
   *  before the server confirms it, so a refused one needs no revert. */
  const moveCard = async (cardId: number, to: { columnId: number; index: number }, lossReason?: string, expectedVersion?: number): Promise<boolean> => {
    if (!result || result.key !== key || !("data" in result) || movingKey.current || undoKey.current || deleteKey.current) return false;
    const from = currentIndex(result.data.board.columns, cardId);
    if (!from || (from.columnId === to.columnId && from.index === to.index)) return false;
    const startKey = key;
    movingKey.current = startKey;
    setMovingFor(startKey);
    setMoveErrorFor(null);
    if (lossReason === undefined) replaceColumns((cols) => applyMove(cols, cardId, to), startKey);
    try {
      const saved = await moveCompanyCard(companySlug, boardId, cardId, to.columnId, to.index, undefined, lossReason, expectedVersion);
      if (keyRef.current !== startKey) return false;
      if (saved?.id !== cardId || saved.column_id !== to.columnId) throw { status_code: 0, detail: "Unexpected response from the server." } as ApiError;
      if (lossReason !== undefined) replaceColumns((cols) => applyMove(cols, cardId, to), startKey);
      replaceColumns((cols) => cols.map((c) => ({ ...c, cards: c.cards?.map((card) => (card.id === cardId ? { ...card, ...saved, position: card.position } : card)) })), startKey);
      return true;
    } catch (err) {
      if (keyRef.current !== startKey) return false;
      const failure = err as Partial<ApiError>;
      if (lossReason === undefined) replaceColumns((cols) => applyMove(cols, cardId, from), startKey);
      const conflict = isCardVersionConflict(err);
      setMoveErrorFor({ key: startKey, text: failure.status_code === 0
        ? "Network error - the move may not have been saved. The board was reloaded."
        : conflict ? `${CARD_CONFLICT_TEXT} The board was reloaded with the latest version; move it again if you still want to.`
        : getErrorMessage(err, "Could not move the card.") });
      if (failure.status_code === 0 || conflict) void reloadBoard(); // never retry the move: just see what the server has
      return false;
    } finally {
      if (movingKey.current === startKey) {
        movingKey.current = null;
        setMovingFor(null);
      }
      if (keyRef.current === startKey) setUndoTick((n) => n + 1);
    }
  };

  const deleteCard = async (cardId: number, title: string, expectedVersion?: number) => {
    if (!result || result.key !== key || !("data" in result) || deleteKey.current || movingKey.current || undoKey.current) return;
    const startKey = key;
    deleteKey.current = startKey;
    setDeletingFor(startKey);
    setDeleteNoticeFor(null);
    const notice = (kind: "ok" | "error" | "uncertain", text: string) => setDeleteNoticeFor({ key: startKey, kind, text });
    try {
      await deleteCompanyCard(companySlug, boardId, cardId, undefined, expectedVersion);
      if (keyRef.current !== startKey) return;
      // Only now, with the server's 204, does the card leave the board; the board is then re-read for the
      // positions the server kept.
      replaceColumns((cols) => cols.map((c) => ({ ...c, cards: c.cards?.filter((card) => card.id !== cardId) })), startKey);
      setConfirmDelete(null);
      const reloaded = await reloadBoard();
      if (keyRef.current !== startKey) return;
      notice(reloaded ? "ok" : "uncertain", reloaded
        ? `Deleted "${title}".`
        : `Deleted "${title}", but the board could not be reloaded. Reload the board to see the current state.`);
    } catch (err) {
      if (keyRef.current !== startKey) return;
      const failure = err as Partial<ApiError>;
      const code = failure.status_code;
      setConfirmDelete(null);
      if (code === 0 || (code != null && code >= 500)) {
        // Outcome unknown: never retried, just re-read what the server has.
        notice("uncertain", `${code === 0 ? "Network error" : "Server error"} - "${title}" may or may not have been deleted. The board was reloaded; check it before trying again.`);
        void reloadBoard();
      } else if (code === 404) {
        notice("error", `"${title}" was not found - it may already have been deleted. The board was reloaded.`);
        void reloadBoard();
      } else if (code === 403) {
        notice("error", "You are not allowed to delete cards.");
      } else if (code === 401) {
        notice("error", "Your session expired.");
      } else if (isCardVersionConflict(err)) {
        notice("error", `${CARD_CONFLICT_TEXT} "${title}" was not deleted. The board was reloaded with the latest version; delete it again if you still want to.`);
        void reloadBoard();
      } else if (code === 409 || code === 422) {
        notice("error", getErrorMessage(err, "Could not delete the card."));
        void reloadBoard();
      } else {
        notice("error", getErrorMessage(err, "Could not delete the card."));
      }
    } finally {
      if (deleteKey.current === startKey) {
        deleteKey.current = null;
        setDeletingFor(null);
      }
      if (keyRef.current === startKey) setUndoTick((n) => n + 1);
    }
  };

  const undoLast = async () => {
    if (!result || result.key !== key || !("data" in result) || undoKey.current || movingKey.current || deleteKey.current) return;
    const startKey = key;
    undoKey.current = startKey;
    setUndoingFor(startKey);
    setUndoNoticeFor(null);
    const notice = (kind: "ok" | "error" | "uncertain", text: string) => setUndoNoticeFor({ key: startKey, kind, text });
    try {
      await undoCompanyChange(companySlug, boardId);
      if (keyRef.current !== startKey) return;
      // Success is only reported after the server confirmed; the board is re-read rather than patched.
      const reloaded = await reloadBoard();
      if (keyRef.current !== startKey) return;
      // A successful undo shows no message (the card's own marker says what changed); only a problem is reported.
      if (!reloaded) notice("uncertain", "Undone, but the board could not be reloaded. Reload the board to see the current state.");
    } catch (err) {
      if (keyRef.current !== startKey) return;
      const failure = err as Partial<ApiError>;
      const code = failure.status_code;
      if (code === 0 || (code != null && code >= 500)) {
        // Outcome unknown: never retried, just re-read what the server has.
        notice("uncertain", `${code === 0 ? "Network error" : "Server error"} - the undo may or may not have been applied. Check the board before trying again.`);
        void reloadBoard();
      } else if (code === 404) {
        notice("error", "Nothing to undo.");
      } else if (isCardVersionConflict(err)) {
        // The server kept the entry (nothing was applied); it simply cannot be applied while the card has moved on.
        notice("error", "A card of this change was changed by someone else since, so nothing was undone. The board was reloaded with the latest version.");
        void reloadBoard();
      } else if (code === 409) {
        notice("error", getErrorMessage(err, "The change can no longer be undone.")); // the entry is used up
        void reloadBoard();
      } else if (code === 403) {
        notice("error", "You are not allowed to undo changes on this board.");
      } else if (code === 401) {
        notice("error", "Your session expired.");
      } else {
        notice("error", getErrorMessage(err, "Could not undo the change."));
      }
    } finally {
      if (undoKey.current === startKey) {
        undoKey.current = null;
        setUndoingFor(null);
      }
      if (keyRef.current === startKey) setUndoTick((n) => n + 1);
    }
  };

  if (!result || result.key !== key) return <p role="status" className="text-sm text-[var(--gray-text)]">Loading board...</p>;

  if ("error" in result) {
    const { text, retryable } = describeFailure(result.error);
    return (
      <div>
        <p role="alert" className="text-sm font-medium text-red-800">{text}</p>
        {retryable && (
          <button type="button" onClick={() => setAttempt((n) => n + 1)}
            className="mt-3 rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90">
            Try again
          </button>
        )}
      </div>
    );
  }

  const { board, me } = result.data;
  const caps = me.capabilities;
  const canCreate = !caps.readOnly && caps.canEditOwnCards;
  // Own cards only for a member; the backend repeats this check (card_rules.can_edit_card).
  const canEditCard = (card: CardResponse) =>
    !caps.readOnly && (caps.canEditAnyCard || (caps.canEditOwnCards && card.salesperson_id != null && card.salesperson_id === me.user_id));
  const openForm = form && form.key === key ? form.target : null;
  const columns = [...board.columns].sort((a, b) => a.position - b.position);
  const openIndex = new Map(columns.filter((c) => c.stage_kind === "open").map((c, i) => [c.id, i]));
  const canMoveCard = canEditCard;
  const cardsById = new Map(columns.flatMap((c) => c.cards ?? []).map((c) => [c.id, c]));
  const reviewsByCard = new Map<number, ReviewItem[]>();
  for (const item of reviews.items) reviewsByCard.set(item.card_id, [...(reviewsByCard.get(item.card_id) ?? []), item]);
  const activeCard = activeCardId != null ? cardsById.get(activeCardId) : undefined;

  const handleDragStart = (event: DragStartEvent) => {
    const raw = String(event.active.id);
    const id = raw.startsWith("card-") ? Number(raw.slice(5)) : null;
    setActiveCardId(id);
    dragVersion.current = id != null ? { cardId: id, version: cardsById.get(id)?.version } : null;
  };
  const handleDragEnd = (event: DragEndEvent) => {
    setActiveCardId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return; // dropped outside: nothing changes
    const cardId = Number(String(active.id).slice(5));
    const card = cardsById.get(cardId);
    const target = planMove(columns, cardId, String(over.id));
    if (!card || !target || !canMoveCard(card)) return;
    // A column is ordered by Deal Value, so dropping a card elsewhere in its own column changes nothing.
    if (target.columnId === card.column_id) return;
    const sourcePosition = columns.find((c) => c.id === card.column_id)?.position ?? 0;
    const targetPosition = columns.find((c) => c.id === target.columnId)?.position ?? 0;
    // The backend repeats this rule; saying it here avoids a request that is certain to be refused.
    if (!caps.canMoveBack && targetPosition < sourcePosition) {
      setMoveErrorFor({ key, text: "Members can only move cards forward, not back to an earlier column." });
      return;
    }
    const started = dragVersion.current;
    dragVersion.current = null;
    const version = started?.cardId === cardId ? started.version : card.version;
    const targetColumn = columns.find((c) => c.id === target.columnId);
    if (targetColumn?.stage_kind === "lost" && card.column_id !== target.columnId) {
      // Entering the lost stage needs a reason: ask for it first. Nothing is sent or shown as moved until it is given.
      setMoveErrorFor(null);
      setLossPrompt({ key, cardId, title: card.title, target, version, reason: "", error: null });
      return;
    }
    void moveCard(cardId, target, undefined, version);
  };

  const submitLoss = async () => {
    if (!lossPrompt || lossPrompt.key !== key) return;
    const reason = lossPrompt.reason.trim();
    if (!reason) {
      setLossPrompt({ ...lossPrompt, error: "Enter the reason this opportunity was lost." });
      return;
    }
    if (reason.length > 1000) {
      setLossPrompt({ ...lossPrompt, error: "The reason can have at most 1000 characters." });
      return;
    }
    const ok = await moveCard(lossPrompt.cardId, lossPrompt.target, reason, lossPrompt.version);
    if (ok) setLossPrompt(null); // on a refusal the form stays, with what was typed, and the error above explains why
  };

  return (
    <div data-testid="central-board">
      <h2 className="font-display text-xl font-semibold text-[var(--navy-dark)]">{board.title}</h2>
      {me.capabilities.readOnly && <p className="mt-1 text-xs text-[var(--gray-text)]">View only</p>}
      {!me.capabilities.readOnly && (undoStatus?.available || undoing) && (
        // The button lives in the top navigation (NavSlot "central-undo-slot"); its description is its tooltip.
        <InNavSlot id="central-undo-slot">
          <button type="button" onClick={() => void undoLast()} disabled={undoing || moving || deleting} data-testid="undo-button"
            title={undoStatus?.description ?? undefined}
            className="rounded-md border border-[var(--nav-line)] px-3 py-1.5 text-sm font-semibold text-[var(--nav-ink)] transition hover:border-[var(--nav-brand)] disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--nav-brand)]">
            {undoing ? "Undoing..." : "Undo"}
          </button>
        </InNavSlot>
      )}
      {undoNotice && (
        <p role={undoNotice.kind === "ok" ? "status" : "alert"} data-testid="undo-notice"
          className={`mt-2 text-sm font-medium ${undoNotice.kind === "ok" ? "text-green-800" : "text-red-800"}`}>
          {undoNotice.text}
        </p>
      )}
      {deleteNotice && (
        <p role={deleteNotice.kind === "ok" ? "status" : "alert"} data-testid="delete-notice"
          className={`mt-2 text-sm font-medium ${deleteNotice.kind === "ok" ? "text-green-800" : "text-red-800"}`}>
          {deleteNotice.text}
        </p>
      )}
      {pendingDelete && (
        <div role="alertdialog" aria-labelledby="delete-confirm-title" data-testid="delete-confirm"
          className="mt-2 flex flex-wrap items-center gap-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2">
          <p id="delete-confirm-title" className="text-sm text-red-900">Delete the card &quot;{pendingDelete.title}&quot;?</p>
          <button type="button" disabled={deleting} onClick={() => void deleteCard(pendingDelete.id, pendingDelete.title, pendingDelete.version)}
            className="rounded-lg bg-red-700 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60">
            {deleting ? "Deleting..." : "Confirm delete"}
          </button>
          <button type="button" disabled={deleting} onClick={() => setConfirmDelete(null)}
            className="rounded-lg border border-[var(--stroke)] px-3 py-1.5 text-xs font-semibold text-[var(--navy-dark)] disabled:opacity-60">
            Cancel
          </button>
        </div>
      )}
      {reviewNotice && reviewNotice.key === key && (
        <p role="status" data-testid="review-notice" className="mt-2 text-sm font-medium text-green-800">{reviewNotice.text}</p>
      )}
      {reviewOpen && reviewOpen.key === key && (
        <CentralReviewDialog companySlug={companySlug} boardId={boardId}
          item={reviews.items.find((i) => i.id === reviewOpen.item.id) ?? reviewOpen.item}
          stillPending={reviews.items.some((i) => i.id === reviewOpen.item.id)}
          onClose={() => setReviewOpen(null)}
          onCleared={(cleared) => {
            setReviewOpen(null);
            setReviewNotice({ key, text: `The review of "${cleared.card_title}" was cleared.` });
            reviews.reload();
          }}
          onFailed={() => reviews.reload()}
          onOpenHistory={(cardId) => { setReviewOpen(null); setHistoryOf({ key, cardId }); }} />
      )}
      {lossPrompt && lossPrompt.key === key && (
        <form data-testid="loss-prompt" role="dialog" aria-label="Loss reason" noValidate
          onSubmit={(e) => { e.preventDefault(); void submitLoss(); }}
          className="mt-2 flex flex-col gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-3">
          <label htmlFor="loss-reason" className="text-sm font-semibold text-red-900">
            Why was &quot;{lossPrompt.title}&quot; lost?
          </label>
          <textarea id="loss-reason" value={lossPrompt.reason} rows={2} maxLength={1000} disabled={moving}
            onChange={(e) => setLossPrompt({ ...lossPrompt, reason: e.target.value, error: null })}
            className="w-full rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-3 py-2 text-sm text-[var(--navy-dark)]" />
          {lossPrompt.error && <p role="alert" data-testid="loss-error" className="text-sm font-medium text-red-800">{lossPrompt.error}</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={moving} className="rounded-lg bg-red-700 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60">
              {moving ? "Moving..." : "Move to lost"}
            </button>
            <button type="button" disabled={moving} onClick={() => setLossPrompt(null)}
              className="rounded-lg border border-[var(--stroke)] px-3 py-1.5 text-xs font-semibold text-[var(--navy-dark)] disabled:opacity-60">
              Cancel
            </button>
          </div>
        </form>
      )}
      {moveError && <p role="alert" data-testid="move-error" className="mt-2 text-sm font-medium text-red-800">{moveError}</p>}
      <DndContext sensors={sensors} collisionDetection={pointerFirst} onDragStart={handleDragStart}
        onDragEnd={handleDragEnd} onDragCancel={() => setActiveCardId(null)}>
      {/* The scroll area is the whole width of the window (it bleeds through the page gutter, -mx-6 / px-6), so the first and
          last columns rest 24px from the edge and every column scrolls into reach; the small top padding and the
          scrollbar space keep borders, rings and the scrollbar from being cut by the overflow. */}
      <div className="-mx-6 mt-4 flex gap-4 overflow-x-auto px-6 pb-3 pt-1 [scrollbar-width:thin]" aria-busy={moving} data-testid="central-columns">
        {columns.map((column) => {
          const tone = stageTone(column.stage_kind, openIndex.get(column.id) ?? 0);
          const cards = sortByDealValue(column.cards);
          return (
            <section key={column.id} aria-label={column.title} data-testid={`central-column-${column.id}`}
              className="flex min-h-[200px] w-72 shrink-0 flex-col rounded-[14px] border border-t-[3px] p-4"
              style={stageToneStyle(tone)}>
              <h3 className="truncate font-display text-lg font-semibold text-[var(--navy-dark)]">{column.title}</h3>
              <p className="text-xs font-semibold text-[var(--gray-text)]">{cards.length} {cards.length === 1 ? "card" : "cards"}</p>
              <DroppableList columnId={column.id} cardIds={cards.map((c) => cardDndId(c.id))}>
                {cards.map((card) => (
                  <CentralCard key={card.id} card={card} reviews={reviewsByCard.get(card.id)} onOpenReview={(item) => { setReviewNotice(null); setReviewOpen({ key, item }); }} movable={canMoveCard(card) && !moving && !undoing && !deleting}
                    onOpen={() => setDetailsOf({ key, cardId: card.id })} />
                ))}
              </DroppableList>
              {canCreate && (
                <button type="button" onClick={() => setForm({ key, target: { mode: "create", columnId: column.id, columnTitle: column.title, stageKind: column.stage_kind } })}
                  className="mt-3 w-full rounded-full border border-dashed border-[var(--stroke)] px-3 py-2 text-xs font-semibold uppercase tracking-wide text-[var(--primary-blue)] transition hover:border-[var(--primary-blue)]">
                  Add a card
                </button>
              )}
            </section>
          );
        })}
      </div>
      <DragOverlay>{activeCard ? <CentralCard card={activeCard} overlay /> : null}</DragOverlay>
      </DndContext>
      {detailsOf && detailsOf.key === key && cardsById.get(detailsOf.cardId) && (() => {
        const card = cardsById.get(detailsOf.cardId)!;
        const column = columns.find((c) => c.id === card.column_id);
        // Every action closes the details first: the edit form, history and delete confirmation are the existing flows,
        // and each takes the card as it is on the board right now (its version included).
        return (
          <CardDetailsModal key={`${key}:${card.id}`} card={card} stageTitle={column?.title ?? ""} stageKind={column?.stage_kind ?? "open"}
            hideBidNote canEdit={canEditCard(card)}
            onEdit={() => { setDetailsOf(null); setForm({ key, target: { mode: "edit", card } }); }}
            onHistory={() => { setDetailsOf(null); setHistoryOf({ key, cardId: card.id }); }}
            onDelete={!caps.readOnly && caps.canDeleteCards && !deleting && !moving && !undoing
              ? () => { setDetailsOf(null); setConfirmDelete({ key, id: card.id, title: card.title, version: card.version }); }
              : undefined}
            onClose={() => setDetailsOf(null)} />
        );
      })()}
      {historyOf && historyOf.key === key && cardsById.get(historyOf.cardId) && (
        <CentralCardHistory key={`${key}:${historyOf.cardId}`} companySlug={companySlug} boardId={boardId}
          cardId={historyOf.cardId} cardTitle={cardsById.get(historyOf.cardId)!.title} onClose={() => setHistoryOf(null)} />
      )}
      {openForm && (
        <CentralCardForm key={`${key}:${openForm.mode === "edit" ? openForm.card.id : `new-${openForm.columnId}`}`}
          companySlug={companySlug} boardId={boardId} target={openForm} canAssign={me.capabilities.canAssignSalesperson}
          onSaved={saveCard} onReload={reloadBoard} onClose={() => setForm(null)} />
      )}
    </div>
  );
};
