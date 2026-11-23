"""Operations that combine the database with the pure planning logic."""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone

from . import catalog, db, generator, inventory as inv
from .recovery import DECAY_HOURS, LoggedSet, recovery

DEFAULT_SETTINGS = {"goal": "hypertrophy", "n_exercises": 5, "units": "lb"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---- settings -------------------------------------------------------------

def settings(conn: sqlite3.Connection) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for k in DEFAULT_SETTINGS:
        out[k] = db.get_setting(conn, k, out[k])
    try:  # self-heal bad stored values (an earlier bug saved this as a float like 1.0)
        out["n_exercises"] = min(10, max(2, int(out["n_exercises"])))
    except (TypeError, ValueError):
        out["n_exercises"] = DEFAULT_SETTINGS["n_exercises"]
    if out["goal"] not in generator.GOALS:
        out["goal"] = DEFAULT_SETTINGS["goal"]
    out["inventory"] = inv.merged(db.get_setting(conn, "inventory"))
    return out


def excluded_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["exercise_id"] for r in conn.execute("SELECT exercise_id FROM excluded")}


# ---- recovery & history ----------------------------------------------------

def recent_sets(conn: sqlite3.Connection, at: datetime) -> list[LoggedSet]:
    since = (at - timedelta(hours=DECAY_HOURS)).isoformat()
    rows = conn.execute(
        "SELECT s.logged_at, we.exercise_id FROM sets s "
        "JOIN workout_exercises we ON we.id = s.workout_exercise_id WHERE s.logged_at >= ?",
        (since,),
    )
    out = []
    for r in rows:
        e = catalog.get(r["exercise_id"])
        out.append(LoggedSet(parse_ts(r["logged_at"]), e["primary"], e["secondary"]))
    return out


def current_recovery(conn: sqlite3.Connection, at: datetime | None = None) -> dict[str, int]:
    at = at or now()
    return recovery(recent_sets(conn, at), at)


def exercise_history(conn: sqlite3.Connection, before_workout: int | None = None,
                     limit: int = 30) -> dict[str, generator.ExerciseHistory]:
    """Per exercise: in how many of the last `limit` logged workouts it appears, and how recently."""
    workouts = [r["workout_id"] for r in conn.execute(
        "SELECT DISTINCT we.workout_id FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id "
        "WHERE (? IS NULL OR we.workout_id < ?) ORDER BY we.workout_id DESC LIMIT ?",
        (before_workout, before_workout, limit))]
    hist: dict[str, generator.ExerciseHistory] = {}
    for ago, wid in enumerate(workouts):
        for r in conn.execute(
                "SELECT DISTINCT we.exercise_id FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id "
                "WHERE we.workout_id = ?", (wid,)):
            h = hist.setdefault(r["exercise_id"], generator.ExerciseHistory())
            h.sessions += 1
            h.last_ago = min(h.last_ago, ago)
    return hist


def pinned_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["exercise_id"] for r in conn.execute("SELECT exercise_id FROM pinned")}


def last_pattern(conn: sqlite3.Connection, before_workout: int | None = None) -> str | None:
    """Pattern of the most recent workout that has logged sets."""
    row = conn.execute(
        "SELECT pattern FROM workouts WHERE pattern IS NOT NULL AND (? IS NULL OR id < ?) "
        "AND EXISTS (SELECT 1 FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id "
        "WHERE we.workout_id = workouts.id) ORDER BY id DESC LIMIT 1",
        (before_workout, before_workout)).fetchone()
    return row["pattern"] if row else None


def last_session_sets(conn: sqlite3.Connection, exercise_id: str, before_workout: int | None = None):
    """(reps, weight) of the most recent earlier workout where this exercise was logged."""
    row = conn.execute(
        "SELECT we.id FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id "
        "WHERE we.exercise_id = ? AND (? IS NULL OR we.workout_id < ?) "
        "ORDER BY s.logged_at DESC LIMIT 1",
        (exercise_id, before_workout, before_workout),
    ).fetchone()
    if not row:
        return []
    return [(r["reps"], r["weight"]) for r in conn.execute(
        "SELECT reps, weight FROM sets WHERE workout_exercise_id = ? ORDER BY id", (row["id"],))]


# ---- workouts ----------------------------------------------------------------

