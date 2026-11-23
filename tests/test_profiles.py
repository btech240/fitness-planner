import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

GYM = {"goal": "hypertrophy", "n_exercises": "4", "units": "lb",
       "barbell_enabled": "on", "barbell_bar": "45", "plate_45": "2", "plate_25": "1",
       "dumbbell_enabled": "on", "dumbbell_from": "5", "dumbbell_to": "50", "dumbbell_step": "5",
       "station_bench": "on", "station_rack": "on"}


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_DB", str(tmp_path / "test.db"))
    from app.main import app
    return app, tmp_path


def phone(app):
    """A separate device: its own cookie jar."""
    return TestClient(app)


def we_ids(html):
    return [int(x) for x in re.findall(r'action="/we/(\d+)/set"', html)]


def test_new_device_sees_picker(app_db):
    app, _ = app_db
    c = phone(app)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/profiles"
    page = c.get("/profiles").text
    assert "Who's working out?" in page and "Add person" in page
    assert 'class="tabbar"' not in page


def test_add_person_remembers_device_and_assigns_colors(app_db):
    app, _ = app_db
    a = phone(app)
    r = a.post("/profiles", data={"name": "Alex", "goal": "strength", "n_exercises": "6"}, follow_redirects=False)
    assert r.status_code == 303 and "fp_profile=" in r.headers["set-cookie"]
    assert a.get("/").status_code == 200
    b = phone(app)
    b.post("/profiles", data={"name": "Sam"})
    picker = b.get("/profiles").text
    assert "--avatar: #FF3EA5" in picker and "--avatar: #2EE6D6" in picker
    settings = a.get("/settings").text
    assert '<option value="strength" selected' in settings and 'value="6"' in settings
    assert "Working out as" in settings and "Alex" in settings


def test_profile_names_validated(app_db):
    app, _ = app_db
    c = phone(app)
    c.post("/profiles", data={"name": "Alex"})
    assert "already a profile called" in c.post("/profiles", data={"name": " alex "}).text
    assert "Enter a name" in c.post("/profiles", data={"name": "   "}).text


def test_history_pins_and_training_settings_are_personal_but_gym_is_shared(app_db):
    app, _ = app_db
    a, b = phone(app), phone(app)
    a.post("/profiles", data={"name": "Alex", "n_exercises": "4"})
    b.post("/profiles", data={"name": "Sam", "n_exercises": "3"})
    a.post("/settings", data=GYM)                       # Alex sets up the gym
    r = a.post("/workout/new")
    ids = we_ids(r.text)
    a.post(f"/we/{ids[0]}/set", data={"reps": "10", "weight": "95"})
    a.post(f"/we/{ids[0]}/pin")

    assert "Generate workout" in b.get("/").text          # Sam has no workout in progress
    sam_settings = b.get("/settings").text
    assert 'name="barbell_enabled" checked' in sam_settings   # shared gym
    assert 'name="n_exercises" type="number" inputmode="numeric" min="2" max="10" value="3"' in sam_settings
    assert "None pinned" in sam_settings                     # Alex's pin isn't Sam's
    assert "No finished workouts yet" in b.get("/history").text
    assert all(f'action="/we/{i}/set"' not in b.get("/").text for i in ids)


def test_one_profile_cannot_touch_anothers_workout(app_db):
    app, tmp = app_db
    a, b = phone(app), phone(app)
    a.post("/profiles", data={"name": "Alex"})
    b.post("/profiles", data={"name": "Sam"})
    a.post("/settings", data=GYM)
    ids = we_ids(a.post("/workout/new").text)
    b.post(f"/we/{ids[0]}/set", data={"reps": "10", "weight": "95"})
    b.post(f"/we/{ids[0]}/pin")
    b.post("/workout/1/finish")
    conn = sqlite3.connect(tmp / "test.db")
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pinned").fetchone()[0] == 0
    assert conn.execute("SELECT finished_at FROM workouts WHERE id = 1").fetchone()[0] is None


def test_switch_rename_and_delete(app_db):
    app, tmp = app_db
    c = phone(app)
    c.post("/profiles", data={"name": "Alex"})
    c.post("/profiles", data={"name": "Sam"})            # this phone is now Sam
    c.post("/profiles/1/use")                            # switch back to Alex
    assert ">Alex<" in c.get("/settings").text

    c.post("/profile/rename", data={"name": "Alexis"})
    assert "Renamed to Alexis" in c.get("/settings?renamed=1").text
    assert "already a profile called Sam" in c.post("/profile/rename", data={"name": "sam"}).text

    c.post("/settings", data=GYM)
    c.post("/workout/new")
    r = c.post("/profile/delete", data={"confirm": "Alex"}, follow_redirects=False)
    assert r.headers["location"].startswith("/settings")   # name didn't match: nothing deleted
    r = c.post("/profile/delete", data={"confirm": "alexis"}, follow_redirects=False)
    assert r.headers["location"] == "/profiles"
    assert c.get("/", follow_redirects=False).headers["location"] == "/profiles"
    conn = sqlite3.connect(tmp / "test.db")
    assert [n for (n,) in conn.execute("SELECT name FROM profiles")] == ["Sam"]
    assert conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 0
    assert json.loads(conn.execute("SELECT value FROM settings WHERE key = 'inventory'").fetchone()[0])


def test_upgrade_from_single_user_backs_up_and_keeps_only_the_gym(tmp_path, monkeypatch):
    path = tmp_path / "fitness.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE workouts (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                               goal TEXT NOT NULL, finished_at TEXT, pattern TEXT);
        CREATE TABLE workout_exercises (id INTEGER PRIMARY KEY, workout_id INTEGER, exercise_id TEXT,
                               position INTEGER, target_sets INTEGER, target_reps INTEGER, suggested_weight REAL);
        CREATE TABLE sets (id INTEGER PRIMARY KEY, workout_exercise_id INTEGER, reps INTEGER, weight REAL, logged_at TEXT);
        CREATE TABLE excluded (exercise_id TEXT PRIMARY KEY);
        CREATE TABLE pinned (exercise_id TEXT PRIMARY KEY);
        INSERT INTO settings VALUES ('inventory', '{"barbell": {"enabled": true, "bar": 45}}');
        INSERT INTO settings VALUES ('goal', '"strength"');
        INSERT INTO workouts(created_at, goal) VALUES ('2026-11-20T10:00:00+00:00', 'strength');
        INSERT INTO pinned VALUES ('Barbell_Squat');
    """)
    old.commit()
    old.close()

    monkeypatch.setenv("FP_DB", str(path))
    from app import db
    conn = db.connect()
    backups = list(tmp_path.glob("fitness.pre-profiles-*.db"))
    assert len(backups) == 1
    assert sqlite3.connect(backups[0]).execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pinned").fetchone()[0] == 0
    assert "profile_id" in {r["name"] for r in conn.execute("PRAGMA table_info(workouts)")}
    assert db.get_setting(conn, "inventory") == {"barbell": {"enabled": True, "bar": 45}}
    assert db.get_setting(conn, "goal") is None
    conn.close()
    db.connect().close()                                   # second open: no second backup
    assert len(list(tmp_path.glob("fitness.pre-profiles-*.db"))) == 1
