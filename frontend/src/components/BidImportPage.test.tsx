import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ImportPreview, ImportPreviewRow } from "@/lib/api";
import { BidImportPage } from "@/components/BidImportPage";

const api = vi.hoisted(() => ({
  getBoard: vi.fn(),
  previewBidImport: vi.fn(),
  confirmBidImport: vi.fn(),
  listBidImports: vi.fn(),
  getBidImport: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }));

const row = (over: Partial<ImportPreviewRow>): ImportPreviewRow => ({
  row_number: 2, code: "B1", customer: "Acme", project_name: "Charger", source_status: "Open", source_stage: "Quote",
  action: "create",
  classification: { status: "mapped", target: null, proposed: null },
  target_stage: "RFI", reasons: [], errors: [], warnings: [], required_decisions: [], duplicate_rows: [],
  existing_card_id: null, changed_fields: [], stage_change: null, move_confirmed: false,
  ...over,
});

const preview = (rows: ImportPreviewRow[], over: Partial<ImportPreview> = {}): ImportPreview => ({
  file: { filename: "bids.xlsx", size_bytes: 10, sha256: "x" },
  sheet: null, file_warnings: [], notice: null, confirmable: true, preview_token: "tok-1", expires_in_minutes: 30,
  summary: {
    total_rows: rows.length, valid_rows: rows.length, rows_with_errors: 0, rows_with_warnings: 0,
    proposed_creates: 1, proposed_updates: 0, unconfirmed_updates: 0, unchanged: 0, internal_duplicate_rows: 0,
    pending_review: 1, ignored: 0, by_target_stage: {}, by_classification: {}, by_action: {},
    revenue_cents_by_currency: {},
  },
  rows, ...over,
});

const pendingRow = row({
  row_number: 3, action: "review", target_stage: null, required_decisions: ["choose_stage"],
  classification: { status: "needs_review", target: null, proposed: null },
});

const upload = async () => {
  const user = userEvent.setup();
  render(<BidImportPage boardId={1} />);
  await user.upload(
    screen.getByLabelText("Bids report (.xlsx)"),
    new File(["x"], "bids.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" })
  );
  await user.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText("3. Rows and decisions");
  return user;
};

beforeEach(() => {
  vi.resetAllMocks();
  api.getBoard.mockResolvedValue({ columns: [{ title: "RFI", position: 1 }, { title: "1st Contact", position: 0 }] });
  api.listBidImports.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
});

describe("BidImportPage", () => {
  it("previews without decisions, then needs a fresh preview after a decision changes", async () => {
    api.previewBidImport.mockResolvedValue(preview([row({}), pendingRow]));
    const user = await upload();
    expect(api.previewBidImport).toHaveBeenCalledWith(1, expect.any(File), []);

    await user.selectOptions(screen.getByLabelText("Stage for row 3"), "RFI");
    expect(screen.getByText(/Your decisions changed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /I reviewed/ })).toBeDisabled();
  });

  it("confirms with the token and decisions of the current preview", async () => {
    api.previewBidImport
      .mockResolvedValueOnce(preview([row({}), pendingRow]))
      .mockResolvedValueOnce(preview([row({}), row({ row_number: 3, target_stage: "RFI" })], { preview_token: "tok-2" }));
    api.confirmBidImport.mockResolvedValue({
      batch: { id: 7 }, created_card_ids: [1, 2], updated_card_ids: [], moved_card_ids: [], skipped: 0, review: 0,
    });
    const user = await upload();

    await user.selectOptions(screen.getByLabelText("Stage for row 3"), "RFI");
    await user.click(screen.getByRole("button", { name: "Update preview" }));
    await waitFor(() => expect(api.previewBidImport).toHaveBeenCalledTimes(2));
    expect(api.previewBidImport).toHaveBeenLastCalledWith(1, expect.any(File), [{ row_number: 3, target_stage: "RFI" }]);

    await user.click(await screen.findByRole("checkbox", { name: /I reviewed/ }));
    await user.click(screen.getByRole("button", { name: "Confirm import" }));
    await waitFor(() =>
      expect(api.confirmBidImport).toHaveBeenCalledWith(1, expect.any(File), [{ row_number: 3, target_stage: "RFI" }], "tok-2")
    );
    expect(await screen.findByText(/Import #7 completed/)).toBeInTheDocument();
  });

  it("blocks confirmation when the backend says the preview is not confirmable", async () => {
    api.previewBidImport.mockResolvedValue(preview([row({})], { confirmable: false, preview_token: null }));
    await upload();
    expect(screen.getByText(/can't be confirmed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeDisabled();
  });

  it("shows the backend's error and drops the preview when confirmation is refused", async () => {
    api.previewBidImport.mockResolvedValue(preview([row({})]));
    api.confirmBidImport.mockRejectedValue({ status_code: 409, detail: "The data changed since the preview; build it again" });
    const user = await upload();
    await user.click(screen.getByRole("checkbox", { name: /I reviewed/ }));
    await user.click(screen.getByRole("button", { name: "Confirm import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The data changed since the preview");
    expect(screen.queryByText("4. Confirm")).not.toBeInTheDocument();
  });

  it("shows a failed batch's reason in the history details", async () => {
    api.listBidImports.mockResolvedValue({
      items: [{ id: 4, filename: "old.xlsx", status: "failed", failure_kind: "conflict", username: "admin",
        created_at: "2026-10-14T10:00:00Z", completed_at: null, created_count: 0, updated_count: 0, skipped_count: 0, review_count: 0 }],
      total: 1, limit: 50, offset: 0,
    });
    api.getBidImport.mockResolvedValue({
      id: 4, filename: "old.xlsx", status: "failed", failure_kind: "conflict", failure_reason: "stage renamed",
      username: "admin", file_sha256: "abc", created_at: "2026-10-14T10:00:00Z", completed_at: null,
      created_count: 0, updated_count: 0, skipped_count: 0, review_count: 0, mapping: null, card_changes_count: 0,
    });
    const user = userEvent.setup();
    render(<BidImportPage boardId={1} />);
    await user.click(screen.getByRole("tab", { name: "History" }));
    await user.click(await screen.findByRole("button", { name: "Details" }));
    expect(await screen.findByText("stage renamed")).toBeInTheDocument();
    expect(screen.getByText(/Nothing was written/)).toBeInTheDocument();
  });
});
