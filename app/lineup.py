"""Pitch slots for the starting XI. Attack is at the top, keeper at the bottom."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby


@dataclass(frozen=True, slots=True)
class PitchSlot:
    key: str
    label: str
    row: int


def make_rows(formation: tuple[PitchSlot, ...]) -> tuple[tuple[PitchSlot, ...], ...]:
    return tuple(
        tuple(group) for _, group in groupby(formation, key=lambda slot: slot.row)
    )


# 4-2-3-1, attack at row 0, keeper at bottom
FORMATION_4231: tuple[PitchSlot, ...] = (
    PitchSlot("lw", "LW", 0),
    PitchSlot("st", "ST", 0),
    PitchSlot("rw", "RW", 0),
    PitchSlot("cam", "CAM", 1),
    PitchSlot("lcm", "CM", 2),
    PitchSlot("rcm", "CM", 2),
    PitchSlot("lb", "LB", 3),
    PitchSlot("lcb", "CB", 3),
    PitchSlot("rcb", "CB", 3),
    PitchSlot("rb", "RB", 3),
    PitchSlot("gk", "GK", 4),
)

# 3-5-2, attack at row 0, keeper at bottom
FORMATION_352: tuple[PitchSlot, ...] = (
    PitchSlot("ls", "LS", 0),
    PitchSlot("rs", "RS", 0),
    PitchSlot("am", "AM", 1),
    PitchSlot("ldm", "DM", 2),
    PitchSlot("rdm", "DM", 2),
    PitchSlot("lwb", "LWB", 3),
    PitchSlot("rwb", "RWB", 3),
    PitchSlot("cb1", "CB", 4),
    PitchSlot("cb2", "CB", 4),
    PitchSlot("cb3", "CB", 4),
    PitchSlot("gk", "GK", 5),
)

FORMATIONS: dict[str, tuple[PitchSlot, ...]] = {
    "4-2-3-1": FORMATION_4231,
    "3-5-2": FORMATION_352,
}

# Union of all slot keys across every formation (used for broad validation).
SLOT_KEYS = {slot.key for slots in FORMATIONS.values() for slot in slots}

# Legacy aliases kept for any external imports.
PITCH_ROWS = make_rows(FORMATION_4231)
