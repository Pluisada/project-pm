import clsx from "clsx";
import type { ConfidenceLevel } from "@/lib/api";

// LOW red, MEDIUM yellow, HIGH green. The text label is always shown, so the level never depends on colour alone;
// the colours are theme variables (globals.css) with a light and a dark value.
const TONE: Record<ConfidenceLevel, string> = {
  LOW: "border-[var(--conf-low-line)] bg-[var(--conf-low-bg)] text-[var(--conf-low-ink)]",
  MEDIUM: "border-[var(--conf-medium-line)] bg-[var(--conf-medium-bg)] text-[var(--conf-medium-ink)]",
  HIGH: "border-[var(--conf-high-line)] bg-[var(--conf-high-bg)] text-[var(--conf-high-ink)]",
};
const DOT: Record<ConfidenceLevel, string> = {
  LOW: "bg-[var(--conf-low-dot)]",
  MEDIUM: "bg-[var(--conf-medium-dot)]",
  HIGH: "bg-[var(--conf-high-dot)]",
};

type ConfidenceBadgeProps = {
  level: ConfidenceLevel | null | undefined;
  // Render a neutral "Not assigned" pill for null; without it null renders nothing (compact Kanban cards).
  showUnassigned?: boolean;
};

export const ConfidenceBadge = ({ level, showUnassigned = false }: ConfidenceBadgeProps) => {
  if (!level) {
    return showUnassigned ? (
      <span
        data-testid="confidence-badge"
        data-level="none"
        className="inline-flex items-center gap-1.5 rounded-full border border-[var(--stroke)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--gray-text)]"
      >
        <span aria-hidden="true" className="h-2 w-2 rounded-full border border-[var(--gray-text)]" />
        Not assigned
      </span>
    ) : null;
  }
  return (
    <span
      data-testid="confidence-badge"
      data-level={level}
      title={`Confidence level: ${level}`}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em]",
        TONE[level]
      )}
    >
      <span aria-hidden="true" className={clsx("h-2 w-2 rounded-full", DOT[level])} />
      {level}
    </span>
  );
};
