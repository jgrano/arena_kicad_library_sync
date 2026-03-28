"""Tests for Arena API client — write operations and safety guards."""
import pytest
from unittest.mock import MagicMock, patch

from arena_kicad_library_sync.arena_client import (
    ArenaClient, ArenaAPI, ArenaAPIError, FORBIDDEN_WRITE_FIELDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(allow_writes: bool) -> ArenaClient:
    """Create an ArenaClient with HTTP disabled and mocked transport."""
    with patch.object(ArenaClient, "__init__", lambda self, **kw: None):
        client = ArenaClient.__new__(ArenaClient)
    client.allow_writes = allow_writes
    client.session_id = "fake-session"
    client.user_guid = "USER-GUID-001"
    client.user_full_name = "Test User"
    client.base_url = "https://api.arenasolutions.com/v1"
    client.requests_remaining = 100
    client._email = "test@example.com"
    client._password = "secret"
    client._workspace_id = None
    client._use_requests = False
    client._session = None
    # Mock the HTTP verbs directly
    client.get = MagicMock()
    client.post = MagicMock()
    client.put = MagicMock()
    client.delete = MagicMock()
    return client


def _make_api(allow_writes: bool) -> tuple[ArenaAPI, ArenaClient]:
    """Return (ArenaAPI, mock_client) pair."""
    client = _make_client(allow_writes)
    api = ArenaAPI(client)
    return api, client


# ---------------------------------------------------------------------------
# Safety guards (allow_writes=False)
# ---------------------------------------------------------------------------

class TestWriteSafety:
    def test_write_requires_allow_writes_flag(self):
        """Verify update_item_attribute raises when allow_writes=False."""
        api, _ = _make_api(allow_writes=False)

        with pytest.raises(ArenaAPIError, match="allow_writes=True"):
            api.update_item_attribute("GUID-001", "description", "new desc")

    def test_update_forbidden_lifecycle(self):
        """Verify updating lifecyclePhase raises FORBIDDEN_OPERATION."""
        api, _ = _make_api(allow_writes=True)

        with pytest.raises(ArenaAPIError, match="FORBIDDEN_OPERATION"):
            api.update_item_attribute("GUID-001", "lifecyclePhase", "Production")

    def test_update_forbidden_revision(self):
        """Verify updating revisionNumber raises FORBIDDEN_OPERATION."""
        api, _ = _make_api(allow_writes=True)

        with pytest.raises(ArenaAPIError, match="FORBIDDEN_OPERATION"):
            api.update_item_attribute("GUID-001", "revisionNumber", "B")

    def test_update_forbidden_workflow(self):
        """Verify updating workflowStatus raises FORBIDDEN_OPERATION."""
        api, _ = _make_api(allow_writes=True)

        with pytest.raises(ArenaAPIError, match="FORBIDDEN_OPERATION"):
            api.update_item_attribute("GUID-001", "workflowStatus", "Approved")

    def test_delete_item_forbidden(self):
        """Verify delete_item always raises FORBIDDEN_OPERATION."""
        api, _ = _make_api(allow_writes=True)

        with pytest.raises(ArenaAPIError, match="FORBIDDEN_OPERATION"):
            api.delete_item("GUID-001")

    def test_delete_item_forbidden_even_without_writes(self):
        """Verify delete_item raises even with allow_writes=False."""
        api, _ = _make_api(allow_writes=False)

        with pytest.raises(ArenaAPIError, match="FORBIDDEN_OPERATION"):
            api.delete_item("GUID-001")

    @pytest.mark.parametrize("field", sorted(FORBIDDEN_WRITE_FIELDS))
    def test_all_forbidden_fields_rejected(self, field):
        """Verify every entry in FORBIDDEN_WRITE_FIELDS is rejected."""
        api, _ = _make_api(allow_writes=True)

        with pytest.raises(ArenaAPIError, match="FORBIDDEN_OPERATION"):
            api.update_item_attribute("GUID-001", field, "anything")

    def test_create_item_requires_allow_writes(self):
        """Verify create_item raises when allow_writes=False."""
        api, _ = _make_api(allow_writes=False)

        with pytest.raises(ArenaAPIError, match="allow_writes=True"):
            api.create_item({"name": "Test"})

    def test_add_item_sourcing_requires_allow_writes(self):
        """Verify add_item_sourcing raises when allow_writes=False."""
        api, _ = _make_api(allow_writes=False)

        with pytest.raises(ArenaAPIError, match="allow_writes=True"):
            api.add_item_sourcing("GUID-001", "Yageo", "RC0402FR-0710KL")


# ---------------------------------------------------------------------------
# Write operations (allow_writes=True)
# ---------------------------------------------------------------------------

class TestWriteOperations:
    def test_update_item_attribute_success(self):
        """Verify successful attribute update with logging."""
        api, client = _make_api(allow_writes=True)

        # GET returns the current item state
        client.get.return_value = {
            "guid": "GUID-001",
            "description": "Old description",
        }
        # PUT returns the updated item
        client.put.return_value = {
            "guid": "GUID-001",
            "description": "New description",
        }

        result = api.update_item_attribute("GUID-001", "description", "New description")

        # Should GET current item first
        client.get.assert_called_once_with("/items/GUID-001")
        # Then PUT the update
        client.put.assert_called_once_with(
            "/items/GUID-001",
            body={"description": "New description"},
        )
        assert result["description"] == "New description"

    def test_update_custom_attribute(self):
        """Verify custom attributes use additionalAttributes format."""
        api, client = _make_api(allow_writes=True)

        client.get.return_value = {
            "guid": "GUID-001",
            "additionalAttributes": [
                {"apiName": "kicad_footprint", "value": "old_fp"},
            ],
        }
        client.put.return_value = {"guid": "GUID-001"}

        api.update_item_attribute(
            "GUID-001",
            "customAttributes.kicad_footprint",
            "Resistor_SMD:R_0402_1005Metric",
        )

        client.put.assert_called_once_with(
            "/items/GUID-001",
            body={"additionalAttributes": [{
                "apiName": "kicad_footprint",
                "value": "Resistor_SMD:R_0402_1005Metric",
            }]},
        )

    def test_create_item_success(self):
        """Verify create_item calls POST and returns result."""
        api, client = _make_api(allow_writes=True)

        client.post.return_value = {
            "guid": "GUID-NEW",
            "number": "830-00099",
            "name": "New Resistor",
        }
        # PUT for setting owner
        client.put.return_value = {}

        item_data = {
            "name": "New Resistor",
            "category": {"guid": "CAT-001"},
        }
        result = api.create_item(item_data)

        client.post.assert_called_once_with("/items", body=item_data)
        assert result["guid"] == "GUID-NEW"
        assert result["number"] == "830-00099"

    def test_create_item_sets_owner(self):
        """Verify owner is set after creation."""
        api, client = _make_api(allow_writes=True)

        client.post.return_value = {
            "guid": "GUID-NEW",
            "number": "830-00099",
        }
        client.put.return_value = {}

        api.create_item({"name": "Test"})

        # The second call should set the owner
        client.put.assert_called_once_with(
            "/items/GUID-NEW",
            body={"owner": {"guid": "USER-GUID-001"}},
        )

    def test_create_item_no_owner_without_user_guid(self):
        """Verify owner is not set when user_guid is missing."""
        api, client = _make_api(allow_writes=True)
        client.user_guid = None

        client.post.return_value = {
            "guid": "GUID-NEW",
            "number": "830-00099",
        }

        api.create_item({"name": "Test"})

        # PUT should not be called because there is no user_guid
        client.put.assert_not_called()

    def test_create_item_owner_failure_swallowed(self):
        """Verify that a failure setting the owner does not propagate."""
        api, client = _make_api(allow_writes=True)

        client.post.return_value = {
            "guid": "GUID-NEW",
            "number": "830-00099",
        }
        client.put.side_effect = ArenaAPIError("Owner update failed", status_code=500)

        # Should not raise despite the PUT failure
        result = api.create_item({"name": "Test"})
        assert result["guid"] == "GUID-NEW"

    def test_add_item_sourcing(self):
        """Verify sourcing data is posted correctly."""
        api, client = _make_api(allow_writes=True)

        client.post.return_value = {
            "guid": "SRC-001",
            "manufacturer": {"name": "Yageo"},
            "mpn": "RC0402FR-0710KL",
        }

        result = api.add_item_sourcing(
            "GUID-001", "Yageo", "RC0402FR-0710KL", description="10k 0402",
        )

        client.post.assert_called_once_with(
            "/items/GUID-001/sourcing",
            body={
                "manufacturer": {"name": "Yageo"},
                "mpn": "RC0402FR-0710KL",
                "description": "10k 0402",
            },
        )
        assert result["mpn"] == "RC0402FR-0710KL"

    def test_add_item_sourcing_without_description(self):
        """Verify sourcing omits description when not provided."""
        api, client = _make_api(allow_writes=True)
        client.post.return_value = {}

        api.add_item_sourcing("GUID-001", "Yageo", "RC0402FR-0710KL")

        expected_body = {
            "manufacturer": {"name": "Yageo"},
            "mpn": "RC0402FR-0710KL",
        }
        client.post.assert_called_once_with(
            "/items/GUID-001/sourcing",
            body=expected_body,
        )
