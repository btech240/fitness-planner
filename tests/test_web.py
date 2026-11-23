import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_DB", str(tmp_path / "test.db"))
    from app.main import app
    return TestClient(app)


GYM_FORM = {
    "goal": "hypertrophy", "n_exercises": "4", "units": "lb",
    "barbell_enabled": "on", "barbell_bar": "45", "ez_bar_bar": "25",
    "plates": "45x4, 25x2, 10x2, 5x2, 2.5x2",
    "dumbbell_enabled": "on", "dumbbell_weights": "5-50/5",
    "kettlebell_weights": "", "cable_weights": "",
    "station_bench": "on", "station_rack": "on",
}


def we_ids(html):
    return [int(x) for x in re.findall(r'action="/we/(\d+)/set"', html)]


def test_full_flow(client):
    assert client.get("/").status_code == 200

    r = client.post("/settings", data=GYM_FORM)
    assert r.status_code == 200 and "Saved." in r.text

    r = client.post("/workout/new")
    ids = we_ids(r.text)
    assert len(ids) == 4

    # regenerate allowed before logging
    r = client.post(f"/workout/1/regenerate")
    ids = we_ids(r.text)
    assert len(ids) == 4

    r = client.post(f"/we/{ids[0]}/set", data={"reps": "10", "weight": "95"})
    logged = re.search(r'<tr class="logged">(.*?)</tr>', r.text, re.S).group(1)
    assert re.findall(r"<td>([^<]+)</td>", logged) == ["10", "95"]
    assert 'id="rest"' in r.text
    assert "Regenerate" not in r.text

    wid = re.search(r'action="/workout/(\d+)/finish"', r.text).group(1)
    assert wid == "2"  # ids are not reused after a regenerate deletes workout 1
    r = client.post(f"/workout/{wid}/finish")
    assert "10×95" in r.text  # history page

    r = client.get("/recovery")
    assert r.status_code == 200 and "%" in r.text


def test_never_suggest_replaces_and_lists(client):
    client.post("/settings", data=GYM_FORM)
    r = client.post("/workout/new")
    first = we_ids(r.text)[0]
    name = re.search(r'id="ex-%d" data-exercise="([^"]+)"' % first, r.text).group(1)
    r = client.post(f"/we/{first}/exclude")
    assert len(we_ids(r.text)) == 4
    assert f'data-exercise="{name}"' not in r.text
    assert name in client.get("/settings").text


def test_pattern_label_and_pinning(client):
    client.post("/settings", data=GYM_FORM)
    r = client.post("/workout/new")
    assert re.search(r"<h1>(Push|Pull|Legs|Upper|Lower|Full body) day</h1>", r.text)
    first = we_ids(r.text)[0]
    name = re.search(r'id="ex-%d" data-exercise="([^"]+)"' % first, r.text).group(1)
    r = client.post(f"/we/{first}/pin")
    assert "Unpin core lift" in r.text
    assert name in client.get("/settings").text.split('id="pinned"')[1].split('id="excluded"')[0]


def test_cable_pulleys_saved(client):
    form = dict(GYM_FORM, cable_enabled="on", cable_weights="10-100/10", cable_pulley_high="on", cable_pulley_low="on")
    client.post("/settings", data=form)
    html = client.get("/settings").text
    assert 'name="cable_pulley_high" checked' in html
    assert 'name="cable_pulley_mid" checked' not in html


PHONE_FORM = {
    "goal": "hypertrophy", "n_exercises": "4", "units": "lb",
    "barbell_enabled": "on", "barbell_bar": "45", "ez_bar_bar": "25",
    "plate_45": "2", "plate_25": "1", "plate_10": "", "plate_2.5": "1",
    "dumbbell_enabled": "on", "dumbbell_from": "5", "dumbbell_to": "52.5", "dumbbell_step": "2.5",
    "kettlebell_enabled": "on", "kettlebell_w": ["35", "53"],
    "cable_from": "", "cable_to": "", "cable_step": "",
    "station_bench": "on",
}


def test_numbers_only_equipment_inputs_save(client):
    r = client.post("/settings", data=PHONE_FORM, follow_redirects=False)
    assert r.status_code == 303
    from app import db, service
    s = service.settings(db.connect())
    inv_ = s["inventory"]
    assert inv_["dumbbell"]["weights"] == "5-52.5/2.5"
    assert inv_["kettlebell"]["weights"] == "35, 53"
    assert inv_["plates"] == "45x4, 25x2, 2.5x2"
    assert inv_["cable"]["weights"] == ""
    html = client.get("/settings").text
    assert 'name="dumbbell_to" type="number" inputmode="decimal" step="any" min="0" value="52.5"' in html
    assert 'name="plate_45" type="number" inputmode="numeric" min="0" step="1" value="2"' in html
    assert 'value="53" checked' in html


def test_exercise_count_survives_plate_inputs(client):
    # regression: the plates loop reused the variable holding n_exercises, saving the last plate's pairs instead
    client.post("/settings", data=dict(PHONE_FORM, n_exercises="6"))
    from app import db, service
    assert service.settings(db.connect())["n_exercises"] == 6
    assert len(we_ids(client.post("/workout/new").text)) == 6


def test_bad_stored_exercise_count_heals(client):
    from app import db, service
    conn = db.connect()
    with conn:
        db.set_setting(conn, "n_exercises", 1.0)
    assert service.settings(conn)["n_exercises"] == 2


def test_range_errors_keep_what_was_typed(client):
    r = client.post("/settings", data=dict(PHONE_FORM, dumbbell_from="50", dumbbell_to="5"))
    assert "nothing was saved" in r.text
    assert "To must be at least From" in r.text
    assert 'name="dumbbell_from" type="number" inputmode="decimal" step="any" min="0" value="50"' in r.text
    r = client.post("/settings", data=dict(PHONE_FORM, dumbbell_step=""))
    assert "Enter a step" in r.text


def test_bad_weights_are_rejected(client):
    form = dict(GYM_FORM, dumbbell_weights="lots")
    r = client.post("/settings", data=form)
    assert "nothing was saved" in r.text
