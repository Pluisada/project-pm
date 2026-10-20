import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExcelDownloadButtons } from "@/components/ExcelDownloadButtons";
import * as api from "@/lib/api";

afterEach(() => vi.restoreAllMocks());

describe("ExcelDownloadButtons", () => {
  it.each(["sales_member", "visitor", "", "Admin"])("renders nothing for %s", (role) => {
    const { container } = render(<ExcelDownloadButtons companySlug="salcomp" boardId={1} role={role} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows both buttons to a company Admin", () => {
    render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="admin" />);
    expect(screen.getByRole("button", { name: "Export Current Database" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Import Template" })).toBeInTheDocument();
  });

  it("downloads both files for a super admin", async () => {
    const spy = vi.spyOn(api, "downloadCompanyExcel").mockResolvedValue("f.xlsx");
    render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    await userEvent.click(screen.getByRole("button", { name: "Download Import Template" }));
    expect(spy).toHaveBeenNthCalledWith(1, "salcomp", 4, "export", expect.any(AbortSignal));
    expect(spy).toHaveBeenNthCalledWith(2, "salcomp", 4, "template", expect.any(AbortSignal));
  });

  it("downloads both files for a company Admin, for their own company and board", async () => {
    const spy = vi.spyOn(api, "downloadCompanyExcel").mockResolvedValue("f.xlsx");
    render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Downloaded f.xlsx");
    await userEvent.click(screen.getByRole("button", { name: "Download Import Template" }));
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy).toHaveBeenNthCalledWith(1, "salcomp", 4, "export", expect.any(AbortSignal));
    expect(spy).toHaveBeenNthCalledWith(2, "salcomp", 4, "template", expect.any(AbortSignal));
  });

  it("disables the buttons while downloading", async () => {
    let finish!: (v: string) => void;
    vi.spyOn(api, "downloadCompanyExcel").mockReturnValue(new Promise((r) => (finish = r)));
    render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    expect(screen.getByRole("button", { name: "Downloading..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download Import Template" })).toBeDisabled();
    finish("f.xlsx");
    await waitFor(() => expect(screen.getByRole("button", { name: "Export Current Database" })).toBeEnabled());
  });

  it.each([
    [403, "You do not have permission to download this file."],
    [401, "Your session expired. Sign in again."],
    [0, "Network error - check your connection and try again."],
    [500, "HTTP 500"],
  ])("shows a clear message for status %i", async (status_code, text) => {
    vi.spyOn(api, "downloadCompanyExcel").mockRejectedValue({ status_code, detail: `HTTP ${status_code}` });
    render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it("re-enables the buttons after a failure and allows another attempt", async () => {
    const spy = vi.spyOn(api, "downloadCompanyExcel")
      .mockRejectedValueOnce({ status_code: 0, detail: "Network error - check connection" })
      .mockResolvedValueOnce("f.xlsx");
    render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export Current Database" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Download Import Template" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Downloaded f.xlsx");
    expect(spy).toHaveBeenCalledTimes(2);
  });

  // A download that rejects with AbortError when its signal fires, like fetch does.
  const abortable = () =>
    vi.spyOn(api, "downloadCompanyExcel").mockImplementation(
      (_c, _b, _k, signal) =>
        new Promise((_resolve, reject) => {
          signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        })
    );

  it("cancels the download when the board changes, without any message or new request", async () => {
    const spy = abortable();
    const { rerender } = render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    const signal = spy.mock.calls[0][3]!;
    expect(signal.aborted).toBe(false);
    rerender(<ExcelDownloadButtons companySlug="salcomp" boardId={5} role="super_admin" />);
    expect(signal.aborted).toBe(true);
    await waitFor(() => expect(screen.getByRole("button", { name: "Export Current Database" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Download Import Template" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("cancels the download when the company changes", async () => {
    const spy = abortable();
    const { rerender } = render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Download Import Template" }));
    const signal = spy.mock.calls[0][3]!;
    rerender(<ExcelDownloadButtons companySlug="acme" boardId={4} role="super_admin" />);
    expect(signal.aborted).toBe(true);
    await waitFor(() => expect(screen.getByRole("button", { name: "Download Import Template" })).toBeEnabled());
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("cancels on unmount", async () => {
    const spy = abortable();
    const { unmount } = render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    unmount();
    expect(spy.mock.calls[0][3]!.aborted).toBe(true);
  });

  it("shows no stale result when a download finishes after the context changed", async () => {
    let finish!: (v: string) => void;
    vi.spyOn(api, "downloadCompanyExcel").mockReturnValue(new Promise((r) => (finish = r)));
    const { rerender } = render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    rerender(<ExcelDownloadButtons companySlug="salcomp" boardId={5} role="super_admin" />);
    await act(async () => finish("old.xlsx"));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export Current Database" })).toBeEnabled();
  });

  it("treats an AbortError as expected, not as a failure, and allows a new download afterwards", async () => {
    const spy = vi.spyOn(api, "downloadCompanyExcel")
      .mockRejectedValueOnce(new DOMException("aborted", "AbortError"))
      .mockResolvedValueOnce("f.xlsx");
    render(<ExcelDownloadButtons companySlug="salcomp" boardId={4} role="super_admin" />);
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Export Current Database" })).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Export Current Database" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Downloaded f.xlsx");
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
