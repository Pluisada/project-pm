"""Commercial KPI engine over the card stage history (Part 19).

Definitions are in docs/KPIS.md - this module implements them and nothing
else. It is independent of HTTP: load_board_history() reads one board (three
queries: columns, live cards, events) and every KPI function is a pure
computation over that snapshot, so one request never re-queries per card and
the same data always gives the same result.

Rules that hold everywhere:
- stages are classified by stage_kind, never by title;
- a "baseline" event is a starting point, never a move, win or loss, and the
  time spent before it is unknown (durations from it are lower bounds);
- money is Deal Value in USD (one currency, no conversion); legacy currency codes are ignored;
- timestamps are naive UTC; periods are half-open [start, end).
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_options import DEAL_CURRENCY, STAGE_LOST, STAGE_OPEN
from reports import annual_amounts
from models import BoardColumn, Card, CardStageEvent
# The same naive-UTC clock the stage-history listener stamps events with;
# routes call kpis.utcnow() so tests can patch it here.
from stage_history import utcnow

PERIOD_PRESETS = ("last_30_days", "last_90_days", "year_to_date", "custom")
TERMINAL_EVENTS = ("won", "lost")


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    start: datetime  # inclusive
    end: datetime    # exclusive
    label: str

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end

    def as_dict(self) -> dict:
        return {"label": self.label, "start": self.start, "end": self.end}

    def previous(self) -> "Period":
        length = self.end - self.start
        return Period(self.start - length, self.start, f"previous_{self.label}")


def resolve_period(
    preset: str, start: Optional[date], end: Optional[date], today: date
) -> Period:
    """Turn query parameters into a Period. Raises ValueError with a message
    suitable for a 422 response."""
    midnight = lambda d: datetime(d.year, d.month, d.day)
    tomorrow = midnight(today) + timedelta(days=1)
    if preset not in PERIOD_PRESETS:
        raise ValueError(f"period must be one of {', '.join(PERIOD_PRESETS)}")
    if preset != "custom":
        if start is not None or end is not None:
            raise ValueError('start and end are only accepted with period="custom"')
        if preset == "last_30_days":
            return Period(midnight(today) - timedelta(days=30), tomorrow, preset)
        if preset == "last_90_days":
            return Period(midnight(today) - timedelta(days=90), tomorrow, preset)
        return Period(datetime(today.year, 1, 1), tomorrow, preset)
    if start is None or end is None:
        raise ValueError('period="custom" requires both start and end (YYYY-MM-DD)')
    if start > end:
        raise ValueError("start must not be after end")
    return Period(midnight(start), midnight(end) + timedelta(days=1), "custom")


# ---------------------------------------------------------------------------
# Board snapshot and stays
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnInfo:
    id: int
    title: str
    position: int
    stage_kind: str


@dataclass(frozen=True)
class CardInfo:
    id: int
    title: str
    column_id: int
    deal_value: Optional[float]
    currency: Optional[str]
    # deal_value is for deal_year; this is deal_year + 1 (0 = none). The
    # historical snapshots below only ever carried deal_value.
    deal_value_next_year: float = 0
    deal_year: Optional[int] = None


@dataclass(frozen=True)
class Event:
    id: int
    card_id: int
    event_type: str
    to_column_id: Optional[int]
    to_stage_kind: Optional[str]
    occurred_at: datetime
    # Value at the moment of the event; only meaningful when snapshot_taken.
    deal_value_snapshot: Optional[float] = None
    deal_currency_snapshot: Optional[str] = None
    snapshot_taken: bool = False


@dataclass
class Stay:
    """One continuous period of a card in one column (docs/KPIS.md section 3)."""
    card_id: int
    column_id: int
    stage_kind: str
    entered_at: datetime
    entry_observed: bool          # False when the stay starts at a baseline
    exited_at: Optional[datetime] = None
    exit_event: Optional[str] = None       # moved/won/lost/reopened/deleted
    exit_to_column_id: Optional[int] = None
    exit_to_stage_kind: Optional[str] = None


@dataclass
class BoardHistory:
    board_id: int
    as_of: datetime
    columns: dict           # column_id -> ColumnInfo (live columns)
    cards: dict             # card_id -> CardInfo (live cards)
    events: dict            # card_id -> [Event] in (occurred_at, id) order
    stays: dict = field(default_factory=dict)  # card_id -> [Stay]
    # column_id -> [Stay], each card's stays in chronological order, so a
    # per-column KPI scans its own stays instead of every stay on the board.
    stays_by_column: dict = field(default_factory=dict)
    tracking_started_at: Optional[datetime] = None


def build_stays(events: list) -> list:
    stays, current = [], None

    def close(at, event_type, to_column=None, to_kind=None):
        current.exited_at = at
        current.exit_event = event_type
        current.exit_to_column_id = to_column
        current.exit_to_stage_kind = to_kind

    for e in events:
        if e.event_type == "deleted":
            if current is not None:
                close(e.occurred_at, "deleted")
                current = None
            continue
        if current is not None:
            close(e.occurred_at, e.event_type, e.to_column_id, e.to_stage_kind)
        current = Stay(
            card_id=e.card_id,
            column_id=e.to_column_id,
            stage_kind=e.to_stage_kind,
            entered_at=e.occurred_at,
            entry_observed=e.event_type != "baseline",
        )
        stays.append(current)
    return stays


def load_board_history(db: Session, board_id: int, as_of: Optional[datetime] = None) -> BoardHistory:
    as_of = as_of or utcnow()
    columns = {
        row.id: ColumnInfo(row.id, row.title, row.position, row.stage_kind)
        for row in db.execute(
            select(BoardColumn.id, BoardColumn.title, BoardColumn.position, BoardColumn.stage_kind)
            .where(BoardColumn.board_id == board_id)
        )
    }
    cards = {
        row.id: CardInfo(row.id, row.title, row.column_id, row.deal_value, row.currency, row.deal_value_next_year, row.deal_year)
        for row in db.execute(
            select(
                Card.id, Card.title, Card.column_id, Card.deal_value, Card.currency,
                Card.deal_value_next_year, Card.deal_year,
            )
            .join(BoardColumn, Card.column_id == BoardColumn.id)
            .where(BoardColumn.board_id == board_id)
        )
    }
    events = defaultdict(list)
    for row in db.execute(
        select(
            CardStageEvent.id, CardStageEvent.card_id, CardStageEvent.event_type,
            CardStageEvent.to_column_id, CardStageEvent.to_stage_kind, CardStageEvent.occurred_at,
            CardStageEvent.deal_value_snapshot, CardStageEvent.deal_currency_snapshot,
            CardStageEvent.value_snapshot_taken,
        )
        .where(CardStageEvent.board_id == board_id, CardStageEvent.occurred_at <= as_of)
        .order_by(CardStageEvent.card_id, CardStageEvent.occurred_at, CardStageEvent.id)
    ):
        events[row.card_id].append(Event(*row))
    history = BoardHistory(board_id, as_of, columns, cards, dict(events))
    history.stays = {card_id: build_stays(evs) for card_id, evs in history.events.items()}
    by_column = defaultdict(list)
    for stays in history.stays.values():
        for stay in stays:
            by_column[stay.column_id].append(stay)
    history.stays_by_column = dict(by_column)
    starts = [evs[0].occurred_at for evs in history.events.values() if evs]
    history.tracking_started_at = min(starts) if starts else None
    return history


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_CENT = Decimal("0.01")


def _days(delta: timedelta) -> float:
    return round(delta.total_seconds() / 86400, 2)


def _duration_stats(days: list) -> dict:
    if not days:
        return {"count": 0, "mean_days": None, "median_days": None}
    return {
        "count": len(days),
        "mean_days": round(sum(days) / len(days), 2),
        "median_days": round(median(days), 2),
    }


def _rate(numerator: int, denominator: int, reason: str) -> dict:
    if denominator == 0:
        return {"value": None, "numerator": numerator, "denominator": 0, "unavailable_reason": reason}
    return {"value": round(numerator / denominator, 4), "numerator": numerator, "denominator": denominator,
            "unavailable_reason": None}


def _currency_totals(pairs) -> dict:
    """{currency: {total, count}} from (value, currency) pairs: summed as
    Decimal, rounded to cents, currencies never mixed. The one money rule."""
    totals = defaultdict(lambda: [Decimal(0), 0])
    for value, currency in pairs:
        entry = totals[currency]
        entry[0] += Decimal(str(value))
        entry[1] += 1
    return {
        currency: {"total": float(total.quantize(_CENT, ROUND_HALF_UP)), "count": count}
        for currency, (total, count) in sorted(totals.items())
    }


def _money(history: BoardHistory, card_ids) -> dict:
    """USD totals of the current values of distinct cards."""
    pairs, without_value = [], 0
    for card_id in sorted(set(card_ids)):
        card = history.cards.get(card_id)
        if card is None or card.deal_value is None:
            without_value += 1  # deleted (value gone) or never valued
        else:
            pairs.append((card.deal_value, DEAL_CURRENCY))
    return {"values": _currency_totals(pairs), "without_value": without_value}


def _annual_money(history: BoardHistory, card_ids) -> dict:
    """The three measures of the current values of distinct cards, per currency:
    current-year (deal_value; a card without one is unavailable, not 0),
    next-year and their total. Only for cards that exist today."""
    current, next_year, total = [], [], []
    for card_id in sorted(set(card_ids)):
        card = history.cards.get(card_id)
        amounts = annual_amounts(card) if card else None
        if amounts is None:
            continue
        if amounts[0] is not None:
            current.append((amounts[0], DEAL_CURRENCY))
        next_year.append((amounts[1], DEAL_CURRENCY))
        total.append((amounts[2], DEAL_CURRENCY))
    return {"current": _currency_totals(current), "next": _currency_totals(next_year), "total": _currency_totals(total)}


def _closing_money(events: list) -> dict:
    """USD totals of the values recorded *on* the given events (one
    per distinct opportunity). Three outcomes per event, never mixed:
    - snapshot with a value -> counted as USD (the stored snapshot is not
      rewritten; whatever currency code it carries is ignored);
    - snapshot taken but the card had no value -> without_value;
    - no snapshot (event predates snapshots) -> value_unavailable: the value
      at that moment is unknown and the card's current value is NOT used."""
    pairs, without_value, unavailable = [], 0, 0
    for event in sorted(events, key=lambda e: e.card_id):
        if not event.snapshot_taken:
            unavailable += 1
        elif event.deal_value_snapshot is None:
            without_value += 1
        else:
            pairs.append((event.deal_value_snapshot, DEAL_CURRENCY))
    return {"values": _currency_totals(pairs), "without_value": without_value, "value_unavailable": unavailable}


