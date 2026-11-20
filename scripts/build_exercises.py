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


# Pulley height each cable exercise needs. Listed explicitly: names are too
# inconsistent to guess reliably. A cable exercise missing here is dropped.
_HIGH = """Cable Crossover|Cable Crunch|Cable Incline Pushdown|Cable Iron Cross|Cable One Arm Tricep Extension|
Cable Rope Rear-Delt Rows|Cable Seated Crunch|Close-Grip Front Lat Pulldown|Face Pull|Full Range-Of-Motion Lat Pulldown|
High Cable Curls|Kneeling Cable Crunch With Alternating Oblique Twists|Kneeling Cable Triceps Extension|
Kneeling High Pulley Row|Kneeling Single-Arm High Pulley Row|Lying Close-Grip Bar Curl On High Pulley|One Arm Lat Pulldown|
One-Arm High-Pulley Cable Side Bends|Overhead Cable Curl|Reverse Grip Triceps Pushdown|Rope Crunch|
Rope Straight-Arm Pulldown|Single-Arm Cable Crossover|Standing Cable Wood Chop|Standing Rope Crunch|Straight-Arm Pulldown|
Triceps Pushdown|Triceps Pushdown - Rope Attachment|Triceps Pushdown - V-Bar Attachment|Underhand Cable Pulldowns|
V-Bar Pulldown|Wide-Grip Lat Pulldown|Wide-Grip Pulldown Behind The Neck|Bosu Ball Cable Crunch With Side Bends"""
_LOW = """Alternating Cable Shoulder Press|Bent Over Low-Pulley Side Lateral|Cable Deadlifts|Cable Hammer Curls - Rope Attachment|
Cable Hip Adduction|Cable Incline Triceps Extension|Cable Lying Triceps Extension|Cable Preacher Curl|Cable Reverse Crunch|
Cable Rope Overhead Triceps Extension|Cable Seated Lateral Raise|Cable Shoulder Press|Cable Shrugs|Cable Wrist Curl|
Elevated Cable Rows|Flat Bench Cable Flyes|Front Cable Raise|Incline Cable Chest Press|Incline Cable Flye|
Low Cable Crossover|Low Cable Triceps Extension|Low Pulley Row To Neck|Lying Cable Curl|One-Legged Cable Kickback|
Pull Through|Reverse Cable Curl|Seated Cable Rows|Seated Cable Shoulder Press|Seated One-arm Cable Pulley Rows|
Seated Two-Arm Palms-Up Low-Pulley Wrist Curl|Shotgun Row|Standing Biceps Cable Curl|Standing Cable Lift|
Standing Low-Pulley Deltoid Raise|Standing Low-Pulley One-Arm Triceps Extension|Standing One-Arm Cable Curl|
Triceps Overhead Extension with Rope|Upright Cable Row"""
_MID = """Cable Chest Press|Cable Internal Rotation|Cable Judo Flip|Cable Rear Delt Fly|Cable Russian Twists|
External Rotation with Cable|Pallof Press|Pallof Press With Rotation|Standing Cable Chest Press"""
PULLEY = {n.strip(): h for h, names in (("high", _HIGH), ("low", _LOW), ("mid", _MID))
          for n in names.replace("\n", "").split("|")}


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
    if equipment == "cable":
        req.append("pulley_" + PULLEY[name])
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
        if x["equipment"] == "cable" and x["name"] not in PULLEY:
            print(f"skipping cable exercise with unknown pulley height: {x['name']}")
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
