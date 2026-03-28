"""Tests for Arena API client -- read operations."""
import pytest
from unittest.mock import MagicMock, patch

from arena_kicad_library_sync.arena_client import (
    ArenaClient,
    ArenaAPI,
    ArenaAPIError,
    KiCadPart,
    LIFECYCLE_MAP,
    _extract_nested,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(session_id="test-session-id-12345"):
    """Create an ArenaClient with mocked internals (no real HTTP)."""
    client = ArenaClient.__new__(ArenaClient)
    client.base_url = "https://api.arenasolutions.com/v1"
    client.allow_writes = False
    client.session_id = session_id
    client._email = "user@example.com"
    client._password = "secret"
    client._workspace_id = None
    client.requests_remaining = None
    client.user_guid = None
    client.user_full_name = None
    client._use_requests = True
    client._session = MagicMock()
    client.MAX_RETRIES = 5
    return client


# ===================================================================
# TestArenaClientAuth
# ===================================================================

class TestArenaClientAuth:
    def test_login_success(self, arena_login_response):
        """Mock a successful login, verify session_id stored."""
        client = _make_client(session_id=None)

        with patch.object(client, "_raw_request", return_value=arena_login_response):
            with patch.object(client, "_resolve_current_user"):
                result = client.login("user@example.com", "secret")

        assert client.session_id == "test-session-id-12345"
        assert result == arena_login_response

    def test_login_failure_401(self):
        """Mock a 401 response, verify ArenaAPIError raised."""
        client = _make_client(session_id=None)

        with patch.object(
            client, "_raw_request",
            side_effect=ArenaAPIError("Unauthorized", status_code=401),
        ):
            with pytest.raises(ArenaAPIError) as exc_info:
                client.login("user@example.com", "wrong-password")

        assert exc_info.value.status_code == 401

    def test_auto_relogin_on_401(self):
        """First request returns 401, then relogin succeeds, then retry succeeds."""
        client = _make_client()

        success_response = {"results": []}

        # _raw_request: first call 401, second call (from relogin) succeeds,
        # third call (retry of original) succeeds
        call_count = 0

        def raw_request_side_effect(method, path, body=None, params=None, auth=True):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Original GET /items -> 401
                raise ArenaAPIError("Session expired", status_code=401)
            if call_count == 2:
                # relogin POST /login
                return {"arenaSessionId": "new-session-id"}
            # Retry of original GET /items
            return success_response

        with patch.object(client, "_raw_request", side_effect=raw_request_side_effect):
            with patch.object(client, "_resolve_current_user"):
                result = client.get("/items")

        assert result == success_response
        assert client.session_id == "new-session-id"


# ===================================================================
# TestArenaAPIRead
# ===================================================================

class TestArenaAPIRead:
    def test_get_items_pagination(self, arena_item_list):
        """Verify get_items passes correct offset/limit params."""
        client = _make_client()
        api = ArenaAPI(client)

        with patch.object(client, "get", return_value=arena_item_list) as mock_get:
            result = api.get_items(page=2, limit=100)

        mock_get.assert_called_once_with("/items", params={"offset": 200, "limit": 100})
        assert result == arena_item_list

    def test_search_by_number(self, arena_item_raw):
        """Verify search_by_number returns first match."""
        client = _make_client()
        api = ArenaAPI(client)

        with patch.object(
            client, "get",
            return_value={"results": [arena_item_raw]},
        ):
            result = api.search_by_number("830-00042")

        assert result is not None
        assert result["guid"] == "GUID-001"
        assert result["number"] == "830-00042"

    def test_search_by_number_not_found(self):
        """Verify None returned when no results."""
        client = _make_client()
        api = ArenaAPI(client)

        with patch.object(client, "get", return_value={"results": []}):
            result = api.search_by_number("999-99999")

        assert result is None

    def test_get_categories(self, arena_categories_response):
        """Verify categories are filtered and sorted."""
        client = _make_client()
        api = ArenaAPI(client)

        with patch.object(client, "get", return_value=arena_categories_response):
            categories = api.get_categories()

        assert len(categories) == 2
        # Should be sorted by prefix: 830 < 831
        assert categories[0]["name"] == "Resistors"
        assert categories[0]["prefix"] == "830"
        assert categories[1]["name"] == "Capacitors"
        assert categories[1]["prefix"] == "831"
        # Each category should have expected keys
        for cat in categories:
            assert "guid" in cat
            assert "name" in cat
            assert "path" in cat
            assert "prefix" in cat
            assert "parentName" in cat


# ===================================================================
# TestRateLimit
# ===================================================================

class TestRateLimit:
    def test_rate_limit_retry(self):
        """429 response -> retry with backoff -> success on attempt 3."""
        client = _make_client()

        call_count = 0

        def raw_request_side_effect(method, path, body=None, params=None, auth=True):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ArenaAPIError("Rate limited", status_code=429)
            return {"results": []}

        with patch.object(client, "_raw_request", side_effect=raw_request_side_effect):
            with patch("arena_kicad_library_sync.arena_client.time.sleep") as mock_sleep:
                result = client.get("/items")

        assert result == {"results": []}
        assert call_count == 3
        # Verify backoff sleep was called twice (attempts 0 and 1)
        assert mock_sleep.call_count == 2
        # First backoff: 2^0 * 10 = 10s, second: 2^1 * 10 = 20s
        mock_sleep.assert_any_call(10)
        mock_sleep.assert_any_call(20)


# ===================================================================
# TestNormalization
# ===================================================================

class TestNormalization:
    def test_normalize_item_field_mapping(self, arena_item_raw, field_mappings):
        """Verify Arena item is normalized to KiCadPart with correct field mapping."""
        client = _make_client()
        api = ArenaAPI(client)

        part = api.normalize_item(arena_item_raw, field_mappings)

        assert isinstance(part, KiCadPart)
        assert part.arena_guid == "GUID-001"
        assert part.arena_number == "830-00042"
        assert part.description == "Thick film resistor, 10kohm, 1%, 0402"
        assert part.category == "Resistors"
        assert part.revision == "A"
        assert part.lifecycle == "Active"  # Production -> Active
        assert part.last_modified_arena == "2026-03-15T10:30:00Z"
        # Field mappings produce custom_fields
        assert part.custom_fields["MPN"] == "830-00042"          # number -> MPN
        assert part.custom_fields["Description"] == "10k Resistor 0402"  # name -> Description
        assert part.custom_fields["Revision"] == "A"
        assert part.custom_fields["Category"] == "Resistors"

    def test_normalize_lifecycle_all_phases(self):
        """Verify all lifecycle phase mappings: DESIGN->Development, PRODUCTION->Active, etc."""
        expected = {
            "DESIGN": "Development",
            "Design": "Development",
            "PRODUCTION": "Active",
            "Production": "Active",
            "OBSOLETE": "Obsolete",
            "Obsolete": "Obsolete",
            "UNRELEASED": "NPI",
            "Unreleased": "NPI",
        }
        for arena_phase, kicad_phase in expected.items():
            assert LIFECYCLE_MAP[arena_phase] == kicad_phase, (
                f"LIFECYCLE_MAP[{arena_phase!r}] should be {kicad_phase!r}"
            )

    def test_extract_nested(self):
        """Test _extract_nested with dot notation."""
        obj = {"a": {"b": {"c": "deep_value"}}, "top": "top_value"}

        assert _extract_nested(obj, "top") == "top_value"
        assert _extract_nested(obj, "a.b.c") == "deep_value"
        assert _extract_nested(obj, "a.b") == {"c": "deep_value"}
        assert _extract_nested(obj, "missing") == ""
        assert _extract_nested(obj, "a.missing") == ""
        assert _extract_nested(obj, "a.b.c.d") == ""  # past a leaf

    def test_normalize_item_with_sourcing(
        self, arena_item_raw, arena_sourcing_response, field_mappings
    ):
        """Verify enrich_with_sourcing populates MPN and manufacturer."""
        client = _make_client()
        api = ArenaAPI(client)

        part = api.normalize_item(arena_item_raw, field_mappings)

        # Before enrichment
        assert part.primary_mpn == ""
        assert part.primary_manufacturer == ""

        with patch.object(
            client, "get", return_value=arena_sourcing_response,
        ):
            enriched = api.enrich_with_sourcing(part)

        assert enriched.primary_manufacturer == "Yageo"
        assert enriched.primary_mpn == "RC0402FR-0710KL"
        assert len(enriched.alternates) == 1
        assert enriched.alternates[0]["manufacturer"] == "Samsung"
        assert enriched.alternates[0]["mpn"] == "RC1005F1002CS"
