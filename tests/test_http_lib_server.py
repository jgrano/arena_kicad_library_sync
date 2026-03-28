"""Tests for KiCad HTTP lib server endpoints."""
import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from arena_kicad_library_sync.server.http_lib_handler import router, _get_db


@pytest.fixture
def mock_db():
    """Create a mock KiCadLibraryDB."""
    db = MagicMock()
    db.get_categories.return_value = ["Resistors", "Capacitors"]
    db.get_parts_by_category.return_value = [
        {
            "id": "GUID-001",
            "arena_number": "830-00042",
            "description": "10k Resistor",
            "value": "10k",
            "kicad_symbol": "Device:R",
            "kicad_footprint": "R_0402",
            "mpn": "RC0402FR",
            "manufacturer": "Yageo",
            "lifecycle": "Active",
            "revision": "A",
            "category": "Resistors",
            "datasheet": "https://example.com",
            "reference": "R",
            "custom_fields": "{}",
        }
    ]
    db.get_part.return_value = db.get_parts_by_category.return_value[0]
    db.get_part_by_number.return_value = db.get_parts_by_category.return_value[0]
    return db


@pytest.fixture
def client(mock_db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_db] = lambda: mock_db
    return TestClient(app)


class TestCategories:
    def test_list_categories(self, client):
        """GET /v1/categories returns category list."""
        resp = client.get("/v1/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "Resistors"
        assert data[0]["name"] == "Resistors"
        assert "Arena PLM" in data[0]["description"]
        assert data[1]["id"] == "Capacitors"


class TestParts:
    def test_parts_by_category(self, client):
        """GET /v1/parts/Resistors returns formatted parts."""
        resp = client.get("/v1/parts/Resistors")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        part = data[0]
        assert part["id"] == "GUID-001"
        assert part["symbolIdStr"] == "Device:R"
        assert part["footprints"] == ["R_0402"]
        assert "fields" in part
        assert part["fields"]["Arena PN"]["value"] == "830-00042"
        assert part["fields"]["MPN"]["value"] == "RC0402FR"
        assert part["fields"]["Value"]["value"] == "10k"

    def test_single_part(self, client):
        """GET /v1/parts/Resistors/GUID-001 returns single part."""
        resp = client.get("/v1/parts/Resistors/GUID-001")
        assert resp.status_code == 200
        part = resp.json()
        assert part["id"] == "GUID-001"
        assert part["symbolIdStr"] == "Device:R"
        assert part["fields"]["Manufacturer"]["value"] == "Yageo"

    def test_part_not_found(self, client, mock_db):
        """GET for nonexistent part returns 404."""
        mock_db.get_part.return_value = None
        mock_db.get_part_by_number.return_value = None
        resp = client.get("/v1/parts/Resistors/NONEXISTENT")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_lifecycle_in_fields(self, client):
        """Verify lifecycle field is present in part fields."""
        resp = client.get("/v1/parts/Resistors/GUID-001")
        assert resp.status_code == 200
        part = resp.json()
        assert "Lifecycle" in part["fields"]
        assert part["fields"]["Lifecycle"]["value"] == "Active"
        assert part["fields"]["Lifecycle"]["visible"] is True
