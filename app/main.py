"""FastAPI application.

Server-rendered pages, plain forms, no client-side framework. That is a
deliberate choice for a matchday app used on a cold touchline with one bar of
signal: every page is one request and works with JavaScript switched off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import stats
from app.db import CLUB_NAME, CLUB_SHORT, create_schema, get_session
from app.models import (
    Appearance,
    AppearanceRole,
    ClubSettings,
    Competition,
    EventType,
    FeePayment,
    FineScheduleItem,
    Fixture,
    FixtureStatus,
    LeagueTableRow,
    LeagueTableSnapshot,
    MatchEvent,
    Player,
    PlayerFine,
    PlayerStatus,
    Season,
    Venue,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

BADGE_MAP: dict[str, str] = {
    "Anniesland West Glasgow New Church AFC": "anniesland",
    "Avendale AFC": "avendale",
    "Craighalbert Spartans FC": "craighalbert",
    "Croftfoot Parish AFC": "croftfoot",
    "Fullarton Irvine AFC": "fullarton",
    "Glasgow Elim AFC": "glasgow-elim",
    "Glasgow Free Churches AFC": "glasgow-free-churches",
    "Hope Community Church Barlanark AFC": "hope-barlanark",
    "Houston and Killellan AFC": "houston-killellan",
    "Law Community AFC": "law",
    "South Glasgow AFC": "south-glasgow",
    "United Churches of Ayr AFC": "uca",
    "West Glasgow New Church AFC": "west-glasgow",
}
templates.env.globals["badge_map"] = BADGE_MAP


def ordinal_suffix(number: int) -> str:
    """English ordinal suffix for a league position, e.g. 3 -> 'rd'."""
    if 11 <= (number % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")


templates.env.globals["ordinal"] = ordinal_suffix

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    create_schema()
    from app.seed import seed
    seed(csv_path=BASE_DIR.parent / "data" / "players.csv")
    yield


app = FastAPI(title=f"{CLUB_SHORT} Club App", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

SessionDep = Annotated[Session, Depends(get_session)]


def _season_or_404(session: Session) -> Season:
    season = stats.current_season(session)
    if season is None:
        raise HTTPException(status_code=404, detail="No current season. Run `python -m app.seed`.")
    return season


def _render(request: Request, template: str, session: Session, **context):
    season = _season_or_404(session)
    base = {
        "club_name": CLUB_NAME,
        "season": season,
        "now": datetime.now(),
    }
    return templates.TemplateResponse(request, template, base | context)


def _active_players(session: Session) -> list[Player]:
    return list(
        session.scalars(
            select(Player)
            .where(Player.status != PlayerStatus.left)
            .order_by(Player.last_name, Player.first_name)
        )
    )


def _fixture_or_404(session: Session, fixture_id: int) -> Fixture:
    fixture = session.get(Fixture, fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return fixture


# ---------------------------------------------------------------- overview --


@app.get("/")
def overview(request: Request, session: SessionDep):
    season = _season_or_404(session)
    table = stats.league_table(session, season.id)
    our_row = next((row for row in table if row.club == CLUB_NAME), None)
    return _render(
        request,
        "overview.html",
        session,
        tab="overview",
        record=stats.team_record(session, season.id),
        next_fixture=stats.next_fixture(session, season.id),
        table=table[:5],
        our_row=our_row,
        top_scorers=stats.top_scorers(session, season.id, limit=5),
        top_assists=stats.top_assists(session, season.id, limit=5),
        top_motm=stats.top_motm(session, season.id, limit=5),
        top_managers_motm=stats.top_managers_motm(session, season.id, limit=5),
    )


# ---------------------------------------------------- fixtures and results --


@app.get("/fixtures")
def fixtures(request: Request, session: SessionDep):
    season = _season_or_404(session)
    all_fixtures = list(
        session.scalars(
            select(Fixture).where(Fixture.season_id == season.id).order_by(Fixture.kickoff_at)
        )
    )
    return _render(
        request,
        "fixtures.html",
        session,
        tab="fixtures",
        upcoming=[f for f in all_fixtures if not f.is_played],
        results=[f for f in all_fixtures if f.is_played][::-1],
        record=stats.team_record(session, season.id),
        next_fixture=stats.next_fixture(session, season.id),
    )


@app.post("/fixtures")
def add_fixture(
    session: SessionDep,
    opponent: Annotated[str, Form()],
    kickoff: Annotated[str, Form()],
    venue: Annotated[str, Form()] = "home",
    competition: Annotated[str, Form()] = "league",
    ground: Annotated[str, Form()] = "",
):
    season = _season_or_404(session)
    session.add(
        Fixture(
            season_id=season.id,
            opponent=opponent.strip(),
            kickoff_at=datetime.fromisoformat(kickoff),
            venue=Venue(venue),
            competition=Competition(competition),
            ground=ground.strip() or None,
        )
    )
    session.commit()
    return RedirectResponse("/fixtures", status_code=303)


@app.get("/fixtures/{fixture_id}")
def match_centre(request: Request, session: SessionDep, fixture_id: int):
    fixture = _fixture_or_404(session, fixture_id)
    appearances = {a.player_id: a for a in fixture.appearances}
    return _render(
        request,
        "match.html",
        session,
        tab="fixtures",
        fixture=fixture,
        players=_active_players(session),
        appearances=appearances,
        goal_types=[EventType.goal, EventType.penalty_scored, EventType.own_goal],
        card_types=[EventType.yellow_card, EventType.red_card],
    )


@app.post("/fixtures/{fixture_id}/result")
def save_result(
    session: SessionDep,
    fixture_id: int,
    goals_for: Annotated[int, Form()],
    goals_against: Annotated[int, Form()],
):
    fixture = _fixture_or_404(session, fixture_id)
    if goals_for < 0 or goals_against < 0:
        raise HTTPException(status_code=400, detail="Scores cannot be negative")
    fixture.goals_for = goals_for
    fixture.goals_against = goals_against
    fixture.status = FixtureStatus.played
    session.commit()
    return RedirectResponse(f"/fixtures/{fixture_id}", status_code=303)


@app.post("/fixtures/{fixture_id}/events")
def add_event(
    session: SessionDep,
    fixture_id: int,
    event_type: Annotated[str, Form()],
    player_id: Annotated[int, Form()],
    minute: Annotated[str, Form()] = "",
    assist_player_id: Annotated[str, Form()] = "",
):
    _fixture_or_404(session, fixture_id)
    session.add(
        MatchEvent(
            fixture_id=fixture_id,
            player_id=player_id,
            type=EventType(event_type),
            minute=int(minute) if minute.strip().isdigit() else None,
            assist_player_id=int(assist_player_id) if assist_player_id.strip().isdigit() else None,
        )
    )
    session.commit()
    return RedirectResponse(f"/fixtures/{fixture_id}", status_code=303)


@app.post("/fixtures/{fixture_id}/events/{event_id}/delete")
def delete_event(session: SessionDep, fixture_id: int, event_id: int):
    event = session.get(MatchEvent, event_id)
    if event is not None and event.fixture_id == fixture_id:
        session.delete(event)
        session.commit()
    return RedirectResponse(f"/fixtures/{fixture_id}", status_code=303)


# ------------------------------------------------------------ squad & stats --


@app.get("/squad")
def squad(request: Request, session: SessionDep):
    season = _season_or_404(session)
    lines = stats.player_lines(session, season.id)
    record = stats.team_record(session, season.id)
    return _render(
        request,
        "squad.html",
        session,
        tab="squad",
        lines=lines,
        squad_size=len(lines),
        team_goals=record.scored,
        goals_assigned=stats.goals_assigned(session, season.id),
    )


@app.post("/squad")
def add_player(
    session: SessionDep,
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    squad_number: Annotated[str, Form()] = "",
    position: Annotated[str, Form()] = "",
):
    first, last = first_name.strip(), last_name.strip()
    if not first or not last:
        raise HTTPException(status_code=400, detail="A player needs a first and last name")
    existing = session.scalar(
        select(Player).where(Player.first_name == first, Player.last_name == last)
    )
    if existing is None:
        session.add(
            Player(
                first_name=first,
                last_name=last,
                squad_number=int(squad_number) if squad_number.strip().isdigit() else None,
                position=position.strip() or None,
            )
        )
        session.commit()
    return RedirectResponse("/squad", status_code=303)


@app.post("/squad/{player_id}/status")
def set_player_status(session: SessionDep, player_id: int, status: Annotated[str, Form()]):
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    player.status = PlayerStatus(status)
    session.commit()
    return RedirectResponse("/squad", status_code=303)


_ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_PHOTOS_DIR = BASE_DIR / "static" / "player_photos"


@app.post("/squad/{player_id}/photo")
async def upload_player_photo(session: SessionDep, player_id: int, photo: UploadFile = File()):
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    suffix = Path(photo.filename or "").suffix.lower()
    if suffix not in _ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Upload a JPG, PNG, WebP or GIF image.")
    _PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"player_{player_id}{suffix}"
    dest = _PHOTOS_DIR / filename
    dest.write_bytes(await photo.read())
    player.photo_filename = filename
    session.commit()
    return RedirectResponse("/squad", status_code=303)


# ----------------------------------------------------------------- lineups --


@app.get("/lineups")
def lineups(request: Request, session: SessionDep):
    season = _season_or_404(session)
    all_fixtures = list(
        session.scalars(
            select(Fixture).where(Fixture.season_id == season.id).order_by(Fixture.kickoff_at)
        )
    )
    return _render(
        request,
        "lineups.html",
        session,
        tab="lineups",
        fixtures=all_fixtures,
        next_fixture=stats.next_fixture(session, season.id),
    )


@app.post("/fixtures/{fixture_id}/lineup")
async def save_lineup(request: Request, session: SessionDep, fixture_id: int):
    fixture = _fixture_or_404(session, fixture_id)
    form = await request.form()
    starters = {int(v) for v in form.getlist("start")}
    subs = {int(v) for v in form.getlist("sub")} - starters

    if len(starters) > 11:
        raise HTTPException(status_code=400, detail="A starting eleven is eleven players")

    for appearance in list(fixture.appearances):
        session.delete(appearance)
    session.flush()

    for player_id in starters:
        session.add(
            Appearance(fixture_id=fixture_id, player_id=player_id, role=AppearanceRole.start)
        )
    for player_id in subs:
        session.add(
            Appearance(
                fixture_id=fixture_id,
                player_id=player_id,
                role=AppearanceRole.sub,
                minute_on=60,
            )
        )
    session.commit()
    return RedirectResponse(f"/fixtures/{fixture_id}", status_code=303)


# ------------------------------------------------------------ league table --


@app.get("/table")
def table(request: Request, session: SessionDep):
    season = _season_or_404(session)
    snapshot = stats.latest_snapshot(session, season.id)
    return _render(
        request,
        "table.html",
        session,
        tab="table",
        rows=list(snapshot.rows) if snapshot else [],
        snapshot=snapshot,
    )


@app.post("/table")
def paste_table(session: SessionDep, pasted: Annotated[str, Form()]):
    """Replace the standings with a pasted snapshot.

    Accepts one club per line: ``Club Name P W D L F A Pts``. This is the
    permanent fallback for when the league feed is unavailable, so it stays a
    first-class feature rather than a debug tool.
    """
    season = _season_or_404(session)
    rows = parse_table(pasted)
    if not rows:
        raise HTTPException(status_code=400, detail="No readable rows in that paste")
    snapshot = LeagueTableSnapshot(season_id=season.id, source="manual paste")
    snapshot.rows = [
        LeagueTableRow(position=position, **row) for position, row in enumerate(rows, start=1)
    ]
    session.add(snapshot)
    session.commit()
    return RedirectResponse("/table", status_code=303)


def parse_table(pasted: str) -> list[dict[str, object]]:
    """Parse pasted standings into row dicts.

    Each line ends with seven integers (P W D L F A Pts); everything before them
    is the club name, so club names containing spaces or digits still work.
    """
    rows: list[dict[str, object]] = []
    for line in pasted.splitlines():
        parts = line.replace("|", " ").split()
        if len(parts) < 8:
            continue
        numbers = parts[-7:]
        if not all(part.lstrip("+-").isdigit() for part in numbers):
            continue
        club = " ".join(parts[:-7]).strip(" .-")
        # Drop a leading position number if the paste included one.
        head = club.split(" ", 1)
        if len(head) == 2 and head[0].rstrip(".").isdigit():
            club = head[1]
        if not club:
            continue
        played, won, drawn, lost, goals_for, goals_against, points = (int(n) for n in numbers)
        rows.append(
            {
                "club": club,
                "played": played,
                "won": won,
                "drawn": drawn,
                "lost": lost,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "points": points,
            }
        )
    return rows


# ------------------------------------------------------------------- fines --


@app.get("/fines")
def fines(request: Request, session: SessionDep):
    schedule = list(
        session.scalars(
            select(FineScheduleItem)
            .where(FineScheduleItem.is_active == True)  # noqa: E712
            .order_by(FineScheduleItem.amount_pence)
        )
    )
    outstanding = list(
        session.scalars(
            select(PlayerFine)
            .where(PlayerFine.paid == False)  # noqa: E712
            .order_by(PlayerFine.issued_at.desc())
        )
    )
    # Eager-load players for the outstanding fines
    for fine in outstanding:
        _ = fine.player
    players = _active_players(session)
    return _render(
        request,
        "fines.html",
        session,
        tab="fines",
        schedule=schedule,
        outstanding=outstanding,
        players=players,
    )


@app.post("/fines")
def add_fine(
    session: SessionDep,
    player_id: Annotated[int, Form()],
    reason: Annotated[str, Form()],
    amount_pence: Annotated[int, Form()],
):
    session.add(
        PlayerFine(
            player_id=player_id,
            reason=reason.strip(),
            amount_pence=amount_pence,
        )
    )
    session.commit()
    return RedirectResponse("/fines", status_code=303)


@app.post("/fines/{fine_id}/pay")
def pay_fine(session: SessionDep, fine_id: int):
    fine = session.get(PlayerFine, fine_id)
    if fine is not None:
        fine.paid = True
        fine.paid_at = datetime.now()
        session.commit()
    return RedirectResponse("/fines", status_code=303)


# -------------------------------------------------------------------- fees --


def _get_or_create_settings(session: Session, season_id: int) -> ClubSettings:
    settings = session.scalar(
        select(ClubSettings).where(ClubSettings.season_id == season_id)
    )
    if settings is None:
        settings = ClubSettings(season_id=season_id, monthly_fee_pence=2000)
        session.add(settings)
        session.flush()
    return settings


@app.post("/fees/settings")
def update_fee_settings(
    session: SessionDep,
    amount: Annotated[str, Form()],
):
    season = _season_or_404(session)
    settings = _get_or_create_settings(session, season.id)
    settings.monthly_fee_pence = int(amount) * 100
    session.commit()
    return RedirectResponse("/fees", status_code=303)


@app.get("/fees")
def fees(request: Request, session: SessionDep):
    season = _season_or_404(session)
    settings = _get_or_create_settings(session, season.id)
    players = _active_players(session)
    now = datetime.now()
    current_month = date_type(now.year, now.month, 1)
    fee_payments = list(
        session.scalars(
            select(FeePayment).where(
                FeePayment.season_id == season.id,
                FeePayment.month == current_month,
            )
        )
    )
    payments_dict = {fp.player_id: fp for fp in fee_payments}
    expected_pence = len(players) * settings.monthly_fee_pence
    outstanding_pence = sum(
        settings.monthly_fee_pence
        for p in players
        if p.id not in payments_dict
    )
    return _render(
        request,
        "fees.html",
        session,
        tab="fees",
        settings=settings,
        players=players,
        payments=payments_dict,
        current_month=current_month,
        expected_pence=expected_pence,
        outstanding_pence=outstanding_pence,
    )


@app.post("/fees/{player_id}/pay")
def pay_fee(session: SessionDep, player_id: int):
    season = _season_or_404(session)
    settings = _get_or_create_settings(session, season.id)
    now = datetime.now()
    current_month = date_type(now.year, now.month, 1)
    existing = session.scalar(
        select(FeePayment).where(
            FeePayment.player_id == player_id,
            FeePayment.season_id == season.id,
            FeePayment.month == current_month,
        )
    )
    if existing is None:
        session.add(
            FeePayment(
                player_id=player_id,
                season_id=season.id,
                month=current_month,
                amount_pence=settings.monthly_fee_pence,
                paid_at=now,
            )
        )
    else:
        existing.paid_at = now
        existing.amount_pence = settings.monthly_fee_pence
    session.commit()
    return RedirectResponse("/fees", status_code=303)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
