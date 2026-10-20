"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { centralLogin, centralLogout, getAuthState, SESSION_EXPIRED_EVENT } from "@/lib/auth";
import {
  getCentralMe,
  getErrorMessage,
  listCompanyBoards,
  type ApiError,
  ROLE_SUPER_ADMIN,
  type BoardResponse,
  type CentralMe,
} from "@/lib/api";
import { CentralBoardAdmin } from "@/components/CentralBoardAdmin";
import { CentralBoardView } from "@/components/CentralBoardView";
import { CentralCardsImport } from "@/components/CentralCardsImport";
import {
  CentralForgotScreen,
  CentralInviteScreen,
  CentralResetScreen,
  readCentralLink,
  type CentralLink,
} from "@/components/CentralLinkScreens";
import { CentralLanding } from "@/components/CentralLanding";
import { CentralMembers } from "@/components/CentralMembers";
import { ExcelDownloadButtons } from "@/components/ExcelDownloadButtons";
import { LoadingScreen } from "@/components/LoadingScreen";

const COMPANY_KEY = "pm_central_company";

/** The remembered company is only a convenience: it is used only if /me still lists it. */
function rememberedCompany(): string | null {
  try {
    return localStorage.getItem(COMPANY_KEY);
  } catch {
    return null;
  }
}

function remember(slug: string | null) {
  try {
    if (slug) localStorage.setItem(COMPANY_KEY, slug);
    else localStorage.removeItem(COMPANY_KEY);
  } catch {
    // storage unavailable: nothing to remember
  }
}

const inputClass =
  "mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none focus:border-[var(--primary-blue)] disabled:opacity-50";
const labelClass = "block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]";

const CentralLogin = ({ onSuccess, onForgot, notice }: { onSuccess: () => void; onForgot: () => void; notice: string | null }) => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await centralLogin(email, password);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
      setPassword("");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <form onSubmit={submit} className="w-full max-w-md rounded-[24px] border border-[var(--stroke)] bg-white/80 p-8 shadow-[var(--shadow)]">
        <h1 className="font-display text-3xl font-semibold text-[var(--navy-dark)]">Sign In</h1>
        {notice && !error && <p role="status" className="mt-4 text-sm text-green-800">{notice}</p>}
        {error && <p role="alert" className="mt-4 text-sm font-medium text-red-800">{error}</p>}
        <label htmlFor="central-email" className={`${labelClass} mt-6`}>Email</label>
        <input id="central-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          disabled={loading} autoComplete="username" className={inputClass} />
        <label htmlFor="central-password" className={`${labelClass} mt-6`}>Password</label>
        <input id="central-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          disabled={loading} autoComplete="current-password" className={inputClass} />
        <button type="submit" disabled={loading || !email || !password}
          className="mt-8 w-full rounded-lg bg-[var(--secondary-purple)] px-6 py-3 font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
          {loading ? "Signing in..." : "Sign In"}
        </button>
        <button type="button" onClick={onForgot} disabled={loading}
          className="mt-4 block w-full text-center text-sm font-semibold text-[var(--primary-blue)] hover:underline">
          Forgot my password?
        </button>
      </form>
    </main>
  );
};

/**
 * Minimal central-mode shell: sign in with the central identity, pick one of the companies the backend
 * lists for the person, and offer the company board's Excel downloads and the Cards import. The backend re-checks membership and
 * role on every company request; nothing here grants access.
 */
