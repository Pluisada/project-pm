import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ImportBatch, ImportBatchRevert, RevertBlocker, RevertPlan, RevertResult } from "@/lib/api";
import { BidImportRevertDialog } from "@/components/BidImportRevertDialog";
import { BidImportHistory } from "@/components/BidImportHistory";

const api = vi.hoisted(() => ({
  getRevertPreview: vi.fn(),
  revertBidImport: vi.fn(),
  listBidImports: vi.fn(),
  getBidImport: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }));

const FP = "a".repeat(64);

const plan = (over: Partial<RevertPlan> = {}): RevertPlan => ({
  batch_id: 7, status: "ready", reason: null, revertible: true, no_commercial_changes: false, ledger_version: 1,
  plan_fingerprint: FP,
  counts: { items: 3, cards_to_delete: 1, cards_to_restore: 1, moves_to_revert: 1, bid_details_to_delete: 1, already_deleted: 1, already_reverted: 0, observed: 0, blocked: 0 },
  blockers: [], dependent_batches: [],
  items: [
    { item_id: 1, card_id: 11, source_key: "B-1", action: "created", observation: null, classification: "delete", blockers: [],
      operations: [{ kind: "delete_card", card_id: 11, fields: null, from_column_id: null, to_column_id: null }] },
    { item_id: 2, card_id: 12, source_key: "B-2", action: "updated", observation: null, classification: "restore", blockers: [],
      operations: [
        { kind: "restore_fields", card_id: 12, fields: ["card.deal_value"], from_column_id: null, to_column_id: null },
        { kind: "move_card", card_id: 12, fields: null, from_column_id: 2, to_column_id: 1 },
      ] },
    { item_id: 3, card_id: 13, source_key: "B-3", action: "created", observation: null, classification: "already_deleted", blockers: [], operations: [] },
  ],
  ...over,
});

const blocker: RevertBlocker = { code: "field_modified_since", card_id: 12, field: "card.deal_value", imported: "1000", current: "2500", batch_ids: [] };

const result = (over: Partial<RevertResult> = {}): RevertResult => ({
  batch: {} as ImportBatch, outcome: "reverted", no_commercial_changes: false,
  counts: { cards_deleted: 1, cards_restored: 1, moves_reverted: 1, already_deleted: 1, bid_details_deleted: 1, already_reverted: 0, observed: 0 },
  ...over,
});

const apiError = (status_code: number, code?: string, detail = "x") => ({ status_code, detail, code, data: code ? { code } : undefined });

const open = async (onChanged = vi.fn(), onClose = vi.fn()) => {
  const user = userEvent.setup();
  render(<BidImportRevertDialog batchId={7} filename="bids.xlsx" onClose={onClose} onChanged={onChanged} />);
  return { user, onChanged, onClose };
};

const confirmButton = () => screen.getByRole("button", { name: /^Revert import$|^Reverting/ });

beforeEach(() => vi.resetAllMocks());

