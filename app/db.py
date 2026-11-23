"""SQLite storage. One file, path from FP_DB (default ./data/fitness.db).

Schema version 2 (household profiles): workouts, pins and exclusions belong to a
profile; the equipment list (settings key "inventory") is shared by the house.
Upgrading a version-1 database starts everyone fresh: it is backed up beside the
database file first, then everything but the equipment list is cleared.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL,
    goal        TEXT NOT NULL DEFAULT 'hypertrophy',
    n_exercises INTEGER NOT NULL DEFAULT 5,
    units       TEXT NOT NULL DEFAULT 'lb',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    goal        TEXT NOT NULL,
    pattern     TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS workout_exercises (
    id               INTEGER PRIMARY KEY,
    workout_id       INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise_id      TEXT NOT NULL,
    position         INTEGER NOT NULL,
    target_sets      INTEGER NOT NULL,
    target_reps      INTEGER NOT NULL,
    suggested_weight REAL
);
CREATE TABLE IF NOT EXISTS sets (
    id                  INTEGER PRIMARY KEY,
    workout_exercise_id INTEGER NOT NULL REFERENCES workout_exercises(id) ON DELETE CASCADE,
    reps                INTEGER NOT NULL,
    weight              REAL,
    logged_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS excluded (
    profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    exercise_id TEXT NOT NULL,
    PRIMARY KEY (profile_id, exercise_id)
);
CREATE TABLE IF NOT EXISTS pinned (
    profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    exercise_id TEXT NOT NULL,
    PRIMARY KEY (profile_id, exercise_id)
);
CREATE INDEX IF NOT EXISTS idx_workouts_profile ON workouts(profile_id);
CREATE INDEX IF NOT EXISTS idx_sets_logged ON sets(logged_at);
CREATE INDEX IF NOT EXISTS idx_we_exercise ON workout_exercises(exercise_id);
CREATE INDEX IF NOT EXISTS idx_we_workout ON workout_exercises(workout_id);
"""

# Tables and settings keys that were personal in version 1 and are cleared on upgrade.
_V1_TABLES = ("sets", "workout_exercises", "workouts", "excluded", "pinned")
_V1_PERSONAL_KEYS = ("goal", "n_exercises", "units")


def db_path() -> str:
    return os.environ.get("FP_DB", str(Path("data") / "fitness.db"))


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        _upgrade(conn, path)
    return conn


def _upgrade(conn: sqlite3.Connection, path: str) -> None:
    """Bring any older database to SCHEMA_VERSION. A version-1 database is backed up, then
    everything except the shared equipment list is cleared (owner's choice: start everyone fresh)."""
    had_data = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workouts'").fetchone() is not None
    if had_data and path != ":memory:":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = Path(path).with_name(f"{Path(path).stem}.pre-profiles-{stamp}.db")
        dest = sqlite3.connect(backup_path)
        try:
            conn.backup(dest)
        finally:
            dest.close()
    for table in _V1_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'").fetchone():
        conn.execute(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(_V1_PERSONAL_KEYS))})",
                     _V1_PERSONAL_KEYS)
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
