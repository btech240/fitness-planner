"""SQLite storage. One file, path from FP_DB (default ./data/fitness.db)."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    goal        TEXT NOT NULL,
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
    exercise_id TEXT PRIMARY KEY
);
CREATE INDEX IF NOT EXISTS idx_sets_logged ON sets(logged_at);
CREATE INDEX IF NOT EXISTS idx_we_exercise ON workout_exercises(exercise_id);
"""


def db_path() -> str:
    return os.environ.get("FP_DB", str(Path("data") / "fitness.db"))


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
