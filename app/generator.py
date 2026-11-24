"""Workout generation and weight/rep suggestions. Pure functions: no database, no web.

How a workout is built:
1. Pick a day PATTERN (push, pull, legs, upper, lower, full) whose main muscles are
   most recovered, avoiding a repeat of the previous workout's pattern.
2. Hand out the exercise slots round-robin over that pattern's recovered muscles.
3. A muscle's first slot goes to one of its CORE lifts (pinned > most-logged > defaults),
   alternating between them; later slots ROTATE through other variations, avoiding
   recently done exercises and near-duplicates of anything already chosen.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

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

READY = 60          # a muscle below this recovery % gets no slots
TIRED = 50          # exercises whose other primary muscles are below this get penalised
MAX_JUMP = 1.15     # after a fully completed session, move up one loadable step if it's at most +15%
BODYWEIGHT_REP_CAP = 5  # bodyweight rep targets climb to goal reps + this, then suggest adding weight

# Muscles in priority order; the first three are the pattern's "main" muscles used to score it.
PATTERNS = {
    "push": ("Push", ["chest", "shoulders", "triceps"]),
    "pull": ("Pull", ["lats", "middle back", "biceps", "traps"]),
    "legs": ("Legs", ["quadriceps", "hamstrings", "glutes", "calves"]),
    "upper": ("Upper", ["chest", "lats", "shoulders", "middle back", "biceps", "triceps"]),
    "lower": ("Lower", ["quadriceps", "hamstrings", "glutes", "abdominals", "calves", "lower back"]),
    "full": ("Full body", ["quadriceps", "chest", "lats", "hamstrings", "shoulders", "middle back"]),
}
REPEAT_PATTERN_PENALTY = 15

# Bonus added to recovery % when ordering muscles inside a pattern.
MUSCLE_PRIORITY = {
    "chest": 20, "lats": 20, "quadriceps": 20, "shoulders": 15, "hamstrings": 15, "middle back": 15,
    "glutes": 12, "biceps": 8, "triceps": 8, "abdominals": 6, "traps": 3, "calves": 3,
    "lower back": 2, "forearms": 0,
}

# Default core lifts per muscle, best first, used until your own history takes over.
CORE_DEFAULTS = {
    "chest": ["Barbell_Bench_Press_-_Medium_Grip", "Dumbbell_Bench_Press", "Incline_Dumbbell_Press", "Pushups"],
    "lats": ["Pullups", "Wide-Grip_Lat_Pulldown", "Chin-Up"],
    "middle back": ["Bent_Over_Barbell_Row", "One-Arm_Dumbbell_Row", "Seated_Cable_Rows"],
    "shoulders": ["Standing_Military_Press", "Dumbbell_Shoulder_Press", "Side_Lateral_Raise"],
    "quadriceps": ["Barbell_Squat", "Goblet_Squat", "Dumbbell_Squat", "Split_Squat_with_Dumbbells"],
    "hamstrings": ["Romanian_Deadlift", "Stiff-Legged_Dumbbell_Deadlift"],
    "glutes": ["Barbell_Hip_Thrust", "Barbell_Glute_Bridge", "Single_Leg_Glute_Bridge"],
    "biceps": ["Barbell_Curl", "Dumbbell_Bicep_Curl", "Hammer_Curls"],
    "triceps": ["Dips_-_Triceps_Version", "Triceps_Pushdown", "Lying_Triceps_Press",
                "Standing_Dumbbell_Triceps_Extension"],
    "traps": ["Barbell_Shrug", "Dumbbell_Shrug"],
    "calves": ["Standing_Barbell_Calf_Raise", "Standing_Dumbbell_Calf_Raise"],
    "abdominals": ["Hanging_Leg_Raise", "Cable_Crunch", "Plank"],
    "lower back": ["Barbell_Deadlift", "Hyperextensions_With_No_Hyperextension_Bench"],
}
CORE_PER_MUSCLE = 2

# Well-known lifts get a scoring boost so rotation slots aren't full of obscure variations.
STAPLES = {
    "Arnold_Dumbbell_Press", "Barbell_Bench_Press_-_Medium_Grip", "Barbell_Curl", "Barbell_Deadlift",
    "Barbell_Glute_Bridge", "Barbell_Hip_Thrust", "Barbell_Incline_Bench_Press_-_Medium_Grip", "Barbell_Lunge",
    "Barbell_Shrug", "Barbell_Squat", "Bent_Over_Barbell_Row", "Bent_Over_Two-Dumbbell_Row",
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
    "Standing_Barbell_Calf_Raise", "Standing_Dumbbell_Calf_Raise", "Good_Morning",
}

# Bodyweight moves that are worthwhile even when weights are available.
BODYWEIGHT_KEEPERS = {
    "Pullups", "Chin-Up", "Dips_-_Triceps_Version", "Pushups", "Hanging_Leg_Raise", "Plank", "Crunches",
    "Russian_Twist", "Single_Leg_Glute_Bridge", "Hyperextensions_With_No_Hyperextension_Bench",
    "Natural_Glute_Ham_Raise", "Inverted_Row",
}

OLYMPIC = re.compile(r"\b(clean|snatch|jerk|power)\b", re.I)

# Near-duplicates: two exercises sharing a family never appear in the same workout.
FAMILIES = [
    ("pullup", re.compile(r"pull-?ups?|chin-?ups?", re.I)),
    ("squat", re.compile(r"(?<!split )squats?\b", re.I)),
    ("lunge", re.compile(r"lunges?|split squat|step[- ]?ups?", re.I)),
    ("deadlift", re.compile(r"deadlifts?", re.I)),
    ("good-morning", re.compile(r"good mornings?", re.I)),
    ("flat-press", re.compile(r"^(?!.*(incline|decline)).*(bench press|floor press|chest press)", re.I)),
    ("incline-press", re.compile(r"incline.*press", re.I)),
    ("overhead-press", re.compile(r"military press|shoulder press|overhead press|arnold|push press", re.I)),
    ("pushup", re.compile(r"push-?ups?", re.I)),
    ("dip", re.compile(r"\bdips?\b", re.I)),
    ("shrug", re.compile(r"shrugs?", re.I)),
    ("hip-thrust", re.compile(r"hip thrust|glute bridge|butt lift", re.I)),
    ("calf-raise", re.compile(r"calf raises?", re.I)),
    ("pulldown", re.compile(r"pulldown", re.I)),
    ("pushdown", re.compile(r"pushdown", re.I)),
    ("lateral-raise", re.compile(r"lateral raise|side lateral|deltoid raise", re.I)),
    ("fly", re.compile(r"fl(y|ye|yes|ies)\b|crossover", re.I)),
]


@dataclass
class ExerciseHistory:
    sessions: int = 0       # workouts this exercise was logged in
    last_ago: int = 99      # 0 = logged in the most recent workout, 1 = the one before, ...


@dataclass
class Plan:
    pattern: str
    exercises: list[dict] = field(default_factory=list)


def families(name: str) -> set[str]:
    fams = {f for f, rx in FAMILIES if rx.search(name)}
    return fams or {name.split(" - ")[0].strip().lower()}


def available_exercises(catalog: list[dict], inventory: dict, excluded: set[str]) -> list[dict]:
    tags = inv.available_equipment_tags(inventory)
    have = inv.available_features(inventory)
    out = []
    for e in catalog:
        if e["id"] in excluded or e["equipment"] not in tags:
            continue
        if not set(e["requires"]) <= have:
            continue
        kind = inv.kind_for_tag(e["equipment"])
        if kind and inv.loadable_weights(inventory, kind) == []:
            continue  # equipment enabled but no weights entered
        out.append(e)
    return out


def _has_loaded_gear(pool: list[dict]) -> bool:
    return any(e["equipment"] not in ("body only", "bands") for e in pool)


# ---- step 1: pattern ----------------------------------------------------------------

def pick_pattern(recovery: dict[str, int], pool: list[dict], last_pattern: str | None,
                 rng: random.Random) -> str:
    have = {m for e in pool for m in e["primary"]}

    def score(key: str) -> float:
        main = [m for m in PATTERNS[key][1][:3] if m in have]
        if not main:
            return -1e9
        weights = [MUSCLE_PRIORITY.get(m, 0) + 1 for m in main]
        avg = sum(w * recovery.get(m, 100) for w, m in zip(weights, main)) / sum(weights)
        coverage = len(main) / 3  # patterns your gear can't fill well score lower
        s = avg * coverage + rng.uniform(0, 8)
        if key == last_pattern:
            s -= REPEAT_PATTERN_PENALTY
        return s

    return max(PATTERNS, key=score)


# ---- step 2: slots ---------------------------------------------------------------------

def allocate_slots(pattern: str, recovery: dict[str, int], pool: list[dict], n: int,
                   rng: random.Random) -> list[str]:
    """Muscle for each slot, round-robin over the pattern's recovered muscles."""
    have = {m for e in pool for m in e["primary"]}
    muscles = [m for m in PATTERNS[pattern][1] if m in have]
    ready = [m for m in muscles if recovery.get(m, 100) >= READY] or muscles[:1]
    ready.sort(key=lambda m: -(recovery.get(m, 100) + MUSCLE_PRIORITY.get(m, 0) + rng.uniform(0, 4)))
    if not ready:
        return []
    width = min(len(ready), max(2, (n + 1) // 2)) if pattern not in ("full", "upper", "lower") else len(ready)
    ring = ready[:width]
    return [ring[i % len(ring)] for i in range(n)]


# ---- step 3: exercises --------------------------------------------------------------------

def core_lifts(muscle: str, pool: list[dict], history: dict[str, ExerciseHistory],
               pins: set[str]) -> list[dict]:
    """Up to CORE_PER_MUSCLE lifts for this muscle: pinned first, then most logged, then defaults."""
    targeting = {e["id"]: e for e in pool if muscle in e["primary"]}
    ordered: list[str] = [i for i in sorted(pins) if i in targeting]
    logged = sorted((i for i in targeting if history.get(i, ExerciseHistory()).sessions >= 2),
                    key=lambda i: -history[i].sessions)
    ordered += [i for i in logged if i not in ordered]
    ordered += [i for i in CORE_DEFAULTS.get(muscle, []) if i in targeting and i not in ordered]
    pinned_count = len([i for i in ordered if i in pins])
    return [targeting[i] for i in ordered[:max(CORE_PER_MUSCLE, pinned_count)]]


def _conflicts(e: dict, chosen: list[dict]) -> bool:
    if any(c["id"] == e["id"] for c in chosen):
        return True
    fams = families(e["name"])
    return any(fams & families(c["name"]) for c in chosen)


def _rotation_score(e: dict, muscle: str, recovery: dict[str, int], history: dict[str, ExerciseHistory],
                    core_ids: set[str], loaded_gear: bool, rng: random.Random) -> float:
    h = history.get(e["id"], ExerciseHistory())
    s = rng.uniform(0, 2.0)
    if e["id"] in STAPLES:
        s += 2.5
    if e.get("level") in ("beginner", "intermediate"):
        s += 0.5
    # rotation means variety: recently or frequently used variations step aside
    if h.last_ago <= 1:
        s -= 3.0
    elif h.last_ago <= 4:
        s -= 1.5
    s -= 0.5 * min(h.sessions, 4)
    if OLYMPIC.search(e["name"]):
        s -= 6.0  # explosive technique lifts aren't routine filler
    if e["id"] in core_ids:
        s -= 1.0  # rotation slots favor variety; core lifts already get their own slot
    if loaded_gear and e["equipment"] == "body only" and e["id"] not in BODYWEIGHT_KEEPERS:
        s -= 2.0
    for m in e["primary"]:
        if m != muscle and recovery.get(m, 100) < TIRED:
            s -= 3.0
    return s


def choose_exercise(pool: list[dict], muscle: str, chosen: list[dict], recovery: dict[str, int],
                    history: dict[str, ExerciseHistory], pins: set[str], rng: random.Random,
                    core_slot: bool) -> dict | None:
    core = core_lifts(muscle, pool, history, pins)
    if core_slot:
        free = [e for e in core if not _conflicts(e, chosen)]
        pinned = [e for e in free if e["id"] in pins]
        if pinned:
            return pinned[0]
        if free:
            # alternate between core lifts: the one done least recently goes first
            return max(free, key=lambda e: (history.get(e["id"], ExerciseHistory()).last_ago, -core.index(e)))
    candidates = [e for e in pool if muscle in e["primary"] and not _conflicts(e, chosen)]
    if not candidates:
        return None
    core_ids = {e["id"] for e in core}
    loaded = _has_loaded_gear(pool)
    return max(candidates, key=lambda e: _rotation_score(e, muscle, recovery, history, core_ids, loaded, rng))


def generate(pool: list[dict], recovery: dict[str, int], history: dict[str, ExerciseHistory],
             pins: set[str], n_exercises: int, rng: random.Random | None = None,
             last_pattern: str | None = None, pattern: str | None = None,
             avoid: set[str] | frozenset[str] = frozenset()) -> Plan:
    """Build a workout. `pattern` forces the day type; `avoid` lists exercises to leave out
    when the gear allows (used by "New exercises": same day, different lifts)."""
    rng = rng or random.Random()
    if not pool:
        return Plan(pattern="full")
    if pattern not in PATTERNS:
        pattern = pick_pattern(recovery, pool, last_pattern, rng)
    slots = allocate_slots(pattern, recovery, pool, n_exercises, rng)
    fresh = [e for e in pool if e["id"] not in avoid]

    def pick(muscle: str, chosen: list[dict], core_slot: bool) -> dict | None:
        # avoided exercises only come back when a muscle has nothing else
        return (choose_exercise(fresh, muscle, chosen, recovery, history, pins, rng, core_slot)
                or choose_exercise(pool, muscle, chosen, recovery, history, pins, rng, core_slot))

    chosen: list[dict] = []
    seen: set[str] = set()
    for muscle in slots:
        e = pick(muscle, chosen, core_slot=muscle not in seen)
        seen.add(muscle)
        if e:
            chosen.append(e)
    # top up from the pattern's other muscles if some slots couldn't be filled
    for muscle in PATTERNS[pattern][1]:
        if len(chosen) >= n_exercises:
            break
        e = pick(muscle, chosen, core_slot=False)
        if e:
            chosen.append(e)
    chosen.sort(key=lambda e: e.get("mechanic") != "compound")  # compounds first, stable otherwise
    return Plan(pattern=pattern, exercises=chosen)


# ---- suggestions ---------------------------------------------------------------------------

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


def suggest_bodyweight_reps(last_sets: list[tuple[int, float | None]], goal_reps: int) -> int:
    """Rep target for an unweighted bodyweight move: last session's average + 1, capped."""
    unweighted = [r for r, w in last_sets if not w]
    if not unweighted:
        return goal_reps
    return min(goal_reps + BODYWEIGHT_REP_CAP, round(sum(unweighted) / len(unweighted)) + 1)
