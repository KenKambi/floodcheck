"""The server starts, serves the dashboard and its assets, and rejects bad requests."""
from fastapi.testclient import TestClient
import server

client = TestClient(server.app)

def test_dashboard_and_assets_are_served():
    page = client.get("/")
    assert page.status_code == 200 and "FloodCheck" in page.text
    for path in ["/static/css/floodcheck.css", "/static/js/floodcheck.js",
                 "/static/assets/logo-full.png", "/static/assets/logo-mark.png"]:
        assert client.get(path).status_code == 200, path

def test_config():
    j = client.get("/api/config").json()
    assert j["max_side_deg"] > 0 and len(j["kenya_bbox"]) == 4

def test_bad_area_is_rejected_with_a_clear_message():
    r = client.post("/api/runs", json={"bbox": [10, 0, 11, 1], "rain_mm": 100, "rain_hours": 24})
    assert r.status_code == 422

def test_unknown_run():
    assert client.get("/api/runs/doesnotexist").status_code == 404

def test_page_does_not_use_blocked_tile_servers():
    js = client.get("/static/js/floodcheck.js").text
    assert "https://tile.openstreetmap.org/{z}" not in js and "basemaps.cartocdn.com/" not in js
    assert "World_Street_Map" in js

def test_own_street_map_provider_from_environment(monkeypatch):
    monkeypatch.setenv("FLOOD_STREET_TILES", "https://tiles.example.org/{z}/{x}/{y}.png")
    monkeypatch.setenv("FLOOD_STREET_ATTRIBUTION", "© Example")
    sm = client.get("/api/config").json()["street_map"]
    assert sm["tiles"] == ["https://tiles.example.org/{z}/{x}/{y}.png"] and sm["attribution"] == "© Example"

def test_cors_is_off_by_default_and_on_when_configured(monkeypatch):
    import importlib
    r = client.get("/api/config", headers={"Origin": "http://localhost:4200"})
    assert "access-control-allow-origin" not in r.headers
    monkeypatch.setenv("FLOOD_CORS_ORIGINS", "http://localhost:4200")
    srv = importlib.reload(server)
    r = TestClient(srv.app).get("/api/config", headers={"Origin": "http://localhost:4200"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:4200"
