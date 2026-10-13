# Commercial KPI Definitions (Part 19)

Every KPI here is computed by `backend/kpis.py` from `card_stage_events` (Part 18) and is exposed read-only under `/api/boards/{board_id}/kpis/...`. The existing `/api/boards/{id}/report` (Part 14) is unchanged. This document is the contract. If code and document disagree, that's a bug.

## 1. Inputs and conventions

**Events.** For each card, its events are ordered by `(occurred_at, id)`. Events written in one database flush share a timestamp, and `id` (insertion order) breaks the tie. So the same data always gives the same order and the same result.

**Stage meaning.** Only the column's `stage_kind` (`open` / `won` / `lost`), copied into each event, is ever used. Titles never are.

**Time.**
- Timestamps are UTC (stored naive).
- Durations are reported in days, rounded to 2 decimals.
- `as_of` is the instant the request is computed: "now" in UTC.

**Periods.** A period is half-open, `[start, end)`, in UTC:

| `period` | `start` | `end` (exclusive) |
|---|---|---|
| `last_30_days` | today − 30 days, 00:00 | tomorrow 00:00 |
| `last_90_days` | today − 90 days, 00:00 | tomorrow 00:00 |
| `year_to_date` | Jan 1 of this year | tomorrow 00:00 |
| `custom` (`start=YYYY-MM-DD&end=YYYY-MM-DD`) | `start` 00:00 | the day after `end` 00:00 (both dates inclusive) |

- "Today" is the UTC date of `as_of`.
- An event belongs to the period when `start <= occurred_at < end`.
- **Previous period** (for comparison) is the interval of the same length ending at `start`: `[start − (end − start), start)`.

**Two kinds of numbers, never compared with each other:**
- **Flow:** counts of events and opportunities in a period, comparable with the previous period.
- **Stock:** the pipeline as it is at `as_of`, such as the stage distribution and aging. Stock numbers have no period and no comparison.

**Money.**
- Totals are always per currency: `{currency: {total, count}}`. They are summed as decimals and rounded to 2 places.
- There is no conversion, and no total, average or comparison ever mixes currencies.
- **Historical (closing) value versus current value (Part 20).** Every stage event records the card's `deal_value` and `currency` at that moment: `deal_value_snapshot`, `deal_currency_snapshot` and `value_snapshot_taken` = true. These are written in the same transaction as the stage change.
- Won and lost money uses those snapshots, never the card's current value. Each value falls into exactly one bucket:
  - **`values`**: the snapshot had a value and a currency;
  - **`without_value`**: the snapshot was taken, but the card had no value then;
  - **`value_unavailable`**: the event predates snapshots, so its value is unknown and **not** replaced by today's value.
- The same cards' **current** values are reported separately as `current_values` / `current_without_value` (deleted cards have none), for comparison only.
- Stock metrics (pipeline distribution, aging) describe the board today, so they use current values.

**Opportunity** means a card. A deleted card's events stay in history and still count in flow metrics (its last event is `deleted`).

## 2. Baseline events

A `baseline` (written once by `migrate_stage_events.py`) means only this: "when tracking started, the card was already in this stage".

- It is **never** a move, a win or a loss. It never counts in won/lost, conversion or velocity.
- It is not the date the card entered the stage. Time spent in that stage before tracking is unknown and never estimated.
- It starts an **unobserved-entry stay**:
  - a duration from a baseline to an observed exit is **partial**, a lower bound;
  - a duration from a baseline to `as_of` is a **current, lower-bound age**.
- Cards with a baseline are never in a creation cohort, because their creation wasn't observed.

`tracking_started_at` is the board's earliest event. Every response carries `coverage`:
- `"complete"` when the period starts at or after tracking start;
- `"partial"` when the period starts before it, meaning events before tracking simply don't exist.

## 3. Stays (the timeline each metric reads)

Walking a card's ordered events gives its stays. A stay is one continuous period in one column:
- `created` → opens an **observed** stay in `to_column`;
- `baseline` → opens an **unobserved-entry** stay in `to_column`;
- `moved` / `won` / `lost` / `reopened` → closes the open stay at the event time (an observed exit), then opens an observed stay in `to_column`;
- `deleted` → closes the open stay (exit by deletion).

Each stay is one of three kinds:

