# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Seed initial data (idempotent — safe to re-run)
python -m app.seed

# Run locally
uvicorn app.main:app --reload

# Tests and lint
python -m pytest
python -m pytest tests/test_routes.py::test_name   # single test
ruff check .
```

## Deploy

The app is deployed on Railway. GitHub push to `main` triggers auto-deploy.
Production URL: `https://united-churches-ayr-production.up.railway.app`

```bash
git push origin main   # triggers Railway build
```

## Architecture

**Stack:** FastAPI + Jinja2 templates + SQLAlchemy. Deliberately no client-side framework — every page is one HTTP request and works with JS off (touchline use on 1 bar of signal). The one JS exception is the pitch drag engine (`_pitch_builder.html`).

**Database:** SQLite locally (`data/uca.db`), PostgreSQL on Railway (via `DATABASE_URL` / `UCA_DATABASE_URL` env vars). Schema is managed by `create_schema()` in `db.py`, which calls `Base.metadata.create_all()` plus manual `ALTER TABLE` migrations for columns added after initial deployment. **Always add a `_ensure_*()` migration function in `db.py` for any new column on an existing table** — `create_all` won't add columns to existing tables in production.

**Data model** (`models.py`): Seven tables. Key relationships:
- `Appearance` links `Player` ↔ `Fixture` with `role` (start/sub/unused), `pitch_slot` (position key like `"lw"`), and `pos_x`/`pos_y` (free-drag position as % of pitch canvas)
- `MatchEvent` records goals, cards, MOTM — all stats are computed at query time from these, never stored
- `LeagueTableSnapshot` stores external standings as a snapshot; never computed from own results
- `Fixture.formation` stores the chosen formation key (`"4-2-3-1"` or `"3-5-2"`)

**Formations** (`lineup.py`): `PitchSlot` dataclass defines each position with a key, label, display row, and default x/y coordinates (0–100%) for free positioning. `FORMATIONS` dict maps formation name → slot tuple. `make_rows()` groups slots by row for fallback rendering. **When adding a new formation, add it to `FORMATIONS` and ensure slot keys don't collide with existing formations (except `gk` which is intentionally shared).**

**Stats** (`stats.py`): Pure query functions — no cached fields. `player_lines()` does one bulk query for appearances and one for events, then zips them in Python. All leaderboard functions call `player_lines()` internally.

**Routes** (`main.py`): All routes in one file. Auth is a session middleware (`_AuthMiddleware`) — credentials in `USERS` dict. `_pitch_context()` is the key helper that assembles everything the pitch builder template needs given a fixture and formation.

**Templates:** `_pitch_builder.html` is included into both `lineups.html` and `match.html`. The pitch renders as a free-form absolute-position canvas (`.pitch-free`) — slots at `left: X%; top: Y%` within the pitch div. Empty slots show at their `default_x`/`default_y` formation positions.

**Static files:** `app/static/player_photos/` holds uploaded player photos (`player_{id}.{ext}`). `app/static/badges/` holds opponent club badge PNGs keyed by `BADGE_MAP` in `main.py`.
