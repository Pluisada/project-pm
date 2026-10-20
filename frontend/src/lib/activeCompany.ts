import { useSyncExternalStore } from "react";

/**
 * The company this tab is working in, kept in memory only.
 *
 * It is the client's source of truth for "which company is current" and it is never read from storage: the
 * remembered choice in localStorage is only a hint the shell validates once at start-up. Another tab changing
 * that hint therefore cannot redirect a request of this tab.
 *
 * It is not an authorization mechanism. The backend validates the token, the membership and the role of every
 * company request from the URL; this module only stops this client from sending, or applying the answer of, a
 * request that belongs to a company the person has since left.
 *
 * Without an active company (before sign-in, after sign-out, or a component used on its own) a request keeps
 * the slug its caller passed. Once the shell registers a company, a request for any other company is refused
 * before it is sent.
 */

/** Raised when work belongs to a company that is no longer the active one. It is an expected interruption,
 *  not a failure: it carries the name "AbortError", so callers that already ignore aborted requests ignore it. */
export class CompanyScopeError extends Error {
  readonly companySlug: string;
  constructor(companySlug: string) {
    super(`The active company is no longer "${companySlug}".`);
    this.name = "AbortError";
    this.companySlug = companySlug;
  }
}

/** What an operation keeps for its whole duration: the company it started in and a way to know it has ended. */
export interface CompanyScope {
  readonly companySlug: string;
  /** Aborted when the active company changes. */
  readonly signal: AbortSignal;
  /** True while the company this scope started in is still the active one. */
  isCurrent(): boolean;
  /** Throws CompanyScopeError if the active company changed since the scope was captured. */
  assertCurrent(): void;
}

let active: string | null = null;
let epoch = new AbortController();
const listeners = new Set<() => void>();

export function getActiveCompany(): string | null {
  return active;
}

/** Make `slug` the active company (or none). A real change aborts every request started under the old one. */
export function setActiveCompany(slug: string | null): void {
  if (slug === active) return;
  epoch.abort();
  epoch = new AbortController();
  active = slug;
  listeners.forEach((listener) => listener());
}

export function subscribeActiveCompany(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useActiveCompany(): string | null {
  return useSyncExternalStore(subscribeActiveCompany, getActiveCompany, () => null);
}

/**
 * Capture the scope of an operation that starts now for `companySlug`. Throws CompanyScopeError if another
 * company is active. When no company is registered the scope never ends on its own.
 */
export function captureScope(companySlug: string): CompanyScope {
  if (active !== null && active !== companySlug) throw new CompanyScopeError(companySlug);
  const registered = active !== null; // a scope started with no registered company never belongs to one
  const signal = registered ? epoch.signal : new AbortController().signal;
  const isCurrent = () => (registered ? active === companySlug && !signal.aborted : true);
  return {
    companySlug,
    signal,
    isCurrent,
    assertCurrent() {
      if (!isCurrent()) throw new CompanyScopeError(companySlug);
    },
  };
}

/**
 * Run a multi-step operation in one company. The scope is captured before the first step and is the only
 * place the steps take the company from, so a later step cannot be sent to a different company. If the
 * company changed meanwhile the result is discarded and CompanyScopeError is thrown instead.
 */
export async function runInCompanyScope<T>(
  companySlug: string,
  work: (scope: CompanyScope) => Promise<T>
): Promise<T> {
  const scope = captureScope(companySlug);
  const result = await work(scope);
  scope.assertCurrent();
  return result;
}

/** Test helper: forget the active company without notifying anyone about an abort that never mattered. */
export function resetActiveCompanyForTests(): void {
  epoch.abort();
  epoch = new AbortController();
  active = null;
  listeners.clear();
}
