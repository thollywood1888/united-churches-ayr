"""Pitch slots for the starting XI. Attack is at the top, keeper at the bottom."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby


@dataclass(frozen=True, slots=True)
class PitchSlot:
    key: str
    label: str
    row: int


# 4-2-3-1, top to bottom.
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

SLOT_KEYS = {slot.key for slot in FORMATION_4231}

PITCH_ROWS: tuple[tuple[PitchSlot, ...], ...] = tuple(
    tuple(group) for _, group in groupby(FORMATION_4231, key=lambda slot: slot.row)
)
