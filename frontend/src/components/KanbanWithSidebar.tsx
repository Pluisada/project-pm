"use client";

import { useState } from "react";
import { KanbanBoardAPI } from "@/components/KanbanBoardAPI";
import { AIChatSidebar } from "@/components/AIChatSidebar";

export const KanbanWithSidebar = ({ boardId }: { boardId: number }) => {
  // Changing the key remounts KanbanBoardAPI, which reloads the board
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="flex h-full overflow-hidden bg-white">
      <div className="flex-1 overflow-auto bg-white">
        <KanbanBoardAPI key={refreshKey} boardId={boardId} />
      </div>

      <div className="w-80 flex-shrink-0 border-l border-[var(--stroke)] bg-white">
        <AIChatSidebar
          boardId={boardId}
          onBoardChange={() => setRefreshKey((key) => key + 1)}
        />
      </div>
    </div>
  );
};
