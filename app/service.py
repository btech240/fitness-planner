"""Operations that combine the database with the pure planning logic.

Everything personal takes `pid`, the current profile's id. The equipment list
(inventory) is shared by every profile.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone

from . import catalog, db, generator, inventory as inv, photos
from .recovery import DECAY_HOURS, LoggedSet, recovery

DEFAULT_SETTINGS = {"goal": "hypertrophy", "n_exercises": 5, "units": "lb"}
PROFILE_COLORS = ["#FF3EA5", "#2EE6D6", "#FFD23F", "#B86BFF", "#FF8A3D", "#4C8DFF", "#7CF29C"]
MAX_NAME = 30


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---- profiles ---------------------------------------------------------------

class ProfileError(ValueError):
    pass


def list_profiles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()


def get_profile(conn: sqlite3.Connection, pid) -> sqlite3.Row | None:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    return conn.execute("SELECT * FROM profiles WHERE id = ?", (pid,)).fetchone()


def _clean_name(conn: sqlite3.Connection, name: str, exclude_pid: int | None = None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ProfileError("Enter a name.")
    if len(name) > MAX_NAME:
        raise ProfileError(f"Keep the name to {MAX_NAME} characters.")
    clash = conn.execute("SELECT name FROM profiles WHERE lower(name) = lower(?) AND id IS NOT ?",
                         (name, exclude_pid)).fetchone()
    if clash:
        raise ProfileError(f"There's already a profile called {clash['name']}.")
    return name


def _clean_goal(goal) -> str:
    return goal if goal in generator.GOALS else DEFAULT_SETTINGS["goal"]


def _clean_count(n) -> int:
    try:
        return min(10, max(2, int(float(n))))
    except (TypeError, ValueError):
        return DEFAULT_SETTINGS["n_exercises"]


def create_profile(conn: sqlite3.Connection, name: str, goal: str = "hypertrophy", n_exercises=5) -> int:
    name = _clean_name(conn, name)
    used = {r["color"] for r in list_profiles(conn)}
    free = [c for c in PROFILE_COLORS if c not in used]
    color = free[0] if free else PROFILE_COLORS[len(used) % len(PROFILE_COLORS)]
    cur = conn.execute(
        "INSERT INTO profiles(name, color, goal, n_exercises, units, created_at) VALUES (?, ?, ?, ?, 'lb', ?)",
        (name, color, _clean_goal(goal), _clean_count(n_exercises), now().isoformat()))
    return cur.lastrowid


def rename_profile(conn: sqlite3.Connection, pid: int, name: str) -> None:
    conn.execute("UPDATE profiles SET name = ? WHERE id = ?", (_clean_name(conn, name, pid), pid))


def delete_profile(conn: sqlite3.Connection, pid: int) -> None:
    """Deletes the profile and, by cascade, its workouts, sets, pins and exclusions."""
    conn.execute("DELETE FROM profiles WHERE id = ?", (pid,))


# ---- settings -------------------------------------------------------------

def settings(conn: sqlite3.Connection, pid: int) -> dict:
    p = get_profile(conn, pid)
    out = dict(DEFAULT_SETTINGS)
    if p:
        out.update(goal=_clean_goal(p["goal"]), n_exercises=_clean_count(p["n_exercises"]),
                   units="kg" if p["units"] == "kg" else "lb")
    out["inventory"] = inv.merged(db.get_setting(conn, "inventory"))
    return out


def save_personal_settings(conn: sqlite3.Connection, pid: int, goal: str, n_exercises, units: str) -> None:
    conn.execute("UPDATE profiles SET goal = ?, n_exercises = ?, units = ? WHERE id = ?",
                 (_clean_goal(goal), _clean_count(n_exercises), "kg" if units == "kg" else "lb", pid))


def excluded_ids(conn: sqlite3.Connection, pid: int) -> set[str]:
    return {r["exercise_id"] for r in conn.execute("SELECT exercise_id FROM excluded WHERE profile_id = ?", (pid,))}


def pinned_ids(conn: sqlite3.Connection, pid: int) -> set[str]:
    return {r["exercise_id"] for r in conn.execute("SELECT exercise_id FROM pinned WHERE profile_id = ?", (pid,))}


# ---- ownership checks (ids in URLs must belong to the current profile) ------------

def owned_workout(conn: sqlite3.Connection, pid: int, wid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM workouts WHERE id = ? AND profile_id = ?", (wid, pid)).fetchone()


def owned_we(conn: sqlite3.Connection, pid: int, we_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT we.* FROM workout_exercises we JOIN workouts w ON w.id = we.workout_id "
        "WHERE we.id = ? AND w.profile_id = ?", (we_id, pid)).fetchone()


def owned_set(conn: sqlite3.Connection, pid: int, set_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT s.* FROM sets s JOIN workout_exercises we ON we.id = s.workout_exercise_id "
        "JOIN workouts w ON w.id = we.workout_id WHERE s.id = ? AND w.profile_id = ?", (set_id, pid)).fetchone()


# ---- recovery & history ----------------------------------------------------

def recent_sets(conn: sqlite3.Connection, pid: int, at: datetime) -> list[LoggedSet]:
    since = (at - timedelta(hours=DECAY_HOURS)).isoformat()
    rows = conn.execute(
        "SELECT s.logged_at, we.exercise_id FROM sets s "
        "JOIN workout_exercises we ON we.id = s.workout_exercise_id "
        "JOIN workouts w ON w.id = we.workout_id WHERE w.profile_id = ? AND s.logged_at >= ?",
        (pid, since),
    )
    out = []
    for r in rows:
        e = catalog.get(r["exercise_id"])
        out.append(LoggedSet(parse_ts(r["logged_at"]), e["primary"], e["secondary"]))
    return out


def current_recovery(conn: sqlite3.Connection, pid: int, at: datetime | None = None) -> dict[str, int]:
    at = at or now()
    return recovery(recent_sets(conn, pid, at), at)


def exercise_history(conn: sqlite3.Connection, pid: int, before_workout: int | None = None,
                     limit: int = 30) -> dict[str, generator.ExerciseHistory]:
    """Per exercise: in how many of the last `limit` logged workouts it appears, and how recently."""
    workouts = [r["workout_id"] for r in conn.execute(
        "SELECT DISTINCT we.workout_id FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id "
        "JOIN workouts w ON w.id = we.workout_id "
        "WHERE w.profile_id = ? AND (? IS NULL OR we.workout_id < ?) ORDER BY we.workout_id DESC LIMIT ?",
        (pid, before_workout, before_workout, limit))]
    hist: dict[str, generator.ExerciseHistory] = {}
    for ago, wid in enumerate(workouts):
        for r in conn.execute(
                "SELECT DISTINCT we.exercise_id FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id "
                "WHERE we.workout_id = ?", (wid,)):
            h = hist.setdefault(r["exercise_id"], generator.ExerciseHistory())
            h.sessions += 1
            h.last_ago = min(h.last_ago, ago)
    return hist


def last_pattern(conn: sqlite3.Connection, pid: int, before_workout: int | None = None) -> str | None:
    """Pattern of this profile's most recent workout that has logged sets."""
    row = conn.execute(
        "SELECT pattern FROM workouts WHERE profile_id = ? AND pattern IS NOT NULL AND (? IS NULL OR id < ?) "
        "AND EXISTS (SELECT 1 FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id "
        "WHERE we.workout_id = workouts.id) ORDER BY id DESC LIMIT 1",
        (pid, before_workout, before_workout)).fetchone()
    return row["pattern"] if row else None


