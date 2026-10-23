import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KanbanWithSidebar } from "@/components/KanbanWithSidebar";

const api = vi.hoisted(() => ({ sendAIMessage: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }));
vi.mock("@/components/KanbanBoardAPI", async () => {
  const { useState } = await import("react");
  return {
    KanbanBoardAPI: () => {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button onClick={() => setOpen(true)}>Open details</button>
          {open && (
            <div role="dialog" aria-modal="true">
              details
              <button onClick={() => setOpen(false)}>Close details</button>
            </div>
          )}
        </div>
      );
    },
  };
});

describe("KanbanWithSidebar responsive drawer", () => {
  // jsdom does not implement scrollIntoView, which the chat calls on new messages.
  Element.prototype.scrollIntoView = vi.fn();

  it("keeps the board reachable and opens and closes the drawer with the controls", async () => {
    const user = userEvent.setup();
    render(<KanbanWithSidebar boardId={1} role="member" />);

    expect(screen.getByRole("button", { name: "Open details" })).toBeInTheDocument();
    expect(screen.queryByTestId("ai-sidebar-backdrop")).not.toBeInTheDocument();
    expect(document.getElementById("ai-sidebar")).toHaveClass("max-lg:invisible");

    const opener = screen.getByRole("button", { name: "Open AI Assistant" });
    await user.click(opener);
    expect(document.getElementById("ai-sidebar")).not.toHaveClass("max-lg:invisible");
    expect(screen.getByRole("button", { name: "Close AI Assistant" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Close AI Assistant" }));
    expect(document.getElementById("ai-sidebar")).toHaveClass("max-lg:invisible");
    expect(screen.getByRole("button", { name: "Open AI Assistant" })).toHaveFocus();
  });

  it("closes on Escape and on backdrop click", async () => {
    const user = userEvent.setup();
    render(<KanbanWithSidebar boardId={1} role="member" />);

    await user.click(screen.getByRole("button", { name: "Open AI Assistant" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("ai-sidebar-backdrop")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open AI Assistant" }));
    await user.click(screen.getByTestId("ai-sidebar-backdrop"));
    expect(screen.queryByTestId("ai-sidebar-backdrop")).not.toBeInTheDocument();
  });

  it("does not close the drawer on Escape while a dialog is open", async () => {
    const user = userEvent.setup();
    render(<KanbanWithSidebar boardId={1} role="member" />);

    await user.click(screen.getByRole("button", { name: "Open AI Assistant" }));
    // The drawer's backdrop covers the board in a browser; open the dialog directly.
    await user.click(screen.getByRole("button", { name: "Open details", hidden: true }));
    await user.keyboard("{Escape}");
    expect(screen.getByTestId("ai-sidebar-backdrop")).toBeInTheDocument();
  });

  it("keeps the conversation when the drawer is closed and reopened", async () => {
    const user = userEvent.setup();
    api.sendAIMessage.mockResolvedValue({ response: "Done", actions_applied: null });
    render(<KanbanWithSidebar boardId={1} role="member" />);

    await user.click(screen.getByRole("button", { name: "Open AI Assistant" }));
    await user.type(screen.getByPlaceholderText(/Ask me to organize/), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Done")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close AI Assistant" }));
    await user.click(screen.getByRole("button", { name: "Open AI Assistant" }));
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(api.sendAIMessage).toHaveBeenCalledWith(1, "hello", []);
  });
});
