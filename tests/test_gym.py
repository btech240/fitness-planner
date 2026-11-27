"""The Gym tab, the extra equipment (machines, gear, trap bar, plates) and add-only exercises."""
import random
import re

import pytest
from fastapi.testclient import TestClient

from app import catalog, generator, inventory as inv

BASE = {"barbell_enabled": "on", "barbell_bar": "45", "plate_45": "2", "plate_25": "2", "plate_10": "2",
        "plate_5": "2", "station_rack": "on", "station_bench": "on"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_DB", str(tmp_path / "test.db"))
    from app.main import app
    c = TestClient(app)
    c.post("/profiles", data={"name": "Tester", "goal": "hypertrophy", "n_exercises": "5"})
    return c


def by_name(name):
    return next(e for e in catalog.EXERCISES if e["name"] == name)


def stored_inventory():
    from app import db, service
    return service.settings(db.connect(), 1)["inventory"]


def test_gym_is_its_own_tab_and_settings_is_personal(client):
    page = client.get("/gym").text
    assert 'href="/gym" aria-current="page"' in page
    for section in ("Bars &amp; weights", "Stations", "Machines", "Other gear", "Trap / hex bar"):
        assert section in page
    settings = client.get("/settings").text
    assert 'name="barbell_enabled"' not in settings and 'href="/gym"' in settings
    client.post("/gym", data=BASE)
    client.post("/settings", data={"goal": "strength", "n_exercises": "4", "units": "lb"})
    assert stored_inventory()["barbell"]["enabled"]                 # personal save left the gym alone


def test_machines_and_gear_are_saved_and_unlock_exercises(client):
    client.post("/gym", data=dict(BASE, machine_smith="on", machine_leg_press="on", gear_ab_wheel="on",
                                  station_ghd="on"))
    inv_ = stored_inventory()
    assert inv_["machines"] == ["smith", "leg_press"] and inv_["gear"] == ["ab_wheel"] and "ghd" in inv_["stations"]
    page = client.get("/gym").text
    assert 'name="machine_smith" checked' in page and 'name="gear_ab_wheel" checked' in page
    pool = {e["name"] for e in generator.available_exercises(catalog.EXERCISES, inv_, set())}
    assert {"Smith Machine Squat", "Leg Press", "Ab Roller", "Glute Ham Raise", "Front Plate Raise"} <= pool
    assert not {"Leg Extensions", "Butterfly", "Suspended Row"} & pool        # not ticked


def test_nothing_new_appears_without_new_gear():
    gym = inv.merged({"barbell": {"enabled": True, "bar": 45}, "stations": ["rack", "bench"]})
    pool = generator.available_exercises(catalog.EXERCISES, gym, set())
    assert not any(e["equipment"] in ("machine", "gear", "trap bar") for e in pool)
    old_style = {"barbell": {"enabled": True, "bar": 45}, "stations": ["rack"]}   # saved before machines existed
    assert generator.available_exercises(catalog.EXERCISES, inv.merged(old_style), set())


def test_weight_suggestions_for_machines_trap_bar_and_plates():
    gym = inv.merged({"trap_bar": {"enabled": True, "bar": 55}, "plates": "45x2, 25x2", "machines": ["smith"]})
    assert inv.load_options(gym, "machine", "lb")[:3] == [5, 10, 15]
    assert inv.load_options(gym, "machine", "kg")[:3] == [2.5, 5, 7.5]
    assert inv.load_options(gym, "trap bar")[:3] == [55, 105, 145]
    assert inv.load_options(gym, "plates") == [25, 45]
    assert inv.load_options(gym, "gear") is None
    assert generator.suggest_weight([(10, 100)], 10, inv.load_options(gym, "machine", "lb")) == 105


def test_add_only_exercises_are_never_picked_but_can_be_added(client):
    client.post("/gym", data=dict(BASE, station_pullup_bar="on", station_plyo_box="on", gear_medicine_ball="on"))
    gym = stored_inventory()
    pool = generator.available_exercises(catalog.EXERCISES, gym, set())
    assert not any(e.get("add_only") for e in pool)
    for seed in range(30):
        plan = generator.generate(pool, {}, {}, set(), 6, random.Random(seed))
        assert not any(e.get("add_only") for e in plan.exercises)
    full = generator.available_exercises(catalog.EXERCISES, gym, set(), include_add_only=True)
    assert {"Power Snatch", "Push Press", "Front Box Jump", "Overhead Slam"} <= {e["name"] for e in full}

    client.post("/workout/new")
    page = client.get("/workout/1/add", params={"q": "snatch"}).text
    snatch = by_name("Power Snatch")["id"]
    assert f'value="{snatch}"' in page
    html = client.post("/workout/1/add", data={"exercise_id": snatch}).text
    assert 'data-exercise="Power Snatch"' in html


def test_picker_names_the_gear(client):
    client.post("/gym", data=dict(BASE, machine_leg_ext_curl="on", gear_stability_ball="on"))
    client.post("/workout/new")
    page = client.get("/workout/1/add", params={"q": "leg curl"}).text
    assert "Leg extension / leg curl ·" in page and "Exercise (stability) ball ·" in page


def test_every_exercise_tag_is_something_the_gym_page_offers():
    known = set(inv.STATIONS) | set(inv.MACHINES) | set(inv.GEAR) | {f"pulley_{p}" for p in inv.PULLEYS}
    tags = {tag for _, tag, _ in inv.EQUIPMENT_KINDS.values()} | {"body only", "gear", "machine", "plates"}
    for e in catalog.EXERCISES:
        assert set(e["requires"]) <= known, e["name"]
        assert e["equipment"] in tags, e["name"]
        if e["equipment"] in ("machine", "gear"):
            assert set(e["requires"]) & (set(inv.MACHINES) | set(inv.GEAR)), e["name"]
