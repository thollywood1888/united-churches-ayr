from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import stats
from app.main import parse_table
from app.models import (
    Appearance,
    AppearanceRole,
    Base,
    EventType,
    Fixture,
    FixtureStatus,
    MatchEvent,
    Player,
    Season,
    Venue,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db


@pytest.fixture()
def season(session: Session) -> Season:
    season = Season(label="2026/27", is_current=True)
    session.add(season)
    session.commit()
    return season


def _played(session: Session, season: Season, goals_for: int, goals_against: int) -> Fixture:
    fixture = Fixture(
        season_id=season.id,
        opponent="Avendale AFC",
        venue=Venue.home,
        kickoff_at=datetime(2026, 8, 29, 10, 0),
        status=FixtureStatus.played,
        goals_for=goals_for,
        goals_against=goals_against,
    )
    session.add(fixture)
    session.commit()
    return fixture


def test_outcome_and_record(session: Session, season: Season) -> None:
    _played(session, season, 2, 2)
    _played(session, season, 3, 1)
    _played(session, season, 0, 1)

    record = stats.team_record(session, season.id)
    assert (record.played, record.won, record.drawn, record.lost) == (3, 1, 1, 1)
    assert record.points == 4
    assert record.goal_difference == 1
    assert record.record_line == "1–1–1"


def test_unplayed_fixtures_do_not_count(session: Session, season: Season) -> None:
    session.add(
        Fixture(
            season_id=season.id,
            opponent="Law Community AFC",
            kickoff_at=datetime(2026, 10, 3, 10, 0),
        )
    )
    session.commit()
    assert stats.team_record(session, season.id).played == 0


def test_player_line_aggregates_appearances_and_events(session: Session, season: Season) -> None:
    scorer = Player(first_name="Taylor", last_name="Hollywood")
    creator = Player(first_name="Euan", last_name="Kerr")
    session.add_all([scorer, creator])
    session.commit()

    fixture = _played(session, season, 2, 2)
    session.add_all(
        [
            Appearance(fixture_id=fixture.id, player_id=scorer.id, role=AppearanceRole.start),
            Appearance(
                fixture_id=fixture.id,
                player_id=creator.id,
                role=AppearanceRole.sub,
                minute_on=60,
                minute_off=90,
            ),
            MatchEvent(
                fixture_id=fixture.id,
                player_id=scorer.id,
                type=EventType.goal,
                minute=12,
                assist_player_id=creator.id,
            ),
            MatchEvent(
                fixture_id=fixture.id,
                player_id=scorer.id,
                type=EventType.penalty_scored,
                minute=70,
            ),
            MatchEvent(fixture_id=fixture.id, player_id=creator.id, type=EventType.yellow_card),
        ]
    )
    session.commit()

    lines = {line.player.name: line for line in stats.player_lines(session, season.id)}

    assert lines["Taylor Hollywood"].goals == 2
    assert lines["Taylor Hollywood"].minutes == 90
    assert lines["Taylor Hollywood"].starts == 1
    assert lines["Euan Kerr"].assists == 1
    assert lines["Euan Kerr"].minutes == 30
    assert lines["Euan Kerr"].yellow_cards == 1
    assert stats.goals_assigned(session, season.id) == 2


def test_unused_substitute_scores_no_appearance(session: Session, season: Season) -> None:
    player = Player(first_name="Ben", last_name="Dixon")
    session.add(player)
    session.commit()
    fixture = _played(session, season, 1, 0)
    session.add(
        Appearance(fixture_id=fixture.id, player_id=player.id, role=AppearanceRole.unused)
    )
    session.commit()

    line = next(line for line in stats.player_lines(session, season.id) if line.player == player)
    assert (line.appearances, line.minutes) == (0, 0)


def test_next_fixture_is_the_earliest_unplayed(session: Session, season: Season) -> None:
    _played(session, season, 1, 1)
    later = Fixture(
        season_id=season.id, opponent="Avendale AFC", kickoff_at=datetime(2026, 9, 5, 10, 0)
    )
    sooner = Fixture(
        season_id=season.id,
        opponent="Croftfoot Parish AFC",
        kickoff_at=datetime(2026, 8, 22, 10, 0),
    )
    session.add_all([later, sooner])
    session.commit()

    assert stats.next_fixture(session, season.id).opponent == "Croftfoot Parish AFC"


@pytest.mark.parametrize(
    ("line", "expected_club"),
    [
        ("1 Glasgow Elim AFC 1 1 0 0 6 1 3", "Glasgow Elim AFC"),
        ("Houston and Killellan AFC 1 0 0 1 0 2 0", "Houston and Killellan AFC"),
        (
            "| 3 | United Churches of Ayr AFC | 1 | 0 | 1 | 0 | 2 | 2 | 1 |",
            "United Churches of Ayr AFC",
        ),
    ],
)
def test_parse_table_handles_common_paste_shapes(line: str, expected_club: str) -> None:
    rows = parse_table(line)
    assert len(rows) == 1
    assert rows[0]["club"] == expected_club


def test_parse_table_skips_headers_and_blank_lines() -> None:
    pasted = "Pos Club P W D L F A Pts\n\nGlasgow Elim AFC 1 1 0 0 6 1 3\n"
    rows = parse_table(pasted)
    assert [row["club"] for row in rows] == ["Glasgow Elim AFC"]
    assert rows[0]["points"] == 3
