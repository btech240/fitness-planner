"""The exercise catalog (built by scripts/build_exercises.py from free-exercise-db)."""

import json
from pathlib import Path

_PATH = Path(__file__).parent / "data" / "exercises.json"

with _PATH.open(encoding="utf-8") as _f:
    EXERCISES: list[dict] = json.load(_f)

BY_ID: dict[str, dict] = {e["id"]: e for e in EXERCISES}


def get(exercise_id: str) -> dict:
    """Catalog entry, or a placeholder if the catalog no longer has this id."""
    return BY_ID.get(exercise_id) or {
        "id": exercise_id, "name": exercise_id.replace("_", " "), "level": None, "mechanic": None,
        "equipment": "body only", "primary": [], "secondary": [], "instructions": [], "requires": [],
    }
