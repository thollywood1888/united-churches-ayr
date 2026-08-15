# AGENTS.md — United Churches of Ayr AFC club app

Read this before changing anything.

## What this is

A matchday and season-record app for one club: squad, fixtures, results,
lineups and the division table. Real product, used by real people on a
touchline. Reliability beats cleverness.

## Stack and why

| Choice | Reason |
| --- | --- |
| FastAPI + Jinja templates, no JS framework | One request per page, works on weak signal and with JS off |
| SQLite via SQLAlchemy 2 | One squad, one league, a single writer. No infrastructure to run |
| Plain HTML forms | Nothing to hydrate, nothing to debug at 10am on a Saturday |
| No migrations tool yet | Schema is young. Add Alembic before the first real user data exists elsewhere |

Do not introduce React, a build step, or a background worker without a written
reason. Complexity has to be earned.

## Rules

1. **Stats are derived, never stored.** Anything countable comes from
   `Appearance` and `MatchEvent` in `app/stats.py`. If you find yourself adding
   a `goals_total` column, stop.
2. **The league table is someone else's data.** It is only ever a snapshot with
   a `fetched_at` and a `source`. Never compute standings from our own results
   and never present a snapshot without showing when it was taken.
3. **Players are never deleted.** Set `Player.status` to `left`.
4. **A result is a played fixture.** No separate results table.
5. **Timestamps are generated in Python**, not by the database. Mixing the two
   caused a silent zero-rows bug; see `tests/test_league_table.py`.
6. **Manual paste stays supported** for the league table, permanently. It is
   the fallback when any feed breaks, not a debug tool.

## Definition of done

- `python -m pytest` passes
- `ruff check .` passes
- The changed page renders at every breakpoint down to 380px wide
- New behaviour has a test; fixed bugs have a regression test naming the cause

## Not yet built

Authentication and roles, player availability, live match logging, and the
league table feed. Permissions must be defined before any screen that exposes
another player's data.
