import re

import pytest
from fastapi.testclient import TestClient

from app import catalog, fetch_photos, photos

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture
def photo_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_DB", str(tmp_path / "test.db"))
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


def test_photos_listed_only_when_downloaded(photo_dir):
    squat = catalog.get("Barbell_Squat")
    assert photos.for_exercise(squat) == []
    (photo_dir / "Barbell_Squat_0.jpg").write_bytes(JPEG)
    (photo_dir / "Barbell_Squat_1.jpg").write_bytes(JPEG)
    assert photos.for_exercise(squat) == [
        {"url": "/photos/Barbell_Squat_0.jpg", "label": "Start"},
        {"url": "/photos/Barbell_Squat_1.jpg", "label": "End"},
    ]


def test_hidden_photo_sets_never_show(photo_dir):
    for ex_id in photos.HIDDEN:
        for n in (0, 1):
            (photo_dir / photos.filename(ex_id, n)).write_bytes(JPEG)
        assert photos.for_exercise(catalog.get(ex_id)) == []


def test_fetch_skips_hidden_and_existing(photo_dir, monkeypatch):
    seen = []
    monkeypatch.setattr(fetch_photos, "_fetch", lambda task: seen.append(task) or "skipped")
    fetch_photos.main(["x", str(photo_dir)])
    names = {dest.name for dest, _ in seen}
    assert "Barbell_Squat_0.jpg" in names
    assert not any(n.startswith(tuple(photos.HIDDEN)) for n in names)
    (photo_dir / "Barbell_Squat_0.jpg").write_bytes(JPEG)
    assert fetch_photos._fetch((photo_dir / "Barbell_Squat_0.jpg", "http://unused")) == "skipped"


def test_photo_route_serves_files_and_rejects_tricks(photo_dir):
    (photo_dir / "Barbell_Squat_0.jpg").write_bytes(JPEG)
    from app.main import app
    client = TestClient(app)
    r = client.get("/photos/Barbell_Squat_0.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == JPEG
    assert client.get("/photos/Barbell_Squat_1.jpg").status_code == 404
    assert client.get("/photos/..%2Ftest.db").status_code == 404
    assert client.get("/photos/test.db").status_code == 404


def test_today_shows_photos_in_how_to(photo_dir):
    for e in catalog.EXERCISES:
        for n in range(min(2, e["photos"])):
            (photo_dir / photos.filename(e["id"], n)).write_bytes(JPEG)
    from app.main import app
    client = TestClient(app)
    client.post("/settings", data={"goal": "hypertrophy", "n_exercises": "4", "units": "lb",
                                   "barbell_enabled": "on", "barbell_bar": "45", "plate_45": "2",
                                   "station_rack": "on", "station_bench": "on"})
    html = client.post("/workout/new").text
    assert 'class="form-photos"' in html
    assert re.search(r'<button type="button" class="form-photo" data-src="/photos/[A-Za-z0-9_\-]+_0\.jpg"', html)
    assert 'id="lightbox"' in html
