"""Extended tests for arena_client.py — covering more code paths."""

import pytest
from unittest.mock import MagicMock, patch
from arena_kicad_library_sync.arena_client import (
    ArenaClient, ArenaAPI, ArenaAPIError, KiCadPart, _make_ssl_context,
)


class TestArenaClientInit:
    def test_default_base_url(self):
        client = ArenaClient()
        assert client.base_url == "https://api.arenasolutions.com/v1"

    def test_custom_base_url(self):
        client = ArenaClient(base_url="https://custom.api.com/v1/")
        assert client.base_url == "https://custom.api.com/v1"

    def test_allow_writes_default_false(self):
        client = ArenaClient()
        assert client.allow_writes is False

    def test_allow_writes_true(self):
        client = ArenaClient(allow_writes=True)
        assert client.allow_writes is True

    def test_initial_state(self):
        client = ArenaClient()
        assert client.session_id is None
        assert client.requests_remaining is None
        assert client.user_guid is None

    def test_workspaces(self):
        assert "Production" in ArenaClient.WORKSPACES
        assert "Sandbox" in ArenaClient.WORKSPACES


class TestArenaClientLogout:
    def test_logout_clears_session(self):
        client = ArenaClient()
        client.session_id = "test-session"
        client._raw_request = MagicMock(return_value={})
        client.logout()
        assert client.session_id is None

    def test_logout_no_session(self):
        client = ArenaClient()
        client.logout()  # Should not raise


class TestArenaClientHTTPMethods:
    def test_get_delegates(self):
        client = ArenaClient()
        client._request = MagicMock(return_value={"result": True})
        result = client.get("/test", params={"k": "v"})
        client._request.assert_called_once_with("GET", "/test", params={"k": "v"})
        assert result == {"result": True}

    def test_post_delegates(self):
        client = ArenaClient()
        client._request = MagicMock(return_value={})
        client.post("/test", body={"key": "val"})
        client._request.assert_called_once_with("POST", "/test", body={"key": "val"})

    def test_put_delegates(self):
        client = ArenaClient()
        client._request = MagicMock(return_value={})
        client.put("/test", body={"key": "val"})
        client._request.assert_called_once_with("PUT", "/test", body={"key": "val"})

    def test_patch_delegates(self):
        client = ArenaClient()
        client._request = MagicMock(return_value={})
        client.patch("/test", body={"key": "val"})
        client._request.assert_called_once_with("PATCH", "/test", body={"key": "val"})

    def test_delete_delegates(self):
        client = ArenaClient()
        client._request = MagicMock(return_value={})
        client.delete("/test")
        client._request.assert_called_once_with("DELETE", "/test")


class TestArenaClientRetry:
    @patch("arena_kicad_library_sync.arena_client.time.sleep")
    def test_max_retries_exceeded(self, mock_sleep):
        client = ArenaClient()
        client.session_id = "test"
        client._raw_request = MagicMock(
            side_effect=ArenaAPIError("rate limited", status_code=429))
        with pytest.raises(ArenaAPIError, match="Max retries"):
            client._request("GET", "/test")
        assert mock_sleep.call_count > 0

    def test_non_retryable_error(self):
        client = ArenaClient()
        client.session_id = "test"
        client._raw_request = MagicMock(
            side_effect=ArenaAPIError("not found", status_code=404))
        with pytest.raises(ArenaAPIError, match="not found"):
            client._request("GET", "/test")


class TestArenaClientParseError:
    def test_parse_error_body_valid(self):
        body = '{"errors": [{"message": "Item not found", "code": "404"}]}'
        result = ArenaClient._parse_error_body(body)
        assert "Item not found" in result
        assert "404" in result

    def test_parse_error_body_empty(self):
        assert ArenaClient._parse_error_body("") == "Unknown error"

    def test_parse_error_body_not_json(self):
        result = ArenaClient._parse_error_body("plain text error")
        assert result == "plain text error"

    def test_parse_error_body_truncated(self):
        long_text = "x" * 1000
        result = ArenaClient._parse_error_body(long_text)
        assert len(result) <= 500


class TestArenaAPIGetModifiedSince:
    def test_single_page(self):
        client = MagicMock()
        client.get.return_value = {"results": [{"guid": "G1"}]}
        api = ArenaAPI(client)
        items = api.get_modified_since("2026-01-01")
        assert len(items) == 1
        client.get.assert_called_once()

    def test_multiple_pages(self):
        client = MagicMock()
        # First page: full 400 results, second page: 10 results
        page1 = [{"guid": f"G{i}"} for i in range(400)]
        page2 = [{"guid": f"G{400+i}"} for i in range(10)]
        client.get.side_effect = [
            {"results": page1},
            {"results": page2},
        ]
        api = ArenaAPI(client)
        items = api.get_modified_since("2026-01-01")
        assert len(items) == 410


