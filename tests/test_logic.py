import random
from datetime import datetime, timedelta, timezone

import pytest

from app import catalog, generator, inventory as inv
from app.recovery import LoggedSet, recovery

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)


# ---- inventory --------------------------------------------------------------

def test_weight_list_parses_ranges_and_values():
    assert inv.parse_weight_list("5-20/5, 25, 52.5") == [5, 10, 15, 20, 25, 52.5]
    assert inv.parse_weight_list("") == []


def test_weight_list_rejects_garbage():
    with pytest.raises(inv.WeightSyntaxError):
        inv.parse_weight_list("heavy")


def test_plate_math_only_loads_symmetric_pairs():
    plates = inv.parse_plates("45x2, 10x2, 5x1")  # single 5 can't be used
    assert inv.plate_loaded_weights(45, plates) == [45, 65, 135, 155]


def test_nearest_loadable_prefers_lighter_on_tie():
    assert inv.nearest_loadable(50, [45, 55]) == 45
    assert inv.nearest_loadable(53, [45, 55]) == 55


# ---- recovery ------------------------------------------------------------------

def sets(n, hours_ago, primary, secondary=()):
    return [LoggedSet(NOW - timedelta(hours=hours_ago), list(primary), list(secondary))] * n


def test_fresh_muscles_are_fully_recovered():
    assert recovery([], NOW)["chest"] == 100


def test_recovery_drops_then_returns_over_three_days():
    assert recovery(sets(10, 0, ["chest"]), NOW)["chest"] == 0
    assert recovery(sets(10, 36, ["chest"]), NOW)["chest"] == 50
    assert recovery(sets(10, 72, ["chest"]), NOW)["chest"] == 100


def test_secondary_muscles_get_half_fatigue():
    r = recovery(sets(10, 0, ["chest"], ["triceps"]), NOW)
    assert r["triceps"] == 50


# ---- generation ---------------------------------------------------------------------

FULL_GYM = inv.merged({
    "barbell": {"enabled": True, "bar": 45},
    "dumbbell": {"enabled": True, "weights": "5-50/5"},
    "stations": ["bench", "rack", "pullup_bar"],
})


def test_pool_respects_equipment_stations_and_exclusions():
    bodyweight_only = generator.available_exercises(catalog.EXERCISES, inv.merged({}), set())
    assert bodyweight_only and all(e["equipment"] == "body only" and not e["requires"] for e in bodyweight_only)

    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, {"Pullups"})
    ids = {e["id"] for e in pool}
    assert "Pullups" not in ids
    assert all(e["equipment"] in {"barbell", "dumbbell", "body only"} for e in pool)
    assert all(set(e["requires"]) <= {"bench", "rack", "pullup_bar"} for e in pool)


def test_enabled_equipment_without_weights_is_ignored():
    gym = inv.merged({"dumbbell": {"enabled": True, "weights": ""}})
    pool = generator.available_exercises(catalog.EXERCISES, gym, set())
    assert all(e["equipment"] != "dumbbell" for e in pool)


def test_generate_avoids_fatigued_muscles():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    rec = {m: 100 for m in generator.TARGET_MUSCLES}
    rec.update(chest=10, triceps=20, shoulders=30)
    for seed in range(20):
        chosen = generator.generate(pool, rec, set(), 5, random.Random(seed))
        assert len(chosen) == 5
        assert len({e["id"] for e in chosen}) == 5
        for e in chosen:
            assert not {"chest", "triceps", "shoulders"} & set(e["primary"]), e["name"]


def test_generate_varies_between_days():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    rec = {m: 100 for m in generator.TARGET_MUSCLES}
    picks = {tuple(e["id"] for e in generator.generate(pool, rec, set(), 5, random.Random(s))) for s in range(10)}
    assert len(picks) > 3


# ---- weight suggestions ---------------------------------------------------------------

OPTIONS = inv.plate_loaded_weights(45, inv.parse_plates("45x4, 25x2, 10x2, 5x2, 2.5x2"))


def test_no_history_means_no_suggestion():
    assert generator.suggest_weight([], 10, OPTIONS) is None
    assert generator.suggest_weight([(10, None)], 10, OPTIONS) is None


def test_hitting_all_reps_progresses():
    s = generator.suggest_weight([(10, 135), (10, 135), (10, 135)], 10, OPTIONS)
    assert s in OPTIONS and 135 < s <= 145


def test_coarse_dumbbell_steps_still_progress():
    dumbbells = inv.parse_weight_list("5-50/5")
    assert generator.suggest_weight([(10, 40)] * 3, 10, dumbbells) == 45   # +12.5%
    assert generator.suggest_weight([(10, 10)] * 3, 10, dumbbells) == 10   # +50% is too big a jump


def test_missing_reps_holds_or_drops():
    s = generator.suggest_weight([(10, 135), (8, 135), (6, 135)], 10, OPTIONS)
    assert s <= 135


def test_goal_change_converts_weight():
    s = generator.suggest_weight([(10, 135)] * 3, 5, OPTIONS)  # hypertrophy -> strength
    assert 150 <= s <= 165
