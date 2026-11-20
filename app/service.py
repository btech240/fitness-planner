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


def familiar_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["exercise_id"] for r in conn.execute(
        "SELECT DISTINCT we.exercise_id FROM workout_exercises we JOIN sets s ON s.workout_exercise_id = we.id")}


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


def _suggest(conn, s: dict, exercise: dict, target_reps: int, workout_id: int | None) -> float | None:
    kind = inv.kind_for_tag(exercise["equipment"])
    options = inv.loadable_weights(s["inventory"], kind) if kind else None
    return generator.suggest_weight(last_session_sets(conn, exercise["id"], workout_id), target_reps, options)


def _add_exercise(conn, s: dict, workout_id: int, exercise: dict, position: int) -> None:
    goal = generator.GOALS[s["goal"]]
    conn.execute(
        "INSERT INTO workout_exercises(workout_id, exercise_id, position, target_sets, target_reps, suggested_weight) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (workout_id, exercise["id"], position, goal.sets, goal.reps,
         _suggest(conn, s, exercise, goal.reps, workout_id)),
    )


def create_workout(conn: sqlite3.Connection, rng: random.Random | None = None) -> int:
    s = settings(conn)
    chosen = generator.generate(_pool(conn, s), current_recovery(conn), familiar_ids(conn),
                                int(s["n_exercises"]), rng)
    cur = conn.execute("INSERT INTO workouts(created_at, goal) VALUES (?, ?)", (now().isoformat(), s["goal"]))
    wid = cur.lastrowid
    for i, e in enumerate(chosen):
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
    if conn.execute("SELECT 1 FROM sets WHERE workout_exercise_id = ?", (we_id,)).fetchone():
        return
    s = settings(conn)
    old = catalog.get(we["exercise_id"])
    others = [catalog.get(r["exercise_id"]) for r in conn.execute(
        "SELECT exercise_id FROM workout_exercises WHERE workout_id = ? AND id != ?", (we["workout_id"], we_id))]
    conn.execute("DELETE FROM workout_exercises WHERE id = ?", (we_id,))
    pool = _pool(conn, s)
    rec = current_recovery(conn)
    replacement = None
    for target in old["primary"]:
        replacement = generator.choose_exercise(pool, target, others, rec, familiar_ids(conn), rng or random.Random())
        if replacement:
            break
    if replacement:
        _add_exercise(conn, s, we["workout_id"], replacement, we["position"])


def workout_detail(conn: sqlite3.Connection, workout_id: int) -> list[dict]:
    items = []
    for we in conn.execute(
            "SELECT * FROM workout_exercises WHERE workout_id = ? ORDER BY position, id", (workout_id,)):
        sets = conn.execute(
            "SELECT * FROM sets WHERE workout_exercise_id = ? ORDER BY id", (we["id"],)).fetchall()
        items.append({
            "we": we,
            "exercise": catalog.get(we["exercise_id"]),
            "sets": sets,
            "prev": last_session_sets(conn, we["exercise_id"], workout_id),
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
