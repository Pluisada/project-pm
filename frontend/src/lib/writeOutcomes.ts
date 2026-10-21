import { useSyncExternalStore } from "react";
import { getActiveCompany } from "@/lib/activeCompany";

/**
 * What became of a write after the person had already left the company it was made in.
 *
 * A write is never aborted by a company switch (the server may already have applied it), so it can finish, fail or
 * stay uncertain while another company is on screen. The page that started it is gone, so nothing would tell the
 * person. This module remembers those outcomes, per company and in memory only, for the shell to show once.
 * A write that settles while its own company is still the active one needs no record: that page reports it.
 */

export type WriteResult = "saved" | "failed" | "unknown";

export interface WriteOutcome {
  id: number;
  companySlug: string;
  /** Plain-language name of the change, for example "Move card". */
  action: string;
  result: WriteResult;
  /** The server's reason, for a failed write. */
  detail?: string;
}

let nextId = 1;
// Bumped when the person or the account changes, so a write from before it can never be shown to whoever follows.
let generation = 0;
let outcomes: WriteOutcome[] = [];
const listeners = new Set<() => void>();

const emit = () => listeners.forEach((listener) => listener());

/** The change an endpoint makes, in words. Only the shape of the URL is read: nothing about the data. */
export function describeWrite(method: string, endpoint: string): string {
  const path = endpoint.split("?")[0].replace(/^\/api\/(?:central\/)?companies\/[^/]+/, "");
  const verb = method.toUpperCase();
  if (/\/excel\/import\/confirm$/.test(path)) return "Cards import";
  if (/\/imports(\/|$)/.test(path)) return "Bid import";
  if (/\/undo$/.test(path)) return "Undo";
  if (/\/cards\/\d+\/position$/.test(path)) return "Move card";
  if (/\/cards\/\d+$/.test(path)) return verb === "DELETE" ? "Delete card" : "Save card";
  if (/\/cards$/.test(path)) return "Create card";
  if (/\/columns(\/\d+)?$/.test(path)) return "Column change";
  if (/^\/boards$/.test(path)) return "Create board";
  if (/\/ai$/.test(path)) return "AI assistant change";
  if (/\/(members|invitations)/.test(path)) return "Member change";
  if (/^\/products/.test(path)) return "Product change";
  return "Change";
}

// Endpoints that take a POST body only to validate it: they persist nothing, so nothing can be "saved" by them.
// The Cards import preview and the Bid import preview. The matching confirmations stay writes.
const PREVIEW_ONLY = /\/(?:excel\/)?imports?\/preview$/;

/** Whether a request changes data on the server, so its outcome is worth reporting after the person has left. */
export function isTrackedWrite(method: string, endpoint: string): boolean {
  const verb = method.toUpperCase();
  if (verb === "GET" || verb === "HEAD") return false;
  return !PREVIEW_ONLY.test(endpoint.split("?")[0]);
}

export interface TrackedWrite {
  saved(): void;
  failed(detail?: string): void;
  unknown(): void;
}

/** Start following a write made in `companySlug`; the returned handle settles it once. */
export function trackWrite(companySlug: string, method: string, endpoint: string): TrackedWrite {
  const action = describeWrite(method, endpoint);
  const startedIn = generation;
  let settled = false;
  const settle = (result: WriteResult, detail?: string) => {
    if (settled) return;
    settled = true;
    if (generation !== startedIn) return; // the session it belonged to has ended
    if (getActiveCompany() === companySlug) return; // its own page is still showing and reports it
    outcomes = [...outcomes, { id: nextId++, companySlug, action, result, ...(detail ? { detail } : {}) }];
    emit();
  };
  return { saved: () => settle("saved"), failed: (detail) => settle("failed", detail), unknown: () => settle("unknown") };
}

export function getWriteOutcomes(): WriteOutcome[] {
  return outcomes;
}

export function dismissWriteOutcome(id: number): void {
  outcomes = outcomes.filter((o) => o.id !== id);
  emit();
}

/** Forget every recorded outcome (sign-out, expiry, another account). */
export function clearWriteOutcomes(): void {
  generation += 1;
  if (outcomes.length === 0) return;
  outcomes = [];
  emit();
}

export function subscribeWriteOutcomes(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useWriteOutcomes(): WriteOutcome[] {
  return useSyncExternalStore(subscribeWriteOutcomes, getWriteOutcomes, () => []);
}

/** Test helper. */
export function resetWriteOutcomesForTests(): void {
  outcomes = [];
  nextId = 1;
  generation = 0;
  listeners.clear();
}
