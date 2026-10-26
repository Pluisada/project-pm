"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  CONFIDENCE_LEVELS,
  createCompanyCard,
  getCompanyLeadOptions,
  CARD_CONFLICT_TEXT,
  getErrorMessage,
  isAbortError,
  isCardVersionConflict,
  updateCompanyCard,
  type ApiError,
  type CardResponse,
  type CardUpdatePayload,
  type LeadOptions,
} from "@/lib/api";

export type CardFormTarget =
  | { mode: "create"; columnId: number; columnTitle: string; stageKind?: string }
  | { mode: "edit"; card: CardResponse };

type Props = {
  companySlug: string;
  boardId: number;
  target: CardFormTarget;
  /** The backend's canAssignSalesperson capability: a new card then needs a chosen salesperson (a Sales Member's is automatically their own). */
  canAssign?: boolean;
  onSaved: (card: CardResponse) => void;
  /** Called after a 403 on save: the person's permissions may have changed, so the board reads them again. */
  onForbidden?: () => void;
  /** Re-reads the board without closing the form (used after a failure whose outcome is unknown); true when it worked. */
  onReload: () => Promise<boolean>;
  onClose: () => void;
};

type Values = {
  title: string;
  details: string;
  company: string;
  contact: string;
  contactEmail: string;
  contactPhone: string;
  dealValue: string;
  dealValueNextYear: string;
  nextStepDue: string;
  nextStep: string;
  leadSource: string;
  productLine: string;
  confidenceLevel: string;
  salespersonId: string;
  lossReason: string;
};

const num = (v: number | null | undefined) => (v == null ? "" : String(v));

function initialValues(target: CardFormTarget): Values {
  const empty: Values = { title: "", details: "", company: "", contact: "", contactEmail: "", contactPhone: "", dealValue: "", dealValueNextYear: "",
    nextStepDue: "", nextStep: "", leadSource: "", productLine: "", confidenceLevel: "", salespersonId: "", lossReason: "" };
  if (target.mode === "create") return empty;
  const c = target.card;
  return {
    title: c.title,
    details: c.details ?? "",
    company: c.company ?? "",
    contact: c.contact ?? "",
    contactEmail: c.contact_email ?? "",
    contactPhone: c.contact_phone ?? "",
    dealValue: num(c.deal_value),
    // A zero next-year amount is the column's "nothing entered", shown empty.
    dealValueNextYear: c.deal_value_next_year ? String(c.deal_value_next_year) : "",
    nextStepDue: c.bid_details?.next_step_due ?? "",
    nextStep: c.bid_details?.next_step ?? "",
    leadSource: c.lead_source ?? "",
    productLine: c.product_line ?? "",
    confidenceLevel: c.confidence_level ?? "",
    salespersonId: c.salesperson_id != null ? String(c.salesperson_id) : "",
    lossReason: "",
  };
}

/** Empty text means "not set": null for a lead field, so a cleared field is cleared and an untouched null stays null. */
const textOrNull = (v: string) => (v.trim() === "" ? null : v.trim());

/** Only the fields whose value differs from the card, in the shape the central PUT accepts. */
export function buildCardUpdates(card: CardResponse, v: Values): CardUpdatePayload {
  const out: CardUpdatePayload = {};
  const title = v.title.trim();
  if (title !== card.title) out.title = title;
  if (v.details !== (card.details ?? "")) out.details = v.details;
  const text: [keyof Values, "company" | "contact" | "contact_email" | "contact_phone" | "lead_source" | "product_line"][] = [
    ["company", "company"], ["contact", "contact"], ["contactEmail", "contact_email"], ["contactPhone", "contact_phone"], ["leadSource", "lead_source"], ["productLine", "product_line"],
  ];
  for (const [field, key] of text) {
    const next = textOrNull(v[field]);
    if (next !== (card[key] ?? null)) (out as Record<string, unknown>)[key] = next;
  }
  // The next commercial action lives in the card's Bid details row; only a changed value is sent, an empty one clears it.
  const dueNext = textOrNull(v.nextStepDue);
  if (dueNext !== (card.bid_details?.next_step_due ?? null)) out.next_step_due = dueNext;
  const actionNext = textOrNull(v.nextStep);
  if (actionNext !== (card.bid_details?.next_step ?? null)) out.next_step = actionNext;
  const level = (v.confidenceLevel || null) as CardResponse["confidence_level"];
  if (level !== (card.confidence_level ?? null)) out.confidence_level = level;
  const deal = v.dealValue === "" ? null : Number(v.dealValue);
  if (deal !== (card.deal_value ?? null)) out.deal_value = deal;
  const nextYear = v.dealValueNextYear === "" ? 0 : Number(v.dealValueNextYear);
  if (nextYear !== (card.deal_value_next_year ?? 0)) out.deal_value_next_year = nextYear;
  // Only changed when someone picked another person (the select is shown to the roles that may assign; the backend decides).
  if (v.salespersonId !== "" && Number(v.salespersonId) !== card.salesperson_id) out.salesperson_id = Number(v.salespersonId);
  return out;
}