def _current_kind(history: BoardHistory, card_id: int) -> Optional[str]:
    card = history.cards.get(card_id)
    column = history.columns.get(card.column_id) if card else None
    return column.stage_kind if column else None


def _ordered_columns(history: BoardHistory, kinds=None) -> list:
    columns = sorted(history.columns.values(), key=lambda c: (c.position, c.id))
    return [c for c in columns if kinds is None or c.stage_kind in kinds]


def coverage(history: BoardHistory, period: Period) -> dict:
    started = history.tracking_started_at
    if started is None:
        status = "no_history"
    elif period.start < started:
        status = "partial"
    else:
        status = "complete"
    return {"status": status, "tracking_started_at": started}


# ---------------------------------------------------------------------------
# KPIs (definitions: docs/KPIS.md)
# ---------------------------------------------------------------------------


def won_lost(history: BoardHistory, period: Period) -> dict:
    """Section 4: won / lost / reopened flow in the period."""
    result = {}
    for event_type in ("won", "lost", "reopened"):
        events = [
            e for evs in history.events.values() for e in evs
            if e.event_type == event_type and period.contains(e.occurred_at)
        ]
        card_ids = sorted({e.card_id for e in events})
        entry = {"events": len(events), "opportunities": len(card_ids)}
        if event_type in TERMINAL_EVENTS:
            entry["card_ids"] = card_ids
            # Closing value: the snapshot on each card's LAST such event in
            # the period (won, reopened, won again at a new price -> the new
            # price), so each opportunity counts once.
            last_event = {}
            for e in events:  # already in (occurred_at, id) order per card
                last_event[e.card_id] = e
            entry.update(_closing_money(list(last_event.values())))
            # The same cards at their current value, for comparison only.
            current = _money(history, card_ids)
            entry["current_values"] = current["values"]
            entry["current_without_value"] = current["without_value"]
            annual = _annual_money(history, card_ids)
            entry["current_next_year_values"] = annual["next"]
            entry["current_total_values"] = annual["total"]
            entry["still_in_outcome"] = sum(1 for c in card_ids if _current_kind(history, c) == event_type)
        result[event_type] = entry
    return result


