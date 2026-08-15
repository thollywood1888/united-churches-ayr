"""Regression cover for snapshot selection.

The first version compared a Python-side ``max(fetched_at)`` against stored
values written by SQLite's ``CURRENT_TIMESTAMP``. The two formats differed by
microseconds, so the table silently returned zero rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import stats
from app.models import Base, LeagueTableRow, LeagueTableSnapshot, Season


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        db.add(Season(id=1, label="2026/27", is_current=True))
        db.commit()
        yield db


def _snapshot(session: Session, clubs: list[str], fetched_at: datetime | None = None) -> None:
    snapshot = LeagueTableSnapshot(season_id=1, source="test")
    if fetched_at is not None:
        snapshot.fetched_at = fetched_at
    snapshot.rows = [
        LeagueTableRow(position=position, club=club) for position, club in enumerate(clubs, start=1)
    ]
    session.add(snapshot)
    session.commit()


def test_a_batch_of_rows_forms_one_snapshot(session: Session) -> None:
    _snapshot(session, ["Glasgow Elim AFC", "United Churches of Ayr AFC"])
    rows = stats.league_table(session, 1)
    assert [row.club for row in rows] == ["Glasgow Elim AFC", "United Churches of Ayr AFC"]


def test_only_the_newest_snapshot_is_returned(session: Session) -> None:
    old = datetime(2026, 8, 22, 18, 0)
    _snapshot(session, ["Avendale AFC"], fetched_at=old)
    _snapshot(session, ["Glasgow Elim AFC", "Avendale AFC"], fetched_at=old + timedelta(days=7))

    rows = stats.league_table(session, 1)
    assert [row.club for row in rows] == ["Glasgow Elim AFC", "Avendale AFC"]


def test_no_snapshot_returns_empty(session: Session) -> None:
    assert stats.league_table(session, 1) == []


def test_goal_difference_is_derived(session: Session) -> None:
    snapshot = LeagueTableSnapshot(season_id=1, source="test")
    snapshot.rows = [
        LeagueTableRow(position=1, club="Glasgow Elim AFC", goals_for=6, goals_against=1)
    ]
    session.add(snapshot)
    session.commit()
    assert stats.league_table(session, 1)[0].goal_difference == 5