describe("BidImportRevertDialog", () => {
  it("shows the plan without conflicts and states that Undo does not cover it", async () => {
    api.getRevertPreview.mockResolvedValue(plan());
    await open();
    expect(await screen.findByText("Cards that will be deleted")).toBeInTheDocument();
    expect(screen.getByText("Fields that will be restored")).toBeInTheDocument();
    expect(screen.getByText("Moves that will be undone")).toBeInTheDocument();
    expect(screen.getAllByText("Already deleted")).toHaveLength(2);
    expect(screen.getByText(/cannot be undone with the Kanban Undo/)).toBeInTheDocument();
    expect(screen.getByText(/import the file again/)).toBeInTheDocument();
    expect(confirmButton()).toBeEnabled();
  });

  it("lists conflicts with values, reason and guidance, and disables confirmation", async () => {
    api.getRevertPreview.mockResolvedValue(plan({ status: "blocked", revertible: false, blockers: [blocker], dependent_batches: [{ batch_id: 9, card_ids: [12] }] }));
    await open();
    const table = await screen.findByRole("table", { name: "Conflicts" });
    expect(table).toHaveTextContent("Card #12");
    expect(table).toHaveTextContent("deal value");
    expect(table).toHaveTextContent("1000");
    expect(table).toHaveTextContent("2500");
    expect(table).toHaveTextContent("changed after the import");
    expect(screen.getByText(/Revert import #9 first/)).toBeInTheDocument();
    expect(confirmButton()).toBeDisabled();
  });

  it("refuses to confirm without a valid fingerprint", async () => {
    api.getRevertPreview.mockResolvedValue(plan({ plan_fingerprint: "nope" }));
    await open();
    await screen.findByText("Cards that will be deleted");
    expect(confirmButton()).toBeDisabled();
  });

  it("confirms with the previewed fingerprint, only then reports success, and refreshes the history", async () => {
    let finish!: (r: RevertResult) => void;
    api.getRevertPreview.mockResolvedValue(plan());
    api.revertBidImport.mockReturnValue(new Promise<RevertResult>((r) => (finish = r)));
    const { user, onChanged } = await open();
    await screen.findByText("Cards that will be deleted");
    await user.click(confirmButton());
    expect(api.revertBidImport).toHaveBeenCalledWith(7, FP);
    expect(screen.queryByText("Import reverted.")).not.toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
    finish(result());
    expect(await screen.findByText("Import reverted.")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("handles a batch that is already reverted", async () => {
    api.getRevertPreview.mockResolvedValue(plan({ status: "already_reverted", revertible: false, reason: "already_reverted" }));
    await open();
    expect(await screen.findByText("This import was already reverted.")).toBeInTheDocument();
    expect(confirmButton()).toBeDisabled();
  });

  it("handles the batch becoming reverted between preview and confirmation", async () => {
    api.getRevertPreview.mockResolvedValue(plan());
    api.revertBidImport.mockRejectedValue(apiError(409, "already_reverted"));
    const { user, onChanged } = await open();
    await screen.findByText("Cards that will be deleted");
    await user.click(confirmButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("already reverted");
    expect(confirmButton()).toBeDisabled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("reloads the preview after a stale fingerprint and keeps the explanation", async () => {
    const fresh = "b".repeat(64);
    api.getRevertPreview.mockResolvedValueOnce(plan()).mockResolvedValueOnce(plan({ plan_fingerprint: fresh }));
    api.revertBidImport.mockRejectedValue(apiError(409, "plan_changed"));
    const { user, onChanged } = await open();
    await screen.findByText("Cards that will be deleted");
    await user.click(confirmButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("The data changed since this preview");
    await waitFor(() => expect(api.getRevertPreview).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing was reverted");
    api.revertBidImport.mockResolvedValue(result());
    await waitFor(() => expect(confirmButton()).toBeEnabled());
    await user.click(confirmButton());
    expect(api.revertBidImport).toHaveBeenLastCalledWith(7, fresh);
    expect(await screen.findByText("Import reverted.")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("explains a busy database (503) on confirm and allows retrying", async () => {
    api.getRevertPreview.mockResolvedValue(plan());
    api.revertBidImport.mockRejectedValueOnce(apiError(503, "import_busy")).mockResolvedValue(result());
    const { user, onChanged } = await open();
    await screen.findByText("Cards that will be deleted");
    await user.click(confirmButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("The database is busy. Nothing was reverted.");
    expect(onChanged).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Import reverted.")).toBeInTheDocument();
  });

  it("explains a busy database on the preview", async () => {
    api.getRevertPreview.mockRejectedValue(apiError(503, "import_busy"));
    await open();
    expect(await screen.findByRole("alert")).toHaveTextContent("The database is busy");
    expect(screen.getByRole("button", { name: "Reload preview" })).toBeInTheDocument();
    expect(confirmButton()).toBeDisabled();
  });

  it("does not claim success or failure when the network drops during confirmation", async () => {
    api.getRevertPreview.mockResolvedValue(plan());
    api.revertBidImport.mockRejectedValue(apiError(0, undefined, "Network error - check connection"));
    const { user } = await open();
    await screen.findByText("Cards that will be deleted");
    await user.click(confirmButton());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("may or may not have been applied");
    expect(screen.queryByText("Import reverted.")).not.toBeInTheDocument();
    expect(confirmButton()).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check import history" })).toBeInTheDocument();
  });

  it("treats revert_outcome_unconfirmed (500) as unknown, not as success", async () => {
    api.getRevertPreview.mockResolvedValue(plan());
    api.revertBidImport.mockRejectedValue(apiError(500, "revert_outcome_unconfirmed"));
    const { user } = await open();
    await screen.findByText("Cards that will be deleted");
    await user.click(confirmButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be confirmed");
    expect(screen.queryByText("Import reverted.")).not.toBeInTheDocument();
    expect(confirmButton()).toBeDisabled();
  });

  it("says nothing changed on a generic 500", async () => {
    api.getRevertPreview.mockResolvedValue(plan());
    api.revertBidImport.mockRejectedValue(apiError(500, "revert_failed"));
    const { user } = await open();
    await screen.findByText("Cards that will be deleted");
    await user.click(confirmButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("The revert failed. Nothing was changed.");
  });

  it("shows a network error on the preview", async () => {
    api.getRevertPreview.mockRejectedValue(apiError(0, undefined, "Network error - check connection"));
    await open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
    expect(confirmButton()).toBeDisabled();
  });
});

describe("BidImportHistory revert state", () => {
  const batch = (over: Omit<Partial<ImportBatch>, "revert"> & { revert?: Partial<ImportBatchRevert> }): ImportBatch => ({
    id: 1, board_id: 1, username: "admin", filename: "a.xlsx", file_sha256: "x", status: "completed",
    failure_kind: null, failure_reason: null, created_count: 1, updated_count: 0, skipped_count: 0, review_count: 0,
    created_at: "2026-10-01T10:00:00Z", completed_at: "2026-10-01T10:01:00Z",
    ...over,
    revert: {
      has_ledger: true, revertible: true, reason: null, reverted: false, reverted_at: null, reverted_by_user_id: null,
      reverted_by_username: null, outcome: null, no_commercial_changes: false, cards_deleted: null, cards_restored: null,
      moves_reverted: null, already_deleted: null, summary: null, ...over.revert,
    },
  });

  it("distinguishes completed, reverted and no-change batches and offers Revert only when eligible", async () => {
    api.listBidImports.mockResolvedValue({
      total: 4, limit: 50, offset: 0,
      items: [
        batch({ id: 1 }),
        batch({ id: 2, revert: { revertible: false, reverted: true, reason: "already_reverted", reverted_by_username: "boss", reverted_at: "2026-10-02T10:00:00Z", outcome: "reverted" } }),
        batch({ id: 3, revert: { no_commercial_changes: true } }),
        batch({ id: 4, revert: { revertible: false, has_ledger: false, reason: "no_ledger" } }),
      ],
    });
    render(<BidImportHistory boardId={1} refreshKey={0} />);
    expect(await screen.findByText("Reverted")).toBeInTheDocument();
    expect(screen.getByText(/by boss/)).toBeInTheDocument();
    expect(screen.getByText("No commercial changes")).toBeInTheDocument();
    expect(screen.getAllByText("completed")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Revert import #1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revert import #3" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revert import #2" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revert import #4" })).not.toBeInTheDocument();
  });

  it("reloads the history after a confirmed revert", async () => {
    api.listBidImports.mockResolvedValueOnce({ total: 1, limit: 50, offset: 0, items: [batch({ id: 1 })] });
    api.getRevertPreview.mockResolvedValue(plan({ batch_id: 1 }));
    api.revertBidImport.mockResolvedValue(result());
    api.listBidImports.mockResolvedValue({
      total: 1, limit: 50, offset: 0,
      items: [batch({ id: 1, revert: { revertible: false, reverted: true, reverted_by_username: "boss", reverted_at: "2026-10-02T10:00:00Z", outcome: "reverted" } })],
    });
    const user = userEvent.setup();
    render(<BidImportHistory boardId={1} refreshKey={0} />);
    await user.click(await screen.findByRole("button", { name: "Revert import #1" }));
    await user.click(await screen.findByRole("button", { name: "Revert import" }));
    expect(await screen.findByText("Import reverted.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/by boss/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Revert import #1" })).not.toBeInTheDocument();
  });
});
