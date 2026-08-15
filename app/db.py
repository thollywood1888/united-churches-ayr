"""Engine and session management.

SQLite is a deliberate choice for v1: one squad, one league, a few hundred rows a
season, and a single writer. It removes an entire class of deployment problem.
The ORM layer is Postgres-compatible, so moving later is a connection-string
change plus a migration tool.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

CLUB_NAME = "United Churches of Ayr AFC"
CLUB_SHORT = "UCA"

DATA_DIR = Path(os.environ.get("UCA_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATABASE_URL = os.environ.get("UCA_DATABASE_URL", f"sqlite:///{DATA_DIR / 'uca.db'}")

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - driver hook
    """Enforce foreign keys and use WAL so reads never block the matchday writer."""
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def create_schema() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