def win_rate_closed(history: BoardHistory, period: Period) -> dict:
    """Section 5: final outcome (last won/lost event in the period) per card."""
    outcomes = {}
    for card_id, evs in history.events.items():
        terminal = [e for e in evs if e.event_type in TERMINAL_EVENTS and period.contains(e.occurred_at)]
        if terminal:
            outcomes[card_id] = terminal[-1].event_type
    won = sum(1 for outcome in outcomes.values() if outcome == "won")
    return _rate(won, len(outcomes), "no opportunity was won or lost in the period")


def cohort(history: BoardHistory, period: Period) -> dict:
    """Section 5: cards whose creation was observed in the period."""
    members = sorted(
        card_id for card_id, evs in history.events.items()
        if evs and evs[0].event_type == "created" and period.contains(evs[0].occurred_at)
    )
    won_first = lost_first = deleted_open = still_open = 0
    for card_id in members:
        first = next((e for e in history.events[card_id] if e.event_type in TERMINAL_EVENTS), None)
        if first is not None:
            won_first += first.event_type == "won"
            lost_first += first.event_type == "lost"
        elif history.events[card_id][-1].event_type == "deleted":
            deleted_open += 1
        else:
            still_open += 1
    size = len(members)
    empty = "no card was created in the period (creation is only observed after tracking started)"
    columns_reached = {card_id: {s.column_id for s in history.stays.get(card_id, [])} for card_id in members}
    reached = []
    for column in _ordered_columns(history):
        count = sum(1 for card_id in members if column.id in columns_reached[card_id])
        reached.append({
            "column_id": column.id, "title": column.title, "stage_kind": column.stage_kind,
            "count": count, "rate": _rate(count, size, empty),
        })
    return {
        "size": size,
        "card_ids": members,
        "won_first": won_first,
        "lost_first": lost_first,
        "still_open": still_open,
        "deleted_without_outcome": deleted_open,
        "win_rate": _rate(won_first, size, empty),
        "closed_win_rate": _rate(won_first, won_first + lost_first, "no card of the cohort has been won or lost yet"),
        "reached": reached,
    }


