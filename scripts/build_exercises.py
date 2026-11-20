"""Build app/data/exercises.json from the free-exercise-db dataset.

Source: https://github.com/yuhonas/free-exercise-db (The Unlicense / public domain).

Usage:
    curl -L -o exercises.raw.json https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json
    python scripts/build_exercises.py exercises.raw.json app/data/exercises.json

Keeps strength exercises whose equipment the app can model, and adds a
`requires` list of stations (bench, rack, pull-up bar, dip station) inferred
from the exercise name, since the dataset does not tag those.
"""

import json
import re
import sys

KEEP_CATEGORIES = {"strength", "powerlifting"}
KEEP_EQUIPMENT = {"barbell", "dumbbell", "cable", "body only", "kettlebells", "bands", "e-z curl bar"}
# Accommodating-resistance and specialty variants need gear the inventory doesn't model.
NEEDS_UNMODELED_GEAR = re.compile(r"chains|plate movers|with bands|reverse band|\bband\b", re.I)


def infer_requires(name: str, equipment: str) -> list[str]:
    n = name.lower()
    req = []
    if re.search(r"\b(bench|incline|decline)\b", n):
        req.append("bench")
    if equipment == "barbell" and re.search(r"\b(squats?|bench press|rack pulls?)\b", n):
        req.append("rack")
    if re.search(r"\b(pull-?ups?|pullups?|chin-?ups?|hanging)\b", n):
        req.append("pullup_bar")
    if re.search(r"\bdips?\b", n) and "bench dip" not in n:
        req.append("dip_station")
    return req


def main(src: str, dst: str) -> None:
    with open(src, encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for x in raw:
        if x.get("category") not in KEEP_CATEGORIES or x.get("equipment") not in KEEP_EQUIPMENT:
            continue
        if not x.get("primaryMuscles"):
            continue
        if NEEDS_UNMODELED_GEAR.search(x["name"]) and x["equipment"] != "bands":
            continue
        out.append({
            "id": x["id"],
            "name": x["name"],
            "level": x.get("level"),
            "mechanic": x.get("mechanic"),
            "equipment": x["equipment"],
            "primary": x["primaryMuscles"],
            "secondary": x.get("secondaryMuscles") or [],
            "instructions": x.get("instructions") or [],
            "requires": infer_requires(x["name"], x["equipment"]),
        })
    out.sort(key=lambda e: e["name"])
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=0, ensure_ascii=False)
    print(f"wrote {len(out)} exercises to {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