def last_session_sets(conn: sqlite3.Connection, pid: int, exercise_id: str, before_workout: int | None = None):
    """(reps, weight) of this profile's most recent earlier workout where the exercise was logged."""
    row = conn.execute(
        "SELECT we.id FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id "
        "JOIN workouts w ON w.id = we.workout_id "
        "WHERE w.profile_id = ? AND we.exercise_id = ? AND (? IS NULL OR we.workout_id < ?) "
        "ORDER BY s.logged_at DESC LIMIT 1",
        (pid, exercise_id, before_workout, before_workout),
    ).fetchone()
    if not row:
        return []
    return [(r["reps"], r["weight"]) for r in conn.execute(
        "SELECT reps, weight FROM sets WHERE workout_exercise_id = ? ORDER BY id", (row["id"],))]


# ---- workouts ----------------------------------------------------------------

def current_workout(conn: sqlite3.Connection, pid: int):
    return conn.execute(
        "SELECT * FROM workouts WHERE profile_id = ? AND finished_at IS NULL ORDER BY id DESC LIMIT 1",
        (pid,)).fetchone()


def _pool(conn: sqlite3.Connection, pid: int, s: dict) -> list[dict]:
    return generator.available_exercises(catalog.EXERCISES, s["inventory"], excluded_ids(conn, pid))


