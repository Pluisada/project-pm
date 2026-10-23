"use client";

import { useEffect, useRef, useState } from "react";
import { KanbanBoardAPI } from "@/components/KanbanBoardAPI";
import { AIChatSidebar } from "@/components/AIChatSidebar";
import type { UserRole } from "@/lib/auth";

export const KanbanWithSidebar = ({ boardId, role }: { boardId: number; role: UserRole }) => {
  // Changing the key remounts KanbanBoardAPI, which reloads the board
  const [refreshKey, setRefreshKey] = useState(0);
  // Below lg the sidebar is a drawer; from lg up it is the right-hand column.
  // Either way it starts closed, and stays mounted so the conversation survives closing it.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const openRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    if (drawerOpen) {
      closeRef.current?.focus();
    } else if (wasOpen.current) {
      openRef.current?.focus();
    }
    wasOpen.current = drawerOpen;
  }, [drawerOpen]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      // The card-details dialog handles its own Escape; leave the drawer open then.
      if (event.key === "Escape" && !document.querySelector('[role="dialog"]')) {
        setDrawerOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

  return (
    <div className="relative flex h-full overflow-hidden bg-[var(--surface-strong)]">
      <div className="flex-1 overflow-auto bg-[var(--surface-strong)]">
        <KanbanBoardAPI key={refreshKey} boardId={boardId} role={role} />
      </div>

      {!drawerOpen && (
        <button
          ref={openRef}
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open AI Assistant"
          aria-expanded={false}
          aria-controls="ai-sidebar"
          className="ai-launcher fixed bottom-4 right-4 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--secondary-purple)] text-white shadow-[var(--shadow)] transition hover:opacity-90"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
      )}

      {drawerOpen && (
        <div
          data-testid="ai-sidebar-backdrop"
          onClick={() => setDrawerOpen(false)}
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
        />
      )}

      <aside
        id="ai-sidebar"
        aria-label="AI Assistant"
        className={`border-l border-[var(--stroke)] bg-[var(--surface-strong)] max-lg:fixed max-lg:inset-y-0 max-lg:right-0 max-lg:z-40 max-lg:w-[min(20rem,100vw)] max-lg:shadow-[var(--shadow)] lg:w-80 lg:flex-shrink-0 ${
          drawerOpen ? "" : "max-lg:invisible lg:hidden"
        }`}
      >
        <AIChatSidebar
          boardId={boardId}
          onClose={() => setDrawerOpen(false)}
          closeRef={closeRef}
          onBoardChange={() => setRefreshKey((key) => key + 1)}
        />
      </aside>
    </div>
  );
};
