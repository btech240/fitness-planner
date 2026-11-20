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


def test_bad_weights_are_rejected(client):
    form = dict(GYM_FORM, dumbbell_weights="lots")
    r = client.post("/settings", data=form)
    assert "nothing was saved" in r.text
