import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralCardsImport } from "@/components/CentralCardsImport";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const caps = { readOnly: false, canEditOwnCards: true, canEditAnyCard: true, canMoveBack: true, canDeleteCards: true,
  canAssignSalesperson: true, canManageBoards: true, canImport: true };

const issue = (row: number | null, column: string | null, code: string, message: string) => ({ row, column, code, message });
const row = (n: number, action: string, extra: Record<string, unknown> = {}) => ({
  row: n, action, card_id: null, title: `Lead ${n}`, stage: "First", changed: [], diffs: [], errors: [], warnings: [], ...extra,
});
const summary = { import_id: 3, created: 2, updated: 1, unchanged: 0, applied_at: "2026-10-20T10:00:00" };
const okPreview = {
  filename: "cards.xlsx", file_sha256: "x",
  summary: { rows: 3, creates: 2, updates: 1, unchanged: 0, invalid: 0, warnings: 0 },
  rows: [row(2, "create"), row(3, "create"), row(4, "update", { card_id: 9, changed: ["title", "stage"], diffs: [
    { field: "title", old: "Old", new: "New" }, { field: "stage", old: "First", new: "Second" }] })],
  file_warnings: [], confirmable: true, blocked_reason: null, already_imported: null, previous_import: null, plan_digest: "digest-1",
};

type Handler = (url: string, form: FormData | null) => Response | Promise<Response>;
function setup(opts: { role?: string; capabilities?: Partial<typeof caps>; me?: () => Response; preview?: Handler; confirm?: Handler } = {}) {
  const calls: { kind: "me" | "preview" | "confirm"; url: string; form: FormData | null }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const form = init?.body instanceof FormData ? init.body : null;
    if (url.endsWith("/me")) {
      calls.push({ kind: "me", url, form });
      return opts.me ? opts.me() : json(200, { user_id: 1, company_slug: "acme", role: opts.role ?? "super_admin", capabilities: { ...caps, ...opts.capabilities } });
    }
    if (url.endsWith("/excel/import/preview")) {
      calls.push({ kind: "preview", url, form });
      return (opts.preview ?? (() => json(200, okPreview)))(url, form);
    }
    if (url.endsWith("/excel/import/confirm")) {
      calls.push({ kind: "confirm", url, form });
      return (opts.confirm ?? (() => json(201, { ...summary, applied: true })))(url, form);
    }
    return json(404);
  }));
  const onImported = vi.fn();
  const view = render(<CentralCardsImport companySlug="acme" boardId={4} onImported={onImported} />);
  return { calls, onImported, ...view, of: (kind: string) => calls.filter((c) => c.kind === kind) };
}