def advancement(history: BoardHistory, period: Period) -> list:
    """Section 5: from each open stage, where did observed entrants go next."""
    rows = []
    for column in _ordered_columns(history, kinds=(STAGE_OPEN,)):
        first_entry = {}
        for stay in history.stays_by_column.get(column.id, []):
            if stay.entry_observed and period.contains(stay.entered_at) and stay.card_id not in first_entry:
                first_entry[stay.card_id] = stay
        counts = {"advanced": 0, "lost": 0, "other_exit": 0, "still_in_stage": 0}
        for stay in first_entry.values():
            if stay.exited_at is None:
                counts["still_in_stage"] += 1
            elif stay.exit_event == "deleted":
                counts["other_exit"] += 1
            elif stay.exit_to_stage_kind == STAGE_LOST:
                counts["lost"] += 1
            else:
                target = history.columns.get(stay.exit_to_column_id)
                if target is not None and target.position > column.position:
                    counts["advanced"] += 1
                else:
                    counts["other_exit"] += 1
        rows.append({
            "column_id": column.id, "title": column.title, "stage_kind": column.stage_kind,
            "position": column.position, "entered": len(first_entry), **counts,
            "rate": _rate(counts["advanced"], len(first_entry), "no observed entry into this stage in the period"),
        })
    return rows


