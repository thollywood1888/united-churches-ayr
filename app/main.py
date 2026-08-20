"""FastAPI application.

Server-rendered pages, plain forms, no client-side framework. That is a
deliberate choice for a matchday app used on a cold touchline with one bar of
signal: every page is one request and works with JavaScript switched off.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import stats
from app.db import CLUB_NAME, CLUB_SHORT, create_schema, get_session
from app.league_table import LEAGUE_TABLE_SOURCE, LEAGUE_TABLE_URL, fetch_league_table
from app.lineup import FORMATION_4231, FORMATIONS, POSITION_GROUPS, make_rows, player_group
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

USERS = {"Gaffer": "jeans1", "squad": "treble1"}
SECRET_KEY = os.environ.get("SECRET_KEY", "uca-afc-2026-change-in-prod")

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


class _AuthMiddleware(BaseHTTPMiddleware):
    _PUBLIC = {"/login", "/healthz"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in self._PUBLIC or path.startswith("/static"):
            return await call_next(request)
        user = request.session.get("user")
        if not user:
            return RedirectResponse("/login", status_code=302)
        if request.method == "POST" and user != "Gaffer" and path != "/logout":
            referer = request.headers.get("referer", "/")
            return RedirectResponse(referer, status_code=303)
        return await call_next(request)


app = FastAPI(title=f"{CLUB_SHORT} Club App", lifespan=lifespan)
app.add_middleware(_AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
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
        "is_gaffer": request.session.get("user") == "Gaffer",
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


# ------------------------------------------------------------------- auth --


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


def _authenticate(username: str, password: str) -> str | None:
    """Return the canonical username if the credentials match.

    Usernames are matched case-insensitively and trimmed so a phone that
    sends ``gaffer`` still unlocks the ``Gaffer`` account.
    """
    offered = username.strip().lower()
    for name, secret in USERS.items():
        if name.lower() == offered and secret == password:
            return name
    return None


@app.post("/login")
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    matched = _authenticate(username, password)
    if matched is not None:
        request.session["user"] = matched
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Incorrect username or password"}, status_code=401
    )


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- overview --


@app.get("/")
def overview(request: Request, session: SessionDep):
    season = _season_or_404(session)
    table = stats.league_table(session, season.id)
    our_row = next((row for row in table if row.club == CLUB_NAME), None)
    nf = stats.next_fixture(session, season.id)
    last_result = session.scalar(
        select(Fixture)
        .where(Fixture.season_id == season.id, Fixture.status == FixtureStatus.played)
        .order_by(Fixture.kickoff_at.desc())
        .limit(1)
    )
    upcoming = list(session.scalars(
        select(Fixture)
        .where(Fixture.season_id == season.id, Fixture.status == FixtureStatus.scheduled)
        .order_by(Fixture.kickoff_at)
        .limit(3)
    ))
    days_to_kickoff = (nf.kickoff_at.date() - datetime.now().date()).days if nf else None
    return _render(
        request,
        "overview.html",
        session,
        tab="overview",
        record=stats.team_record(session, season.id),
        next_fixture=nf,
        table=table[:5],
        our_row=our_row,
        top_scorers=stats.top_scorers(session, season.id, limit=5),
        top_assists=stats.top_assists(session, season.id, limit=5),
        top_motm=stats.top_motm(session, season.id, limit=5),
        top_managers_motm=stats.top_managers_motm(session, season.id, limit=5),
        last_result=last_result,
        upcoming_fixtures=upcoming,
        days_to_kickoff=days_to_kickoff,
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
def match_centre(
    request: Request,
    session: SessionDep,
    fixture_id: int,
    slot: str | None = None,
):
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
        **_pitch_context(fixture, _active_players(session), slot, f"/fixtures/{fixture_id}"),
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


def _player_has_history(session: Session, player_id: int) -> bool:
    if session.scalar(select(Appearance.id).where(Appearance.player_id == player_id).limit(1)):
        return True
    if session.scalar(
        select(MatchEvent.id)
        .where((MatchEvent.player_id == player_id) | (MatchEvent.assist_player_id == player_id))
        .limit(1)
    ):
        return True
    if session.scalar(select(PlayerFine.id).where(PlayerFine.player_id == player_id).limit(1)):
        return True
    if session.scalar(select(FeePayment.id).where(FeePayment.player_id == player_id).limit(1)):
        return True
    return False


_SQUAD_POSITIONS = ["Goalkeeper", "Defender", "Midfielder", "Forward"]


@app.get("/squad")
def squad(request: Request, session: SessionDep):
    season = _season_or_404(session)
    lines = stats.player_lines(session, season.id)
    record = stats.team_record(session, season.id)
    left = list(
        session.scalars(
            select(Player)
            .where(Player.status == PlayerStatus.left)
            .order_by(Player.last_name, Player.first_name)
        )
    )
    scorers = stats.top_scorers(session, season.id, limit=1)
    assisters = stats.top_assists(session, season.id, limit=1)
    motm_leaders = stats.top_motm(session, season.id, limit=1)
    grouped: dict[str, list] = {pos: [] for pos in _SQUAD_POSITIONS}
    ungrouped: list = []
    for line in lines:
        pos = line.player.position
        if pos in grouped:
            grouped[pos].append(line)
        else:
            ungrouped.append(line)
    return _render(
        request,
        "squad.html",
        session,
        tab="squad",
        lines=lines,
        position_groups=[(pos, grouped[pos]) for pos in _SQUAD_POSITIONS if grouped[pos]],
        ungrouped_lines=ungrouped,
        left_players=left,
        squad_size=len(lines),
        team_goals=record.scored,
        goals_assigned=stats.goals_assigned(session, season.id),
        unassigned_count=len(ungrouped),
        top_scorer=scorers[0] if scorers else None,
        top_assister=assisters[0] if assisters else None,
        top_motm_player=motm_leaders[0] if motm_leaders else None,
    )


@app.get("/squad/{player_id}")
def edit_player_page(request: Request, session: SessionDep, player_id: int):
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return _render(
        request,
        "player.html",
        session,
        tab="squad",
        player=player,
        has_history=_player_has_history(session, player.id),
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
    number = int(squad_number) if squad_number.strip().isdigit() else None
    role = position.strip() or None
    existing = session.scalar(
        select(Player).where(Player.first_name == first, Player.last_name == last)
    )
    if existing is None:
        session.add(
            Player(first_name=first, last_name=last, squad_number=number, position=role)
        )
    else:
        existing.status = PlayerStatus.active
        if number is not None:
            existing.squad_number = number
        if role is not None:
            existing.position = role
    session.commit()
    return RedirectResponse("/squad", status_code=303)


@app.post("/squad/{player_id}")
def update_player(
    session: SessionDep,
    player_id: int,
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    squad_number: Annotated[str, Form()] = "",
    position: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "active",
):
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    first, last = first_name.strip(), last_name.strip()
    if not first or not last:
        raise HTTPException(status_code=400, detail="A player needs a first and last name")
    clash = session.scalar(
        select(Player).where(
            Player.first_name == first,
            Player.last_name == last,
            Player.id != player_id,
        )
    )
    if clash is not None:
        raise HTTPException(status_code=400, detail="That name is already on the books")
    player.first_name = first
    player.last_name = last
    player.squad_number = int(squad_number) if squad_number.strip().isdigit() else None
    player.position = position.strip() or None
    player.status = PlayerStatus(status)
    session.commit()
    return RedirectResponse("/squad", status_code=303)


@app.post("/squad/{player_id}/position")
def update_player_position(
    session: SessionDep,
    player_id: int,
    position: Annotated[str, Form()] = "",
):
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    pos = position.strip()
    valid = {"Goalkeeper", "Defender", "Midfielder", "Forward", ""}
    if pos not in valid:
        raise HTTPException(status_code=400, detail="Invalid position")
    player.position = pos or None
    session.commit()
    return RedirectResponse("/squad", status_code=303)


@app.post("/squad/{player_id}/remove")
def remove_player(session: SessionDep, player_id: int):
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    player.status = PlayerStatus.left
    if not _player_has_history(session, player_id) and player.photo_filename:
        photo = _PHOTOS_DIR / player.photo_filename
        if photo.is_file():
            photo.unlink()
    session.commit()
    return RedirectResponse("/squad", status_code=303)


@app.post("/squad/{player_id}/restore")
def restore_player(session: SessionDep, player_id: int):
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    player.status = PlayerStatus.active
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


def _safe_next(raw: str | None, fallback: str) -> str:
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return fallback


def _lineups_url(
    fixture_id: int,
    *,
    slot: str | None = None,
    group: str | None = None,
    q: str | None = None,
    saved: bool = False,
) -> str:
    params: dict[str, str] = {"fixture_id": str(fixture_id)}
    if slot:
        params["slot"] = slot
    if group and group != "ALL":
        params["group"] = group
    if q:
        params["q"] = q
    if saved:
        params["saved"] = "1"
    return "/lineups?" + urlencode(params)


def _sync_captain(fixture: Fixture) -> None:
    if fixture.captain_player_id is None:
        return
    captain = _appearance_for_player(fixture, fixture.captain_player_id)
    if captain is None or captain.role is not AppearanceRole.start:
        fixture.captain_player_id = None


def _pitch_context(
    fixture: Fixture, players: list[Player], pick_slot: str | None, return_to: str
) -> dict:
    formation_key = fixture.formation if fixture.formation in FORMATIONS else "4-2-3-1"
    formation_slots = FORMATIONS[formation_key]
    formation_rows = make_rows(formation_slots)
    slot_keys = {slot.key for slot in formation_slots}

    by_slot: dict[str, Appearance] = {}
    unplaced: list[Appearance] = []
    bench: list[Appearance] = []
    for appearance in fixture.appearances:
        if appearance.role is AppearanceRole.sub:
            bench.append(appearance)
        elif appearance.pitch_slot in slot_keys:
            by_slot[appearance.pitch_slot] = appearance
        elif appearance.role is AppearanceRole.start:
            unplaced.append(appearance)
    taken = {appearance.player_id for appearance in fixture.appearances}
    pick = pick_slot if pick_slot in slot_keys else None
    if return_to.startswith("/fixtures/"):
        next_url = return_to
        pick_base = return_to
    else:
        next_url = f"/lineups?fixture_id={fixture.id}"
        pick_base = next_url
    on_pitch_ids = {appearance.player_id for appearance in by_slot.values()}
    bench_ids = {appearance.player_id for appearance in bench}
    first_empty = next((slot.key for slot in formation_slots if slot.key not in by_slot), None)
    sidebar: list[dict[str, object]] = []
    for player in players:
        if player.id in on_pitch_ids:
            continue
        if player.status is PlayerStatus.injured:
            status = "unavailable"
        elif player.id in bench_ids:
            status = "bench"
        else:
            status = "available"
        sidebar.append(
            {
                "player": player,
                "group": player_group(player.position),
                "status": status,
            }
        )
    return {
        "pitch_slots": formation_slots,
        "pitch_rows": formation_rows,
        "formation_key": formation_key,
        "formations": list(FORMATIONS.keys()),
        "slot_fill": by_slot,
        "unplaced_starters": unplaced,
        "bench": bench,
        "available_players": [player for player in players if player.id not in taken],
        "all_players": players,
        "pick_slot": pick,
        "pick_label": next((s.label for s in formation_slots if s.key == pick), None),
        "next_url": next_url,
        "pick_base": pick_base,
        "starters_on_pitch": len(by_slot),
        "spots_remaining": 11 - len(by_slot),
        "sidebar_players": sidebar,
        "first_empty_slot": first_empty,
        "position_groups": POSITION_GROUPS,
        "captain_id": fixture.captain_player_id,
        "xi_players": [by_slot[slot.key].player for slot in formation_slots if slot.key in by_slot],
    }


def _appearance_for_player(fixture: Fixture, player_id: int) -> Appearance | None:
    return next((a for a in fixture.appearances if a.player_id == player_id), None)


def _appearance_in_slot(fixture: Fixture, slot: str) -> Appearance | None:
    return next((a for a in fixture.appearances if a.pitch_slot == slot), None)


@app.get("/lineups")
def lineups(
    request: Request,
    session: SessionDep,
    fixture_id: int | None = None,
    slot: str | None = None,
    group: str | None = None,
    q: str | None = None,
    saved: str | None = None,
):
    season = _season_or_404(session)
    all_fixtures = list(
        session.scalars(
            select(Fixture).where(Fixture.season_id == season.id).order_by(Fixture.kickoff_at)
        )
    )
    selected = next((f for f in all_fixtures if f.id == fixture_id), None)
    if selected is None:
        selected = stats.next_fixture(session, season.id) or (all_fixtures[0] if all_fixtures else None)
    players = _active_players(session)
    pitch = _pitch_context(selected, players, slot, "/lineups") if selected else {
        "pitch_slots": FORMATION_4231,
        "pitch_rows": make_rows(FORMATION_4231),
        "formation_key": "4-2-3-1",
        "formations": list(FORMATIONS.keys()),
        "slot_fill": {},
        "unplaced_starters": [],
        "bench": [],
        "available_players": players,
        "all_players": players,
        "pick_slot": None,
        "pick_label": None,
        "next_url": "/lineups",
        "pick_base": "/lineups",
        "starters_on_pitch": 0,
        "spots_remaining": 11,
        "sidebar_players": [],
        "first_empty_slot": None,
        "position_groups": POSITION_GROUPS,
        "captain_id": None,
        "xi_players": [],
    }
    if selected:
        dest = _lineups_url(selected.id, group=group, q=q)
        pitch["next_url"] = dest
        pitch["pick_base"] = dest
        group_key = (group or "ALL").upper()
        query = (q or "").strip().lower()
        sidebar = list(pitch["sidebar_players"])
        if group_key in POSITION_GROUPS and group_key != "ALL":
            sidebar = [row for row in sidebar if row["group"] == group_key]
        if query:
            def _matches(row: dict) -> bool:
                player = row["player"]
                if query in player.name.lower():
                    return True
                number = player.squad_number
                return number is not None and query in str(number)

            sidebar = [row for row in sidebar if _matches(row)]
        pitch["sidebar_players"] = sidebar
        pitch["active_group"] = group_key if group_key in POSITION_GROUPS else "ALL"
        pitch["search_q"] = q or ""
    else:
        pitch["active_group"] = "ALL"
        pitch["search_q"] = ""
    return _render(
        request,
        "lineups.html",
        session,
        tab="lineups",
        fixtures=all_fixtures,
        selected_fixture=selected,
        next_fixture=stats.next_fixture(session, season.id),
        saved=saved == "1",
        **pitch,
    )


@app.post("/fixtures/{fixture_id}/lineup/place")
def place_on_pitch(
    session: SessionDep,
    fixture_id: int,
    slot: Annotated[str, Form()],
    player_id: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
):
    fixture = _fixture_or_404(session, fixture_id)
    formation_slots = FORMATIONS.get(fixture.formation or "4-2-3-1", FORMATION_4231)
    valid_slot_keys = {s.key for s in formation_slots}
    if slot not in valid_slot_keys:
        raise HTTPException(status_code=400, detail="Unknown pitch position")
    dest = _safe_next(next, f"/lineups?fixture_id={fixture_id}")

    occupying = _appearance_in_slot(fixture, slot)
    if not player_id.strip():
        if occupying is not None:
            session.delete(occupying)
            session.commit()
        return RedirectResponse(dest, status_code=303)

    chosen_id = int(player_id)
    if session.get(Player, chosen_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    if occupying is not None and occupying.player_id != chosen_id:
        session.delete(occupying)
        session.flush()

    existing = _appearance_for_player(fixture, chosen_id)
    if existing is None:
        starters = sum(
            1
            for appearance in fixture.appearances
            if appearance.role is AppearanceRole.start
        )
        if starters >= 11:
            return RedirectResponse(dest, status_code=303)
        existing = Appearance(
            fixture_id=fixture_id,
            player_id=chosen_id,
            role=AppearanceRole.start,
        )
        session.add(existing)
    existing.role = AppearanceRole.start
    existing.pitch_slot = slot
    existing.minute_on = 0
    slot_defaults = [s for s in formation_slots if s.key == slot]
    if slot_defaults:
        existing.pos_x = slot_defaults[0].default_x
        existing.pos_y = slot_defaults[0].default_y
    _sync_captain(fixture)
    session.commit()
    return RedirectResponse(dest, status_code=303)


@app.post("/fixtures/{fixture_id}/lineup/bench")
def toggle_bench(
    session: SessionDep,
    fixture_id: int,
    player_id: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
):
    fixture = _fixture_or_404(session, fixture_id)
    dest = _safe_next(next, f"/lineups?fixture_id={fixture_id}")
    if not player_id.strip():
        return RedirectResponse(dest, status_code=303)
    chosen_id = int(player_id)
    existing = _appearance_for_player(fixture, chosen_id)
    if existing is None:
        session.add(
            Appearance(
                fixture_id=fixture_id,
                player_id=chosen_id,
                role=AppearanceRole.sub,
                minute_on=60,
            )
        )
    elif existing.role is AppearanceRole.sub:
        session.delete(existing)
    else:
        existing.role = AppearanceRole.sub
        existing.pitch_slot = None
        existing.minute_on = 60
    _sync_captain(fixture)
    session.commit()
    return RedirectResponse(dest, status_code=303)


@app.post("/fixtures/{fixture_id}/lineup/swap")
def swap_slots(
    session: SessionDep,
    fixture_id: int,
    slot_a: Annotated[str, Form()],
    slot_b: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
):
    fixture = _fixture_or_404(session, fixture_id)
    formation_slots = FORMATIONS.get(fixture.formation or "4-2-3-1", FORMATION_4231)
    valid_keys = {s.key for s in formation_slots}
    if slot_a not in valid_keys or slot_b not in valid_keys:
        raise HTTPException(status_code=400, detail="Unknown pitch position")
    app_a = _appearance_in_slot(fixture, slot_a)
    app_b = _appearance_in_slot(fixture, slot_b)
    if app_a is not None and app_b is not None:
        app_a.pitch_slot, app_b.pitch_slot = slot_b, slot_a
    elif app_a is not None:
        app_a.pitch_slot = slot_b
    elif app_b is not None:
        app_b.pitch_slot = slot_a
    session.commit()
    return RedirectResponse(_safe_next(next, f"/lineups?fixture_id={fixture_id}"), status_code=303)


@app.post("/fixtures/{fixture_id}/lineup/move")
async def move_player(request: Request, session: SessionDep, fixture_id: int):
    fixture = _fixture_or_404(session, fixture_id)
    form = await request.form()
    player_id = int(form["player_id"])
    pos_x = float(form["pos_x"])
    pos_y = float(form["pos_y"])
    appearance = _appearance_for_player(fixture, player_id)
    if appearance is None or appearance.role is not AppearanceRole.start:
        return Response(status_code=404)
    appearance.pos_x = max(0.0, min(100.0, pos_x))
    appearance.pos_y = max(0.0, min(100.0, pos_y))
    session.commit()
    return Response(status_code=204)


@app.post("/fixtures/{fixture_id}/formation")
def set_formation(
    session: SessionDep,
    fixture_id: int,
    formation: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
):
    fixture = _fixture_or_404(session, fixture_id)
    if formation not in FORMATIONS:
        raise HTTPException(status_code=400, detail="Unknown formation")
    fixture.formation = formation
    valid_keys = {slot.key for slot in FORMATIONS[formation]}
    # Slots do not map across shapes. Drop starters the new pitch cannot show,
    # otherwise the board looks empty while the 11-player cap still blocks adds.
    for appearance in list(fixture.appearances):
        if appearance.role is AppearanceRole.start and appearance.pitch_slot not in valid_keys:
            session.delete(appearance)
    _sync_captain(fixture)
    session.commit()
    dest = _safe_next(next, f"/lineups?fixture_id={fixture_id}")
    return RedirectResponse(dest, status_code=303)


@app.post("/fixtures/{fixture_id}/lineup/clear")
def clear_lineup(
    session: SessionDep,
    fixture_id: int,
    next: Annotated[str, Form()] = "",
):
    fixture = _fixture_or_404(session, fixture_id)
    dest = _safe_next(next, f"/lineups?fixture_id={fixture_id}")
    if fixture.is_played:
        raise HTTPException(status_code=400, detail="Cannot clear a played match")
    for appearance in list(fixture.appearances):
        session.delete(appearance)
    fixture.formation = "4-2-3-1"
    fixture.captain_player_id = None
    session.commit()
    return RedirectResponse(dest, status_code=303)


@app.post("/fixtures/{fixture_id}/captain")
def set_captain(
    session: SessionDep,
    fixture_id: int,
    player_id: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
):
    fixture = _fixture_or_404(session, fixture_id)
    dest = _safe_next(next, f"/lineups?fixture_id={fixture_id}")
    if not player_id.strip():
        fixture.captain_player_id = None
        session.commit()
        return RedirectResponse(dest, status_code=303)
    chosen_id = int(player_id)
    appearance = _appearance_for_player(fixture, chosen_id)
    if appearance is None or appearance.role is not AppearanceRole.start:
        raise HTTPException(status_code=400, detail="Captain must be in the starting XI")
    fixture.captain_player_id = chosen_id
    session.commit()
    return RedirectResponse(dest, status_code=303)


@app.post("/fixtures/{fixture_id}/lineup")
async def save_lineup(request: Request, session: SessionDep, fixture_id: int):
    fixture = _fixture_or_404(session, fixture_id)
    form = await request.form()
    starters = {int(v) for v in form.getlist("start")}
    subs = {int(v) for v in form.getlist("sub")} - starters

    if len(starters) > 11:
        raise HTTPException(status_code=400, detail="A starting eleven is eleven players")

    formation_slots = FORMATIONS.get(fixture.formation or "4-2-3-1", FORMATION_4231)
    valid_slot_keys = {s.key for s in formation_slots}
    kept_slots = {
        appearance.player_id: appearance.pitch_slot
        for appearance in fixture.appearances
        if appearance.player_id in starters and appearance.pitch_slot in valid_slot_keys
    }
    for appearance in list(fixture.appearances):
        session.delete(appearance)
    session.flush()

    for player_id in starters:
        session.add(
            Appearance(
                fixture_id=fixture_id,
                player_id=player_id,
                role=AppearanceRole.start,
                pitch_slot=kept_slots.get(player_id),
            )
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


# -------------------------------------------------- confirm / unconfirm XI --


@app.post("/fixtures/{fixture_id}/lineup/confirm")
def confirm_lineup(session: SessionDep, fixture_id: int):
    fixture = _fixture_or_404(session, fixture_id)
    fixture.lineup_confirmed = True
    session.commit()
    return RedirectResponse(f"/fixtures/{fixture_id}/lineup-card", status_code=303)


@app.post("/fixtures/{fixture_id}/lineup/unconfirm")
def unconfirm_lineup(session: SessionDep, fixture_id: int):
    fixture = _fixture_or_404(session, fixture_id)
    fixture.lineup_confirmed = False
    session.commit()
    return RedirectResponse(f"/lineups?fixture_id={fixture_id}", status_code=303)


# --------------------------------------------------------- lineup card view --


@app.get("/fixtures/{fixture_id}/lineup-card")
def lineup_card(request: Request, session: SessionDep, fixture_id: int):
    season = _season_or_404(session)
    fixture = _fixture_or_404(session, fixture_id)
    formation_key = fixture.formation if fixture.formation in FORMATIONS else "4-2-3-1"
    formation_slots = FORMATIONS[formation_key]
    slot_keys = {slot.key for slot in formation_slots}
    by_slot: dict[str, Appearance] = {}
    bench: list[Appearance] = []
    for appearance in fixture.appearances:
        if appearance.role is AppearanceRole.sub:
            bench.append(appearance)
        elif appearance.pitch_slot in slot_keys:
            by_slot[appearance.pitch_slot] = appearance
    # XI ordered GK-first for the numbered list
    xi = [
        {"slot": slot, "player": by_slot[slot.key].player}
        for slot in reversed(formation_slots)
        if slot.key in by_slot
    ]
    context = {
        "request": request,
        "club_name": CLUB_NAME,
        "fixture": fixture,
        "formation_key": formation_key,
        "formation_slots": formation_slots,
        "by_slot": by_slot,
        "bench": bench,
        "xi": xi,
        "captain_id": fixture.captain_player_id,
        "is_gaffer": request.session.get("user") == "Gaffer",
    }
    return templates.TemplateResponse(request, "lineup_card.html", context)


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
        league_table_url=LEAGUE_TABLE_URL,
        fetch_error=request.query_params.get("fetch") == "failed",
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


@app.post("/table/fetch")
def fetch_table(session: SessionDep):
    """Pull a fresh snapshot from the official Churches League table."""
    season = _season_or_404(session)
    try:
        rows = fetch_league_table()
    except (OSError, ValueError):
        return RedirectResponse("/table?fetch=failed", status_code=303)
    snapshot = LeagueTableSnapshot(season_id=season.id, source=LEAGUE_TABLE_SOURCE)
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


@app.post("/fines/schedule")
def add_schedule_item(
    session: SessionDep,
    description: Annotated[str, Form()],
    amount_pence: Annotated[int, Form()],
):
    session.add(FineScheduleItem(description=description.strip(), amount_pence=amount_pence))
    session.commit()
    return RedirectResponse("/fines", status_code=303)


@app.post("/fines/schedule/reset")
def reset_schedule(session: SessionDep):
    for item in session.scalars(select(FineScheduleItem)):
        item.is_active = False
    new_items = [
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
    for item in new_items:
        session.add(item)
    session.commit()
    return RedirectResponse("/fines", status_code=303)


@app.post("/fines/schedule/{item_id}/remove")
def remove_schedule_item(session: SessionDep, item_id: int):
    item = session.get(FineScheduleItem, item_id)
    if item is not None:
        item.is_active = False
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
    settings.monthly_fee_pence = round(float(amount) * 100)
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


@app.get("/links")
def links(request: Request, session: SessionDep):
    return _render(request, "links.html", session, tab="links")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
