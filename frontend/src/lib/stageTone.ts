/**
 * The one place that maps a stage to its colour tone. The tones are the CSS variables --stage-<tone>-bg / -accent
 * in app/globals.css: open stages walk blue, cyan, violet, amber in pipeline order, a won stage is green and a lost
 * stage red. No open tone is a green or a red, so the outcome stages are never confused with a stage still in
 * play (turquoise and lime, used before, read as green next to Award). An open stage beyond the fourth gets the
 * neutral slate, which carries no progression meaning, instead of repeating a tone. The tone comes from the stage
 * kind and the order among the open stages, never from a title, because stages can be renamed.
 */
export const OPEN_TONES = ["blue", "cyan", "violet", "amber"] as const;

export type StageTone = (typeof OPEN_TONES)[number] | "slate" | "green" | "red";

export function stageTone(kind: string, openIndex: number): StageTone {
  if (kind === "won") return "green";
  if (kind === "lost") return "red";
  return OPEN_TONES[Math.max(openIndex, 0)] ?? "slate";
}

/** Inline style for a column tinted with `tone`. */
export function stageToneStyle(tone: StageTone) {
  return {
    backgroundColor: `var(--stage-${tone}-bg)`,
    borderColor: `color-mix(in srgb, var(--stage-${tone}-accent) 40%, white)`,
    borderTopColor: `var(--stage-${tone}-accent)`,
  };
}
