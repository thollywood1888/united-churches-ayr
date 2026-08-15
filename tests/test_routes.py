from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


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
        yield test_client


def test_every_tab_renders(client: TestClient) -> None:
    for path in ("/", "/fixtures", "/squad", "/lineups", "/table", "/healthz"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_seeded_fixtures_appear(client: TestClient) -> None:
    body = client.get("/fixtures").text
    assert "Anniesland West Glasgow New Church AFC" in body
    assert "Houston and Killellan AFC" in body


def test_add_player_then_record_a_goal(client: TestClient) -> None:
    client.post(
        "/squad", data={"first_name": "Blair", "last_name": "Grieve"}, follow_redirects=True
    )
    assert "Blair Grieve" in client.get("/squad").text

    fixture_id = 1
    client.post(
        f"/fixtures/{fixture_id}/result",
        data={"goals_for": 2, "goals_against": 1},
        follow_redirects=True,
    )
    client.post(
        f"/fixtures/{fixture_id}/events",
        data={"event_type": "goal", "player_id": 1, "minute": "23", "assist_player_id": ""},
        follow_redirects=True,
    )

    overview = client.get("/").text
    assert "Blair Grieve" in overview  # leading scorer
    squad = client.get("/squad").text
    assert "Blair Grieve" in squad


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
