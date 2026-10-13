"use client";

import { useEffect, useState } from "react";
import {
  completeSignup,
  getAuthState,
  getSetupStatus,
  logout,
  resetPassword,
  UserRole,
} from "@/lib/auth";
import { listBoards } from "@/lib/api";
import { KanbanWithSidebar } from "@/components/KanbanWithSidebar";
import { LoginPage } from "@/components/LoginPage";
import { SetupPage } from "@/components/SetupPage";
import { SignupPage } from "@/components/SignupPage";
import { SetPasswordPage } from "@/components/SetPasswordPage";
import { ForgotPasswordPage } from "@/components/ForgotPasswordPage";
import { ManageUsersPage } from "@/components/ManageUsersPage";
import { LoadingScreen } from "@/components/LoadingScreen";
import { ReportsPage } from "@/components/ReportsPage";
import { DashboardPage } from "@/components/DashboardPage";

export const ProtectedRoute = () => {
  const [username, setUsername] = useState<string | null>(null);
  const [role, setRole] = useState<UserRole | null>(null);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedBoardId, setSelectedBoardId] = useState<number | null>(null);
  const [view, setView] = useState<"board" | "manage-users" | "reports" | "dashboard">("board");
  const [authView, setAuthView] = useState<
    "login" | "signup" | "complete-signup" | "forgot-password" | "reset-password"
  >("login");
  // Token from an emailed link (?signup_token= or ?reset_token=)
  const [linkToken, setLinkToken] = useState<string | null>(null);
  const [loginNotice, setLoginNotice] = useState<string | null>(null);

  // Auth lives in localStorage, so it can only be read after mount — reading it
  // during render would not match the prerendered static export.
  useEffect(() => {
    const bootstrap = async () => {
      // Emailed links (/?signup_token=... or /?reset_token=...) take priority
      // over any session already stored in this browser.
      const params = new URLSearchParams(window.location.search);
      const signupToken = params.get("signup_token");
      const resetToken = params.get("reset_token");
      if (signupToken || resetToken) {
        setLinkToken(signupToken ?? resetToken);
        setAuthView(signupToken ? "complete-signup" : "reset-password");
        setIsLoading(false);
        return;
      }

      const auth = getAuthState();
      if (auth.isAuthenticated) {
        setUsername(auth.username);
        setRole(auth.role);
        setIsLoading(false);
        return;
      }

      try {
        const status = await getSetupStatus();
        setNeedsSetup(status.needs_setup);
      } catch (err) {
        console.error("Failed to check setup status", err);
        setNeedsSetup(false);
      }
      setIsLoading(false);
    };

    bootstrap();
  }, []);

  useEffect(() => {
    if (!username) return;

    const loadBoards = async () => {
      try {
        const boards = await listBoards();
        // Auto-select first board (MVP has 1 board)
        if (boards.length > 0) {
          setSelectedBoardId(boards[0].id);
        }
      } catch (err) {
        console.error("Failed to load boards", err);
        // A stored token that expired (or was issued for another DB) 401s here;
        // drop it and fall back to login instead of spinning forever.
        if ((err as { status_code?: number }).status_code === 401) {
          await logout();
          const status = await getSetupStatus();
          setNeedsSetup(status.needs_setup);
          setUsername(null);
          setRole(null);
        }
      }
    };

    loadBoards();
  }, [username]);

  const handleAuthSuccess = (loggedInUsername: string, loggedInRole: UserRole) => {
    setUsername(loggedInUsername);
    setRole(loggedInRole);
  };

  const leaveLink = (notice: string | null = null) => {
    window.history.replaceState(null, "", window.location.pathname);
    setLinkToken(null);
    setAuthView("login");
    setLoginNotice(notice);
  };

  const handleLogout = async () => {
    await logout();
    setUsername(null);
    setRole(null);
    // An admin demonstrably exists by now (we just logged one out), so
    // don't fall back to the stale needsSetup=true from initial bootstrap.
    setNeedsSetup(false);
    setSelectedBoardId(null);
    setView("board");
  };

  if (isLoading) {
    return <LoadingScreen message="Loading..." />;
  }

  if (authView === "complete-signup" && linkToken) {
    return (
      <SetPasswordPage
        title="Set Your Password"
        subtitle="Choose a password to finish creating your account"
        submitLabel="Create Account"
        onSubmit={(password) => completeSignup(linkToken, password)}
        onComplete={() => leaveLink("Account created. Sign in with your email and new password.")}
        onBackToLogin={() => leaveLink()}
      />
    );
  }

  if (authView === "reset-password" && linkToken) {
    return (
      <SetPasswordPage
        title="Reset Your Password"
        subtitle="Choose a new password for your account"
        submitLabel="Update Password"
        onSubmit={(password) => resetPassword(linkToken, password)}
        onComplete={() => leaveLink("Password updated. Sign in with your new password.")}
        onBackToLogin={() => leaveLink()}
      />
    );
  }

  if (!username) {
    if (needsSetup) {
      return <SetupPage onSetupSuccess={handleAuthSuccess} />;
    }
    if (authView === "signup") {
      return <SignupPage onBackToLogin={() => setAuthView("login")} />;
    }
    if (authView === "forgot-password") {
      return <ForgotPasswordPage onBackToLogin={() => setAuthView("login")} />;
    }
    return (
      <LoginPage
        onLoginSuccess={handleAuthSuccess}
        onSignUp={() => {
          setLoginNotice(null);
          setAuthView("signup");
        }}
        onForgotPassword={() => {
          setLoginNotice(null);
          setAuthView("forgot-password");
        }}
        notice={loginNotice}
      />
    );
  }

  if (!selectedBoardId) {
    return <LoadingScreen message="Loading boards..." />;
  }

  return (
    <div className="flex h-screen flex-col">
      {/* Header with user info and logout */}
      <div className="flex items-center justify-end gap-4 border-b border-[var(--stroke)] bg-white p-4">
        <span className="text-sm text-[var(--gray-text)]">
          Welcome, <span className="font-semibold text-[var(--navy-dark)]">{username}</span>
        </span>
        {view === "board" && (
          <button
            onClick={() => setView("dashboard")}
            className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] transition hover:border-[var(--primary-blue)]"
          >
            Dashboard
          </button>
        )}
        {view === "board" && (
          <button
            onClick={() => setView("reports")}
            className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] transition hover:border-[var(--primary-blue)]"
          >
            Reports
          </button>
        )}
        {role === "admin" && view === "board" && (
          <button
            onClick={() => setView("manage-users")}
            className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] transition hover:border-[var(--primary-blue)]"
          >
            Manage Users
          </button>
        )}
        <button
          onClick={handleLogout}
          className="rounded-lg bg-[var(--accent-yellow)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] transition hover:opacity-80"
        >
          Logout
        </button>
      </div>

      {/* Kanban board with AI sidebar, or the admin's user management screen */}
      <div className="min-h-0 flex-1 overflow-auto">
        {view === "manage-users" ? (
          <ManageUsersPage onBack={() => setView("board")} />
        ) : view === "reports" ? (
          <ReportsPage boardId={selectedBoardId} onBack={() => setView("board")} />
        ) : view === "dashboard" ? (
          <DashboardPage boardId={selectedBoardId} onBack={() => setView("board")} />
        ) : (
          <KanbanWithSidebar boardId={selectedBoardId} role={role ?? "member"} />
        )}
      </div>
    </div>
  );
};
