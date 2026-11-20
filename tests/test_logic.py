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


FRESH = {m: 100 for m in generator.TARGET_MUSCLES}
H = generator.ExerciseHistory


def gen(pool, rec=None, history=None, pins=frozenset(), n=5, seed=0, last=None):
    return generator.generate(pool, rec or FRESH, history or {}, set(pins), n, random.Random(seed), last)


def test_generate_avoids_fatigued_muscles():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    rec = dict(FRESH, chest=10, triceps=20, shoulders=30)
    for seed in range(20):
        plan = gen(pool, rec, seed=seed)
        assert plan.pattern != "push"
        assert len(plan.exercises) == 5
        for e in plan.exercises:
            assert not {"chest", "triceps", "shoulders"} & set(e["primary"]), e["name"]


def test_day_follows_a_pattern():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    for seed in range(30):
        plan = gen(pool, seed=seed)
        allowed = set(generator.PATTERNS[plan.pattern][1])
        assert all(set(e["primary"]) & allowed for e in plan.exercises), (plan.pattern, [e["name"] for e in plan.exercises])


def test_pattern_does_not_repeat_when_everything_is_fresh():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    for seed in range(20):
        assert gen(pool, seed=seed, last="push").pattern != "push"


def test_tired_muscles_pick_the_other_pattern():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    legs_done = dict(FRESH, quadriceps=20, hamstrings=25, glutes=30)
    assert all(gen(pool, legs_done, seed=s).pattern in ("push", "pull", "upper") for s in range(20))


def test_no_near_duplicates_in_one_workout():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    for seed in range(40):
        plan = gen(pool, seed=seed, n=8)
        names = [e["name"] for e in plan.exercises]
        fams = [generator.families(n) for n in names]
        for i in range(len(fams)):
            for j in range(i + 1, len(fams)):
                assert not fams[i] & fams[j], names


def test_core_lift_leads_and_alternates():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    rng = random.Random(0)
    first = generator.choose_exercise(pool, "chest", [], FRESH, {}, set(), rng, core_slot=True)
    assert first["id"] == "Barbell_Bench_Press_-_Medium_Grip"
    hist = {"Barbell_Bench_Press_-_Medium_Grip": H(sessions=3, last_ago=0),
            "Dumbbell_Bench_Press": H(sessions=3, last_ago=2)}
    second = generator.choose_exercise(pool, "chest", [], FRESH, hist, set(), rng, core_slot=True)
    assert second["id"] == "Dumbbell_Bench_Press"


def test_rotation_slot_skips_last_sessions_exercise():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    chosen = [catalog.get("Barbell_Bench_Press_-_Medium_Grip")]
    for seed in range(15):
        hist = {"Incline_Dumbbell_Press": H(sessions=1, last_ago=0)}
        e = generator.choose_exercise(pool, "chest", chosen, FRESH, hist, set(), random.Random(seed), core_slot=False)
        assert e["id"] != "Incline_Dumbbell_Press"


def test_pinned_lift_always_included_for_its_muscle():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    for seed in range(15):
        e = generator.choose_exercise(pool, "chest", [], FRESH, {}, {"Dumbbell_Flyes"}, random.Random(seed), True)
        assert e["id"] == "Dumbbell_Flyes"


def test_bodyweight_filler_avoided_when_weights_exist():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    for seed in range(30):
        for e in gen(pool, seed=seed).exercises:
            assert e["equipment"] != "body only" or e["id"] in generator.BODYWEIGHT_KEEPERS, e["name"]


def test_cable_pulleys_filter_exercises():
    gym = inv.merged({"cable": {"enabled": True, "weights": "10-100/10", "pulleys": ["high", "low"]}})
    ids = {e["id"] for e in generator.available_exercises(catalog.EXERCISES, gym, set())}
    assert "Triceps_Pushdown" in ids and "Seated_Cable_Rows" in ids
    assert "Pallof_Press" not in ids and "Standing_Cable_Chest_Press" not in ids


def test_generate_varies_between_days():
    pool = generator.available_exercises(catalog.EXERCISES, FULL_GYM, set())
    picks = {tuple(e["id"] for e in gen(pool, seed=s).exercises) for s in range(10)}
    assert len(picks) > 3


def test_bodyweight_reps_progress_and_cap():
    assert generator.suggest_bodyweight_reps([], 10) == 10
    assert generator.suggest_bodyweight_reps([(8, None), (7, None), (6, None)], 10) == 8
    assert generator.suggest_bodyweight_reps([(15, None)] * 3, 10) == 15


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
