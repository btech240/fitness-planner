"""Build app/data/exercises.json from the free-exercise-db dataset.

Source: https://github.com/yuhonas/free-exercise-db (The Unlicense / public domain).

Usage:
    curl -L -o exercises.raw.json https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json
    python scripts/build_exercises.py exercises.raw.json app/data/exercises.json

Keeps strength exercises whose equipment the app can model, plus a hand-tagged
list (EXTRA) of machine, small-gear, Olympic and plyometric exercises. Each gets a
`requires` list (stations, machines, gear, pulley heights; inferred from the name
for the core set), and `add_only` marks exercises offered only through Add exercise.
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


# ---- exercises outside the core strength set, tagged by hand -----------------------------
# The dataset files these under "machine", "other", "olympic weightlifting", "plyometrics"
# etc., which says too little about the gear they need. Each is listed explicitly:
#   M:key  machine (weights in machine steps), needs machine `key`
#   S      Smith machine (+ bench when the name says bench/incline/decline)
#   G:key  unloaded gear `key` (reps progress like bodyweight)
#   B      bodyweight            B:key  bodyweight needing station `key`
#   P      a single weight plate P:key  plate + station `key`
#   T      trap bar              D:key  dumbbell + station `key`
#   BB     barbell (stations inferred from the name)     BD:key  bands + station `key`
#   DB / BN  dumbbell / bands, nothing else needed
# A trailing "+" marks add-only: offered in Add exercise, never picked automatically.
_EXTRA = """
Ab Crunch Machine=M:ab_machine | Butterfly=M:pec_deck | Reverse Machine Flyes=M:pec_deck |
Calf-Machine Shoulder Shrug=M:calf_machine | Calf Press=M:calf_machine | Seated Calf Raise=M:calf_machine |
Standing Calf Raises=M:calf_machine | Calf Press On The Leg Press Machine=M:leg_press | Leg Press=M:leg_press |
Narrow Stance Leg Press=M:leg_press | Hack Squat=M:leg_press | Narrow Stance Hack Squats=M:leg_press |
Lying Machine Squat=M:leg_press | Leg Extensions=M:leg_ext_curl | Single-Leg Leg Extension=M:leg_ext_curl |
Lying Leg Curls=M:leg_ext_curl | Seated Leg Curl=M:leg_ext_curl | Standing Leg Curl=M:leg_ext_curl |
Thigh Abductor=M:hip_machine | Thigh Adductor=M:hip_machine | Machine Bench Press=M:press_machine |
Machine Shoulder (Military) Press=M:press_machine | Machine Bicep Curl=M:arm_machine |
Machine Preacher Curls=M:arm_machine | Machine Triceps Extension=M:arm_machine | Dip Machine=M:dip_machine |
Leverage Chest Press=M:lever | Leverage Deadlift=M:lever | Leverage Decline Chest Press=M:lever |
Leverage High Row=M:lever | Leverage Incline Chest Press=M:lever | Leverage Iso Row=M:lever |
Leverage Shoulder Press=M:lever | Leverage Shrug=M:lever | Lying T-Bar Row=M:lever |
Reverse Hyperextension=M:reverse_hyper |
Chair Squat=S | Lunge Sprint=S+ | Decline Smith Press=S | Smith Machine Behind the Back Shrug=S |
Smith Machine Bench Press=S | Smith Machine Bent Over Row=S | Smith Machine Calf Raise=S |
Smith Machine Close-Grip Bench Press=S | Smith Machine Decline Press=S | Smith Machine Hang Power Clean=S+ |
Smith Machine Hip Raise=S | Smith Machine Incline Bench Press=S | Smith Machine Leg Press=S |
Smith Machine One-Arm Upright Row=S | Smith Machine Overhead Shoulder Press=S | Smith Machine Pistol Squat=S |
Smith Machine Reverse Calf Raises=S | Smith Machine Squat=S | Smith Machine Stiff-Legged Deadlift=S |
Smith Machine Upright Row=S | Smith Single-Leg Split Squat=S |
Glute Ham Raise=B:ghd | Hyperextensions (Back Extensions)=B:ghd | Single-Leg High Box Squat=B:plyo_box |
Dips - Chest Version=B:dip_station | Parallel Bar Dip=B:dip_station | Knee/Hip Raise On Parallel Bars=B:dip_station |
Gironda Sternum Chins=B:pullup_bar | Mixed Grip Chin=B:pullup_bar | Side To Side Chins=B:pullup_bar |
Rocky Pull-Ups/Pulldowns=B:pullup_bar | Scapular Pull-Up=B:pullup_bar | Bodyweight Mid Row=B:pullup_bar |
Muscle Up=B:pullup_bar+ | Kipping Muscle Up=B:pullup_bar+ | One Arm Chin-Up=B:pullup_bar+ |
Inverted Row=B:rack | Bodyweight Walking Lunge=B | Decline Push-Up=B:bench | Floor Glute-Ham Raise=B |
Platform Hamstring Slides=B | Band Assisted Pull-Up=BD:pullup_bar | Seated Band Hamstring Curl=BN |
Weighted Pull Ups=D:pullup_bar | Trap Bar Deadlift=T |
Front Plate Raise=P | Plate Pinch=P | Plate Twist=P | Svend Press=P | Reverse Plate Curls=P | Otis-Up=P |
Standing Olympic Plate Hand Squeeze=P | Lying Face Down Plate Neck Resistance=P |
Lying Face Up Plate Neck Resistance=P | Weighted Bench Dip=P:bench |
Ab Roller=G:ab_wheel | Wrist Roller=G:wrist_roller | Battling Ropes=G:battle_ropes+ |
Ball Leg Curl=G:stability_ball | Downward Facing Balance=G:stability_ball | Exercise Ball Crunch=G:stability_ball |
Exercise Ball Pull-In=G:stability_ball | Physioball Hip Bridge=G:stability_ball |
Push-Ups With Feet On An Exercise Ball=G:stability_ball | Weighted Ball Hyperextension=G:stability_ball |
Weighted Ball Side Bend=G:stability_ball | Weighted Crunches=G:medicine_ball | One-Arm Medicine Ball Slam=G:medicine_ball+ |
Inverted Row with Straps=G:suspension | Ring Dips=G:suspension | Suspended Fallout=G:suspension |
Suspended Push-Up=G:suspension | Suspended Reverse Crunch=G:suspension | Suspended Row=G:suspension |
Suspended Split Squat=G:suspension |
Sled Overhead Backward Walk=G:sled+ | Sled Overhead Triceps Extension=G:sled+ | Sled Reverse Flye=G:sled+ |
Sled Row=G:sled+ | Sled Push=G:sled+ | Sled Drag - Harness=G:sled+ | Backward Drag=G:sled+ |
Bear Crawl Sled Drags=G:sled+ | Forward Drag with Press=G:sled+ | Sandbag Load=G:sandbag+ | Farmer's Walk=DB+ |
Clean=BB+ | Clean Deadlift=BB+ | Clean Pull=BB+ | Clean Shrug=BB+ | Clean and Jerk=BB+ | Frankenstein Squat=BB+ |
Hang Clean=BB+ | Hang Clean - Below the Knees=BB+ | Hang Snatch=BB+ | Hang Snatch - Below Knees=BB+ |
Heaving Snatch Balance=BB+ | Jerk Balance=BB+ | Jerk Dip Squat=BB+ | Kneeling Jump Squat=BB+ | Muscle Snatch=BB+ |
Olympic Squat=BB+ | Overhead Squat=BB+ | Power Jerk=BB+ | Power Snatch=BB+ | Push Press=BB+ |
Push Press - Behind the Neck=BB+ | Rack Delivery=BB+ | Romanian Deadlift from Deficit=BB+ | Snatch=BB+ |
Snatch Balance=BB+ | Snatch Deadlift=BB+ | Snatch Shrug=BB+ | Split Clean=BB+ | Split Jerk=BB+ | Split Snatch=BB+ |
Wide Stance Stiff Legs=BB+ |
Alternate Leg Diagonal Bound=B+ | Bench Jump=B:bench+ | Bench Sprint=B:bench+ | Double Leg Butt Kick=B+ |
Fast Skipping=B+ | Isometric Chest Squeezes=B+ | Knee Tuck Jump=B+ | Lateral Bound=B+ | Mountain Climbers=B+ |
Plyo Push-up=B+ | Rocket Jump=B+ | Scissors Jump=B+ | Side Standing Long Jump=B+ | Split Jump=B+ |
Standing Long Jump=B+ | Star Jump=B+ | Single Leg Butt Kick=B+ | Single-Leg Hop Progression=B+ |
Single-Leg Lateral Hop=B+ | Single-Leg Stride Jump=B+ | Stride Jump Crossover=B+ | Quick Leap=B+ |
Box Jump (Multiple Response)=B:plyo_box+ | Box Skip=B:plyo_box+ | Front Box Jump=B:plyo_box+ |
Lateral Box Jump=B:plyo_box+ | Side to Side Box Shuffle=B:plyo_box+ | Depth Jump Leap=B:plyo_box+ |
Linear Depth Jump=B:plyo_box+ | Incline Push-Up Depth Jump=B:plyo_box+ | Drop Push=B:plyo_box+ |
Dumbbell Seated Box Jump=D:plyo_box+ | Vertical Swing=DB+ |
Backward Medicine Ball Throw=G:medicine_ball+ | Catch and Overhead Throw=G:medicine_ball+ |
Chest Push (multiple response)=G:medicine_ball+ | Chest Push (single response)=G:medicine_ball+ |
Medicine Ball Chest Pass=G:medicine_ball+ | Medicine Ball Full Twist=G:medicine_ball+ |
Medicine Ball Scoop Throw=G:medicine_ball+ | Overhead Slam=G:medicine_ball+ |
Standing Two-Arm Overhead Throw=G:medicine_ball+ | Supine Chest Throw=G:medicine_ball+ |
Supine One-Arm Overhead Throw=G:medicine_ball+ | Supine Two-Arm Overhead Throw=G:medicine_ball+
"""
EXTRA = dict(item.strip().split("=") for item in _EXTRA.replace("\n", " ").split("|") if item.strip())

_SIMPLE = {"B": "body only", "G": "gear", "M": "machine", "P": "plates", "D": "dumbbell", "BD": "bands",
           "DB": "dumbbell", "BN": "bands", "T": "trap bar"}


def extra_spec(name: str, spec: str) -> tuple[str, list[str], bool]:
    """(equipment, requires, add_only) for a hand-tagged exercise."""
    add_only = spec.endswith("+")
    code, _, key = spec.rstrip("+").partition(":")
    if code == "S":
        return "machine", ["smith"] + (["bench"] if re.search(r"bench|incline|decline", name, re.I) else []), add_only
    if code == "BB":
        return "barbell", infer_requires(name, "barbell"), add_only
    return _SIMPLE[code], [key] if key else [], add_only


def _entry(x: dict, equipment: str, requires: list[str], add_only: bool) -> dict:
    e = {
        "id": x["id"],
        "name": x["name"],
        "level": x.get("level"),
        "mechanic": x.get("mechanic"),
        "equipment": equipment,
        "primary": x["primaryMuscles"],
        "secondary": x.get("secondaryMuscles") or [],
        "instructions": x.get("instructions") or [],
        "requires": requires,
        # start/end position photos in the source repo at exercises/<id>/<n>.jpg
        "photos": len(x.get("images") or []),
    }
    if add_only:
        e["add_only"] = True   # offered in Add exercise, never picked automatically
    return e


def main(src: str, dst: str) -> None:
    with open(src, encoding="utf-8") as f:
        raw = json.load(f)
    unknown = sorted(set(EXTRA) - {x["name"] for x in raw})
    if unknown:
        raise SystemExit(f"hand-tagged names not in the dataset: {unknown}")
    out = []
    for x in raw:
        if not x.get("primaryMuscles"):
            continue
        if x["name"] in EXTRA:
            out.append(_entry(x, *extra_spec(x["name"], EXTRA[x["name"]])))
            continue
        if x.get("category") not in KEEP_CATEGORIES or x.get("equipment") not in KEEP_EQUIPMENT:
            continue
        if NEEDS_UNMODELED_GEAR.search(x["name"]) and x["equipment"] != "bands":
            continue
        if x["equipment"] == "cable" and x["name"] not in PULLEY:
            print(f"skipping cable exercise with unknown pulley height: {x['name']}")
            continue
        out.append(_entry(x, x["equipment"], infer_requires(x["name"], x["equipment"]), False))
    out.sort(key=lambda e: e["name"])
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=0, ensure_ascii=False)
    print(f"wrote {len(out)} exercises to {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