/** Maximum length of the Next Action Description; the backend enforces the same limit (schemas.NEXT_STEP_MAX_LENGTH). */
export const NEXT_ACTION_MAX_LENGTH = 100;

const inputClass =
  "mt-1 w-full rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] px-3 py-2 text-sm text-[var(--navy-dark)] outline-none focus:border-[var(--primary-blue)] disabled:opacity-50";
const labelClass = "block text-xs font-semibold text-[var(--gray-text)]";

type Failure = { text: string; unknownOutcome: boolean; conflict?: boolean };

function describeFailure(err: Partial<ApiError>, action: string): Failure {
  if (isCardVersionConflict(err)) {
    return {
      text: `${CARD_CONFLICT_TEXT} What you typed is kept below, but saving it now would overwrite the other change, so Save is off. Reload the board to see the latest version, note your changes, then close this form and open the card again.`,
      unknownOutcome: false,
      conflict: true,
    };
  }
  switch (err.status_code) {
    case 0:
      return { text: `Could not confirm whether the card was ${action}: the connection failed. Reload the board to check before trying again.`, unknownOutcome: true };
    case 403:
      return { text: "You are not allowed to make this change.", unknownOutcome: false };
    case 404:
      return { text: "The board, column or card no longer exists. Reload the board.", unknownOutcome: false };
    case 422:
      return { text: getErrorMessage(err, "Some values are not valid."), unknownOutcome: false };
    default:
      if ((err.status_code ?? 500) >= 500) {
        return { text: `The server failed and the card may or may not have been ${action}. Reload the board to check before trying again.`, unknownOutcome: true };
      }
      return { text: getErrorMessage(err, "Could not save the card."), unknownOutcome: false };
  }
}

/**
 * Create / edit form for one card of one company board. It never retries a write by itself, keeps what was
 * typed after any failure, and drops the result of a request that was started for another company or board
 * (the parent remounts it on a change, which aborts the request and marks it stale).
 */
