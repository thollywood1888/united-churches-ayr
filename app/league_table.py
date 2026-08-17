"""Read SECL standings from the league site. Manual paste stays the fallback."""

from __future__ import annotations

import urllib.error
import urllib.request
from html.parser import HTMLParser

LEAGUE_TABLE_URL = "https://www.churchesleague.com/league-tables-2026-2027"
LEAGUE_TABLE_SOURCE = "churchesleague.com/league-tables-2026-2027"


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._table is not None and self._row is not None:
            if any(cell for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _is_int(value: str) -> bool:
    return bool(value) and value.lstrip("+-").isdigit()


def parse_league_html(html: str) -> list[dict[str, object]]:
    """Turn the Churches League HTML table into the same row dicts as parse_table."""
    parser = _TableParser()
    parser.feed(html)
    rows: list[dict[str, object]] = []
    for table in parser.tables:
        for cells in table:
            cleaned = [cell.strip() for cell in cells]
            labels = {cell.lower() for cell in cleaned[:3]}
            if not cleaned or cleaned[0].lower() in {"", "pos"} or "club" in labels:
                continue
            numbers: list[int] = []
            cut = len(cleaned)
            for index in range(len(cleaned) - 1, -1, -1):
                if not _is_int(cleaned[index]):
                    break
                numbers.append(int(cleaned[index]))
                cut = index
            numbers.reverse()
            if len(numbers) < 7:
                continue
            # Site columns: P W D L F A GD Pts. Paste format drops GD.
            if len(numbers) >= 8:
                played, won, drawn, lost, goals_for, goals_against, _gd, points = numbers[-8:]
            else:
                played, won, drawn, lost, goals_for, goals_against, points = numbers[-7:]
            name_parts = [part for part in cleaned[:cut] if part and not _is_int(part)]
            club = " ".join(name_parts).strip(" .-")
            if not club:
                continue
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
        if rows:
            break
    return rows


def fetch_league_html(url: str = LEAGUE_TABLE_URL) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "UCA-club-app/1.0 (+https://united-churches-ayr-production.up.railway.app)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OSError(f"Could not fetch {url}") from exc


def fetch_league_table(url: str = LEAGUE_TABLE_URL) -> list[dict[str, object]]:
    rows = parse_league_html(fetch_league_html(url))
    if not rows:
        raise ValueError("No standings found on the league page")
    return rows