def current_workout(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM workouts WHERE finished_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()


def _pool(conn: sqlite3.Connection, s: dict) -> list[dict]:
    return generator.available_exercises(catalog.EXERCISES, s["inventory"], excluded_ids(conn))


def is_unloaded(exercise: dict) -> bool:
    return exercise["equipment"] in ("body only", "bands")


def _targets(conn, s: dict, exercise: dict, workout_id: int | None) -> tuple[int, float | None]:
    """(target reps, suggested weight) for an exercise in a new workout."""
    goal = generator.GOALS[s["goal"]]
    last = last_session_sets(conn, exercise["id"], workout_id)
    if is_unloaded(exercise) and not any(w for _, w in last):
        return generator.suggest_bodyweight_reps(last, goal.reps), None
    kind = inv.kind_for_tag(exercise["equipment"])
    options = inv.loadable_weights(s["inventory"], kind) if kind else None
    return goal.reps, generator.suggest_weight(last, goal.reps, options)


def _add_exercise(conn, s: dict, workout_id: int, exercise: dict, position: int) -> None:
    goal = generator.GOALS[s["goal"]]
    reps, weight = _targets(conn, s, exercise, workout_id)
    conn.execute(
        "INSERT INTO workout_exercises(workout_id, exercise_id, position, target_sets, target_reps, suggested_weight) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (workout_id, exercise["id"], position, goal.sets, reps, weight),
    )


def create_workout(conn: sqlite3.Connection, rng: random.Random | None = None) -> int:
    s = settings(conn)
    plan = generator.generate(_pool(conn, s), current_recovery(conn), exercise_history(conn),
                              pinned_ids(conn), int(s["n_exercises"]), rng, last_pattern(conn))
    cur = conn.execute("INSERT INTO workouts(created_at, goal, pattern) VALUES (?, ?, ?)",
                       (now().isoformat(), s["goal"], plan.pattern))
    wid = cur.lastrowid
    for i, e in enumerate(plan.exercises):
        _add_exercise(conn, s, wid, e, i)
    return wid


def workout_has_sets(conn: sqlite3.Connection, workout_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM sets s JOIN workout_exercises we ON we.id = s.workout_exercise_id "
        "WHERE we.workout_id = ? LIMIT 1", (workout_id,)).fetchone() is not None


def exclude_and_replace(conn: sqlite3.Connection, we_id: int, rng: random.Random | None = None) -> None:
    """Add the exercise to the never-suggest list; replace it in the workout if it has no sets yet."""
    we = conn.execute("SELECT * FROM workout_exercises WHERE id = ?", (we_id,)).fetchone()
    if not we:
        return
    conn.execute("INSERT OR IGNORE INTO excluded(exercise_id) VALUES (?)", (we["exercise_id"],))
    conn.execute("DELETE FROM pinned WHERE exercise_id = ?", (we["exercise_id"],))
    if conn.execute("SELECT 1 FROM sets WHERE workout_exercise_id = ?", (we_id,)).fetchone():
        return
    s = settings(conn)
    old = catalog.get(we["exercise_id"])
    others = [catalog.get(r["exercise_id"]) for r in conn.execute(
        "SELECT exercise_id FROM workout_exercises WHERE workout_id = ? AND id != ?", (we["workout_id"], we_id))]
    conn.execute("DELETE FROM workout_exercises WHERE id = ?", (we_id,))
    pool = _pool(conn, s)
    rec = current_recovery(conn)
    hist = exercise_history(conn, we["workout_id"])
    pins = pinned_ids(conn)
    w = conn.execute("SELECT pattern FROM workouts WHERE id = ?", (we["workout_id"],)).fetchone()
    pattern_muscles = generator.PATTERNS.get(w["pattern"] if w else None, ("", []))[1]
    replacement = None
    for target in list(old["primary"]) + [m for m in pattern_muscles if m not in old["primary"]]:
        replacement = generator.choose_exercise(pool, target, others, rec, hist, pins,
                                                rng or random.Random(), core_slot=False)
        if replacement:
            break
    if replacement:
        _add_exercise(conn, s, we["workout_id"], replacement, we["position"])


def toggle_pin(conn: sqlite3.Connection, exercise_id: str) -> bool:
    """Pin or unpin an exercise as a core lift. Returns the new state."""
    if conn.execute("SELECT 1 FROM pinned WHERE exercise_id = ?", (exercise_id,)).fetchone():
        conn.execute("DELETE FROM pinned WHERE exercise_id = ?", (exercise_id,))
        return False
    conn.execute("INSERT INTO pinned(exercise_id) VALUES (?)", (exercise_id,))
    return True


def workout_detail(conn: sqlite3.Connection, workout_id: int) -> list[dict]:
    w = conn.execute("SELECT goal FROM workouts WHERE id = ?", (workout_id,)).fetchone()
    cap = generator.GOALS[w["goal"]].reps + generator.BODYWEIGHT_REP_CAP if w else None
    pins = pinned_ids(conn)
    items = []
    for we in conn.execute(
            "SELECT * FROM workout_exercises WHERE workout_id = ? ORDER BY position, id", (workout_id,)):
        sets = conn.execute(
            "SELECT * FROM sets WHERE workout_exercise_id = ? ORDER BY id", (we["id"],)).fetchall()
        ex = catalog.get(we["exercise_id"])
        prev = last_session_sets(conn, we["exercise_id"], workout_id)
        unweighted_prev = [r for r, wt in prev if not wt]
        items.append({
            "we": we,
            "exercise": ex,
            "sets": sets,
            "prev": prev,
            "pinned": we["exercise_id"] in pins,
            # bodyweight move maxed out at the rep cap last time: time to add load
            "add_weight": bool(is_unloaded(ex) and cap and unweighted_prev and we["target_reps"] >= cap
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
