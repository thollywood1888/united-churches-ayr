"""One-time migration: copy local SQLite data to Railway Postgres.

Run with:
    railway run --service united-churches-ayr -- .venv/bin/python migrate_to_railway.py
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import make_transient

LOCAL_URL = f"sqlite:///{Path(__file__).resolve().parent / 'data' / 'uca.db'}"
raw = os.environ.get("DATABASE_URL", "")
TARGET_URL = raw.replace("postgres://", "postgresql://", 1) if raw else None

if not TARGET_URL:
    raise SystemExit("No DATABASE_URL. Run: railway run --service united-churches-ayr -- .venv/bin/python migrate_to_railway.py")

from app.models import (
    Appearance, ClubSettings, FeePayment, FineScheduleItem, Fixture,
    LeagueTableRow, LeagueTableSnapshot, MatchEvent, Player, PlayerFine, Season,
)
from app.models import Base

src_engine = create_engine(LOCAL_URL)
dst_engine = create_engine(TARGET_URL, connect_args={"sslmode": "require"})
Base.metadata.create_all(dst_engine)

TABLES = [
    Season, Player, Fixture, Appearance, MatchEvent,
    LeagueTableSnapshot, LeagueTableRow, FineScheduleItem,
    PlayerFine, ClubSettings, FeePayment,
]

print("Migrating local SQLite → Railway Postgres...")
with Session(src_engine) as src, Session(dst_engine) as dst:
    for Model in TABLES:
        rows = src.query(Model).all()
        for row in rows:
            src.expunge(row)
            make_transient(row)
            dst.merge(row)
        dst.commit()
        print(f"  {Model.__tablename__:30s} {len(rows)} rows")

    # Reset Postgres sequences so new inserts don't collide with migrated IDs
    with dst_engine.begin() as conn:
        for Model in TABLES:
            table = Model.__tablename__
            try:
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
                ))
            except Exception as e:
                print(f"  (sequence reset skipped for {table}: {e})")

print("Done.")