export const CentralCardForm = ({ companySlug, boardId, target, canAssign = false, onSaved, onReload, onForbidden, onClose }: Props) => {
  const [values, setValues] = useState<Values>(() => initialValues(target));
  const [options, setOptions] = useState<LeadOptions | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Create mode only: a previous attempt may have been stored, so another "Add card" could duplicate it.
  const [uncertain, setUncertain] = useState(false);
  const [reload, setReload] = useState<"idle" | "loading" | "done" | "failed">("idle");
  const [acceptDuplicate, setAcceptDuplicate] = useState(false);
  const inFlight = useRef(false); // set synchronously, so a double click cannot start a second write
  const live = useRef<AbortController | null>(null);
  const edit = target.mode === "edit";

  useEffect(() => {
    const controller = new AbortController();
    live.current = controller;
    getCompanyLeadOptions(companySlug, controller.signal).then(
      (o) => { if (!controller.signal.aborted) setOptions(o); },
      () => { /* the selects simply stay limited to the current value */ }
    );
    return () => controller.abort();
  }, [companySlug]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !inFlight.current) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = (field: keyof Values) => (e: { target: { value: string } }) =>
    setValues((prev) => ({ ...prev, [field]: e.target.value }));

  const invalidNumber = (v: string) => v !== "" && (!Number.isFinite(Number(v)) || Number(v) < 0);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (inFlight.current) return;
    if (uncertain && !acceptDuplicate) return; // a deliberate confirmation is required first
    setFailure(null);
    if (!values.title.trim()) return setFieldError("Title is required.");
    const needsSalesperson = target.mode === "create" && canAssign;
    const needsLossReason = target.mode === "create" && target.stageKind === "lost";
    if (needsLossReason && !values.lossReason.trim()) return setFieldError("Enter the reason this opportunity was lost.");
    if (needsLossReason && values.lossReason.trim().length > 1000) return setFieldError("The loss reason can have at most 1000 characters.");
    if (needsSalesperson && !values.salespersonId) return setFieldError("Choose the salesperson for this card.");
    if (values.nextStep.trim().length > NEXT_ACTION_MAX_LENGTH) {
      return setFieldError(`The next action description can have at most ${NEXT_ACTION_MAX_LENGTH} characters.`);
    }
    if (values.nextStepDue && !/^\d{4}-\d{2}-\d{2}$/.test(values.nextStepDue)) {
      return setFieldError("The next action date must be a valid date.");
    }
    if (invalidNumber(values.dealValue) || invalidNumber(values.dealValueNextYear)) {
      return setFieldError("Deal values must be numbers of zero or more.");
    }
    setFieldError(null);

    const controller = live.current;
    if (!controller || controller.signal.aborted) return;
    let call: Promise<CardResponse>;
    if (target.mode === "create") {
      call = createCompanyCard(companySlug, boardId, {
        column_id: target.columnId,
        title: values.title.trim(),
        ...(values.details.trim() ? { details: values.details.trim() } : {}),
        ...(values.dealValue !== "" ? { deal_value: Number(values.dealValue) } : {}),
        ...(values.dealValueNextYear !== "" ? { deal_value_next_year: Number(values.dealValueNextYear) } : {}),
        ...(needsSalesperson ? { salesperson_id: Number(values.salespersonId) } : {}),
        ...(needsLossReason ? { loss_reason: values.lossReason.trim() } : {}),
      });
    } else {
      const updates = buildCardUpdates(target.card, values);
      if (Object.keys(updates).length === 0) return onClose();
      // The version is the one of the card this form was opened on (never re-read before saving).
      call = updateCompanyCard(companySlug, boardId, target.card.id, updates, undefined, target.card.version);
    }

    // The write carries no AbortSignal on purpose: if the form goes away mid-save (the company or board changed) the
    // server may still apply it, and aborting would hide an outcome nobody can see. The guards below only keep a
    // discarded form from showing the answer.
    inFlight.current = true;
    setSaving(true);
    setAcceptDuplicate(false); // each further attempt needs its own confirmation
    try {
      const saved = await call;
      if (!controller.signal.aborted) onSaved(saved);
    } catch (err) {
      if (controller.signal.aborted || isAbortError(err)) return;
      // A 401 already ended the session (the app shows the sign-in screen); anything else is shown here.
      if ((err as Partial<ApiError>).status_code === 401) return;
      const described = describeFailure(err as Partial<ApiError>, edit ? "updated" : "created");
      setFailure(described);
      if ((err as Partial<ApiError>).status_code === 403) onForbidden?.();
      if (!edit && described.unknownOutcome) { setUncertain(true); setReload("idle"); }
    } finally {
      inFlight.current = false;
      if (!controller.signal.aborted) setSaving(false);
    }
  };

  const checkBoard = async () => {
    setReload("loading");
    const ok = await onReload();
    if (!live.current?.signal.aborted) setReload(ok ? "done" : "failed");
  };

  const blocked = uncertain && !acceptDuplicate;

  const withCurrent = (list: string[] | undefined, current: string) =>
    current && !(list ?? []).includes(current) ? [current, ...(list ?? [])] : (list ?? []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <form onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="central-card-form-title" noValidate
        className="flex max-h-[90vh] w-full max-w-xl flex-col gap-3 overflow-y-auto rounded-[24px] border border-[var(--stroke)] bg-[var(--surface-strong)] p-6 shadow-[var(--shadow)]">
        <h3 id="central-card-form-title" className="font-display text-lg font-semibold text-[var(--navy-dark)]">
          {edit ? "Edit opportunity" : `New card in ${target.columnTitle}`}
        </h3>
        {(fieldError || failure) && (
          <div role="alert" className="text-sm font-medium text-red-800">
            <p>{fieldError ?? failure?.text}</p>
            {(failure?.unknownOutcome || failure?.conflict) && (
              <button type="button" onClick={() => void checkBoard()} disabled={reload === "loading"}
                className="mt-1 font-semibold text-[var(--primary-blue)] hover:underline disabled:opacity-50">
                Reload board
              </button>
            )}
          </div>
        )}
        {uncertain && !edit && (
          <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <p>
              {reload === "done"
                ? "The board was reloaded. Look for this card in the column: the earlier attempt is not confirmed either way, so it may or may not be there."
                : reload === "failed"
                  ? "The board could not be reloaded, so it is still unknown whether the card was saved."
                  : "The card may already have been saved."}{" "}
              Adding it again can create a duplicate. If the card is on the board, cancel this form.
            </p>
            <label className="mt-2 flex items-start gap-2 font-medium">
              <input type="checkbox" checked={acceptDuplicate} onChange={(e) => setAcceptDuplicate(e.target.checked)} disabled={saving} className="mt-0.5" />
              <span>I understand this may create a duplicate card and want to add it again.</span>
            </label>
          </div>
        )}
        <label className={labelClass}>Title
          <input value={values.title} onChange={set("title")} maxLength={255} disabled={saving} className={inputClass} />
        </label>
        <label className={labelClass}>Details
          <textarea value={values.details} onChange={set("details")} maxLength={5000} rows={3} disabled={saving} className={inputClass} />
        </label>
        {canAssign && (
          <label className={labelClass}>Salesperson
            <select value={values.salespersonId} onChange={set("salespersonId")} disabled={saving} className={inputClass}>
              <option value="">{edit ? "Not assigned" : "Choose a salesperson"}</option>
              {(options?.salespeople ?? []).map((p) => <option key={p.id} value={p.id}>{p.username}</option>)}
              {edit && target.card.salesperson_id != null && !(options?.salespeople ?? []).some((p) => p.id === target.card.salesperson_id) && (
                <option value={target.card.salesperson_id}>{target.card.salesperson_name ?? `User ${target.card.salesperson_id}`} (current)</option>
              )}
            </select>
          </label>
        )}
        {!edit && target.stageKind === "lost" && (
          <label className={labelClass}>Loss reason (required)
            <textarea value={values.lossReason} onChange={set("lossReason")} maxLength={1000} rows={2} disabled={saving} className={inputClass} />
          </label>
        )}
        <div className="grid grid-cols-2 gap-3">
          <label className={labelClass}>Deal value, current year (USD)
            <input type="number" min={0} step="any" value={values.dealValue} onChange={set("dealValue")} disabled={saving} className={inputClass} />
          </label>
          <label className={labelClass}>Deal value, next year (USD)
            <input type="number" min={0} step="any" value={values.dealValueNextYear} onChange={set("dealValueNextYear")} disabled={saving} className={inputClass} />
          </label>
        </div>
        {edit && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <label className={labelClass}>Customer
                <input value={values.company} onChange={set("company")} maxLength={255} disabled={saving} className={inputClass} />
              </label>
              <label className={labelClass}>Contact
                <input value={values.contact} onChange={set("contact")} maxLength={255} disabled={saving} className={inputClass} />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className={labelClass}>Contact email
                <input type="email" value={values.contactEmail} onChange={set("contactEmail")} maxLength={254} disabled={saving} autoComplete="off" className={inputClass} />
              </label>
              <label className={labelClass}>Contact telephone
                <input type="tel" value={values.contactPhone} onChange={set("contactPhone")} maxLength={40} disabled={saving} autoComplete="off" className={inputClass} />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className={labelClass}>Next Action Date
                <input type="date" value={values.nextStepDue} onChange={set("nextStepDue")} disabled={saving} className={inputClass} />
              </label>
              <label className={labelClass}>Confidence level
                <select value={values.confidenceLevel} onChange={set("confidenceLevel")} disabled={saving} className={inputClass}>
                  <option value="">Not assigned</option>
                  {CONFIDENCE_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </label>
            </div>
            <div>
              <label className={labelClass}>Next Action Description
                <input value={values.nextStep} onChange={set("nextStep")} maxLength={NEXT_ACTION_MAX_LENGTH} disabled={saving}
                  aria-describedby="next-action-count" placeholder="e.g. Call the buyer about the quote" className={inputClass} />
              </label>
              <span id="next-action-count" className="mt-1 block text-right text-[11px] text-[var(--gray-text)]">{values.nextStep.length}/{NEXT_ACTION_MAX_LENGTH}</span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className={labelClass}>Lead source
                <select value={values.leadSource} onChange={set("leadSource")} disabled={saving} className={inputClass}>
                  <option value="">None</option>
                  {withCurrent(options?.lead_sources, values.leadSource).map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <label className={labelClass}>Product line
                <select value={values.productLine} onChange={set("productLine")} disabled={saving} className={inputClass}>
                  <option value="">None</option>
                  {withCurrent(options?.product_lines, values.productLine).map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
            </div>
          </>
        )}
        <div className="mt-2 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={saving}
            className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-sm font-semibold text-[var(--gray-text)] disabled:opacity-50">
            Cancel
          </button>
          <button type="submit" disabled={saving || blocked || !!failure?.conflict}
            className="rounded-lg bg-[var(--secondary-purple)] px-5 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50">
            {saving ? "Saving..." : edit ? "Save" : uncertain ? "Add card again" : "Add card"}
          </button>
        </div>
      </form>
    </div>
  );
};
