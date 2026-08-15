# United Churches of Ayr AFC — club app

Squad, fixtures, results, lineups and the SECL Premier Division table in one
place. Server-rendered Python, one SQLite file, no build step.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m app.seed                 # season, 11 fixtures, division, starter squad
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. To use it from a phone on the same network:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## What's seeded

- **Season** 2026/27, SECL Premier Division
- **11 fixtures**, 22 Aug to 14 Nov 2026, home/away as supplied
- **13 clubs** in the division, all on zero — real standings come from the
  league, so paste them on the League table tab
- **33 players** from `data/players.csv`

`data/players.csv` is partial: it holds the names visible in the squad list you
sent, not all 49. Finish the file and re-run `python -m app.seed`, or add the
rest through the Squad tab. Seeding is idempotent — running it twice adds
nothing twice.

## Using it on a Saturday

1. **Before the match** — Lineups tab, pick the fixture, tick the starting XI
   and the bench, save.
2. **After the match** — open the fixture, enter the score, then add each goal
   with its scorer, minute and assist. The Squad tab flags any goals that have
   no scorer attached, so nothing quietly goes missing.
3. **Sunday** — paste the new league table.

Every player statistic is calculated from those entries. Nothing needs typing
twice.

## Tests

```bash
python -m pytest      # 19 tests
ruff check .
```

## Layout

```
app/models.py      seven tables — the whole data model
app/stats.py       every derived statistic, computed at query time
app/main.py        routes and the league table parser
app/templates/     five tabs plus the match centre
app/seed.py        idempotent season/fixture/squad seeding
```

## Roadmap

| Milestone | Contents |
| --- | --- |
| Shipped | Squad and stats, fixtures and results, lineups, table with paste |
| M3 | Player logins, roles, availability ("can you play Saturday?") |
| M4 | Live match logging, thumb-sized, tolerant of a dropped connection |
| M5 | League table feed from the league site, with paste as fallback |

Availability is the feature that makes a squad open the app twice. Build it
before, not after, the live logging.
