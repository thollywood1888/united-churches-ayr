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

def _resolve_db_url() -> str:
    url = os.environ.get("UCA_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        # Railway supplies postgres:// but SQLAlchemy 2 requires postgresql://
        return url.replace("postgres://", "postgresql://", 1)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DATA_DIR / 'uca.db'}"

DATABASE_URL = _resolve_db_url()

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
