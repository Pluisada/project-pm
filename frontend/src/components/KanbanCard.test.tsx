import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DndContext } from "@dnd-kit/core";
import { SortableContext } from "@dnd-kit/sortable";
import { KanbanCard } from "@/components/KanbanCard";
import type { Card } from "@/lib/kanban";
import { dealValueTotal, formatMoney } from "@/lib/format";

const show = (details: string, lead?: Card["lead"]) =>
  render(
    <DndContext>
      <SortableContext items={["card-1"]}>
        <KanbanCard
          card={{ id: "card-1", title: "Opp", details, lead }}
          canDelete={false}
          onDelete={() => {}}
          onOpenHistory={() => {}}
          onOpenDetails={() => {}}
        />
      </SortableContext>
    </DndContext>
  );

describe("KanbanCard details", () => {
  it("shows details read-only, keeping line breaks and clamping the display to three lines", () => {
    show("line one\nline two");
    const text = screen.getByTestId("card-details-text");
    expect(text.textContent).toBe("line one\nline two");
    expect(text.className).toContain("whitespace-pre-wrap");
    expect(text.className).toContain("line-clamp-3");
    expect(document.querySelectorAll("input, textarea")).toHaveLength(0);
  });

  it("renders nothing for empty details", () => {
    show("");
    expect(screen.queryByTestId("card-details-text")).toBeNull();
  });
});

describe("KanbanCard Deal Value", () => {
  const lead = (current: number | null, nextYear: number): Card["lead"] => ({
    company: null, contact: null, dealValue: dealValueTotal(current, nextYear),
    expectedCloseDate: null, productLine: null, salespersonName: null, overdue: false,
    nextStep: null, nextStepDue: null, nextStepOverdue: false, sourceCode: null,
  });

  it("shows the sum of both annual values as one amount", () => {
    show("", lead(100000, 50000));
    expect(screen.getByText(formatMoney(150000, "USD"))).toBeInTheDocument();
    expect(screen.queryByText(formatMoney(50000, "USD"))).toBeNull();
  });

  it("shows just the current year when next year is zero", () => {
    show("", lead(100000, 0));
    expect(screen.getByText(formatMoney(100000, "USD"))).toBeInTheDocument();
  });

  it("shows the amount in USD even though the lead carries no currency", () => {
    show("", lead(500, 0));
    expect(screen.getByText("$500")).toBeInTheDocument();
  });

  it("hides an unset value", () => {
    show("", lead(null, 0));
    expect(screen.queryByText(/\$/)).toBeNull();
  });
});

describe("KanbanCard Next Action", () => {
  const lead = (nextStep: string | null, nextStepDue: string | null): Card["lead"] => ({
    company: null, contact: null, dealValue: null, expectedCloseDate: null, productLine: null,
    salespersonName: null, overdue: false, nextStep, nextStepDue, nextStepOverdue: false, sourceCode: null,
  });

  it("shows the Action and Next Action Date, truncating only the display", () => {
    const long = "x".repeat(100);
    show("", lead(long, "2026-10-23"));
    const action = screen.getByText(long);
    expect(action.className).toContain("truncate");
    expect(action.textContent).toBe(long);
    expect(screen.getByText(/23/)).toBeInTheDocument();
  });

  it("shows a date without an Action, and nothing when both are absent", () => {
    const { unmount } = show("", lead(null, "2026-10-23"));
    expect(screen.getByText("Next:")).toBeInTheDocument();
    unmount();
    show("", lead(null, null));
    expect(screen.queryByText("Next:")).toBeNull();
  });
});

describe("KanbanCard probability level", () => {
  const lead = (confidenceLevel: "LOW" | "MEDIUM" | "HIGH" | null): Card["lead"] => ({
    company: null, contact: null, dealValue: null, expectedCloseDate: null, productLine: null,
    confidenceLevel, salespersonName: null, overdue: false, nextStep: null, nextStepDue: null,
    nextStepOverdue: false, sourceCode: null,
  });

  it.each([["LOW"], ["MEDIUM"], ["HIGH"]] as const)("shows %s as a coloured indicator with its text", (level) => {
    show("", lead(level));
    const badge = screen.getByTestId("confidence-badge");
    expect(badge).toHaveTextContent(level);
    expect(badge).toHaveAttribute("data-level", level);
    expect(badge.className).toContain(`--conf-${level.toLowerCase()}-`);
  });

  it("shows no indicator when no level is assigned", () => {
    show("", lead(null));
    expect(screen.queryByTestId("confidence-badge")).toBeNull();
  });

  it("shows no indicator on the local demo card (no lead)", () => {
    show("");
    expect(screen.queryByTestId("confidence-badge")).toBeNull();
  });
});
