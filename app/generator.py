"""Workout generation and weight suggestions. Pure functions: no database, no web."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from . import inventory as inv
from .recovery import TARGET_MUSCLES


@dataclass(frozen=True)
class Goal:
    label: str
    sets: int
    reps: int
    rest_seconds: int


GOALS = {
    "strength": Goal("Strength", 4, 5, 180),
    "hypertrophy": Goal("Muscle building", 3, 10, 90),
    "endurance": Goal("Endurance", 3, 15, 45),
}

READY = 60          # a muscle below this recovery % is not chosen as a target
TIRED = 50          # exercises whose other primary muscles are below this get penalised
MAX_JUMP = 1.15     # after a fully completed session, move up one loadable step if it's at most +15%

# Bonus added to recovery % when ranking target muscles, so big movers lead the
# workout and small muscles (calves, forearms...) only come up when the big ones are tired.
MUSCLE_PRIORITY = {
    "chest": 20, "lats": 20, "quadriceps": 20, "shoulders": 15, "hamstrings": 15, "middle back": 15,
    "glutes": 12, "biceps": 8, "triceps": 8, "abdominals": 6, "traps": 3, "calves": 3,
    "lower back": 2, "forearms": 0,
}

# Well-known lifts get a scoring boost so workouts aren't full of obscure variations.
STAPLES = {
    "Arnold_Dumbbell_Press", "Barbell_Bench_Press_-_Medium_Grip", "Barbell_Curl", "Barbell_Deadlift",
    "Barbell_Glute_Bridge", "Barbell_Hip_Thrust", "Barbell_Incline_Bench_Press_-_Medium_Grip", "Barbell_Lunge",
    "Barbell_Shrug", "Barbell_Squat", "Bent_Over_Barbell_Row", "Bent_Over_Two-Dumbbell_Row", "Bodyweight_Squat",
    "Cable_Crossover", "Cable_Crunch", "Cable_Rope_Overhead_Triceps_Extension", "Chin-Up",
    "Close-Grip_Barbell_Bench_Press", "Crunches", "Dips_-_Triceps_Version", "Dumbbell_Bench_Press",
    "Dumbbell_Bicep_Curl", "Dumbbell_Flyes", "Dumbbell_Lunges", "Dumbbell_Shoulder_Press", "Dumbbell_Shrug",
    "Dumbbell_Step_Ups", "EZ-Bar_Curl", "Face_Pull", "Front_Barbell_Squat", "Goblet_Squat", "Hammer_Curls",
    "Hanging_Leg_Raise", "Incline_Dumbbell_Curl", "Incline_Dumbbell_Flyes", "Incline_Dumbbell_Press",
    "Lying_Triceps_Press", "One-Arm_Dumbbell_Row", "One-Arm_Kettlebell_Swings", "Plank", "Pullups", "Pushups",
    "Reverse_Flyes", "Romanian_Deadlift", "Seated_Cable_Rows", "Seated_Dumbbell_Press", "Side_Lateral_Raise",
    "Single_Leg_Glute_Bridge", "Split_Squat_with_Dumbbells", "Standing_Dumbbell_Triceps_Extension",
    "Standing_Military_Press", "Stiff-Legged_Barbell_Deadlift", "Stiff-Legged_Dumbbell_Deadlift",
    "Sumo_Deadlift", "Triceps_Pushdown", "Triceps_Pushdown_-_Rope_Attachment", "Upright_Barbell_Row",
    "Wide-Grip_Lat_Pulldown", "Dumbbell_Floor_Press", "Dumbbell_Squat", "Russian_Twist",
}


def available_exercises(catalog: list[dict], inventory: dict, excluded: set[str]) -> list[dict]:
    tags = inv.available_equipment_tags(inventory)
    stations = set(inventory.get("stations") or [])
    out = []
    for e in catalog:
        if e["id"] in excluded or e["equipment"] not in tags:
            continue
        if not set(e["requires"]) <= stations:
            continue
        kind = inv.kind_for_tag(e["equipment"])
        if kind and inv.loadable_weights(inventory, kind) == []:
            continue  # equipment enabled but no weights entered
        out.append(e)
    return out


def _base_name(name: str) -> str:
    return name.split(" - ")[0].strip().lower()


def _score(e: dict, target: str, recovery: dict[str, int], familiar: set[str],
           first_for_target: bool, rng: random.Random) -> float:
    s = rng.uniform(0, 1.5)
    if e["id"] in familiar:
        s += 1.0
    if e["id"] in STAPLES:
        s += 2.5
    if e.get("level") in ("beginner", "intermediate"):
        s += 0.5
    if first_for_target and e.get("mechanic") == "compound":
        s += 2.0
    for m in e["primary"]:
        if m != target and recovery.get(m, 100) < TIRED:
            s -= 3.0
    return s


def pick_targets(recovery: dict[str, int], pool: list[dict], n_exercises: int,
                 rng: random.Random) -> list[str]:
    """Most-recovered muscles that have at least one available exercise."""
    have = {m for e in pool for m in e["primary"]}
    k = max(1, min(3, math.ceil(n_exercises / 2)))
    ranked = sorted(
        (m for m in TARGET_MUSCLES if m in have),
        key=lambda m: recovery.get(m, 100) + MUSCLE_PRIORITY.get(m, 0) + rng.uniform(0, 10),
        reverse=True,
    )
    ready = [m for m in ranked if recovery.get(m, 100) >= READY]
    return (ready or ranked)[:k]


def choose_exercise(pool: list[dict], target: str, chosen: list[dict], recovery: dict[str, int],
                    familiar: set[str], rng: random.Random) -> dict | None:
    taken_ids = {e["id"] for e in chosen}
    taken_bases = {_base_name(e["name"]) for e in chosen}
    first = not any(target in e["primary"] for e in chosen)
    candidates = [
        e for e in pool
        if target in e["primary"] and e["id"] not in taken_ids and _base_name(e["name"]) not in taken_bases
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda e: _score(e, target, recovery, familiar, first, rng))


def generate(pool: list[dict], recovery: dict[str, int], familiar: set[str], n_exercises: int,
             rng: random.Random | None = None) -> list[dict]:
    """Pick n exercises from the pool, round-robin over the most-recovered target muscles."""
    rng = rng or random.Random()
    targets = pick_targets(recovery, pool, n_exercises, rng)
    chosen: list[dict] = []
    stalled = 0
    i = 0
    while len(chosen) < n_exercises and targets and stalled < len(targets):
        target = targets[i % len(targets)]
        e = choose_exercise(pool, target, chosen, recovery, familiar, rng)
        if e:
            chosen.append(e)
            stalled = 0
        else:
            stalled += 1
        i += 1
    # compound lifts first, then keep target order
    return sorted(chosen, key=lambda e: e.get("mechanic") != "compound")


def suggest_weight(last_sets: list[tuple[int, float | None]], target_reps: int,
                   options: list[float] | None) -> float | None:
    """Next working weight from the previous session's sets for this exercise.

    Estimates a one-rep max (Epley) from the best set, converts it back to the
    target rep count, and adds ~2.5% if every set reached the target reps.
    Returns None when there is no weighted history or the equipment has no load.
    """
    weighted = [(r, w) for r, w in last_sets if w]
    if not weighted or not options:
        return None
    e1rm = max(w * (1 + r / 30) for r, w in weighted)
    hit = all(r >= target_reps for r, _ in last_sets)
    target = e1rm / (1 + target_reps / 30) * (1.025 if hit else 1.0)
    pick = inv.nearest_loadable(target, options)
    top = max(w for _, w in weighted)
    if hit and pick <= top:
        heavier = [w for w in options if w > top]
        if heavier and heavier[0] <= top * MAX_JUMP:
            pick = heavier[0]
    return pick
