from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
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
    _ensure_appearance_pitch_slot()
    _ensure_fixture_formation()
    _ensure_appearance_pos()
    _ensure_fixture_captain()
    _ensure_fixture_lineup_confirmed()
    _ensure_club_settings_gaffer()


def _ensure_appearance_pitch_slot() -> None:
    inspector = inspect(engine)
    if "appearance" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("appearance")}
    if "pitch_slot" in columns:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE appearance ADD COLUMN pitch_slot VARCHAR(16)"))


def _ensure_appearance_pos() -> None:
    inspector = inspect(engine)
    if "appearance" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("appearance")}
    with engine.begin() as conn:
        if "pos_x" not in columns:
            conn.execute(text("ALTER TABLE appearance ADD COLUMN pos_x FLOAT"))
        if "pos_y" not in columns:
            conn.execute(text("ALTER TABLE appearance ADD COLUMN pos_y FLOAT"))


def _ensure_fixture_formation() -> None:
    inspector = inspect(engine)
    if "fixture" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("fixture")}
    if "formation" in columns:
        return
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE fixture ADD COLUMN formation VARCHAR(10) DEFAULT '4-2-3-1'")
        )


def _ensure_fixture_captain() -> None:
    inspector = inspect(engine)
    if "fixture" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("fixture")}
    if "captain_player_id" in columns:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE fixture ADD COLUMN captain_player_id INTEGER"))


def _ensure_fixture_lineup_confirmed() -> None:
    inspector = inspect(engine)
    if "fixture" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("fixture")}
    if "lineup_confirmed" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE fixture ADD COLUMN lineup_confirmed BOOLEAN DEFAULT FALSE"))


def _ensure_club_settings_gaffer() -> None:
    inspector = inspect(engine)
    if "club_settings" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("club_settings")}
    with engine.begin() as conn:
        if "gaffer_name" not in columns:
            conn.execute(text("ALTER TABLE club_settings ADD COLUMN gaffer_name VARCHAR(200)"))
        if "gaffer_photo" not in columns:
            conn.execute(text("ALTER TABLE club_settings ADD COLUMN gaffer_photo VARCHAR(200)"))


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
