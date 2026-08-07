"use client";

import { useState, useCallback } from "react";
import { KanbanBoardAPI } from "@/components/KanbanBoardAPI";
import { AIChatSidebar } from "@/components/AIChatSidebar";
import { type BoardDetail } from "@/lib/api";

export const KanbanWithSidebar = ({ boardId }: { boardId: number }) => {
  const [refreshKey, setRefreshKey] = useState(0);

  const handleBoardUpdate = useCallback((board: BoardDetail) => {
    // Increment refresh key to force KanbanBoardAPI to reload
    setRefreshKey((prev) => prev + 1);
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      {/* Main Kanban Board */}
      <div className="flex-1 overflow-auto bg-white">
        <KanbanBoardAPI key={refreshKey} boardId={boardId} />
      </div>

      {/* AI Chat Sidebar */}
      <div className="w-80 flex-shrink-0 border-l border-[var(--stroke)] bg-white">
        <AIChatSidebar boardId={boardId} onBoardUpdate={handleBoardUpdate} />
      </div>
    </div>
  );
};
