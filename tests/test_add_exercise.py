"""Adding an exercise to today's workout: Suggest one, and the Choose-an-exercise picker."""
import re

import pytest
from fastapi.testclient import TestClient

from app import catalog, generator

GYM_FORM = {
    "goal": "hypertrophy", "n_exercises": "4", "units": "lb",
    "barbell_enabled": "on", "barbell_bar": "45", "ez_bar_bar": "25",
    "plates": "45x4, 25x2, 10x2, 5x2, 2.5x2",
    "dumbbell_enabled": "on", "dumbbell_weights": "5-50/5",
    "kettlebell_weights": "", "cable_weights": "",
    "station_bench": "on", "station_rack": "on",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_DB", str(tmp_path / "test.db"))
    from app.main import app
    c = TestClient(app)
    c.post("/profiles", data={"name": "Tester", "goal": "hypertrophy", "n_exercises": "4"})
    c.post("/settings", data=GYM_FORM)
    return c


def cards(html):
    return [(int(i), n) for i, n in re.findall(r'id="ex-(\d+)" data-exercise="([^"]+)"', html)]


def by_name(name):
    return next(e for e in catalog.EXERCISES if e["name"] == name)


def day_muscles(html):
    label = re.search(r"<h1>([^<]+) day</h1>", html).group(1)
    return next(m for lbl, m in generator.PATTERNS.values() if lbl == label)


def picker_ids(html):
    return re.findall(r'name="exercise_id" value="([^"]+)"', html)


def test_suggest_adds_an_on_theme_exercise_at_the_end_even_mid_workout(client):
    html = client.post("/workout/new").text
    before = cards(html)
    client.post(f"/we/{before[0][0]}/set", data={"reps": "10", "weight": "95"})
    html = client.post("/workout/1/suggest").text
    after = cards(html)
    assert after[:-1] == before and len(after) == len(before) + 1
    new_id, new_name = after[-1]
    assert new_name not in [n for _, n in before]
    assert set(by_name(new_name)["primary"]) & set(day_muscles(html))
    assert re.search(r'id="ex-%d"[^>]*\bopen\b' % new_id, html)
    assert "Best fit for" in html and "Choose myself" in html


def test_picker_lists_todays_muscles_and_marks_whats_in_the_workout(client):
    html = client.post("/workout/new").text
    names = [n for _, n in cards(html)]
    page = client.get("/workout/1/add").text
    assert "Today's muscles" in page and "Other muscles" in page
    muscle = re.search(r'class="mchip" href="[^"]+" aria-current="true">([^<]+)<', page).group(1).lower()
    assert muscle in day_muscles(html)
    ids = picker_ids(page)
    assert ids and all(muscle in catalog.get(i)["primary"] for i in ids)
    in_workout = [n for n in names if muscle in by_name(n)["primary"]]
    for n in in_workout:
        assert by_name(n)["id"] not in ids and "already in today" in page


def test_picker_search_and_other_muscles(client):
    client.post("/workout/new")
    page = client.get("/workout/1/add", params={"q": "curl"}).text
    ids = picker_ids(page)
    assert ids and all("curl" in catalog.get(i)["name"].lower() for i in ids)
    page = client.get("/workout/1/add", params={"muscle": "biceps"}).text
    assert all("biceps" in catalog.get(i)["primary"] for i in picker_ids(page))


def test_add_chosen_appends_and_opens_it(client):
    before = cards(client.post("/workout/new").text)
    choice = picker_ids(client.get("/workout/1/add", params={"muscle": "biceps"}).text)[0]
    html = client.post("/workout/1/add", data={"exercise_id": choice}).text
    after = cards(html)
    assert after[:-1] == before and after[-1][1] == catalog.get(choice)["name"]
    assert re.search(r'id="ex-%d"[^>]*\bopen\b' % after[-1][0], html)
    assert cards(client.post("/workout/1/add", data={"exercise_id": choice}).text) == after  # no duplicates


def test_add_rejects_what_the_gym_cant_do_or_is_excluded(client):
    before = cards(client.post("/workout/new").text)
    cable = next(e["id"] for e in catalog.EXERCISES if e["equipment"] == "cable")
    assert cards(client.post("/workout/1/add", data={"exercise_id": cable}).text) == before
    assert cards(client.post("/workout/1/add", data={"exercise_id": "not-a-real-id"}).text) == before
    choice = picker_ids(client.get("/workout/1/add", params={"muscle": "biceps"}).text)[0]
    we_id = cards(client.post("/workout/1/add", data={"exercise_id": choice}).text)[-1][0]
    client.post(f"/we/{we_id}/exclude")
    assert choice not in picker_ids(client.get("/workout/1/add", params={"muscle": "biceps"}).text)
    assert catalog.get(choice)["name"] not in [n for _, n in cards(
        client.post("/workout/1/add", data={"exercise_id": choice}).text)]


def test_cannot_add_to_someone_elses_or_a_finished_workout(client):
    client.post("/workout/new")
    choice = picker_ids(client.get("/workout/1/add", params={"muscle": "biceps"}).text)[0]
    client.post("/profiles", data={"name": "Other", "goal": "strength", "n_exercises": "3"})
    assert client.get("/workout/1/add", follow_redirects=False).status_code == 303
    client.post("/workout/1/add", data={"exercise_id": choice})
    client.post("/workout/1/suggest")
    client.post("/profiles/1/use")
    html = client.get("/").text
    assert len(cards(html)) == 4
    we_id = cards(html)[0][0]
    client.post(f"/we/{we_id}/set", data={"reps": "10", "weight": "95"})
    client.post("/workout/1/finish")
    assert client.get("/workout/1/add", follow_redirects=False).status_code == 303
    from app import db
    count = lambda: db.connect().execute("SELECT COUNT(*) FROM workout_exercises WHERE workout_id = 1").fetchone()[0]
    n = count()
    client.post("/workout/1/suggest")
    client.post("/workout/1/add", data={"exercise_id": choice})
    assert count() == n == 4