def is_unloaded(exercise: dict) -> bool:
    return exercise["equipment"] in ("body only", "bands")


def _targets(conn, pid: int, s: dict, exercise: dict, workout_id: int | None) -> tuple[int, float | None]:
    """(target reps, suggested weight) for an exercise in a new workout."""
    goal = generator.GOALS[s["goal"]]
    last = last_session_sets(conn, pid, exercise["id"], workout_id)
    if is_unloaded(exercise) and not any(w for _, w in last):
        return generator.suggest_bodyweight_reps(last, goal.reps), None
    kind = inv.kind_for_tag(exercise["equipment"])
    options = inv.loadable_weights(s["inventory"], kind) if kind else None
    return goal.reps, generator.suggest_weight(last, goal.reps, options)


def _add_exercise(conn, pid: int, s: dict, workout_id: int, exercise: dict, position: int) -> None:
    goal = generator.GOALS[s["goal"]]
    reps, weight = _targets(conn, pid, s, exercise, workout_id)
    conn.execute(
        "INSERT INTO workout_exercises(workout_id, exercise_id, position, target_sets, target_reps, suggested_weight) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (workout_id, exercise["id"], position, goal.sets, reps, weight),
    )


def create_workout(conn: sqlite3.Connection, pid: int, rng: random.Random | None = None) -> int:
    s = settings(conn, pid)
    plan = generator.generate(_pool(conn, pid, s), current_recovery(conn, pid), exercise_history(conn, pid),
                              pinned_ids(conn, pid), int(s["n_exercises"]), rng, last_pattern(conn, pid))
    cur = conn.execute("INSERT INTO workouts(profile_id, created_at, goal, pattern) VALUES (?, ?, ?, ?)",
                       (pid, now().isoformat(), s["goal"], plan.pattern))
    wid = cur.lastrowid
    for i, e in enumerate(plan.exercises):
        _add_exercise(conn, pid, s, wid, e, i)
    return wid


def workout_has_sets(conn: sqlite3.Connection, workout_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM sets s JOIN workout_exercises we ON we.id = s.workout_exercise_id "
        "WHERE we.workout_id = ? LIMIT 1", (workout_id,)).fetchone() is not None


def _has_sets(conn: sqlite3.Connection, we_id: int) -> bool:
    return conn.execute("SELECT 1 FROM sets WHERE workout_exercise_id = ?", (we_id,)).fetchone() is not None


def skipped_ids(conn: sqlite3.Connection, workout_id: int) -> set[str]:
    """Exercises passed over in this workout (swapped, removed or regenerated away)."""
    return {r["exercise_id"] for r in conn.execute(
        "SELECT exercise_id FROM workout_skips WHERE workout_id = ?", (workout_id,))}


def _skip(conn: sqlite3.Connection, workout_id: int, exercise_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO workout_skips(workout_id, exercise_id) VALUES (?, ?)",
                 (workout_id, exercise_id))


