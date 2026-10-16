import type { Card } from "@/lib/kanban";

type KanbanCardPreviewProps = {
  card: Card;
};

export const KanbanCardPreview = ({ card }: KanbanCardPreviewProps) => (
  <article className="rounded-xl border border-[rgba(3,33,71,0.1)] bg-white px-3 py-2.5 shadow-[0_8px_16px_rgba(3,33,71,0.12)]">
    <h4 className="line-clamp-2 break-words font-display text-sm font-semibold leading-5 text-[var(--navy-dark)]">{card.title}</h4>
    {card.lead?.company && (
      <p className="mt-0.5 truncate text-xs text-[var(--gray-text)]">{card.lead.company}</p>
    )}
  </article>
);
