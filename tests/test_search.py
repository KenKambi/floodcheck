"""Place search: typed coordinates and input checks (no internet needed)."""
import pytest
from fastapi.testclient import TestClient
import floodmodel as fm
import geocode as geo
import server

client = TestClient(server.app)

def test_coordinates_are_recognised():
    assert geo.parse_coordinates("-0.45, 39.65") == (-0.45, 39.65)
    assert geo.parse_coordinates("-0.45 39.65") == (-0.45, 39.65)
    assert geo.parse_coordinates("Market centre") is None

def test_coordinates_search_skips_the_online_service():
    res = geo.search("-0.45, 39.65", fm.KENYA_BBOX)
    assert res[0]["kind"] == "coordinates" and res[0]["lat"] == -0.45

def test_coordinates_outside_the_country_are_rejected():
    with pytest.raises(ValueError):
        geo.search("48.85, 2.35", fm.KENYA_BBOX)

def test_search_endpoint():
    assert client.get("/api/search", params={"q": "-0.45, 39.65"}).json()["results"][0]["lon"] == 39.65
    assert client.get("/api/search", params={"q": "a"}).status_code == 422