def _replace(conn: sqlite3.Connection, pid: int, we, avoid: set[str], rng: random.Random | None) -> int | None:
    """Swap the workout exercise `we` for another one aimed at the same muscle (falling back to the
    day's other muscles). Exercises in `avoid` are used only when nothing else fits; the exercise
    being replaced never comes back. Returns the new workout-exercise id, or None (left as it was)."""
    s = settings(conn, pid)
    old = catalog.get(we["exercise_id"])
    others = [catalog.get(r["exercise_id"]) for r in conn.execute(
        "SELECT exercise_id FROM workout_exercises WHERE workout_id = ? AND id != ?", (we["workout_id"], we["id"]))]
    pool = [e for e in _pool(conn, pid, s) if e["id"] != we["exercise_id"]]
    fresh = [e for e in pool if e["id"] not in avoid]
    rec = current_recovery(conn, pid)
    hist = exercise_history(conn, pid, we["workout_id"])
    pins = pinned_ids(conn, pid)
    rng = rng or random.Random()
    w = conn.execute("SELECT pattern FROM workouts WHERE id = ?", (we["workout_id"],)).fetchone()
    pattern_muscles = generator.PATTERNS.get(w["pattern"] if w else None, ("", []))[1]
    primary = list(old["primary"]) if old else []
    targets = primary + [m for m in pattern_muscles if m not in primary]
    replacement = None
    for candidates in (fresh, pool):
        for target in targets:
            replacement = generator.choose_exercise(candidates, target, others, rec, hist, pins, rng, core_slot=False)
            if replacement:
                break
        if replacement:
            break
    if not replacement:
        return None
    conn.execute("DELETE FROM workout_exercises WHERE id = ?", (we["id"],))
    _add_exercise(conn, pid, s, we["workout_id"], replacement, we["position"])
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def exclude_and_replace(conn: sqlite3.Connection, pid: int, we_id: int, rng: random.Random | None = None) -> None:
    """Add the exercise to this profile's never-suggest list; replace it in the workout if it has no sets yet."""
    we = owned_we(conn, pid, we_id)
    if not we:
        return
    conn.execute("INSERT OR IGNORE INTO excluded(profile_id, exercise_id) VALUES (?, ?)", (pid, we["exercise_id"]))
    conn.execute("DELETE FROM pinned WHERE profile_id = ? AND exercise_id = ?", (pid, we["exercise_id"]))
    if _has_sets(conn, we_id):
        return
    if _replace(conn, pid, we, skipped_ids(conn, we["workout_id"]), rng) is None:
        conn.execute("DELETE FROM workout_exercises WHERE id = ?", (we_id,))


def swap_exercise(conn: sqlite3.Connection, pid: int, we_id: int, rng: random.Random | None = None) -> int | None:
    """Replace one exercise in today's workout (no sets logged on it yet) with the next-best one for
    the same muscle. Swapped-away exercises don't come back in this workout while others fit.
    Returns the new workout-exercise id, or None if nothing changed."""
    we = owned_we(conn, pid, we_id)
    if not we or _has_sets(conn, we_id) or not _is_open(conn, we["workout_id"]):
        return None
    _skip(conn, we["workout_id"], we["exercise_id"])
    return _replace(conn, pid, we, skipped_ids(conn, we["workout_id"]), rng)


def remove_exercise(conn: sqlite3.Connection, pid: int, we_id: int) -> bool:
    """Drop an exercise from today's workout only (no sets logged on it, and never the last one)."""
    we = owned_we(conn, pid, we_id)
    if not we or _has_sets(conn, we_id) or not _is_open(conn, we["workout_id"]):
        return False
    count = conn.execute("SELECT COUNT(*) FROM workout_exercises WHERE workout_id = ?",
                         (we["workout_id"],)).fetchone()[0]
    if count <= 1:
        return False
    _skip(conn, we["workout_id"], we["exercise_id"])
    conn.execute("DELETE FROM workout_exercises WHERE id = ?", (we_id,))
    return True


