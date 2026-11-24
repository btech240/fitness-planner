"""Home-screen icon, favicon and web manifest."""
import re
import struct

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_DB", str(tmp_path / "test.db"))
    from app.main import app
    return TestClient(app)


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_iphone_gets_a_180px_png_home_screen_icon(client):
    page = client.get("/profiles").text
    href = re.search(r'<link rel="apple-touch-icon" href="([^"]+)"', page).group(1)
    assert href.startswith("/static/apple-touch-icon.png?v=")
    r = client.get(href)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert png_size(r.content) == (180, 180)
    assert 'content="Rackmount"' in page                      # name under the icon


def test_manifest_names_the_app_and_its_icons_load(client):
    page = client.get("/profiles").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page
    r = client.get("/manifest.webmanifest")
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["name"] == "Rackmount" and m["display"] == "standalone" and m["start_url"] == "/"
    for icon in m["icons"]:
        got = client.get(icon["src"])
        assert got.status_code == 200
        if icon["type"] == "image/png":
            w, h = png_size(got.content)
            assert f"{w}x{h}" == icon["sizes"]


def test_favicon_ico_is_served(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and png_size(r.content) == (32, 32)
