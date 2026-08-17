"""Pitch slots for the starting XI. Attack is at the top, keeper at the bottom."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby


@dataclass(frozen=True, slots=True)
class PitchSlot:
    key: str
    label: str
    row: int
    default_x: float = 50.0
    default_y: float = 50.0


def make_rows(formation: tuple[PitchSlot, ...]) -> tuple[tuple[PitchSlot, ...], ...]:
    return tuple(
        tuple(group) for _, group in groupby(formation, key=lambda slot: slot.row)
    )


# 4-2-3-1, attack at row 0, keeper at bottom — spaced like a matchday graphic
FORMATION_4231: tuple[PitchSlot, ...] = (
    PitchSlot("st", "ST", 0, 50, 10),
    PitchSlot("lw", "LW", 1, 18, 28),
    PitchSlot("cam", "CAM", 1, 50, 28),
    PitchSlot("rw", "RW", 1, 82, 28),
    PitchSlot("lcm", "CM", 2, 35, 48),
    PitchSlot("rcm", "CM", 2, 65, 48),
    PitchSlot("lb", "LB", 3, 12, 68),
    PitchSlot("lcb", "CB", 3, 36, 68),
    PitchSlot("rcb", "CB", 3, 64, 68),
    PitchSlot("rb", "RB", 3, 88, 68),
    PitchSlot("gk", "GK", 4, 50, 88),
)

# 3-5-2, attack at row 0, keeper at bottom
FORMATION_352: tuple[PitchSlot, ...] = (
    PitchSlot("ls", "LS", 0, 32, 10),
    PitchSlot("rs", "RS", 0, 68, 10),
    PitchSlot("am", "AM", 1, 50, 28),
    PitchSlot("ldm", "DM", 2, 34, 46),
    PitchSlot("rdm", "DM", 2, 66, 46),
    PitchSlot("lwb", "LWB", 3, 10, 62),
    PitchSlot("rwb", "RWB", 3, 90, 62),
    PitchSlot("cb1", "CB", 4, 26, 76),
    PitchSlot("cb2", "CB", 4, 50, 76),
    PitchSlot("cb3", "CB", 4, 74, 76),
    PitchSlot("gk", "GK", 5, 50, 90),
)

POSITION_GROUPS = ("ALL", "GK", "DEF", "MID", "FWD")

_GROUP_ALIASES = {
    "goalkeeper": "GK",
    "gk": "GK",
    "defender": "DEF",
    "defence": "DEF",
    "defense": "DEF",
    "midfielder": "MID",
    "midfield": "MID",
    "forward": "FWD",
    "striker": "FWD",
    "attacker": "FWD",
}


def player_group(position: str | None) -> str:
    if not position:
        return "ALL"
    return _GROUP_ALIASES.get(position.strip().lower(), "ALL")

FORMATIONS: dict[str, tuple[PitchSlot, ...]] = {
    "4-2-3-1": FORMATION_4231,
    "3-5-2": FORMATION_352,
}

# Union of all slot keys across every formation (used for broad validation).
SLOT_KEYS = {slot.key for slots in FORMATIONS.values() for slot in slots}

# Legacy aliases kept for any external imports.
PITCH_ROWS = make_rows(FORMATION_4231)
