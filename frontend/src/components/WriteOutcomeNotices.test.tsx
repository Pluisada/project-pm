import { afterEach, describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WriteOutcomeNotices } from "@/components/WriteOutcomeNotices";
import { resetActiveCompanyForTests, setActiveCompany } from "@/lib/activeCompany";
import { resetWriteOutcomesForTests, trackWrite } from "@/lib/writeOutcomes";

const companies = [{ slug: "lity", name: "Lity", role: "super_admin" }];
const leave = (slug: string, method: string, endpoint: string, settle: (t: ReturnType<typeof trackWrite>) => void) => {
  setActiveCompany(slug);
  const tracked = trackWrite(slug, method, endpoint);
  setActiveCompany("salcomp");
  act(() => settle(tracked));
};

describe("WriteOutcomeNotices", () => {
  afterEach(() => {
    resetWriteOutcomesForTests();
    resetActiveCompanyForTests();
  });

  it("renders nothing when there is nothing to report", () => {
    const { container } = render(<WriteOutcomeNotices companies={companies} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says what happened for each kind of outcome, naming the company, and never claims success without an answer", () => {
    render(<WriteOutcomeNotices companies={companies} />);
    leave("lity", "PUT", "/api/companies/lity/boards/3/cards/7/position", (t) => t.saved());
    leave("lity", "DELETE", "/api/companies/lity/boards/3/cards/7", (t) => t.failed("Access denied"));
    leave("lity", "POST", "/api/companies/lity/boards/3/cards", (t) => t.unknown());
    expect(screen.getByRole("status")).toHaveTextContent('"Move card" in Lity finished saving after you left it');
    const alerts = screen.getAllByRole("alert");
    expect(alerts[0]).toHaveTextContent('"Delete card" in Lity was refused by the server: Access denied.');
    // A refusal says what the server answered, never that nothing changed there.
    expect(alerts[0]).not.toHaveTextContent(/Nothing changed|was not saved/);
    expect(alerts[1]).toHaveTextContent('"Create card" in Lity may or may not have been saved');
  });

  it("keeps an unknown outcome conservative: no refusal wording and no server text", () => {
    render(<WriteOutcomeNotices companies={companies} />);
    leave("lity", "POST", "/api/companies/lity/boards/3/cards", (t) => t.unknown());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("may or may not have been saved");
    expect(alert).not.toHaveTextContent(/refused|Nothing changed|saved after/);
  });

  it("falls back to the slug for a company the account no longer lists, and can be dismissed", async () => {
    render(<WriteOutcomeNotices companies={[]} />);
    leave("lity", "POST", "/api/companies/lity/boards", (t) => t.saved());
    expect(screen.getByRole("status")).toHaveTextContent('"Create board" in lity');
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
