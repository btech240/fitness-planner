"""Muscle recovery from logged sets.

Each logged set adds fatigue to the muscles it works: 1.0 to each primary
muscle, 0.5 to each secondary muscle. That fatigue fades linearly to zero over
DECAY_HOURS. Recovery % = 100 * (1 - fatigue / FULL_FATIGUE), floored at 0.

So 10 hard sets for chest leave chest at 0% right afterwards, about 50% a day
and a half later, and 100% after three days. Three sets yesterday leave about 80%.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DECAY_HOURS = 72.0
FULL_FATIGUE = 10.0
SECONDARY_WEIGHT = 0.5

# Muscles the generator builds workouts around (dataset names).
TARGET_MUSCLES = [
    "chest", "shoulders", "triceps", "biceps", "lats", "middle back", "traps",
    "lower back", "quadriceps", "hamstrings", "glutes", "calves", "abdominals", "forearms",
]
ALL_MUSCLES = TARGET_MUSCLES + ["adductors", "abductors", "neck"]


@dataclass
class LoggedSet:
    logged_at: datetime
    primary: list[str]
    secondary: list[str]


def fatigue(sets: list[LoggedSet], now: datetime) -> dict[str, float]:
    f = {m: 0.0 for m in ALL_MUSCLES}
    for s in sets:
        hours = (now - s.logged_at).total_seconds() / 3600
        decay = max(0.0, 1.0 - hours / DECAY_HOURS)
        if decay <= 0:
            continue
        for m in s.primary:
            f[m] = f.get(m, 0.0) + decay
        for m in s.secondary:
            f[m] = f.get(m, 0.0) + SECONDARY_WEIGHT * decay
    return f


def recovery_from_fatigue(f: dict[str, float]) -> dict[str, int]:
    return {m: max(0, round(100 * (1 - v / FULL_FATIGUE))) for m, v in f.items()}


def recovery(sets: list[LoggedSet], now: datetime) -> dict[str, int]:
    return recovery_from_fatigue(fatigue(sets, now))
