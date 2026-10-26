import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CardHistoryModal } from "@/components/CardHistoryModal";
import type { CardEditHistoryEntry } from "@/lib/api";

const entry = (changes: CardEditHistoryEntry["changes"], id = 1): CardEditHistoryEntry => ({
  id, edited_by: "ana", changes, created_at: "2026-10-01T10:00:00Z",
});

const show = (entries: CardEditHistoryEntry[]) =>
  render(<CardHistoryModal cardTitle="Opp" entries={entries} isLoading={false} onClose={vi.fn()} />);

describe("CardHistoryModal confidence level", () => {
  it("renders a change between two levels under a readable label", () => {
    show([entry({ confidence_level: { old: "LOW", new: "HIGH" } })]);
    expect(screen.getByText("Confidence level")).toBeInTheDocument();
    expect(screen.getByText("LOW")).toBeInTheDocument();
    expect(screen.getByText(/HIGH/)).toBeInTheDocument();
  });

  it("renders a transition from Not assigned and to Not assigned", () => {
    show([
      entry({ confidence_level: { old: null, new: "MEDIUM" } }, 1),
      entry({ confidence_level: { old: "MEDIUM", new: null } }, 2),
    ]);
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Not assigned -> MEDIUM");
    expect(items[1]).toHaveTextContent("MEDIUM -> Not assigned");
    expect(screen.queryByText(/\(empty\)/)).toBeNull();
  });

  it("keeps (empty) for other cleared fields", () => {
    show([entry({ company: { old: "Acme", new: null } })]);
    expect(screen.getByRole("listitem")).toHaveTextContent("Acme -> (empty)");
  });
});