export const CentralApp = () => {
  const [ready, setReady] = useState(false);
  // A one-time invitation or reset link from the address bar. The token is kept only in memory, never in storage.
  // Read once on mount (this component only renders in the browser, after the sign-in mode was fetched).
  const [link, setLink] = useState<CentralLink | null>(() =>
    typeof window === "undefined" ? null : readCentralLink(window.location.search)
  );
  const [forgot, setForgot] = useState(false);
  // The sign-in form stays behind the landing page's LOGIN button; a notice (expired session, finished link) opens it.
  const [loginOpen, setLoginOpen] = useState(false);
  const [me, setMe] = useState<CentralMe | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [companySlug, setCompanySlug] = useState<string | null>(null);
  // Boards are kept with the company they were loaded for, so a company change never shows stale ones.
  const [loaded, setLoaded] = useState<{ slug: string; boards: BoardResponse[] } | null>(null);
  const [pickedBoardId, setPickedBoardId] = useState<number | null>(null);
  // Bumped after a column change so the board view is read again from the server (in place, without a remount).
  const [structureTick, setStructureTick] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  // The company whose board list failed for a reason a retry can fix (network, 5xx); `attempt` re-runs the load.
  const [boardsFailed, setBoardsFailed] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  // Companies the backend refused (403) are never auto-selected again, or a lone company would loop.
  const denied = useRef(new Set<string>());

  const loadMe = useCallback(async () => {
    try {
      const identity = await getCentralMe();
      setMe(identity);
      const remembered = rememberedCompany();
      const listed = identity.companies.map((c) => c.slug);
      const pickable = listed.filter((slug) => !denied.current.has(slug));
      setCompanySlug((current) =>
        current && listed.includes(current)
          ? current
          : remembered && pickable.includes(remembered)
            ? remembered
            : pickable.length === 1 && listed.length === 1
              ? pickable[0]
              : null
      );
    } catch (err) {
      // A 401 already cleared the session and fired the expiry event; anything else is shown.
      if ((err as Partial<ApiError>).status_code !== 401) {
        setProblem(getErrorMessage(err, "Could not load your account."));
      }
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    // Take the token out of the address bar (history, referrers, screenshots).
    if (link) window.history.replaceState(null, "", window.location.pathname);
  }, [link]);

  useEffect(() => {
    const start = async () => {
      if (getAuthState().isAuthenticated) await loadMe();
      else setReady(true);
    };
    start();
  }, [loadMe]);

  useEffect(() => {
    const onExpired = () => {
      setMe(null);
      setCompanySlug(null);
      setLoaded(null);
      setNotice("Your session expired. Sign in again.");
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  useEffect(() => {
    if (!companySlug) return;
    let cancelled = false;
    const controller = new AbortController();
    listCompanyBoards(companySlug, controller.signal)
      .then((list) => {
        if (!cancelled) setLoaded({ slug: companySlug, boards: list });
      })
      .catch((err: Partial<ApiError>) => {
        if (cancelled || err.status_code === 401) return;
        if (err.status_code === 403) {
          // Access changed since sign-in: forget the company and re-read what the backend allows.
          denied.current.add(companySlug);
          remember(null);
          setCompanySlug(null);
          setProblem("You no longer have access to that company.");
          loadMe();
        } else {
          setProblem(getErrorMessage(err, "Could not load the boards."));
          setBoardsFailed(companySlug);
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [companySlug, attempt, loadMe]);

  // Re-reads the board list of one company on request (after a board creation of unknown outcome).
  const boardsSeq = useRef(0);
  const refreshBoards = useCallback(async (slug: string): Promise<boolean> => {
    const seq = ++boardsSeq.current;
    try {
      const list = await listCompanyBoards(slug);
      if (seq === boardsSeq.current) setLoaded({ slug, boards: list });
      return true;
    } catch {
      return false;
    }
  }, []);

  const chooseCompany = (slug: string) => {
    denied.current.delete(slug);
    remember(slug);
    setProblem(null);
    setBoardsFailed(null);
    setPickedBoardId(null);
    setCompanySlug(slug);
  };

  // Only for the company currently selected; the effect re-reads companySlug, so it never loads another one.
  const retryBoards = () => {
    setProblem(null);
    setBoardsFailed(null);
    setAttempt((n) => n + 1);
  };

  const signOut = async () => {
    await centralLogout();
    remember(null);
    setLoginOpen(false);
    setMe(null);
    setCompanySlug(null);
    setLoaded(null);
    setPickedBoardId(null);
    setNotice(null);
    setProblem(null);
    setBoardsFailed(null);
    denied.current.clear();
  };

  const retry = () => {
    setProblem(null);
    setReady(false);
    loadMe();
  };

  const linkDone = async (message: string) => {
    // The person may be signed in as someone else; the next sign-in must be a deliberate one.
    await centralLogout();
    remember(null);
    setMe(null);
    setCompanySlug(null);
    setLoaded(null);
    setProblem(null);
    setLink(null);
    setForgot(false);
    setNotice(message);
  };

  if (!ready) return <LoadingScreen message="Loading..." />;
  if (link?.kind === "invite") return <CentralInviteScreen token={link.token} onDone={linkDone} />;
  if (link?.kind === "reset") return <CentralResetScreen token={link.token} onDone={linkDone} />;
  if (!me && problem && getAuthState().isAuthenticated) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6">
        <div className="text-center">
          <p role="alert" className="text-sm font-medium text-red-800">{problem}</p>
          <button type="button" onClick={retry}
            className="mt-4 rounded-lg bg-[var(--secondary-purple)] px-6 py-3 font-semibold text-white transition hover:opacity-90">
            Try again
          </button>
          <button type="button" onClick={signOut} className="ml-4 text-sm font-semibold text-[var(--primary-blue)] hover:underline">
            Sign out
          </button>
        </div>
      </main>
    );
  }
  if (!me && forgot) return <CentralForgotScreen onBack={() => setForgot(false)} />;
  if (!me) {
    return (
      <CentralLanding open={loginOpen || Boolean(notice)} onLogin={() => setLoginOpen(true)}>
        <CentralLogin notice={notice} onForgot={() => setForgot(true)} onSuccess={() => { setNotice(null); setProblem(null); loadMe(); }} />
      </CentralLanding>
    );
  }

  const company = me.companies.find((c) => c.slug === companySlug) ?? null;
  const boards = loaded && loaded.slug === companySlug ? loaded.boards : [];
  const boardId = pickedBoardId !== null && boards.some((b) => b.id === pickedBoardId) ? pickedBoardId : (boards[0]?.id ?? null);

  return (
    <div className="mx-auto max-w-7xl px-6 py-10">
      <header className="flex items-center justify-between">
        <p className="text-sm text-[var(--gray-text)]">{me.email}</p>
        <button type="button" onClick={signOut} className="text-sm font-semibold text-[var(--primary-blue)] hover:underline">
          Sign out
        </button>
      </header>
      {problem && <p role="alert" className="mt-6 text-sm font-medium text-red-800">{problem}</p>}
      {boardsFailed !== null && boardsFailed === companySlug && (
        <button type="button" onClick={retryBoards}
          className="mt-3 rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90">
          Try again
        </button>
      )}
      <section className="mt-8">
        <label htmlFor="company-select" className={labelClass}>Company</label>
        {me.companies.length === 0 ? (
          <p className="mt-2 text-sm text-[var(--gray-text)]">You do not belong to any company yet.</p>
        ) : (
          <select id="company-select" value={companySlug ?? ""} onChange={(e) => e.target.value && chooseCompany(e.target.value)}
            className={inputClass}>
            <option value="">Choose a company</option>
            {me.companies.map((c) => (
              <option key={c.slug} value={c.slug}>{c.name}</option>
            ))}
          </select>
        )}
      </section>
      {company && boards.length > 1 && (
        <section className="mt-6">
          <label htmlFor="board-select" className={labelClass}>Board</label>
          <select id="board-select" value={boardId ?? ""} onChange={(e) => setPickedBoardId(Number(e.target.value))} className={inputClass}>
            {boards.map((b) => (
              <option key={b.id} value={b.id}>{b.title}</option>
            ))}
          </select>
        </section>
      )}
      {company && boardId !== null && (
        <section className="mt-8">
          <CentralBoardView key={`${company.slug}:${boardId}`} companySlug={company.slug} boardId={boardId} refreshToken={structureTick} />
        </section>
      )}
      {company && loaded && loaded.slug === company.slug && (
        <section className="mt-8">
          <CentralBoardAdmin key={company.slug} companySlug={company.slug} boards={boards} boardId={boardId}
            onSelectBoard={setPickedBoardId} onRefreshBoards={() => refreshBoards(company.slug)} onColumnsChanged={() => setStructureTick((n) => n + 1)}
            onBoardCreated={(created) => {
              setLoaded((cur) => (cur && cur.slug === company.slug ? { slug: cur.slug, boards: [...cur.boards, created] } : cur));
              setPickedBoardId(created.id);
            }} />
        </section>
      )}
      {company && loaded && loaded.slug === company.slug && company.role === ROLE_SUPER_ADMIN && (
        <section className="mt-8">
          <CentralMembers key={company.slug} companySlug={company.slug} companyName={company.name} />
        </section>
      )}
      {company && boardId !== null && (
        <section className="mt-8">
          <ExcelDownloadButtons key={`${company.slug}:${boardId}`} companySlug={company.slug} boardId={boardId} role={company.role} />
        </section>
      )}
      {company && boardId !== null && (
        <section className="mt-8">
          <CentralCardsImport key={`${company.slug}:${boardId}`} companySlug={company.slug} boardId={boardId}
            onImported={() => setStructureTick((n) => n + 1)} />
        </section>
      )}
    </div>
  );
};