def stage_time(history: BoardHistory, period: Period) -> list:
    """Section 6: complete / partial durations of stays exited in the period,
    plus stays still open at as_of."""
    rows = []
    for column in _ordered_columns(history):
        complete, partial, current = [], [], []
        ended_by_deletion = observed_entries = baseline_entries = 0
        for stay in history.stays_by_column.get(column.id, []):
            if stay.exited_at is None:
                current.append(_days(history.as_of - stay.entered_at))
                if stay.entry_observed:
                    observed_entries += 1
                else:
                    baseline_entries += 1
            elif period.contains(stay.exited_at):
                if stay.exit_event == "deleted":
                    ended_by_deletion += 1
                elif stay.entry_observed:
                    complete.append(_days(stay.exited_at - stay.entered_at))
                else:
                    partial.append(_days(stay.exited_at - stay.entered_at))
        rows.append({
            "column_id": column.id, "title": column.title, "stage_kind": column.stage_kind,
            "complete": _duration_stats(complete),
            "partial": _duration_stats(partial),
            "ended_by_deletion": ended_by_deletion,
            "current": {**_duration_stats(current), "observed_entries": observed_entries,
                        "baseline_entries": baseline_entries},
        })
    return rows


def velocity(history: BoardHistory, period: Period) -> dict:
    """Section 7: created -> first won/lost, for first conclusions in the period."""
    cycles = {"won": [], "lost": []}
    excluded = 0
    for evs in history.events.values():
        first = next((e for e in evs if e.event_type in TERMINAL_EVENTS), None)
        if first is None or not period.contains(first.occurred_at):
            continue
        if evs[0].event_type == "created":
            cycles[first.event_type].append(_days(first.occurred_at - evs[0].occurred_at))
        else:
            excluded += 1
    return {
        "won": _duration_stats(cycles["won"]),
        "lost": _duration_stats(cycles["lost"]),
        "excluded_unobserved_creation": excluded,
    }


def aging(history: BoardHistory) -> list:
    """Section 8: open opportunities and how long they've been in their stage."""
    rows = []
    for card in history.cards.values():
        column = history.columns.get(card.column_id)
        if column is None or column.stage_kind != STAGE_OPEN:
            continue
        stays = history.stays.get(card.id, [])
        stay = stays[-1] if stays and stays[-1].exited_at is None and stays[-1].column_id == card.column_id else None
        rows.append({
            "card_id": card.id, "title": card.title, "column_id": column.id, "column_title": column.title,
            "stage_kind": column.stage_kind,
            "entered_at": stay.entered_at if stay else None,
            "entry_observed": stay.entry_observed if stay else None,
            "age_days": _days(history.as_of - stay.entered_at) if stay else None,
            "history": ("observed" if stay.entry_observed else "since_tracking_start") if stay else "none",
            "deal_value": card.deal_value, "currency": DEAL_CURRENCY,
            "deal_value_next_year": card.deal_value_next_year or 0,
            "deal_value_total": (annual_amounts(card) or (None, None, None))[2],
            "deal_year": card.deal_year,
        })
    rows.sort(key=lambda r: (-(r["age_days"] if r["age_days"] is not None else -1), r["card_id"]))
    return rows


def pipeline(history: BoardHistory) -> list:
    """Section 9: live cards per stage (USD)."""
    by_column = defaultdict(list)
    for card in history.cards.values():
        by_column[card.column_id].append(card.id)
    rows = []
    for c in _ordered_columns(history):
        annual = _annual_money(history, by_column[c.id])
        rows.append({
            "column_id": c.id, "title": c.title, "stage_kind": c.stage_kind,
            "count": len(by_column[c.id]), **_money(history, by_column[c.id]),
            "next_year_values": annual["next"], "total_values": annual["total"],
        })
    return rows


def pipeline_deal_years(history: BoardHistory) -> list:
    """The deal_year of every live card with an amount: one year normally, several when totals span years."""
    return sorted({c.deal_year for c in history.cards.values() if c.deal_year is not None and annual_amounts(c)})


def period_kpis(history: BoardHistory, period: Period) -> dict:
    """The flow KPIs of one period (the unit compared with the previous period)."""
    return {
        "period": period.as_dict(),
        "coverage": coverage(history, period),
        **won_lost(history, period),
        "win_rate_closed": win_rate_closed(history, period),
        "cohort": cohort(history, period),
        "velocity": velocity(history, period),
    }
