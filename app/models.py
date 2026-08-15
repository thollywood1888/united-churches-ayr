"""Data model for the club app.

Design notes
------------
* Players are never deleted. ``Player.status`` moves to ``left`` so historic
  appearances and goals survive.
* A result *is* a played fixture, so scores live on ``Fixture`` rather than in a
  separate results table.
* There are no stats tables. Every statistic (top scorer, minutes, cards) is
  derived from ``Appearance`` and ``MatchEvent`` at query time. Adding a new
  stat is a query, not a migration.
* The league table is a *snapshot* of someone else's data. It is never computed
  from our own results and always carries the time and source it came from.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PlayerStatus(StrEnum):
    active = "active"
    injured = "injured"
    left = "left"


class Competition(StrEnum):
    league = "league"
    cup = "cup"
    friendly = "friendly"


class FixtureStatus(StrEnum):
    scheduled = "scheduled"
    played = "played"
    postponed = "postponed"
    cancelled = "cancelled"


class Venue(StrEnum):
    home = "home"
    away = "away"
    neutral = "neutral"


class AppearanceRole(StrEnum):
    start = "start"
    sub = "sub"
    unused = "unused"


class EventType(StrEnum):
    goal = "goal"
    own_goal = "own_goal"
    penalty_scored = "penalty_scored"
    penalty_missed = "penalty_missed"
    assist = "assist"
    yellow_card = "yellow_card"
    red_card = "red_card"
    motm = "motm"
    managers_motm = "managers_motm"


#: Event types that add one to a player's goal tally.
GOAL_EVENTS = (EventType.goal, EventType.penalty_scored)


class AvailabilityStatus(StrEnum):
    available = "available"
    unavailable = "unavailable"
    maybe = "maybe"


class Season(Base):
    __tablename__ = "season"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(16), unique=True)  # "2026/27"
    division: Mapped[str] = mapped_column(String(80), default="SECL Premier Division")
    is_current: Mapped[bool] = mapped_column(default=False)

    fixtures: Mapped[list[Fixture]] = relationship(back_populates="season")


class Player(Base):
    __tablename__ = "player"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(60))
    last_name: Mapped[str] = mapped_column(String(60))
    squad_number: Mapped[int | None] = mapped_column(default=None)
    position: Mapped[str | None] = mapped_column(String(20), default=None)
    status: Mapped[PlayerStatus] = mapped_column(
        Enum(PlayerStatus, native_enum=False), default=PlayerStatus.active
    )
    photo_filename: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    appearances: Mapped[list[Appearance]] = relationship(back_populates="player")
    events: Mapped[list[MatchEvent]] = relationship(
        back_populates="player", foreign_keys="MatchEvent.player_id"
    )

    __table_args__ = (UniqueConstraint("first_name", "last_name", name="uq_player_name"),)

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Fixture(Base):
    __tablename__ = "fixture"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"))
    competition: Mapped[Competition] = mapped_column(
        Enum(Competition, native_enum=False), default=Competition.league
    )
    opponent: Mapped[str] = mapped_column(String(120))
    venue: Mapped[Venue] = mapped_column(Enum(Venue, native_enum=False), default=Venue.home)
    ground: Mapped[str | None] = mapped_column(String(160), default=None)
    kickoff_at: Mapped[datetime]
    status: Mapped[FixtureStatus] = mapped_column(
        Enum(FixtureStatus, native_enum=False), default=FixtureStatus.scheduled
    )
    goals_for: Mapped[int | None] = mapped_column(default=None)
    goals_against: Mapped[int | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(String(500), default=None)

    season: Mapped[Season] = relationship(back_populates="fixtures")
    appearances: Mapped[list[Appearance]] = relationship(
        back_populates="fixture", cascade="all, delete-orphan"
    )
    events: Mapped[list[MatchEvent]] = relationship(
        back_populates="fixture", cascade="all, delete-orphan"
    )
    availability: Mapped[list[Availability]] = relationship(
        back_populates="fixture", cascade="all, delete-orphan"
    )

    @property
    def is_played(self) -> bool:
        return self.status is FixtureStatus.played and self.goals_for is not None

    @property
    def outcome(self) -> str | None:
        """'W', 'D' or 'L' from our point of view."""
        if not self.is_played or self.goals_against is None or self.goals_for is None:
            return None
        if self.goals_for > self.goals_against:
            return "W"
        return "D" if self.goals_for == self.goals_against else "L"

    @property
    def scoreline(self) -> str:
        if not self.is_played:
            return "—"
        return f"{self.goals_for}–{self.goals_against}"


class Appearance(Base):
    __tablename__ = "appearance"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixture.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    role: Mapped[AppearanceRole] = mapped_column(Enum(AppearanceRole, native_enum=False))
    shirt_number: Mapped[int | None] = mapped_column(default=None)
    pitch_slot: Mapped[str | None] = mapped_column(String(16), default=None)
    minute_on: Mapped[int] = mapped_column(default=0)
    minute_off: Mapped[int] = mapped_column(default=90)

    fixture: Mapped[Fixture] = relationship(back_populates="appearances")
    player: Mapped[Player] = relationship(back_populates="appearances")

    __table_args__ = (UniqueConstraint("fixture_id", "player_id", name="uq_appearance"),)

    @property
    def minutes(self) -> int:
        if self.role is AppearanceRole.unused:
            return 0
        return max(0, self.minute_off - self.minute_on)


class MatchEvent(Base):
    __tablename__ = "match_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixture.id"))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"), default=None)
    type: Mapped[EventType] = mapped_column(Enum(EventType, native_enum=False))
    minute: Mapped[int | None] = mapped_column(default=None)
    assist_player_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"), default=None)

    fixture: Mapped[Fixture] = relationship(back_populates="events")
    player: Mapped[Player | None] = relationship(back_populates="events", foreign_keys=[player_id])
    assist_player: Mapped[Player | None] = relationship(foreign_keys=[assist_player_id])


class Availability(Base):
    __tablename__ = "availability"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixture.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    status: Mapped[AvailabilityStatus] = mapped_column(Enum(AvailabilityStatus, native_enum=False))
    responded_at: Mapped[datetime] = mapped_column(default=datetime.now)

    fixture: Mapped[Fixture] = relationship(back_populates="availability")
    player: Mapped[Player] = relationship()

    __table_args__ = (UniqueConstraint("fixture_id", "player_id", name="uq_availability"),)


class LeagueTableSnapshot(Base):
    """One capture of the division standings, taken at a point in time.

    A snapshot is an explicit entity rather than "rows that share a timestamp":
    rows written in a single batch get microsecond-different defaults, which
    made an implied grouping unreliable.
    """

    __tablename__ = "league_table_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"))
    fetched_at: Mapped[datetime] = mapped_column(default=datetime.now)
    source: Mapped[str] = mapped_column(String(200), default="manual")

    rows: Mapped[list[LeagueTableRow]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="LeagueTableRow.position",
    )


class LeagueTableRow(Base):
    """One club's line within a snapshot."""

    __tablename__ = "league_table_row"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("league_table_snapshot.id"))
    position: Mapped[int]
    club: Mapped[str] = mapped_column(String(120))
    played: Mapped[int] = mapped_column(default=0)
    won: Mapped[int] = mapped_column(default=0)
    drawn: Mapped[int] = mapped_column(default=0)
    lost: Mapped[int] = mapped_column(default=0)
    goals_for: Mapped[int] = mapped_column(default=0)
    goals_against: Mapped[int] = mapped_column(default=0)
    points: Mapped[int] = mapped_column(default=0)

    snapshot: Mapped[LeagueTableSnapshot] = relationship(back_populates="rows")

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


class FineScheduleItem(Base):
    __tablename__ = "fine_schedule_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(String(120))
    amount_pence: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=True)


class PlayerFine(Base):
    __tablename__ = "player_fine"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    reason: Mapped[str] = mapped_column(String(200))
    amount_pence: Mapped[int]
    issued_at: Mapped[datetime] = mapped_column(default=datetime.now)
    due_date: Mapped[date_type | None] = mapped_column(default=None)
    paid: Mapped[bool] = mapped_column(default=False)
    paid_at: Mapped[datetime | None] = mapped_column(default=None)

    player: Mapped["Player"] = relationship()


class ClubSettings(Base):
    __tablename__ = "club_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"), unique=True)
    monthly_fee_pence: Mapped[int] = mapped_column(default=2000)


class FeePayment(Base):
    __tablename__ = "fee_payment"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"))
    month: Mapped[date_type]
    amount_pence: Mapped[int]
    paid_at: Mapped[datetime | None] = mapped_column(default=None)

    player: Mapped["Player"] = relationship()

    __table_args__ = (UniqueConstraint("player_id", "season_id", "month", name="uq_fee_payment"),)