| Kind | Entry | Exit |
|---|---|---|
| **complete** | observed | observed |
| **partial** | baseline | observed |
| **current** | observed or baseline | none: still in the stage at `as_of` |

## 4. Won / lost (flow, per period)

| Field | Definition |
|---|---|
| `won.events` | Number of `won` events in the period, every transition counted (won, reopened, won again = 2) |
| `won.opportunities` | Number of distinct cards with at least one `won` event in the period (the sequence above = 1) |
| `won.card_ids` | Those cards, sorted |
| `won.values` | Per-currency totals of the **closing value**: the snapshot on each distinct card's **last** `won` event in the period, each card counted once. Example: won at 100, reopened, repriced to 140, won again gives 140 once |
| `won.without_value` / `won.value_unavailable` | Distinct cards whose last `won` event recorded no value / has no snapshot (unknown) |
| `won.current_values` / `won.current_without_value` | The same cards at their value today, separate from the history |
| `won.still_won` | How many of those distinct cards are in a `won` stage at `as_of` |
| `lost.*` | The same, for `lost` |
| `reopened.events` / `.opportunities` | `reopened` events in the period, and distinct cards with one |

A baseline in a won/lost stage is **not** a win or loss in any period. Event date and card creation date are unrelated: a card created a year ago and won yesterday counts yesterday.

## 5. Conversion and win rates

Each rate has its own denominator. A rate is `null` (with the reason in `unavailable_reason`) when its denominator is 0. All rates count distinct cards, so no rate can exceed 100%.

| Rate | Numerator | Denominator | Reliable now? |
|---|---|---|---|
| **`win_rate_closed`** | Cards whose **last** won/lost event in the period is `won` | Distinct cards with at least one won/lost event in the period | Yes: it uses only observed transitions |
| **`cohort.win_rate`** | Cohort cards whose **first** won/lost event (up to `as_of`) is `won` | Cohort size, which includes cards still open, so the rate matures over time | Yes for cohorts after tracking start |
| **`cohort.closed_win_rate`** | Same numerator | Cohort cards with a first won/lost event | Same |
| **`cohort.reached[stage]`** | Cohort cards with any stay in that stage (creation stage included) | Cohort size | Same |
| **`advancement[stage].rate`** (open stages only; leaving won/lost is a reopening, see section 4) | Of those entrants, cards whose first exit from that stay went to a column with a higher `position` | Distinct cards with an **observed entry** into the stage in the period (`created` or a move into it, never a baseline) | Yes |

- The **cohort** is the distinct cards with a `created` event in the period. Cards with a baseline never qualify, since their creation predates tracking.
- `advancement[stage]` uses each card's **first** observed entry into the stage in the period. A destination column that no longer exists has no known position, so that exit counts as `other_exit`.
- `advancement[stage]` also reports, for the same entrants:
  - `lost`: the exit went to a `lost` stage;
  - `other_exit`: the exit went backward, to an open column with a lower position, or the card was deleted;
  - `still_in_stage`.
- **Not the Reports screen's "Win rate".** `/api/boards/{id}/report` (Part 14) has a `kpis.win_rate` that is a **stock** ratio: cards currently in won ÷ cards currently in won or lost, over all time. `win_rate_closed` here is a **flow** ratio for a period. They legitimately differ; label them differently in any UI that shows both.
- **Reopenings never duplicate an opportunity.** Every rate counts distinct cards. `win_rate_closed` uses each card's final outcome in the period. Cohort metrics use the card's first outcome.

## 6. Time in stage (per column)

For stays whose **exit** falls in the period:
- `complete`: count, mean and median days of complete stays. These are true durations.
- `partial`: the same for partial stays. These are lower bounds, kept apart from `complete`.
- Stays that ended because the card was deleted are excluded, and counted in `ended_by_deletion`.

Plus `current`, the stays still open at `as_of`, with no period. It reports count, mean and median days so far, and how many have an observed entry versus a baseline entry.

## 7. Velocity (sales cycle)

**Formula:** `cycle_days = first_won_or_lost_at − created_at`, for cards whose `created` event was observed and whose **first** won/lost event falls in the period.

- Reported separately for `won` and `lost` outcomes: count, mean and median.
- Later reopen/close cycles of the same card are ignored, so the first conclusion defines the cycle.
- `excluded_unobserved_creation` counts cards that concluded in the period but have a baseline instead of an observed creation.
- The classic "pipeline velocity" formula (opportunities × value × win rate ÷ cycle) is **not** provided: it multiplies by a value total, which can't be a single number across currencies.

