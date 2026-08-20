"""Derived statistics.

Nothing in here is stored. Every figure the app shows is computed from
``Appearance`` and ``MatchEvent``, which is why adding a new stat never needs a
schema change.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    GOAL_EVENTS,
    Appearance,
    AppearanceRole,
    Competition,
    EventType,
    Fixture,
    FixtureStatus,
    LeagueTableRow,
    LeagueTableSnapshot,
    MatchEvent,
    Player,
    PlayerStatus,
    Season,
)


@dataclass(frozen=True, slots=True)
class TeamRecord:
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    scored: int = 0
    conceded: int = 0

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn

    @property
    def goal_difference(self) -> int:
        return self.scored - self.conceded

    @property
    def record_line(self) -> str:
        return f"{self.won}–{self.drawn}–{self.lost}"


@dataclass(frozen=True, slots=True)
class PlayerLine:
    player: Player
    appearances: int = 0
    starts: int = 0
    minutes: int = 0
    goals: int = 0
    assists: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    motm: int = 0
    managers_motm: int = 0
    clean_sheets: int = 0

    @property
    def goals_per_start(self) -> float:
        return round(self.goals / self.starts, 2) if self.starts else 0.0


def current_season(session: Session) -> Season | None:
    return session.scalar(select(Season).where(Season.is_current.is_(True)))


def team_record(
    session: Session, season_id: int, competition: Competition | None = Competition.league
) -> TeamRecord:
    stmt = select(Fixture).where(
        Fixture.season_id == season_id,
        Fixture.status == FixtureStatus.played,
        Fixture.goals_for.is_not(None),
    )
    if competition is not None:
        stmt = stmt.where(Fixture.competition == competition)

    played = won = drawn = lost = scored = conceded = 0
    for fixture in session.scalars(stmt):
        played += 1
        scored += fixture.goals_for or 0
        conceded += fixture.goals_against or 0
        match fixture.outcome:
            case "W":
                won += 1
            case "D":
                drawn += 1
            case _:
                lost += 1
    return TeamRecord(played, won, drawn, lost, scored, conceded)


def _event_counts(session: Session, season_id: int) -> dict[tuple[int, str], int]:
    """Return {(player_id, event_type): count} for one season."""
    rows = session.execute(
        select(MatchEvent.player_id, MatchEvent.type, func.count())
        .join(Fixture, Fixture.id == MatchEvent.fixture_id)
        .where(Fixture.season_id == season_id, MatchEvent.player_id.is_not(None))
        .group_by(MatchEvent.player_id, MatchEvent.type)
    ).all()
    return {(pid, etype.value): count for pid, etype, count in rows}


def _assist_counts(session: Session, season_id: int) -> dict[int, int]:
    # Assists recorded as assist_player_id on a goal event
    linked = session.execute(
        select(MatchEvent.assist_player_id, func.count())
        .join(Fixture, Fixture.id == MatchEvent.fixture_id)
        .where(Fixture.season_id == season_id, MatchEvent.assist_player_id.is_not(None))
        .group_by(MatchEvent.assist_player_id)
    ).all()
    # Assists recorded as a standalone EventType.assist event
    standalone = session.execute(
        select(MatchEvent.player_id, func.count())
        .join(Fixture, Fixture.id == MatchEvent.fixture_id)
        .where(
            Fixture.season_id == season_id,
            MatchEvent.type == EventType.assist,
            MatchEvent.player_id.is_not(None),
        )
        .group_by(MatchEvent.player_id)
    ).all()
    totals: dict[int, int] = {}
    for pid, count in linked:
        totals[pid] = totals.get(pid, 0) + count
    for pid, count in standalone:
        totals[pid] = totals.get(pid, 0) + count
    return totals


def player_lines(session: Session, season_id: int, include_left: bool = False) -> list[PlayerLine]:
    """One row per player: appearances, minutes, goals, assists, cards."""
    player_stmt = select(Player).order_by(Player.last_name, Player.first_name)
    if not include_left:
        player_stmt = player_stmt.where(Player.status != PlayerStatus.left)
    players = list(session.scalars(player_stmt))

    appearances: dict[int, list[Appearance]] = {}
    for appearance in session.scalars(
        select(Appearance)
        .join(Fixture, Fixture.id == Appearance.fixture_id)
        .where(Fixture.season_id == season_id)
    ):
        appearances.setdefault(appearance.player_id, []).append(appearance)

    # fixture_id set where team kept a clean sheet
    clean_sheet_fixture_ids: set[int] = {
        f.id
        for f in session.scalars(
            select(Fixture).where(
                Fixture.season_id == season_id,
                Fixture.status == FixtureStatus.played,
                Fixture.goals_against == 0,
            )
        )
    }

    events = _event_counts(session, season_id)
    assists = _assist_counts(session, season_id)

    lines: list[PlayerLine] = []
    for player in players:
        played = [a for a in appearances.get(player.id, []) if a.role is not AppearanceRole.unused]
        goals = sum(events.get((player.id, e.value), 0) for e in GOAL_EVENTS)
        cs = sum(1 for a in played if a.fixture_id in clean_sheet_fixture_ids)
        lines.append(
            PlayerLine(
                player=player,
                appearances=len(played),
                starts=sum(1 for a in played if a.role is AppearanceRole.start),
                minutes=sum(a.minutes for a in played),
                goals=goals,
                assists=assists.get(player.id, 0),
                yellow_cards=events.get((player.id, EventType.yellow_card.value), 0),
                red_cards=events.get((player.id, EventType.red_card.value), 0),
                motm=events.get((player.id, EventType.motm.value), 0),
                managers_motm=events.get((player.id, EventType.managers_motm.value), 0),
                clean_sheets=cs,
            )
        )
    return lines


def top_scorers(session: Session, season_id: int, limit: int = 5) -> list[PlayerLine]:
    scoring = [line for line in player_lines(session, season_id) if line.goals]
    scoring.sort(key=lambda line: (-line.goals, -line.assists, line.player.last_name))
    return scoring[:limit]


def top_assists(session: Session, season_id: int, limit: int = 5) -> list[PlayerLine]:
    lines = [line for line in player_lines(session, season_id) if line.assists]
    lines.sort(key=lambda line: (-line.assists, line.player.last_name))
    return lines[:limit]


def top_motm(session: Session, season_id: int, limit: int = 5) -> list[PlayerLine]:
    lines = [line for line in player_lines(session, season_id) if line.motm]
    lines.sort(key=lambda line: (-line.motm, line.player.last_name))
    return lines[:limit]


def top_managers_motm(session: Session, season_id: int, limit: int = 5) -> list[PlayerLine]:
    lines = [line for line in player_lines(session, season_id) if line.managers_motm]
    lines.sort(key=lambda line: (-line.managers_motm, line.player.last_name))
    return lines[:limit]


def goals_assigned(session: Session, season_id: int) -> int:
    """Goals attributed to a named player, used to flag unassigned goals."""
    return (
        session.scalar(
            select(func.count())
            .select_from(MatchEvent)
            .join(Fixture, Fixture.id == MatchEvent.fixture_id)
            .where(
                Fixture.season_id == season_id,
                MatchEvent.type.in_(GOAL_EVENTS),
                MatchEvent.player_id.is_not(None),
            )
        )
        or 0
    )


def latest_snapshot(session: Session, season_id: int) -> LeagueTableSnapshot | None:
    """The most recent standings capture for a season, or None if never taken."""
    return session.scalar(
        select(LeagueTableSnapshot)
        .where(LeagueTableSnapshot.season_id == season_id)
        .order_by(LeagueTableSnapshot.fetched_at.desc(), LeagueTableSnapshot.id.desc())
        .limit(1)
    )


def league_table(session: Session, season_id: int) -> list[LeagueTableRow]:
    snapshot = latest_snapshot(session, season_id)
    return list(snapshot.rows) if snapshot else []


def next_fixture(session: Session, season_id: int) -> Fixture | None:
    return session.scalar(
        select(Fixture)
        .where(Fixture.season_id == season_id, Fixture.status == FixtureStatus.scheduled)
        .order_by(Fixture.kickoff_at)
        .limit(1)
    )
