"""Seed the database with the 2026/27 season, fixture list and division.

Idempotent: safe to run repeatedly. It only inserts what is missing and never
overwrites data you have entered.

Run with::

    python -m app.seed
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import CLUB_NAME, DATA_DIR, SessionLocal, create_schema
from app.models import (
    ClubSettings,
    FineScheduleItem,
    Fixture,
    LeagueTableRow,
    LeagueTableSnapshot,
    Player,
    Season,
    Venue,
)

SEASON_LABEL = "2026/27"
DIVISION = "SECL Premier Division"

#: (kickoff, opponent, venue) — as supplied by the club.
FIXTURES: list[tuple[str, str, Venue]] = [
    ("2026-08-22 10:00", "Anniesland West Glasgow New Church AFC", Venue.away),
    ("2026-08-29 10:00", "Avendale AFC", Venue.home),
    ("2026-09-05 10:00", "Croftfoot Parish AFC", Venue.away),
    ("2026-09-19 10:00", "Fullarton Irvine AFC", Venue.home),
    ("2026-10-03 10:00", "Law Community AFC", Venue.away),
    ("2026-10-10 10:00", "Hope Community Church Barlanark AFC", Venue.home),
    ("2026-10-17 10:00", "West Glasgow New Church AFC", Venue.away),
    ("2026-10-24 10:00", "Glasgow Free Churches AFC", Venue.home),
    ("2026-10-31 10:00", "Glasgow Elim AFC", Venue.away),
    ("2026-11-07 10:00", "Craighalbert Spartans FC", Venue.home),
    ("2026-11-14 10:00", "Houston and Killellan AFC", Venue.away),
]

#: Clubs in the division. Seeded at zero — the real standings come from the
#: league site, never from our own results.
DIVISION_CLUBS: list[str] = [
    "Anniesland West Glasgow New Church AFC",
    "Avendale AFC",
    "Craighalbert Spartans FC",
    "Croftfoot Parish AFC",
    "Fullarton Irvine AFC",
    "Glasgow Elim AFC",
    "Glasgow Free Churches AFC",
    "Hope Community Church Barlanark AFC",
    "Houston and Killellan AFC",
    "Law Community AFC",
    "South Glasgow AFC",
    CLUB_NAME,
    "West Glasgow New Church AFC",
]


def _get_or_create_season(session: Session) -> Season:
    season = session.scalar(select(Season).where(Season.label == SEASON_LABEL))
    if season is None:
        season = Season(label=SEASON_LABEL, division=DIVISION, is_current=True)
        session.add(season)
        session.flush()
    return season


def _seed_players(session: Session, csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    existing = {(p.first_name, p.last_name) for p in session.scalars(select(Player))}
    added = 0
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            first, last = row["first_name"].strip(), row["last_name"].strip()
            if not first or not last or (first, last) in existing:
                continue
            number = row.get("squad_number", "").strip()
            session.add(
                Player(
                    first_name=first,
                    last_name=last,
                    squad_number=int(number) if number.isdigit() else None,
                    position=(row.get("position") or "").strip() or None,
                )
            )
            existing.add((first, last))
            added += 1
    return added


def _seed_fixtures(session: Session, season: Season) -> int:
    existing = {
        (f.opponent, f.kickoff_at)
        for f in session.scalars(select(Fixture).where(Fixture.season_id == season.id))
    }
    added = 0
    for raw_kickoff, opponent, venue in FIXTURES:
        kickoff = datetime.strptime(raw_kickoff, "%Y-%m-%d %H:%M")
        if (opponent, kickoff) in existing:
            continue
        session.add(
            Fixture(season_id=season.id, opponent=opponent, venue=venue, kickoff_at=kickoff)
        )
        added += 1
    return added


def _seed_fine_schedule(session: Session) -> int:
    if session.scalar(select(FineScheduleItem).limit(1)) is not None:
        return 0
    items = [
        FineScheduleItem(description="Late for a home game", amount_pence=100),
        FineScheduleItem(description="Kicking the ball over the fence", amount_pence=100),
        FineScheduleItem(description="Late for training", amount_pence=100),
        FineScheduleItem(description="Getting nutmegged in training", amount_pence=100),
        FineScheduleItem(description="Forgetting kit", amount_pence=200),
        FineScheduleItem(description="Ball goes into the car park", amount_pence=200),
        FineScheduleItem(description="No training kit", amount_pence=200),
        FineScheduleItem(description="Red card", amount_pence=500),
        FineScheduleItem(description="Out drinking the night before a game", amount_pence=500),
    ]
    for item in items:
        session.add(item)
    return len(items)


def _seed_club_settings(session: Session, season: Season) -> int:
    existing = session.scalar(
        select(ClubSettings).where(ClubSettings.season_id == season.id)
    )
    if existing is not None:
        return 0
    session.add(ClubSettings(season_id=season.id, monthly_fee_pence=2650))
    return 1


def _seed_table(session: Session, season: Season) -> int:
    already = session.scalar(
        select(LeagueTableSnapshot).where(LeagueTableSnapshot.season_id == season.id).limit(1)
    )
    if already is not None:
        return 0
    snapshot = LeagueTableSnapshot(season_id=season.id, source="seed:pre-season")
    snapshot.rows = [
        LeagueTableRow(position=position, club=club)
        for position, club in enumerate(DIVISION_CLUBS, start=1)
    ]
    session.add(snapshot)
    return len(DIVISION_CLUBS)


def seed(session: Session | None = None, csv_path: Path | None = None) -> dict[str, int]:
    create_schema()
    owns_session = session is None
    session = session or SessionLocal()
    csv_path = csv_path or DATA_DIR / "players.csv"
    try:
        season = _get_or_create_season(session)
        counts = {
            "players": _seed_players(session, csv_path),
            "fixtures": _seed_fixtures(session, season),
            "table_rows": _seed_table(session, season),
            "fine_schedule": _seed_fine_schedule(session),
            "club_settings": _seed_club_settings(session, season),
        }
        session.commit()
        return counts
    finally:
        if owns_session:
            session.close()


if __name__ == "__main__":  # pragma: no cover
    result = seed()
    print(
        f"Seeded {result['players']} players, {result['fixtures']} fixtures, "
        f"{result['table_rows']} league table rows, "
        f"{result['fine_schedule']} fine schedule items, "
        f"{result['club_settings']} club settings."
    )