const workbook = () => new File(["x"], "cards.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
async function chooseAndPreview(user = userEvent.setup()) {
  await screen.findByLabelText("Excel workbook (.xlsx)");
  await user.upload(screen.getByLabelText("Excel workbook (.xlsx)"), workbook());
  await user.click(screen.getByRole("button", { name: "Preview import" }));
  return user;
}

beforeEach(() => vi.useRealTimers());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("role visibility", () => {
  it.each([
    ["sales member", { canImport: false }],
    ["visitor", { canImport: false, readOnly: true }],
    ["read-only even with canImport", { readOnly: true }],
  ])("shows nothing for a %s", async (_label, capabilities) => {
    const { container, of } = setup({ capabilities });
    await waitFor(() => expect(of("me")).toHaveLength(1));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing when the capabilities cannot be read", async () => {
    const { container, of } = setup({ me: () => json(500) });
    await waitFor(() => expect(of("me")).toHaveLength(1));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the controls to someone who can import", async () => {
    setup();
    expect(await screen.findByRole("heading", { name: "Import Cards from Excel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview import" })).toBeDisabled(); // no file yet
  });
});

describe("preview", () => {
  it("sends the chosen file to the board's preview endpoint and shows creates and updates apart", async () => {
    const { of } = setup();
    await chooseAndPreview();
    const region = await screen.findByRole("region", { name: "Import preview" });
    expect(of("preview")).toHaveLength(1);
    expect(of("preview")[0].url).toContain("/api/companies/acme/boards/4/excel/import/preview");
    expect((of("preview")[0].form!.get("file") as File).name).toBe("cards.xlsx");
    expect(of("confirm")).toHaveLength(0); // a preview never confirms
    const table = within(region).getByRole("table", { name: /create or update/i });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("Create")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Update")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Old -> New")).toBeInTheDocument();
    expect(within(rows[2]).getByText("First -> Second")).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Confirm import (2 new, 1 updated)" })).toBeEnabled();
  });

  it("lists errors and warnings with their row and column, and blocks the confirmation", async () => {
    setup({
      preview: () => json(200, {
        ...okPreview, confirmable: false, blocked_reason: "validation_errors",
        summary: { ...okPreview.summary, invalid: 1, warnings: 2 },
        rows: [row(2, "invalid", { errors: [issue(2, "stage", "unknown_stage", "Stage is not a column title of this board")] }),
          row(3, "create", { warnings: [issue(3, "stage", "stage_defaulted", 'Empty stage: the card goes to "First"')] })],
        file_warnings: [issue(null, "lead_source", "missing_optional_column", "Optional column absent; the field stays empty")],
      }),
    });
    await chooseAndPreview();
    await screen.findByRole("region", { name: "Import preview" });
    expect(screen.getByText("Row 2, stage: Stage is not a column title of this board")).toBeInTheDocument();
    expect(screen.getByText(/Row 3, stage: Empty stage/)).toBeInTheDocument();
    expect(screen.getByText("File, lead_source: Optional column absent; the field stays empty")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirm import/ })).toBeDisabled();
  });

  it("explains a workbook that was already imported and cannot be confirmed", async () => {
    setup({ preview: () => json(200, { ...okPreview, confirmable: false, blocked_reason: "already_imported", already_imported: summary }) });
    await chooseAndPreview();
    expect(await screen.findByRole("alert")).toHaveTextContent(/already imported .* cannot be confirmed again, even if its cards were changed or deleted/);
    expect(screen.getByRole("button", { name: /Confirm import/ })).toBeDisabled();
  });

  it("shows the server's reason when the file is refused", async () => {
    setup({ preview: () => json(422, { detail: { code: "file_rejected", message: "The file can't be imported", file_errors: [{ message: "The workbook has no \"Cards\" sheet" }] } }) });
    await chooseAndPreview();
    expect(await screen.findByRole("alert")).toHaveTextContent('The file can\'t be imported: The workbook has no "Cards" sheet');
    expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument();
  });

  it("shows a network failure of the preview and allows another preview", async () => {
    let n = 0;
    const { of } = setup({ preview: () => { if (n++ === 0) throw new TypeError("failed to fetch"); return json(200, okPreview); } });
    const user = await chooseAndPreview();
    expect(await screen.findByRole("alert")).toHaveTextContent("Network error");
    await user.click(screen.getByRole("button", { name: "Preview import" }));
    expect(await screen.findByRole("region", { name: "Import preview" })).toBeInTheDocument();
    expect(of("preview")).toHaveLength(2);
  });

  it("drops the preview when another file is chosen", async () => {
    setup();
    const user = await chooseAndPreview();
    await screen.findByRole("region", { name: "Import preview" });
    await user.upload(screen.getByLabelText("Excel workbook (.xlsx)"), new File(["y"], "other.xlsx"));
    expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument();
  });
});

