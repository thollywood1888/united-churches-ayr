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


# 4-2-3-1, attack at row 0, keeper at bottom
FORMATION_4231: tuple[PitchSlot, ...] = (
    PitchSlot("lw", "LW", 0, 15, 8),
    PitchSlot("st", "ST", 0, 50, 8),
    PitchSlot("rw", "RW", 0, 85, 8),
    PitchSlot("cam", "CAM", 1, 50, 25),
    PitchSlot("lcm", "CM", 2, 30, 42),
    PitchSlot("rcm", "CM", 2, 70, 42),
    PitchSlot("lb", "LB", 3, 8, 62),
    PitchSlot("lcb", "CB", 3, 33, 62),
    PitchSlot("rcb", "CB", 3, 67, 62),
    PitchSlot("rb", "RB", 3, 92, 62),
    PitchSlot("gk", "GK", 4, 50, 83),
)

# 3-5-2, attack at row 0, keeper at bottom
FORMATION_352: tuple[PitchSlot, ...] = (
    PitchSlot("ls", "LS", 0, 30, 8),
    PitchSlot("rs", "RS", 0, 70, 8),
    PitchSlot("am", "AM", 1, 50, 26),
    PitchSlot("ldm", "DM", 2, 30, 43),
    PitchSlot("rdm", "DM", 2, 70, 43),
    PitchSlot("lwb", "LWB", 3, 8, 58),
    PitchSlot("rwb", "RWB", 3, 92, 58),
    PitchSlot("cb1", "CB", 4, 22, 73),
    PitchSlot("cb2", "CB", 4, 50, 73),
    PitchSlot("cb3", "CB", 4, 78, 73),
    PitchSlot("gk", "GK", 5, 50, 87),
)

FORMATIONS: dict[str, tuple[PitchSlot, ...]] = {
    "4-2-3-1": FORMATION_4231,
    "3-5-2": FORMATION_352,
}

# Union of all slot keys across every formation (used for broad validation).
SLOT_KEYS = {slot.key for slots in FORMATIONS.values() for slot in slots}

# Legacy aliases kept for any external imports.
PITCH_ROWS = make_rows(FORMATION_4231)
