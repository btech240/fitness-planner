"""Start/end position photos for exercises (free-exercise-db, public domain).

Photos are downloaded once into a folder next to the database (see fetch_photos.py)
and served at /photos/<exercise id>_<0|1>.jpg. Nothing is fetched at request time.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import db

SOURCE_URL = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/{id}/{n}.jpg"

# Photo sets the owner reviewed as not helpful (photo check, 2026-11-23): How to shows text only.
HIDDEN = {"Barbell_Hip_Thrust", "Dips_-_Triceps_Version"}

FILENAME_RX = re.compile(r"^[A-Za-z0-9_\-]+_[01]\.jpg$")
LABELS = ("Start", "End")


def photos_dir() -> Path:
    """FP_PHOTOS, else a `photos` folder beside the database file."""
    return Path(os.environ.get("FP_PHOTOS") or Path(db.db_path()).parent / "photos")


def filename(exercise_id: str, n: int) -> str:
    return f"{exercise_id}_{n}.jpg"


def for_exercise(exercise: dict) -> list[dict]:
    """[{url, label}] for the photos of this exercise that are downloaded and not hidden."""
    if exercise["id"] in HIDDEN or not exercise.get("photos"):
        return []
    folder = photos_dir()
    out = []
    for n in range(min(2, exercise["photos"])):
        name = filename(exercise["id"], n)
        if (folder / name).is_file():
            out.append({"url": f"/photos/{name}", "label": LABELS[n]})
    return out
