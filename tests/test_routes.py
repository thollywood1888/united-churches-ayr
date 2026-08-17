from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """Boot the app against a throwaway database, seeded with the real fixture list."""
    monkeypatch.setenv("UCA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UCA_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    for module in ("app.db", "app.models", "app.stats", "app.seed", "app.main"):
        sys.modules.pop(module, None)

    seed = importlib.import_module("app.seed")
    main = importlib.import_module("app.main")
    seed.seed(csv_path=tmp_path / "missing.csv")

    with TestClient(main.app) as test_client:
        test_client.post("/login", data={"username": "Gaffer", "password": "jeans1"})
        yield test_client


def test_login_accepts_gaffer_any_case(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UCA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UCA_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    for module in ("app.db", "app.models", "app.stats", "app.seed", "app.main"):
        sys.modules.pop(module, None)
    seed = importlib.import_module("app.seed")
    main = importlib.import_module("app.main")
    seed.seed(csv_path=tmp_path / "missing.csv")
    with TestClient(main.app) as anon:
        denied = anon.get("/", follow_redirects=False)
        assert denied.status_code == 302
        assert denied.headers["location"] == "/login"

        for username in ("gaffer", "Gaffer", "GAFFER", " gaffer "):
            response = anon.post(
                "/login",
                data={"username": username, "password": "jeans1"},
                follow_redirects=False,
            )
            assert response.status_code == 303, username
            assert response.headers["location"] == "/"

        bad = anon.post(
            "/login",
            data={"username": "gaffer", "password": "wrong"},
            follow_redirects=False,
        )
        assert bad.status_code == 401
        assert "Incorrect username or password" in bad.text


def test_every_tab_renders(client: TestClient) -> None:
    for path in ("/", "/fixtures", "/squad", "/lineups", "/table", "/healthz"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_seeded_fixtures_appear(client: TestClient) -> None:
    body = client.get("/fixtures").text
    assert "Anniesland West Glasgow New Church AFC" in body
    assert "Houston and Killellan AFC" in body


def _player_id(first: str, last: str) -> int:
    from app.db import SessionLocal
    from app.models import Player

    with SessionLocal() as session:
        player = session.scalar(
            select(Player).where(Player.first_name == first, Player.last_name == last)
        )
        assert player is not None
        return player.id


def test_squad_can_add_edit_and_remove_a_player(client: TestClient) -> None:
    page = client.get("/squad").text
    assert "Add a player" in page
    assert 'action="/squad"' in page

    client.post(
        "/squad",
        data={
            "first_name": "Calum",
            "last_name": "Test",
            "squad_number": "7",
            "position": "Forward",
        },
        follow_redirects=True,
    )
    assert "Calum Test" in client.get("/squad").text
    player_id = _player_id("Calum", "Test")

    edit = client.get(f"/squad/{player_id}")
    assert edit.status_code == 200
    assert "Calum" in edit.text

    client.post(
        f"/squad/{player_id}",
        data={
            "first_name": "Calum",
            "last_name": "Test",
            "squad_number": "9",
            "position": "Midfielder",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert "9" in client.get(f"/squad/{player_id}").text

    client.post(f"/squad/{player_id}/remove", follow_redirects=True)
    squad = client.get("/squad").text
    assert "Calum Test" not in squad or "Left the club" in squad
    assert "Calum Test" not in squad.split("Left the club")[0]


def test_remove_keeps_a_player_who_has_played(client: TestClient) -> None:
    client.post("/squad", data={"first_name": "History", "last_name": "Boy"}, follow_redirects=True)
    player_id = _player_id("History", "Boy")
    client.post("/fixtures/1/lineup/place", data={"slot": "st", "player_id": str(player_id)})
    client.post(f"/squad/{player_id}/remove", follow_redirects=True)
    page = client.get("/squad").text
    assert "History Boy" in page
    assert "Left the club" in page
    client.post(f"/squad/{player_id}/restore", follow_redirects=True)
    restored = client.get("/squad").text
    assert "History Boy" in restored.split("Left the club")[0]


def test_add_player_then_record_a_goal(client: TestClient) -> None:
    client.post(
        "/squad", data={"first_name": "Blair", "last_name": "Grieve"}, follow_redirects=True
    )
    assert "Blair Grieve" in client.get("/squad").text
    blair_id = _player_id("Blair", "Grieve")

    fixture_id = 1
    client.post(
        f"/fixtures/{fixture_id}/result",
        data={"goals_for": 2, "goals_against": 1},
        follow_redirects=True,
    )
    client.post(
        f"/fixtures/{fixture_id}/events",
        data={"event_type": "goal", "player_id": blair_id, "minute": "23", "assist_player_id": ""},
        follow_redirects=True,
    )

    overview = client.get("/").text
    assert "Blair Grieve" in overview  # leading scorer
    squad = client.get("/squad").text
    assert "Blair Grieve" in squad


def test_lineups_page_shows_pitch_slots(client: TestClient) -> None:
    page = client.get("/lineups").text
    assert "Matchday lineup" in page
    assert "Starting XI" in page
    assert page.count("md-token") >= 11
    assert 'aria-label="GK, empty"' in page
    assert 'option value="4-2-3-1" selected' in page
    assert page.count('name="player_id"') >= 11


def test_each_game_week_lineup_starts_empty(client: TestClient) -> None:
    for fixture_id in (1, 2, 3):
        page = client.get(f"/lineups?fixture_id={fixture_id}").text
        assert "0 of 11 selected" in page
        assert "11 spots remaining" in page
        assert "are in the XI but not on a position yet" not in page


def test_placing_on_one_week_leaves_other_weeks_empty(client: TestClient) -> None:
    client.post(
        "/squad",
        data={"first_name": "Ethan", "last_name": "Isolate"},
        follow_redirects=True,
    )
    player_id = _player_id("Ethan", "Isolate")
    client.post(
        "/fixtures/1/lineup/place",
        data={"slot": "st", "player_id": str(player_id), "next": "/lineups?fixture_id=1"},
    )
    week_one = client.get("/lineups?fixture_id=1").text
    week_two = client.get("/lineups?fixture_id=2").text
    assert 'aria-label="ST: Ethan Isolate"' in week_one
    assert "1 of 11 selected" in week_one
    assert "0 of 11 selected" in week_two
    assert 'aria-label="ST: Ethan Isolate"' not in week_two
    assert "11 spots remaining" in week_two


def test_clear_lineup_empties_an_unplayed_week(client: TestClient) -> None:
    client.post(
        "/squad",
        data={"first_name": "Clark", "last_name": "Reset"},
        follow_redirects=True,
    )
    player_id = _player_id("Clark", "Reset")
    client.post(
        "/fixtures/2/lineup/place",
        data={"slot": "st", "player_id": str(player_id), "next": "/lineups?fixture_id=2"},
    )
    client.post(
        "/fixtures/2/lineup/bench",
        data={"player_id": str(player_id), "next": "/lineups?fixture_id=2"},
    )
    # Moved to the bench — still named for that week until cleared.
    named = client.get("/lineups?fixture_id=2").text
    assert "Reset" in named

    response = client.post(
        "/fixtures/2/lineup/clear",
        data={"next": "/lineups?fixture_id=2"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    empty = client.get("/lineups?fixture_id=2").text
    assert "0 of 11 selected" in empty
    assert 'aria-label="ST: Clark Reset"' not in empty
    assert 'aria-label="GK: Clark Reset"' not in empty
    assert "11 spots remaining" in empty

    client.post(
        "/fixtures/2/lineup/place",
        data={"slot": "gk", "player_id": str(player_id), "next": "/lineups?fixture_id=2"},
    )
    again = client.get("/lineups?fixture_id=2").text
    assert 'aria-label="GK: Clark Reset"' in again


def test_clear_lineup_refuses_a_played_match(client: TestClient) -> None:
    client.post(
        "/squad",
        data={"first_name": "Ross", "last_name": "Kept"},
        follow_redirects=True,
    )
    player_id = _player_id("Ross", "Kept")
    client.post(
        "/fixtures/1/lineup/place",
        data={"slot": "st", "player_id": str(player_id)},
    )
    client.post("/fixtures/1/result", data={"goals_for": "2", "goals_against": "2"})
    response = client.post(
        "/fixtures/1/lineup/clear",
        data={"next": "/lineups?fixture_id=1"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    page = client.get("/lineups?fixture_id=1").text
    assert 'aria-label="ST: Ross Kept"' in page


def test_formation_switch_drops_slots_the_new_shape_cannot_show(client: TestClient) -> None:
    client.post(
        "/squad",
        data={"first_name": "John", "last_name": "Currie"},
        follow_redirects=True,
    )
    client.post(
        "/squad",
        data={"first_name": "Liam", "last_name": "Dunnachie"},
        follow_redirects=True,
    )
    currie = _player_id("John", "Currie")
    keeper = _player_id("Liam", "Dunnachie")
    client.post("/fixtures/1/lineup/place", data={"slot": "st", "player_id": str(currie)})
    client.post("/fixtures/1/lineup/place", data={"slot": "gk", "player_id": str(keeper)})

    client.post(
        "/fixtures/1/formation",
        data={"formation": "3-5-2", "next": "/lineups?fixture_id=1"},
    )
    page = client.get("/lineups?fixture_id=1").text
    assert 'option value="3-5-2" selected' in page
    assert 'aria-label="ST: John Currie"' not in page
    assert 'aria-label="LS: John Currie"' not in page
    assert 'aria-label="GK: Liam Dunnachie"' in page
    assert "are in the XI but not on a position yet" not in page

    client.post(
        "/fixtures/1/lineup/place",
        data={"slot": "ls", "player_id": str(currie), "next": "/lineups?fixture_id=1"},
    )
    placed = client.get("/lineups?fixture_id=1").text
    assert 'aria-label="LS: John Currie"' in placed


def test_captain_must_be_in_the_starting_xi(client: TestClient) -> None:
    client.post(
        "/squad",
        data={"first_name": "Liam", "last_name": "Skipper"},
        follow_redirects=True,
    )
    player_id = _player_id("Liam", "Skipper")
    denied = client.post(
        "/fixtures/1/captain",
        data={"player_id": str(player_id), "next": "/lineups?fixture_id=1"},
        follow_redirects=False,
    )
    assert denied.status_code == 400
    client.post("/fixtures/1/lineup/place", data={"slot": "st", "player_id": str(player_id)})
    ok = client.post(
        "/fixtures/1/captain",
        data={"player_id": str(player_id), "next": "/lineups?fixture_id=1"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    page = client.get("/lineups?fixture_id=1").text
    assert f'value="{player_id}" selected' in page
    assert "is-captain" in page


def test_each_position_dropdown_lists_the_whole_squad(client: TestClient) -> None:
    client.post(
        "/squad",
        data={"first_name": "Kyle", "last_name": "Campbell", "position": "Forward"},
        follow_redirects=True,
    )
    player_id = _player_id("Kyle", "Campbell")
    page = client.get("/lineups?fixture_id=1").text
    assert page.count(f'value="{player_id}"') >= 11
    assert page.count("Kyle Campbell") >= 11
    assert "Add player" in page

    client.post(
        "/fixtures/1/lineup/place",
        data={"slot": "st", "player_id": str(player_id), "next": "/lineups?fixture_id=1"},
    )
    filled = client.get("/lineups?fixture_id=1").text
    assert 'aria-label="ST: Kyle Campbell"' in filled
    assert "1 of 11 selected" in filled
    assert f'id="sheet-select-st"' in filled
    assert f'value="{player_id}" selected' in filled


def test_adding_a_player_shows_their_face_thumbnail(client: TestClient) -> None:
    client.post(
        "/squad",
        data={"first_name": "Ethan", "last_name": "White"},
        follow_redirects=True,
    )
    player_id = _player_id("Ethan", "White")
    from app.db import SessionLocal
    from app.models import Player

    with SessionLocal() as session:
        player = session.get(Player, player_id)
        assert player is not None
        player.photo_filename = "player_34.jpg"
        session.commit()

    page = client.get("/lineups?fixture_id=1").text
    assert "md-face-card" in page
    assert "/static/player_photos/player_34.jpg" in page

    client.post(
        "/fixtures/1/lineup/place",
        data={"slot": "st", "player_id": str(player_id), "next": "/lineups?fixture_id=1"},
    )
    filled = client.get("/lineups?fixture_id=1").text
    assert 'src="/static/player_photos/player_34.jpg"' in filled
    assert "has-photo" in filled
    assert 'aria-label="ST: Ethan White"' in filled


def test_place_player_on_a_pitch_slot(client: TestClient) -> None:
    client.post(
        "/squad",
        data={"first_name": "Ethan", "last_name": "White"},
        follow_redirects=True,
    )
    ethan_id = _player_id("Ethan", "White")
    response = client.post(
        "/fixtures/1/lineup/place",
        data={"slot": "st", "player_id": str(ethan_id), "next": "/lineups?fixture_id=1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/lineups?fixture_id=1").text
    assert "White" in page
    assert 'aria-label="ST: Ethan White"' in page


def test_lineup_rejects_more_than_eleven_starters(client: TestClient) -> None:
    for index in range(12):
        client.post(
            "/squad",
            data={"first_name": f"Player{index}", "last_name": "Test"},
            follow_redirects=True,
        )
    response = client.post(
        "/fixtures/1/lineup",
        data={"start": [str(i) for i in range(1, 13)]},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_fetch_from_league_site_writes_a_snapshot(client: TestClient, monkeypatch) -> None:
    html = (
        Path(__file__).parent.joinpath("fixtures", "secl_premier_table.html").read_text()
    )
    monkeypatch.setattr("app.league_table.fetch_league_html", lambda url=None: html)
    response = client.post("/table/fetch", follow_redirects=True)
    assert response.status_code == 200
    page = response.text
    assert "Glasgow Free Churches AFC" in page
    assert "churchesleague.com/league-tables-2026-2027" in page
    rows = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert rows.index("United Churches of Ayr AFC") < rows.index("West Glasgow New Church AFC")


def test_failed_league_fetch_keeps_the_last_snapshot(client: TestClient, monkeypatch) -> None:
    client.post(
        "/table",
        data={"pasted": "Glasgow Elim AFC 1 1 0 0 6 1 3"},
        follow_redirects=True,
    )
    monkeypatch.setattr(
        "app.league_table.fetch_league_html",
        lambda url=None: (_ for _ in ()).throw(OSError("down")),
    )
    response = client.post("/table/fetch", follow_redirects=False)
    assert response.status_code == 303
    assert "fetch=failed" in response.headers["location"]
    page = client.get("/table?fetch=failed").text
    assert "Glasgow Elim AFC" in page
    assert "Could not read the Churches League table" in page


def test_pasted_standings_replace_the_snapshot(client: TestClient) -> None:
    pasted = (
        "Glasgow Elim AFC 1 1 0 0 6 1 3\n"
        "Fullarton Irvine AFC 1 1 0 0 2 0 3\n"
        "United Churches of Ayr AFC 1 0 1 0 2 2 1\n"
    )
    client.post("/table", data={"pasted": pasted}, follow_redirects=True)
    page = client.get("/table").text
    assert "manual paste" in page

    # The club name also appears in the masthead, so compare inside the table body only.
    rows = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert rows.index("Glasgow Elim AFC") < rows.index("United Churches of Ayr AFC")
    assert "seed:pre-season" not in page


def test_league_position_renders_with_an_ordinal(client: TestClient) -> None:
    client.post(
        "/table",
        data={"pasted": "United Churches of Ayr AFC 1 0 1 0 2 2 1"},
        follow_redirects=True,
    )
    assert "1st" in client.get("/").text
