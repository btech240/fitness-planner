"""Home-gym inventory: which equipment exists and which exact weights can be loaded.

The inventory is a plain dict (stored as JSON in settings):

    {
      "barbell":   {"enabled": True, "bar": 45},
      "ez_bar":    {"enabled": False, "bar": 25},
      "plates":    "45x2, 25x2, 10x2, 5x2, 2.5x2",   # total plates owned, shared by barbell + EZ bar
      "dumbbell":  {"enabled": True, "weights": "5-50/5"},
      "kettlebell":{"enabled": False, "weights": "35, 53"},
      "cable":     {"enabled": False, "weights": "10-200/10"},
      "bands":     {"enabled": False},
      "stations":  ["bench", "rack", "pullup_bar"],
    }
"""

from __future__ import annotations

import re

# kind -> (label, dataset equipment tag, how weight is specified)
EQUIPMENT_KINDS = {
    "barbell": ("Barbell", "barbell", "plates"),
    "ez_bar": ("EZ curl bar", "e-z curl bar", "plates"),
    "dumbbell": ("Dumbbells (weight of one)", "dumbbell", "list"),
    "kettlebell": ("Kettlebells", "kettlebells", "list"),
    "cable": ("Cable stack", "cable", "list"),
    "bands": ("Resistance bands", "bands", "none"),
}

PULLEYS = {"high": "High", "low": "Low", "mid": "Mid (chest height)"}

STATIONS = {
    "bench": "Adjustable bench",
    "rack": "Squat rack / stands",
    "pullup_bar": "Pull-up bar",
    "dip_station": "Dip station",
}

DEFAULT_INVENTORY = {
    "barbell": {"enabled": False, "bar": 45},
    "ez_bar": {"enabled": False, "bar": 25},
    "plates": "45x2, 25x2, 10x2, 5x2, 2.5x2",
    "dumbbell": {"enabled": False, "weights": "5-50/5"},
    "kettlebell": {"enabled": False, "weights": ""},
    "cable": {"enabled": False, "weights": "10-150/10", "pulleys": ["high", "low", "mid"]},
    "bands": {"enabled": False},
    "stations": [],
}


class WeightSyntaxError(ValueError):
    pass


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        raise WeightSyntaxError(f"not a number: {s!r}") from None


_RANGE = re.compile(r"^([\d.]+)\s*-\s*([\d.]+)\s*(?:/|by|step)\s*([\d.]+)$", re.I)


def parse_weight_list(text: str) -> list[float]:
    """Parse '5, 10, 15' or '5-50/5' (or a mix, comma-separated) into sorted weights."""
    out: set[float] = set()
    for item in (p.strip() for p in (text or "").split(",")):
        if not item:
            continue
        m = _RANGE.match(item)
        if m:
            lo, hi, step = (_num(g) for g in m.groups())
            if step <= 0 or hi < lo:
                raise WeightSyntaxError(f"bad range: {item!r}")
            n = int(round((hi - lo) / step))
            out.update(round(lo + i * step, 2) for i in range(n + 1))
        else:
            out.add(_num(item))
    return sorted(out)


def parse_plates(text: str) -> dict[float, int]:
    """Parse '45x2, 25x4, 10' into {plate weight: total count}. A bare weight means a pair."""
    plates: dict[float, int] = {}
    for item in (p.strip() for p in (text or "").split(",")):
        if not item:
            continue
        w, _, c = item.lower().partition("x")
        count = int(_num(c)) if c.strip() else 2
        plates[_num(w.strip())] = plates.get(_num(w.strip()), 0) + count
    return plates


def plate_loaded_weights(bar: float, plates: dict[float, int]) -> list[float]:
    """Every total reachable by loading the same plates on both sides of the bar."""
    per_side = {0.0}
    for w, count in plates.items():
        pairs = count // 2
        per_side = {round(s + w * k, 2) for s in per_side for k in range(pairs + 1)}
    return sorted(round(bar + 2 * s, 2) for s in per_side)


def loadable_weights(inventory: dict, kind: str) -> list[float] | None:
    """Weights loadable for a kind; None means the kind has no load (bands, bodyweight)."""
    spec = EQUIPMENT_KINDS[kind][2]
    cfg = inventory.get(kind) or {}
    if spec == "plates":
        return plate_loaded_weights(float(cfg.get("bar") or 0), parse_plates(inventory.get("plates", "")))
    if spec == "list":
        return parse_weight_list(cfg.get("weights", ""))
    return None


def available_equipment_tags(inventory: dict) -> set[str]:
    tags = {"body only"}
    for kind, (_, tag, _) in EQUIPMENT_KINDS.items():
        if (inventory.get(kind) or {}).get("enabled"):
            tags.add(tag)
    return tags


def available_features(inventory: dict) -> set[str]:
    """Station and pulley-position tags an exercise's `requires` list is checked against."""
    have = set(inventory.get("stations") or [])
    cable = inventory.get("cable") or {}
    if cable.get("enabled"):
        have |= {f"pulley_{p}" for p in cable.get("pulleys", PULLEYS)}
    return have


def kind_for_tag(tag: str) -> str | None:
    for kind, (_, t, _) in EQUIPMENT_KINDS.items():
        if t == tag:
            return kind
    return None


def nearest_loadable(target: float, options: list[float]) -> float:
    """Closest loadable weight to target; ties go to the lighter weight."""
    return min(options, key=lambda w: (abs(w - target), w))


def merged(inventory: dict | None) -> dict:
    """Inventory with defaults filled in for any missing keys."""
    inv = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_INVENTORY.items()}
    for k, v in (inventory or {}).items():
        if isinstance(v, dict) and isinstance(inv.get(k), dict):
            inv[k].update(v)
        else:
            inv[k] = v
    return inv
