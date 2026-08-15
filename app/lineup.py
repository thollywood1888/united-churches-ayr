"""Pitch slots for the starting XI. Attack is at the top, keeper at the bottom."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PitchSlot:
    key: str
    label: str
    x: float
    y: float


# 4-2-3-1, coordinates are percentages of the pitch face.
FORMATION_4231: tuple[PitchSlot, ...] = (
    PitchSlot("lw", "LW", 16, 12),
    PitchSlot("st", "ST", 50, 9),
    PitchSlot("rw", "RW", 84, 12),
    PitchSlot("cam", "CAM", 50, 29),
    PitchSlot("lcm", "CM", 34, 45),
    PitchSlot("rcm", "CM", 66, 45),
    PitchSlot("lb", "LB", 10, 64),
    PitchSlot("lcb", "CB", 34, 68),
    PitchSlot("rcb", "CB", 66, 68),
    PitchSlot("rb", "RB", 90, 64),
    PitchSlot("gk", "GK", 50, 88),
)

SLOT_KEYS = {slot.key for slot in FORMATION_4231}
