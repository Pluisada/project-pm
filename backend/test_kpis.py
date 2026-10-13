"""KPI engine (kpis.py) and its read-only API, on synthetic histories with
hand-computed expectations. In-memory databases only - never pm.db.

Cards and events are inserted with plain table inserts so each test controls
the exact timeline (the ORM listener would stamp "now")."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import kpis
from auth import create_access_token, hash_password
from database import get_db
from main import app
from models import Base, Board, Card, CardStageEvent, Column, User

SEPT = kpis.Period(datetime(2026, 9, 1), datetime(2026, 10, 1), "custom")
AS_OF = datetime(2026, 10, 15, 12, 0)
D = lambda day, hour=0, minute=0: datetime(2026, 9, day, hour, minute)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class Board_:
    """Board with Lead, Qualified, Proposal (open), Award (won), Lost (lost)."""

    def __init__(self, db):
        self.db = db
        board = Board(user_id=1, title="B")
        db.add(board)
        db.flush()
        self.id = board.id
        self.cols = {}
        for pos, (title, kind) in enumerate(
            [("Lead", "open"), ("Qualified", "open"), ("Proposal", "open"), ("Award", "won"), ("Lost", "lost")]
        ):
            col = Column(board_id=board.id, title=title, position=pos, stage_kind=kind)
            db.add(col)
            db.flush()
            self.cols[title] = col
        db.commit()
        self._next_event = 1

    def card(self, card_id, column, value=None, currency=None):
        self.db.execute(Card.__table__.insert().values(
            id=card_id, column_id=self.cols[column].id, title=f"card {card_id}", position=0,
            deal_value=value, currency=currency, created_at=AS_OF, updated_at=AS_OF,
        ))
        self.db.commit()

    def event(self, card_id, event_type, at, to=None, frm=None, event_id=None, snapshot=True):
        """Like the app's listener, record the card's value as of now, unless
        snapshot=False (an event from before value snapshots existed)."""
        to_col = self.cols[to] if to else None
        from_col = self.cols[frm] if frm else None
        card = self.db.get(Card, card_id) if snapshot else None
        self.db.execute(CardStageEvent.__table__.insert().values(
            deal_value_snapshot=card.deal_value if card else None,
            deal_currency_snapshot=card.currency if card else None,
            value_snapshot_taken=snapshot,
            id=event_id or self._next_event, card_id=card_id, board_id=self.id, event_type=event_type,
            from_column_id=from_col.id if from_col else None, from_stage_kind=from_col.stage_kind if from_col else None,
            to_column_id=to_col.id if to_col else None, to_stage_kind=to_col.stage_kind if to_col else None,
            source="system", occurred_at=at,
        ))
        self._next_event = max(self._next_event, event_id or self._next_event) + 1
        self.db.commit()

    def history(self, as_of=AS_OF):
        return kpis.load_board_history(self.db, self.id, as_of)


@pytest.fixture
def b(db):
    return Board_(db)


# ---------------------------------------------------------------------------
# 1-5, 14: won / lost / reopened
# ---------------------------------------------------------------------------


class TestWonLost:
    def test_created_then_won(self, b):
        b.card(1, "Award", 100, "EUR")
        b.event(1, "created", D(2), to="Lead")
        b.event(1, "won", D(12), to="Award", frm="Lead")
        result = kpis.won_lost(b.history(), SEPT)
        assert (result["won"]["events"], result["won"]["opportunities"], result["won"]["card_ids"]) == (1, 1, [1])
        assert result["won"]["values"] == {"EUR": {"total": 100.0, "count": 1}}
        assert result["won"]["still_in_outcome"] == 1
        assert result["lost"]["events"] == 0

    def test_created_then_lost(self, b):
        b.card(1, "Lost", 50, "USD")
        b.event(1, "created", D(2), to="Lead")
        b.event(1, "lost", D(5), to="Lost", frm="Lead")
        result = kpis.won_lost(b.history(), SEPT)
        assert (result["lost"]["events"], result["lost"]["opportunities"]) == (1, 1)
        assert result["won"]["opportunities"] == 0

    def test_win_date_not_creation_date(self, b):
        """Created in August, won in September: a September win."""
        b.card(1, "Award", 10, "EUR")
        b.event(1, "created", datetime(2026, 8, 3), to="Lead")
        b.event(1, "won", D(20), to="Award", frm="Lead")
        assert kpis.won_lost(b.history(), SEPT)["won"]["opportunities"] == 1
        aug = kpis.Period(datetime(2026, 8, 1), datetime(2026, 9, 1), "custom")
        assert kpis.won_lost(b.history(), aug)["won"]["opportunities"] == 0

    def test_reopened_after_won(self, b):
        b.card(1, "Proposal")
        b.event(1, "created", D(1), to="Lead")
        b.event(1, "won", D(5), to="Award", frm="Lead")
        b.event(1, "reopened", D(8), to="Proposal", frm="Award")
        h = b.history()
        result = kpis.won_lost(h, SEPT)
        assert (result["won"]["events"], result["won"]["still_in_outcome"]) == (1, 0)
        assert (result["reopened"]["events"], result["reopened"]["opportunities"]) == (1, 1)
        # Final outcome in period is still "won" (reopen isn't an outcome).
        assert kpis.win_rate_closed(h, SEPT)["value"] == 1.0

    def test_reopened_after_lost(self, b):
        b.card(1, "Lead")
        b.event(1, "created", D(1), to="Lead")
        b.event(1, "lost", D(3), to="Lost", frm="Lead")
        b.event(1, "reopened", D(4), to="Lead", frm="Lost")
        result = kpis.won_lost(b.history(), SEPT)
        assert (result["lost"]["events"], result["lost"]["still_in_outcome"], result["reopened"]["events"]) == (1, 0, 1)

    def test_multiple_cycles(self, b):
        """won -> reopened -> lost -> reopened -> won, all in September."""
        b.card(1, "Award", 30, "EUR")
        b.event(1, "created", D(1), to="Lead")
        b.event(1, "won", D(3), to="Award", frm="Lead")
        b.event(1, "reopened", D(4), to="Proposal", frm="Award")
        b.event(1, "lost", D(6), to="Lost", frm="Proposal")
        b.event(1, "reopened", D(7), to="Proposal", frm="Lost")
        b.event(1, "won", D(9), to="Award", frm="Proposal")
        h = b.history()
        result = kpis.won_lost(h, SEPT)
        assert (result["won"]["events"], result["won"]["opportunities"]) == (2, 1)
        assert (result["lost"]["events"], result["lost"]["opportunities"]) == (1, 1)
        assert result["reopened"]["events"] == 2
        assert result["won"]["values"] == {"EUR": {"total": 30.0, "count": 1}}  # counted once
        assert kpis.win_rate_closed(h, SEPT) == {"value": 1.0, "numerator": 1, "denominator": 1,
                                                  "unavailable_reason": None}
        cohort = kpis.cohort(h, SEPT)
        assert (cohort["won_first"], cohort["lost_first"]) == (1, 0)  # first outcome
        assert kpis.velocity(h, SEPT)["won"] == {"count": 1, "mean_days": 2.0, "median_days": 2.0}

    def test_baseline_in_won_stage_is_not_a_win(self, b):
        b.card(1, "Award", 10, "EUR")
        b.event(1, "baseline", D(10), to="Award")
        h = b.history()
        assert kpis.won_lost(h, SEPT)["won"]["opportunities"] == 0
        assert kpis.win_rate_closed(h, SEPT)["value"] is None


# ---------------------------------------------------------------------------
# 6, 7, 9: open cards, baselines, coverage
# ---------------------------------------------------------------------------


class TestBaselineAndCoverage:
    def test_open_card_aging(self, b):
        b.card(1, "Qualified")
        b.event(1, "created", D(1), to="Lead")
        b.event(1, "moved", datetime(2026, 10, 5, 12), to="Qualified", frm="Lead")
        [row] = kpis.aging(b.history())
        assert (row["card_id"], row["column_title"], row["age_days"], row["entry_observed"], row["history"]) == (
            1, "Qualified", 10.0, True, "observed")

    def test_baseline_then_move(self, b):
        b.card(1, "Proposal")
        b.event(1, "baseline", D(10), to="Qualified")
        b.event(1, "moved", D(14), to="Proposal", frm="Qualified")
        h = b.history()
        qualified = next(s for s in kpis.stage_time(h, SEPT) if s["title"] == "Qualified")
        assert qualified["partial"] == {"count": 1, "mean_days": 4.0, "median_days": 4.0}  # lower bound
        assert qualified["complete"]["count"] == 0
        assert kpis.cohort(h, SEPT)["size"] == 0                 # creation not observed
        row = next(r for r in kpis.advancement(h, SEPT) if r["title"] == "Qualified")
        assert row["entered"] == 0                               # baseline is not an entry
        proposal = next(r for r in kpis.advancement(h, SEPT) if r["title"] == "Proposal")
        assert (proposal["entered"], proposal["still_in_stage"]) == (1, 1)

    def test_baseline_aging_is_lower_bound(self, b):
        b.card(1, "Lead")
        b.event(1, "baseline", datetime(2026, 10, 13, 18), to="Lead")
        [row] = kpis.aging(b.history())
        assert (row["entry_observed"], row["history"], row["age_days"]) == (False, "since_tracking_start", 1.75)

    def test_baseline_win_excluded_from_velocity(self, b):
        b.card(1, "Award")
        b.event(1, "baseline", D(2), to="Proposal")
        b.event(1, "won", D(9), to="Award", frm="Proposal")
        v = kpis.velocity(b.history(), SEPT)
        assert (v["won"]["count"], v["excluded_unobserved_creation"]) == (0, 1)
        assert kpis.won_lost(b.history(), SEPT)["won"]["opportunities"] == 1  # the move IS observed

    def test_no_history_before_tracking(self, b):
        b.card(1, "Lead")
        b.event(1, "baseline", datetime(2026, 10, 13), to="Lead")
        h = b.history()
        assert kpis.coverage(h, SEPT) == {"status": "partial", "tracking_started_at": datetime(2026, 10, 13)}
        late = kpis.Period(datetime(2026, 10, 14), datetime(2026, 10, 16), "custom")
        assert kpis.coverage(h, late)["status"] == "complete"
        assert kpis.won_lost(h, SEPT)["won"]["events"] == 0

    def test_board_without_events(self, b):
        b.card(1, "Lead")
        h = b.history()
        assert kpis.coverage(h, SEPT) == {"status": "no_history", "tracking_started_at": None}
        [row] = kpis.aging(h)
        assert (row["age_days"], row["history"]) == (None, "none")


# ---------------------------------------------------------------------------
# 3, 8: stage time, advancement, deletion
# ---------------------------------------------------------------------------


class TestStagesAndDeletion:
    def test_multi_stage_durations_and_advancement(self, b):
        b.card(1, "Award")
        b.event(1, "created", D(1), to="Lead")
        b.event(1, "moved", D(3), to="Qualified", frm="Lead")       # 2 days in Lead
        b.event(1, "moved", D(8), to="Proposal", frm="Qualified")   # 5 days in Qualified
        b.event(1, "won", D(9, 12), to="Award", frm="Proposal")     # 1.5 days in Proposal
        h = b.history()
        times = {s["title"]: s for s in kpis.stage_time(h, SEPT)}
        assert times["Lead"]["complete"]["mean_days"] == 2.0
        assert times["Qualified"]["complete"]["mean_days"] == 5.0
        assert times["Proposal"]["complete"]["mean_days"] == 1.5
        assert times["Award"]["current"]["count"] == 1
        adv = {r["title"]: r for r in kpis.advancement(h, SEPT)}
        assert set(adv) == {"Lead", "Qualified", "Proposal"}         # open stages only
        assert all(r["rate"]["value"] == 1.0 for r in adv.values())

    def test_advancement_categories(self, b):
        for card_id in (1, 2, 3, 4):
            b.card(card_id, "Lead")
            b.event(card_id, "created", D(1), to="Qualified")
        b.event(1, "moved", D(2), to="Proposal", frm="Qualified")    # advanced
        b.event(2, "lost", D(2), to="Lost", frm="Qualified")         # lost
        b.event(3, "moved", D(2), to="Lead", frm="Qualified")        # backward
        # card 4 stays
        row = next(r for r in kpis.advancement(b.history(), SEPT) if r["title"] == "Qualified")
        assert (row["entered"], row["advanced"], row["lost"], row["other_exit"], row["still_in_stage"]) == (4, 1, 1, 1, 1)
        assert row["rate"]["value"] == 0.25

    def test_deleted_card(self, b):
        b.event(7, "created", D(1), to="Lead")
        b.event(7, "won", D(4), to="Award", frm="Lead")
        b.event(7, "deleted", D(6), frm="Award")
        h = b.history()
        result = kpis.won_lost(h, SEPT)
        assert result["won"]["opportunities"] == 1
        assert (result["won"]["values"], result["won"]["without_value"], result["won"]["still_in_outcome"]) == ({}, 1, 0)
        award = next(s for s in kpis.stage_time(h, SEPT) if s["title"] == "Award")
        assert (award["ended_by_deletion"], award["complete"]["count"], award["current"]["count"]) == (1, 0, 0)
        assert kpis.aging(h) == []  # deleted cards aren't open opportunities

    def test_deleted_before_outcome_in_cohort(self, b):
        b.event(8, "created", D(1), to="Lead")
        b.event(8, "deleted", D(2), frm="Lead")
        c = kpis.cohort(b.history(), SEPT)
        assert (c["size"], c["deleted_without_outcome"], c["still_open"]) == (1, 1, 0)


# ---------------------------------------------------------------------------
# 10-13, 20: periods, empty data, ties, determinism
# ---------------------------------------------------------------------------


class TestPeriodsAndOrdering:
    def test_period_boundaries(self, b):
        b.card(1, "Award")
        b.card(2, "Award")
        b.event(1, "created", datetime(2026, 8, 1), to="Lead")
        b.event(2, "created", datetime(2026, 8, 1), to="Lead")
        b.event(1, "won", datetime(2026, 9, 1, 0, 0), to="Award", frm="Lead")     # start: included
        b.event(2, "won", datetime(2026, 10, 1, 0, 0), to="Award", frm="Lead")    # end: excluded
        assert kpis.won_lost(b.history(), SEPT)["won"]["card_ids"] == [1]

    def test_custom_period_includes_end_date(self):
        p = kpis.resolve_period("custom", date(2026, 9, 1), date(2026, 9, 30), date(2026, 10, 15))
        assert (p.start, p.end) == (datetime(2026, 9, 1), datetime(2026, 10, 1))
        assert p.previous() == kpis.Period(datetime(2026, 8, 2), datetime(2026, 9, 1), "previous_custom")

    @pytest.mark.parametrize("preset,start", [
        ("last_30_days", datetime(2026, 9, 15)),
        ("last_90_days", datetime(2026, 7, 17)),
        ("year_to_date", datetime(2026, 1, 1)),
    ])
    def test_presets(self, preset, start):
        p = kpis.resolve_period(preset, None, None, date(2026, 10, 15))
        assert (p.start, p.end) == (start, datetime(2026, 10, 16))

    @pytest.mark.parametrize("args", [
        ("custom", None, date(2026, 9, 1)),
        ("custom", date(2026, 9, 2), date(2026, 9, 1)),
        ("last_30_days", date(2026, 9, 1), None),
        ("weekly", None, None),
    ])
    def test_invalid_periods(self, args):
        with pytest.raises(ValueError):
            kpis.resolve_period(*args, date(2026, 10, 15))

    def test_empty_period_and_zero_denominators(self, b):
        h = b.history()
        summary = kpis.period_kpis(h, SEPT)
        assert summary["won"]["events"] == 0 and summary["won"]["values"] == {}
        for rate in (summary["win_rate_closed"], summary["cohort"]["win_rate"], summary["cohort"]["closed_win_rate"]):
            assert rate["value"] is None and rate["denominator"] == 0 and rate["unavailable_reason"]
        assert all(r["rate"]["value"] is None for r in kpis.advancement(h, SEPT))
        assert summary["velocity"]["won"] == {"count": 0, "mean_days": None, "median_days": None}

    def test_equal_timestamps_ordered_by_id(self, b):
        """created and moved in the same flush share a timestamp."""
        b.card(1, "Qualified")
        b.event(1, "moved", D(5), to="Qualified", frm="Lead", event_id=20)
        b.event(1, "created", D(5), to="Lead", event_id=10)
        h = b.history()
        stays = h.stays[1]
        assert [(s.column_id, s.exited_at is None) for s in stays] == [
            (b.cols["Lead"].id, False), (b.cols["Qualified"].id, True)]
        lead = next(s for s in kpis.stage_time(h, SEPT) if s["title"] == "Lead")
        assert lead["complete"] == {"count": 1, "mean_days": 0.0, "median_days": 0.0}

    def test_out_of_order_inserts_are_sorted_by_time(self, b):
        b.card(1, "Award")
        b.event(1, "won", D(9), to="Award", frm="Lead", event_id=1)
        b.event(1, "created", D(1), to="Lead", event_id=2)
        assert [e.event_type for e in b.history().events[1]] == ["created", "won"]
        assert kpis.velocity(b.history(), SEPT)["won"]["mean_days"] == 8.0

    def test_deterministic(self, b):
        for card_id in range(1, 6):
            b.card(card_id, "Award", card_id * 10, "EUR")
            b.event(card_id, "created", D(card_id), to="Lead")
            b.event(card_id, "won", D(card_id + 3), to="Award", frm="Lead")
        first = kpis.period_kpis(b.history(), SEPT)
        assert all(kpis.period_kpis(b.history(), SEPT) == first for _ in range(3))


# ---------------------------------------------------------------------------
# 15, 16: currencies
# ---------------------------------------------------------------------------


class TestCurrencies:
    def test_totals_per_currency_without_conversion(self, b):
        for card_id, value, currency in [(1, 0.1, "EUR"), (2, 0.2, "EUR"), (3, 1000, "USD"), (4, None, None)]:
            b.card(card_id, "Award", value, currency)
            b.event(card_id, "created", D(1), to="Lead")
            b.event(card_id, "won", D(2), to="Award", frm="Lead")
        won = kpis.won_lost(b.history(), SEPT)["won"]
        assert won["values"] == {"EUR": {"total": 0.3, "count": 2}, "USD": {"total": 1000.0, "count": 1}}
        assert won["without_value"] == 1

    def test_pipeline_distribution(self, b):
        b.card(1, "Lead", 5, "EUR")
        b.card(2, "Lead", 7, "BRL")
        b.card(3, "Lead")
        lead = next(s for s in kpis.pipeline(b.history()) if s["title"] == "Lead")
        assert (lead["count"], lead["values"], lead["without_value"]) == (
            3, {"BRL": {"total": 7.0, "count": 1}, "EUR": {"total": 5.0, "count": 1}}, 1)


# ---------------------------------------------------------------------------
# 17-19: API
# ---------------------------------------------------------------------------


@pytest.fixture
def api(db, b, monkeypatch):
    monkeypatch.setattr(kpis, "utcnow", lambda: AS_OF)
    db.add(User(username="rep", password_hash=hash_password("password123"), role="member"))
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), {"Authorization": f"Bearer {create_access_token(data={'sub': 'rep'})}"}
    app.dependency_overrides.clear()


def _seed_mixed(b):
    b.card(1, "Award", 100, "EUR")
    b.event(1, "created", D(1), to="Lead")
    b.event(1, "won", D(4), to="Award", frm="Lead")
    b.card(2, "Lost", 40, "USD")
    b.event(2, "baseline", D(1), to="Proposal")
    b.event(2, "lost", D(6), to="Lost", frm="Proposal")
    b.card(3, "Qualified", 5, "EUR")
    b.event(3, "created", D(2), to="Lead")
    b.event(3, "moved", D(5), to="Qualified", frm="Lead")


class TestApi:
    PARAMS = {"period": "custom", "start": "2026-09-01", "end": "2026-09-30"}

    def test_summary_matches_service(self, api, b, db):
        client, headers = api
        _seed_mixed(b)
        response = client.get(f"/api/boards/{b.id}/kpis/summary", params={**self.PARAMS, "compare": "true"}, headers=headers)
        assert response.status_code == 200
        body = response.json()
        expected = kpis.period_kpis(b.history(), SEPT)
        assert body["current"]["won"] == expected["won"]
        assert body["current"]["win_rate_closed"] == expected["win_rate_closed"] == {
            "value": 0.5, "numerator": 1, "denominator": 2, "unavailable_reason": None}
        assert body["current"]["cohort"]["card_ids"] == [1, 3]
        assert body["previous"]["period"]["label"] == "previous_custom"
        assert body["previous"]["won"]["events"] == 0

    def test_other_endpoints_match_service(self, api, b):
        client, headers = api
        _seed_mixed(b)
        h = b.history()
        conv = client.get(f"/api/boards/{b.id}/kpis/conversion", params=self.PARAMS, headers=headers).json()
        assert [r["entered"] for r in conv["advancement"]] == [r["entered"] for r in kpis.advancement(h, SEPT)]
        times = client.get(f"/api/boards/{b.id}/kpis/stage-time", params=self.PARAMS, headers=headers).json()
        assert [s["complete"] for s in times["stages"]] == [s["complete"] for s in kpis.stage_time(h, SEPT)]
        aging = client.get(f"/api/boards/{b.id}/kpis/aging", headers=headers).json()
        assert [r["card_id"] for r in aging["opportunities"]] == [3]
        pipeline = client.get(f"/api/boards/{b.id}/kpis/pipeline", headers=headers).json()
        assert [s["count"] for s in pipeline["stages"]] == [s["count"] for s in kpis.pipeline(h)]

    def test_pipeline_counts_match_existing_report(self, api, b):
        client, headers = api
        _seed_mixed(b)
        report = client.get(f"/api/boards/{b.id}/report", headers=headers).json()
        pipeline = client.get(f"/api/boards/{b.id}/kpis/pipeline", headers=headers).json()
        assert [s["count"] for s in report["stages"]] == [s["count"] for s in pipeline["stages"]]

    @pytest.mark.parametrize("params", [
        {"period": "weekly"},
        {"period": "custom", "start": "2026-09-01"},
        {"period": "custom", "start": "2026-09-30", "end": "2026-09-01"},
        {"period": "custom", "start": "2026-13-01", "end": "2026-09-01"},
        {"period": "last_30_days", "start": "2026-09-01"},
    ])
    def test_invalid_filters_rejected(self, api, b, params):
        client, headers = api
        response = client.get(f"/api/boards/{b.id}/kpis/summary", params=params, headers=headers)
        assert response.status_code == 422

    @pytest.mark.parametrize("path", ["summary", "conversion", "stage-time", "aging", "pipeline"])
    def test_requires_authentication(self, api, b, path):
        client, _ = api
        assert client.get(f"/api/boards/{b.id}/kpis/{path}").status_code == 401

    def test_unknown_board(self, api):
        client, headers = api
        assert client.get("/api/boards/999/kpis/pipeline", headers=headers).status_code == 404

    def test_reads_never_write(self, api, b, db):
        client, headers = api
        _seed_mixed(b)
        before = (db.query(CardStageEvent).count(), db.query(Card).count())
        for path in ("summary", "conversion", "stage-time", "aging", "pipeline"):
            client.get(f"/api/boards/{b.id}/kpis/{path}", headers=headers)
        assert (db.query(CardStageEvent).count(), db.query(Card).count()) == before

    def test_no_user_data_exposed(self, api, b):
        client, headers = api
        _seed_mixed(b)
        body = client.get(f"/api/boards/{b.id}/kpis/summary", params=self.PARAMS, headers=headers).text
        assert "rep" not in body and "user_id" not in body