def new_exercises(conn: sqlite3.Connection, pid: int, workout_id: int, rng: random.Random | None = None) -> bool:
    """Rebuild a not-yet-started workout: same type of day, avoiding every exercise already shown in it."""
    w = owned_workout(conn, pid, workout_id)
    if not w or w["finished_at"] or workout_has_sets(conn, workout_id):
        return False
    for r in conn.execute("SELECT exercise_id FROM workout_exercises WHERE workout_id = ?", (workout_id,)).fetchall():
        _skip(conn, workout_id, r["exercise_id"])
    conn.execute("DELETE FROM workout_exercises WHERE workout_id = ?", (workout_id,))
    s = settings(conn, pid)
    plan = generator.generate(_pool(conn, pid, s), current_recovery(conn, pid),
                              exercise_history(conn, pid, workout_id), pinned_ids(conn, pid),
                              int(s["n_exercises"]), rng, last_pattern(conn, pid),
                              pattern=w["pattern"], avoid=skipped_ids(conn, workout_id))
    for i, e in enumerate(plan.exercises):
        _add_exercise(conn, pid, s, workout_id, e, i)
    return True


def _is_open(conn: sqlite3.Connection, workout_id: int) -> bool:
    row = conn.execute("SELECT finished_at FROM workouts WHERE id = ?", (workout_id,)).fetchone()
    return bool(row) and row["finished_at"] is None


def toggle_pin(conn: sqlite3.Connection, pid: int, exercise_id: str) -> bool:
    """Pin or unpin an exercise as one of this profile's core lifts. Returns the new state."""
    if conn.execute("SELECT 1 FROM pinned WHERE profile_id = ? AND exercise_id = ?", (pid, exercise_id)).fetchone():
        conn.execute("DELETE FROM pinned WHERE profile_id = ? AND exercise_id = ?", (pid, exercise_id))
        return False
    conn.execute("INSERT INTO pinned(profile_id, exercise_id) VALUES (?, ?)", (pid, exercise_id))
    return True


def workout_detail(conn: sqlite3.Connection, pid: int, workout_id: int) -> list[dict]:
    w = owned_workout(conn, pid, workout_id)
    if not w:
        return []
    cap = generator.GOALS[w["goal"]].reps + generator.BODYWEIGHT_REP_CAP
    pins = pinned_ids(conn, pid)
    items = []
    for we in conn.execute(
            "SELECT * FROM workout_exercises WHERE workout_id = ? ORDER BY position, id", (workout_id,)):
        sets = conn.execute(
            "SELECT * FROM sets WHERE workout_exercise_id = ? ORDER BY id", (we["id"],)).fetchall()
        ex = catalog.get(we["exercise_id"])
        prev = last_session_sets(conn, pid, we["exercise_id"], workout_id)
        unweighted_prev = [r for r, wt in prev if not wt]
        items.append({
            "we": we,
            "exercise": ex,
            "sets": sets,
            "prev": prev,
            "pinned": we["exercise_id"] in pins,
            "photos": photos.for_exercise(ex),
            # bodyweight move maxed out at the rep cap last time: time to add load
            "add_weight": bool(is_unloaded(ex) and unweighted_prev and we["target_reps"] >= cap
                               and min(unweighted_prev) >= cap),
        })
    return items


def active_exercise_id(items: list[dict]) -> int | None:
    """The exercise to show expanded: the first one started but unfinished, else the first not started."""
    for it in items:
        if 0 < len(it["sets"]) < it["we"]["target_sets"]:
            return it["we"]["id"]
    for it in items:
        if not it["sets"]:
            return it["we"]["id"]
    return None


def last_set_time(conn: sqlite3.Connection, workout_id: int) -> str | None:
    row = conn.execute(
        "SELECT MAX(s.logged_at) AS t FROM sets s JOIN workout_exercises we ON we.id = s.workout_exercise_id "
        "WHERE we.workout_id = ?", (workout_id,)).fetchone()
    return row["t"] if row else None
