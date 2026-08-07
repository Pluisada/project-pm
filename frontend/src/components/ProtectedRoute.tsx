"use client";

import { useEffect, useState } from "react";
import { getAuthState, logout } from "@/lib/auth";
import { listBoards, type BoardResponse } from "@/lib/api";
import { KanbanWithSidebar } from "@/components/KanbanWithSidebar";
import { LoginPage } from "@/components/LoginPage";

export const ProtectedRoute = () => {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [username, setUsername] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [boards, setBoards] = useState<BoardResponse[]>([]);
  const [selectedBoardId, setSelectedBoardId] = useState<number | null>(null);
  const [boardsLoading, setBoardsLoading] = useState(false);

  useEffect(() => {
    // Check if user is already logged in
    const auth = getAuthState();
    setIsAuthenticated(auth.isAuthenticated);
    setUsername(auth.username);
    setIsLoading(false);
  }, []);

  // Load boards when authenticated
  useEffect(() => {
    if (!isAuthenticated) return;

    const loadBoards = async () => {
      setBoardsLoading(true);
      try {
        const data = await listBoards();
        setBoards(data);
        // Auto-select first board (MVP has 1 board)
        if (data.length > 0 && !selectedBoardId) {
          setSelectedBoardId(data[0].id);
        }
      } catch (err) {
        console.error("Failed to load boards", err);
      } finally {
        setBoardsLoading(false);
      }
    };

    loadBoards();
  }, [isAuthenticated, selectedBoardId]);

  const handleLoginSuccess = (user: string) => {
    setUsername(user);
    setIsAuthenticated(true);
  };

  const handleLogout = async () => {
    await logout();
    setIsAuthenticated(false);
    setUsername(null);
    setBoards([]);
    setSelectedBoardId(null);
  };

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="inline-block">
            <div className="h-12 w-12 animate-spin rounded-full border-4 border-[var(--primary-blue)] border-t-transparent"></div>
          </div>
          <p className="mt-4 text-sm text-[var(--gray-text)]">Loading...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated || !username) {
    return <LoginPage onLoginSuccess={handleLoginSuccess} />;
  }

  if (boardsLoading || !selectedBoardId) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="inline-block">
            <div className="h-12 w-12 animate-spin rounded-full border-4 border-[var(--primary-blue)] border-t-transparent"></div>
          </div>
          <p className="mt-4 text-sm text-[var(--gray-text)]">Loading boards...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      {/* Header with user info and logout */}
      <div className="fixed right-0 top-0 z-50 flex items-center gap-4 p-4">
        <span className="text-sm text-[var(--gray-text)]">
          Welcome, <span className="font-semibold text-[var(--navy-dark)]">{username}</span>
        </span>
        <button
          onClick={handleLogout}
          className="rounded-lg bg-[var(--accent-yellow)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--navy-dark)] transition hover:opacity-80"
        >
          Logout
        </button>
      </div>

      {/* Kanban board with AI sidebar */}
      <KanbanWithSidebar boardId={selectedBoardId} />
    </div>
  );
};