## 8. Aging (stock, at `as_of`)

One row per card currently in an **open** stage. Cards in won or lost stages are not open opportunities and are excluded. Each row has:
- `card_id`, `title`, `column_id`, `column_title` (display only), `stage_kind`;
- `entered_at`: the start of the current stay;
- `entry_observed`: false when the stay started at a baseline;
- `age_days`: `as_of − entered_at`. It is a **lower bound** when `entry_observed` is false;
- `deal_value`, `currency`.

Rows are sorted by `age_days` (highest first), then `card_id`. A card with no events at all gets `entered_at`/`age_days` = null and `history: "none"`. This can't happen after the migration, but it's handled.

## 9. Pipeline distribution (stock, at `as_of`)

For every column in position order:
- `stage_kind`;
- the number of cards;
- per-currency totals;
- the number of cards `without_value`.

It's computed from the live cards, so it equals the existing report's per-stage counts.

## 10. API

Every endpoint is read-only `GET`. Each needs a login, either member or admin, the same as the existing report. Unknown boards return 404. Each request loads the board once (three queries) and computes from that snapshot.

| Endpoint | Query parameters | Response (`schemas.py`) |
|---|---|---|
| `/api/boards/{id}/kpis/summary` | `period` (default `last_30_days`), `start`, `end`, `compare` (default false) | `KpiSummaryResponse`: `as_of`; `current` and `previous`, each a `PeriodKpis` with `period`, `coverage`, `won`, `lost`, `reopened`, `win_rate_closed`, `cohort`, `velocity` |
| `/api/boards/{id}/kpis/conversion` | `period`, `start`, `end` | `KpiConversionResponse`: `win_rate_closed`, `cohort` (with `reached` per stage), `advancement` (open stages) |
| `/api/boards/{id}/kpis/stage-time` | `period`, `start`, `end` | `KpiStageTimeResponse`: per stage `complete`, `partial`, `ended_by_deletion`, `current` |
| `/api/boards/{id}/kpis/aging` | none (stock) | `KpiAgingResponse`: `tracking_started_at` and one row per open opportunity |
| `/api/boards/{id}/kpis/pipeline` | none (stock) | `KpiPipelineResponse`: per stage `count`, `values` per currency, `without_value` |

- `period` is one of `last_30_days`, `last_90_days`, `year_to_date`, `custom`.
- `start`/`end` are `YYYY-MM-DD` and are only valid with `custom`, where both are required and `start <= end`.
- Anything else returns 422.
- No user names or ids are returned: only card ids, titles and values, which the board already shows.

## 11. Known limitations

- **History starts at tracking start.** For the development database that is 2026-10-13, with only baselines. Every flow metric is empty, and every duration is partial or a lower bound, until real moves accumulate.
- **Values before Part 20 are unknown.** Events written before value snapshots existed (in the dev DB, the 7 baselines from 2026-10-13) have `value_snapshot_taken` = false and count as `value_unavailable`, never as today's value. From then on, every event records its value, including `deleted` events, so a deleted won deal keeps its closing value.
- **Values are not reconstructed from edits.** Only events snapshot values; editing a card's value without a stage change records nothing.
- **No per-salesperson or per-product breakdowns** in the KPI endpoints yet; the existing report still has them.
- **Deleting a column or board** ends the stays of its cards by deletion. Their earlier events still count.

## 12. Executive Dashboard (Part 21)

`frontend/src/components/DashboardPage.tsx` displays these KPIs without recomputing any of them. It only formats what the API returns. Display rules:
- A null rate or duration shows "-", with the API's `unavailable_reason` where there is one. It is never shown as 0.
- Closing value, the "value unavailable" count and today's value are separate columns. Today's value never fills in for a missing closing value.
- A previous period that ended before tracking started reads "not tracked". A previous period with partial coverage is marked "(partial history)".
- Durations and ages that start at a baseline are labelled "at least".
- Period dates are shown as UTC calendar dates with the end inclusive, matching the API's `[start, end)` interval.
- Numbers are shown only for the filters that produced them. Nothing is shown while a custom range is incomplete or a new period is loading.