class TestArenaAPIGetRevision:
    def test_from_item(self):
        client = MagicMock()
        api = ArenaAPI(client)
        assert api.get_revision({"revisionNumber": "B"}) == "B"

    def test_from_api(self):
        client = MagicMock()
        client.get.return_value = {"results": [{"revisionNumber": "C"}]}
        api = ArenaAPI(client)
        result = api.get_revision({"guid": "G1"})
        assert result == "C"


class TestArenaAPIGetLifecycle:
    def test_dict_lifecycle(self):
        client = MagicMock()
        api = ArenaAPI(client)
        assert api.get_lifecycle({"lifecyclePhase": {"name": "Production"}}) == "Production"

    def test_string_lifecycle(self):
        client = MagicMock()
        api = ArenaAPI(client)
        assert api.get_lifecycle({"lifecyclePhase": "Active"}) == "Active"


class TestArenaAPIDenormalize:
    def test_denormalize_basic(self):
        client = MagicMock()
        api = ArenaAPI(client)
        part = KiCadPart(
            description="Test Part",
            primary_mpn="MPN1",
            kicad_footprint="R_0402",
        )
        mappings = {
            "Description": "name",
            "MPN": "number",
            "Footprint": "customAttributes.kicad_footprint",
        }
        result = api.denormalize_part(part, mappings)
        assert result["name"] == "Test Part"
        assert result["number"] == "MPN1"
        assert result["customAttributes"]["kicad_footprint"] == "R_0402"

    def test_denormalize_skips_forbidden(self):
        client = MagicMock()
        api = ArenaAPI(client)
        part = KiCadPart(lifecycle="Active", revision="A")
        mappings = {"Lifecycle": "lifecyclePhase", "Revision": "revisionNumber"}
        result = api.denormalize_part(part, mappings)
        assert "lifecyclePhase" not in result
        assert "revisionNumber" not in result

    def test_denormalize_skips_empty(self):
        client = MagicMock()
        api = ArenaAPI(client)
        part = KiCadPart()
        mappings = {"MPN": "number"}
        result = api.denormalize_part(part, mappings)
        assert "number" not in result


class TestArenaAPINormalize:
    def test_normalize_all_fields(self):
        client = MagicMock()
        api = ArenaAPI(client)
        raw = {
            "guid": "G1",
            "number": "830-001",
            "name": "Test",
            "description": "Description",
            "revisionNumber": "A",
            "lifecyclePhase": {"name": "PRODUCTION"},
            "category": {"name": "Resistors"},
            "modifiedDateTime": "2026-03-15T10:00:00Z",
        }
        part = api.normalize_item(raw, {"number": "MPN", "name": "Description"})
        assert part.arena_guid == "G1"
        assert part.arena_number == "830-001"
        assert part.lifecycle == "Active"
        assert part.category == "Resistors"
        assert part.revision == "A"

    def test_normalize_string_lifecycle(self):
        client = MagicMock()
        api = ArenaAPI(client)
        raw = {"guid": "G1", "number": "830", "name": "T",
               "lifecyclePhase": "Obsolete", "category": "C"}
        part = api.normalize_item(raw, {})
        assert part.lifecycle == "Obsolete"

    def test_normalize_string_category(self):
        client = MagicMock()
        api = ArenaAPI(client)
        raw = {"guid": "G1", "number": "830", "name": "T",
               "category": "TestCat"}
        part = api.normalize_item(raw, {})
        assert part.category == "TestCat"


class TestArenaAPIEnrichSourcing:
    def test_enrich_no_guid(self):
        client = MagicMock()
        api = ArenaAPI(client)
        part = KiCadPart()
        result = api.enrich_with_sourcing(part)
        assert result.primary_mpn == ""
        client.get.assert_not_called()

    def test_enrich_with_alternates(self):
        client = MagicMock()
        client.get.return_value = {
            "results": [
                {"manufacturer": {"name": "Mfr1"}, "mpn": "MPN1"},
                {"manufacturer": {"name": "Mfr2"}, "mpn": "MPN2"},
            ]
        }
        api = ArenaAPI(client)
        part = KiCadPart(arena_guid="G1")
        result = api.enrich_with_sourcing(part)
        assert result.primary_mpn == "MPN1"
        assert result.primary_manufacturer == "Mfr1"
        assert len(result.alternates) == 1
        assert result.alternates[0]["mpn"] == "MPN2"

    def test_enrich_api_error_handled(self):
        client = MagicMock()
        client.get.side_effect = Exception("API error")
        api = ArenaAPI(client)
        part = KiCadPart(arena_guid="G1")
        result = api.enrich_with_sourcing(part)
        assert result.primary_mpn == ""


class TestArenaAPIGetBom:
    def test_single_page(self):
        client = MagicMock()
        client.get.return_value = {"results": [{"guid": "L1"}]}
        api = ArenaAPI(client)
        bom = api.get_bom("G1")
        assert len(bom) == 1

    def test_empty_bom(self):
        client = MagicMock()
        client.get.return_value = {"results": []}
        api = ArenaAPI(client)
        bom = api.get_bom("G1")
        assert len(bom) == 0


class TestSSLContext:
    def test_make_ssl_context_returns_context(self):
        ctx = _make_ssl_context()
        assert ctx is not None