describe("confirmation", () => {
  it("needs an explicit click, sends the preview's digest once, and shows the result", async () => {
    const { of, onImported } = setup();
    const user = await chooseAndPreview();
    await screen.findByRole("region", { name: "Import preview" });
    expect(of("confirm")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: /Confirm import/ }));
    const result = await screen.findByRole("region", { name: "Import result" });
    expect(result).toHaveTextContent("2 created, 1 updated, 0 unchanged");
    expect(of("confirm")).toHaveLength(1);
    const form = of("confirm")[0].form!;
    expect(form.get("confirm")).toBe("true");
    expect(form.get("plan_digest")).toBe("digest-1");
    expect((form.get("file") as File).name).toBe("cards.xlsx");
    expect(onImported).toHaveBeenCalledTimes(1);
  });

  it("sends one request for a double click", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const { of } = setup({ confirm: async () => { await gate; return json(201, { ...summary, applied: true }); } });
    const user = await chooseAndPreview();
    const button = await screen.findByRole("button", { name: /Confirm import/ });
    await user.dblClick(button);
    expect(screen.getByRole("button", { name: "Importing..." })).toBeDisabled();
    release();
    await screen.findByRole("region", { name: "Import result" });
    expect(of("confirm")).toHaveLength(1);
  });

  it("keeps the preview and says nothing changed when the server reports a definite failure, never retrying by itself", async () => {
    const { of } = setup({ confirm: () => json(500, { detail: { code: "import_failed", message: "The import failed and nothing was changed", applied: false } }) });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was changed; you may confirm again.");
    await new Promise((r) => setTimeout(r, 50));
    expect(of("confirm")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Confirm import/ })).toBeEnabled();
  });

  it("treats a lost response as an unknown outcome: no confirm button, no automatic retry", async () => {
    const { of, onImported } = setup({ confirm: () => { throw new TypeError("failed to fetch"); } });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    expect(await screen.findByText("Result unknown - do not import again yet")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("may or may not have been applied");
    expect(screen.queryByRole("button", { name: /Confirm import/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview import" })).toBeDisabled();
    await new Promise((r) => setTimeout(r, 50));
    expect(of("confirm")).toHaveLength(1);
    expect(onImported).not.toHaveBeenCalled();
  });

  it("treats a 5xx without 'applied: false' as an unknown outcome too", async () => {
    setup({ confirm: () => json(502, {}) });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    expect(await screen.findByText("Result unknown - do not import again yet")).toBeInTheDocument();
  });

  it("'Check result' only previews, and reports an import that was applied", async () => {
    let previews = 0;
    const { of, onImported } = setup({
      preview: () => (previews++ === 0 ? json(200, okPreview)
        : json(200, { ...okPreview, confirmable: false, blocked_reason: "already_imported", already_imported: summary })),
      confirm: () => { throw new TypeError("failed to fetch"); },
    });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    await user.click(await screen.findByRole("button", { name: "Check result" }));
    const result = await screen.findByRole("region", { name: "Import result" });
    expect(result).toHaveTextContent("2 created, 1 updated, 0 unchanged");
    expect(screen.getByRole("status")).toHaveTextContent("The import was applied on");
    expect(of("confirm")).toHaveLength(1); // the check never confirms
    expect(onImported).toHaveBeenCalledTimes(1);
  });

  it("'Check result' recognises an applied update-only import from the ledger even though the preview still proposes changes", async () => {
    let previews = 0;
    setup({
      preview: () => (previews++ === 0 ? json(200, okPreview)
        : json(200, { ...okPreview, confirmable: false, blocked_reason: "already_imported", already_imported: summary,
          previous_import: summary, summary: { ...okPreview.summary, creates: 0, updates: 1, unchanged: 0 } })),
      confirm: () => { throw new TypeError("failed to fetch"); },
    });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    await user.click(await screen.findByRole("button", { name: "Check result" }));
    expect(await screen.findByRole("status")).toHaveTextContent("The import was applied on");
    expect(screen.queryByRole("button", { name: /Confirm import/ })).not.toBeInTheDocument();
  });

  it("'Check result' with no ledger record says so, without claiming more, and offers an explicit confirmation", async () => {
    const { of } = setup({ confirm: () => { throw new TypeError("failed to fetch"); } });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    await user.click(await screen.findByRole("button", { name: "Check result" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("no record of this workbook being applied");
    expect(status).not.toHaveTextContent("was not applied");
    expect(of("confirm")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Confirm import/ })).toBeEnabled();
  });

  it("presents import_not_initialized as a definite non-application and offers no confirmation", async () => {
    const { of } = setup({ confirm: () => json(503, { detail: { code: "import_not_initialized", message: "The Excel import is not set up for this company yet", applied: false } }) });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not set up for this company yet. Nothing was imported.");
    expect(screen.queryByText("Result unknown - do not import again yet")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Confirm import/ })).not.toBeInTheDocument();
    expect(of("confirm")).toHaveLength(1);
  });

  it("presents import_ledger_incompatible as a definite non-application that needs an administrator, with no retry", async () => {
    const { of } = setup({ confirm: () => json(503, { detail: { code: "import_ledger_incompatible", message: "x", applied: false } }) });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/unexpected structure. Nothing was imported. An administrator must investigate/);
    expect(screen.queryByText("Result unknown - do not import again yet")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Confirm import/ })).not.toBeInTheDocument();
    expect(of("confirm")).toHaveLength(1);
  });

  it("presents import_ledger_incompatible on the preview the same way", async () => {
    setup({ preview: () => json(503, { detail: { code: "import_ledger_incompatible", message: "x", applied: false } }) });
    await chooseAndPreview();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Nothing was imported. An administrator must investigate/);
  });

  it("presents import_not_initialized on the preview as nothing imported", async () => {
    setup({ preview: () => json(503, { detail: { code: "import_not_initialized", message: "x", applied: false } }) });
    await chooseAndPreview();
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was imported.");
  });

  it("shows current and proposed values for destructive changes, with safe wording for empty and unassigned", async () => {
    setup({
      preview: () => json(200, { ...okPreview, rows: [row(2, "update", { card_id: 1, changed: ["salesperson_id", "deal_value", "deal_value_next_year", "company"], diffs: [
        { field: "salesperson_id", old: "ana@example.com", new: null },
        { field: "deal_value", old: "5.00", new: null },
        { field: "deal_value_next_year", old: "7.00", new: "0.00" },
        { field: "company", old: "Acme", new: "" },
      ] })] }),
    });
    await chooseAndPreview();
    const region = await screen.findByRole("region", { name: "Import preview" });
    expect(within(region).getByText("ana@example.com -> (unassigned)")).toBeInTheDocument();
    expect(within(region).getByText("5.00 -> (empty)")).toBeInTheDocument();
    expect(within(region).getByText("7.00 -> 0.00")).toBeInTheDocument();
    expect(within(region).getByText("Acme -> (empty)")).toBeInTheDocument();
  });

  it("tells the person when the workbook was already imported and asks for a new preview", async () => {
    const { of } = setup({ confirm: () => json(409, { detail: { code: "already_imported", message: "This workbook was already imported on this board", import_summary: summary } }) });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already imported on .*2 created, 1 updated, 0 unchanged\) and cannot be applied again. Nothing was changed now/);
    expect(screen.queryByRole("button", { name: /Confirm import/ })).not.toBeInTheDocument();
    expect(of("confirm")).toHaveLength(1);
  });

  it("drops a stale preview instead of confirming it again", async () => {
    setup({ confirm: () => json(409, { detail: { code: "preview_stale", message: "The workbook or the board changed since the preview; build the preview again" } }) });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("changed since the preview");
    expect(screen.queryByRole("button", { name: /Confirm import/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview import" })).toBeEnabled();
  });

  it.each([[401, "Your session expired"], [403, "do not have permission"]])("explains a %i answer", async (status, text) => {
    setup({ confirm: () => json(status, { detail: "x" }) });
    const user = await chooseAndPreview();
    await user.click(await screen.findByRole("button", { name: /Confirm import/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });
});

describe("accessibility and keyboard", () => {
  it("can be driven with the keyboard and moves focus to the preview heading", async () => {
    const { of } = setup();
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Excel workbook (.xlsx)");
    await user.upload(input, workbook());
    screen.getByRole("button", { name: "Preview import" }).focus();
    await user.keyboard("{Enter}");
    const heading = await screen.findByRole("heading", { name: "Preview of cards.xlsx" });
    await waitFor(() => expect(heading).toHaveFocus());
    const confirmButton = screen.getByRole("button", { name: /Confirm import/ });
    confirmButton.focus();
    await user.keyboard(" ");
    await screen.findByRole("region", { name: "Import result" });
    expect(of("confirm")).toHaveLength(1);
  });

  it("labels the file input, the regions and the table columns", async () => {
    setup();
    await chooseAndPreview();
    const region = await screen.findByRole("region", { name: "Import preview" });
    expect(screen.getByLabelText("Excel workbook (.xlsx)")).toHaveAttribute("type", "file");
    expect(screen.getByRole("region", { name: "Import Cards from Excel" })).toBeInTheDocument();
    for (const name of ["Row", "Operation", "Card", "Stage", "Changes"]) {
      expect(within(region).getByRole("columnheader", { name })).toBeInTheDocument();
    }
  });
});
