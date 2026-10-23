"use client";

import { useEffect, useState } from "react";
import {
  completeSignup,
  getAuthState,
  getSetupStatus,
  logout,
  resetPassword,
  SESSION_EXPIRED_EVENT,
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
import { BidImportPage } from "@/components/BidImportPage";
import { AppNav, type AppView } from "@/components/AppNav";

export const ProtectedRoute = () => {
  const [username, setUsername] = useState<string | null>(null);
  const [role, setRole] = useState<UserRole | null>(null);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedBoardId, setSelectedBoardId] = useState<number | null>(null);
  const [view, setView] = useState<AppView>("board");
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
      }
    };

    loadBoards();
  }, [username]);

  // apiCall clears the stored session on a 401 and fires this event; drop the
  // in-memory session too so the login screen shows (no redirect, so no loop).
  useEffect(() => {
    const onExpired = () => {
      setUsername(null);
      setRole(null);
      setSelectedBoardId(null);
      setView("board");
      setNeedsSetup(false);
      setAuthView("login");
      setLoginNotice("Your session expired. Sign in again.");
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

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
    <div className="legacy-shell flex h-screen flex-col">
      <AppNav
        view={view}
        role={role}
        username={username}
        onNavigate={setView}
        onLogout={handleLogout}
      />

      {/* Kanban board with AI sidebar, or the admin's user management screen */}
      <div className="min-h-0 flex-1 overflow-auto">
        {view === "manage-users" ? (
          <ManageUsersPage />
        ) : view === "reports" ? (
          <ReportsPage boardId={selectedBoardId} />
        ) : view === "import" && role === "admin" ? (
          <BidImportPage boardId={selectedBoardId} />
        ) : view === "dashboard" ? (
          <DashboardPage boardId={selectedBoardId} />
        ) : (
          <KanbanWithSidebar boardId={selectedBoardId} role={role ?? "member"} />
        )}
      </div>
    </div>
  );
};
