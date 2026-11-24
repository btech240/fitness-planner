"""Swap, Remove and New exercises on today's workout, plus the v2 -> v3 database upgrade."""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import catalog

GYM_FORM = {
    "goal": "hypertrophy", "n_exercises": "5", "units": "lb",
    "barbell_enabled": "on", "barbell_bar": "45", "ez_bar_bar": "25",
    "plates": "45x4, 25x2, 10x2, 5x2, 2.5x2",
    "dumbbell_enabled": "on", "dumbbell_weights": "5-50/5",
    "kettlebell_weights": "", "cable_enabled": "on", "cable_weights": "10-100/10",
    "cable_pulley_high": "on", "cable_pulley_low": "on",
    "station_bench": "on", "station_rack": "on", "station_pullup_bar": "on", "station_dip_station": "on",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_DB", str(tmp_path / "test.db"))
    from app.main import app
    c = TestClient(app)
    c.post("/profiles", data={"name": "Tester", "goal": "hypertrophy", "n_exercises": "5"})
    c.post("/settings", data=GYM_FORM)
    return c


def cards(html):
    """[(workout-exercise id, exercise name)] in page order."""
    return [(int(i), n) for i, n in re.findall(r'id="ex-(\d+)" data-exercise="([^"]+)"', html)]


def primary_of(name):
    return next(e["primary"] for e in catalog.EXERCISES if e["name"] == name)


def test_swap_replaces_one_exercise_for_the_same_muscle(client):
    before = cards(client.post("/workout/new").text)
    we_id, name = before[1]
    r = client.post(f"/we/{we_id}/swap")
    after = cards(r.text)
    assert len(after) == len(before)
    assert name not in [n for _, n in after]
    new_id, new_name = after[1]                            # same position
    assert set(primary_of(new_name)) & set(primary_of(name))
    assert re.search(r'<details class="ex[^"]*" id="ex-%d"[^>]*\bopen\b' % new_id, r.text)  # swapped-in card is open
    assert name not in client.get("/settings").text.split('id="excluded"')[1]  # not added to Never suggest


def test_repeated_swaps_never_bring_an_exercise_back(client):
    we_id, name = cards(client.post("/workout/new").text)[0]
    seen = [name]
    for _ in range(4):
        html = client.post(f"/we/{we_id}/swap").text
        we_id, name = cards(html)[0]
        assert name not in seen
        seen.append(name)


def test_remove_drops_one_and_keeps_the_rest(client):
    before = cards(client.post("/workout/new").text)
    r = client.post(f"/we/{before[2][0]}/remove")
    after = cards(r.text)
    assert [n for _, n in after] == [n for _, n in before if n != before[2][1]]


def test_cannot_remove_the_last_exercise(client):
    html = client.post("/workout/new").text
    while len(cards(html)) > 1:
        html = client.post(f"/we/{cards(html)[0][0]}/remove").text
    (we_id, name), = cards(html)
    assert f"/we/{we_id}/remove" not in html
    assert cards(client.post(f"/we/{we_id}/remove").text) == [(we_id, name)]


def test_swap_and_remove_lock_once_a_set_is_logged(client):
    we_id, name = cards(client.post("/workout/new").text)[0]
    html = client.post(f"/we/{we_id}/set", data={"reps": "10", "weight": "95"}).text
    assert f"/we/{we_id}/swap" not in html and f"/we/{we_id}/remove" not in html
    assert (we_id, name) in cards(client.post(f"/we/{we_id}/swap").text)
    assert (we_id, name) in cards(client.post(f"/we/{we_id}/remove").text)


def test_new_exercises_keeps_the_day_and_changes_every_exercise(client):
    html = client.post("/workout/new").text
    day = re.search(r"<h1>([^<]+)</h1>", html).group(1)
    first = {n for _, n in cards(html)}
    html = client.post("/workout/1/regenerate").text
    assert re.search(r"<h1>([^<]+)</h1>", html).group(1) == day
    second = {n for _, n in cards(html)}
    assert len(second) == len(first) and not first & second
    assert f"Same {day}, all different exercises." in html
    html = client.post("/workout/1/regenerate").text      # third round avoids both earlier sets where it can
    third = {n for _, n in cards(html)}
    assert len(third & (first | second)) <= 2


def test_new_exercises_only_before_the_first_set(client):
    we_id, name = cards(client.post("/workout/new").text)[0]
    client.post(f"/we/{we_id}/set", data={"reps": "10", "weight": "95"})
    assert (we_id, name) in cards(client.post("/workout/1/regenerate").text)


def test_another_profile_cannot_swap_or_remove(client):
    we_id, name = cards(client.post("/workout/new").text)[0]
    client.post("/profiles", data={"name": "Other", "goal": "strength", "n_exercises": "3"})
    client.post(f"/we/{we_id}/swap")
    client.post(f"/we/{we_id}/remove")
    client.post("/profiles/1/use")
    assert (we_id, name) in cards(client.get("/").text)


def test_upgrade_from_v2_keeps_everything(tmp_path, monkeypatch):
    path = tmp_path / "fitness.db"
    monkeypatch.setenv("FP_DB", str(path))
    from app import db
    conn = db.connect()
    conn.execute("DROP TABLE workout_skips")               # make it look like a version-2 database
    conn.execute("PRAGMA user_version = 2")
    conn.execute("INSERT INTO profiles(name, color, created_at) VALUES ('A', '#fff', 'now')")
    conn.execute("INSERT INTO workouts(profile_id, created_at, goal) VALUES (1, 'now', 'strength')")
    conn.execute("INSERT INTO pinned VALUES (1, 'Barbell_Squat')")
    conn.commit()
    conn.close()

    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 3
    assert conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM pinned").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM workout_skips").fetchone()[0] == 0
    assert not list(tmp_path.glob("*.pre-profiles-*.db"))  # no backup/wipe for this step
    conn.close()
