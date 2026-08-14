"use client";

import { useEffect, useState } from "react";
import { getAuthState, getSetupStatus, logout, UserRole } from "@/lib/auth";
import { listBoards } from "@/lib/api";
import { KanbanWithSidebar } from "@/components/KanbanWithSidebar";
import { LoginPage } from "@/components/LoginPage";
import { SetupPage } from "@/components/SetupPage";
import { ManageUsersPage } from "@/components/ManageUsersPage";

const LoadingScreen = ({ message }: { message: string }) => (
  <div className="flex min-h-screen items-center justify-center">
    <div className="text-center">
      <div className="inline-block">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-[var(--primary-blue)] border-t-transparent"></div>
      </div>
      <p className="mt-4 text-sm text-[var(--gray-text)]">{message}</p>
    </div>
  </div>
);

export const ProtectedRoute = () => {
  const [username, setUsername] = useState<string | null>(null);
  const [role, setRole] = useState<UserRole | null>(null);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedBoardId, setSelectedBoardId] = useState<number | null>(null);
  const [view, setView] = useState<"board" | "manage-users">("board");

  // Auth lives in localStorage, so it can only be read after mount — reading it
  // during render would not match the prerendered static export.
  useEffect(() => {
    const bootstrap = async () => {
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

  const handleAuthSuccess = (loggedInUsername: string, loggedInRole: UserRole) => {
    setUsername(loggedInUsername);
    setRole(loggedInRole);
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

  if (!username) {
    if (needsSetup) {
      return <SetupPage onSetupSuccess={handleAuthSuccess} />;
    }
    return <LoginPage onLoginSuccess={handleAuthSuccess} />;
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
        ) : (
          <KanbanWithSidebar boardId={selectedBoardId} />
        )}
      </div>
    </div>
  );
};
