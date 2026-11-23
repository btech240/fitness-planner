"""No login by design (home network), so the app must at least refuse requests that
another website could make on a household member's behalf, and expose nothing extra."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("FP_ALLOWED_HOSTS", raising=False)
    from app.main import app
    c = TestClient(app)
    c.post("/profiles", data={"name": "Tester"})
    return c


def test_cross_site_form_posts_are_blocked(client):
    for origin in ("http://evil.example", "null"):
        r = client.post("/profiles", data={"name": "Intruder"}, headers={"Origin": origin})
        assert r.status_code == 403
    r = client.post("/profile/delete", data={"confirm": "Tester"},
                    headers={"Referer": "http://evil.example/page"}, follow_redirects=False)
    assert r.status_code == 403
    assert "Tester" in client.get("/settings").text          # still there


def test_same_origin_posts_work(client):
    r = client.post("/profiles", data={"name": "Sam"}, headers={"Origin": "http://testserver"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.post("/workout/new", headers={"Referer": "http://testserver/"}, follow_redirects=False)
    assert r.status_code == 303


def test_unknown_host_names_rejected_when_allowlist_set(client, monkeypatch):
    monkeypatch.setenv("FP_ALLOWED_HOSTS", "192.168.1.50,fitness,localhost")
    assert client.get("/", headers={"Host": "rebind.attacker.example"}).status_code == 400
    assert client.get("/healthz", headers={"Host": "192.168.1.50:8000"}).status_code == 200
    assert client.get("/healthz", headers={"Host": "FITNESS:8000"}).status_code == 200


def test_no_api_schema_or_docs_exposed(client):
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404


def test_photo_route_cannot_read_other_files(client):
    for name in ("..%2F..%2Fetc%2Fpasswd", "fitness.db", "x_0.jpg%00.db", "..\\fitness.db"):
        assert client.get(f"/photos/{name}").status_code == 404


def test_profile_names_are_escaped(client):
    client.post("/profiles", data={"name": "<script>alert(1)</script>"})
    html = client.get("/profiles").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
