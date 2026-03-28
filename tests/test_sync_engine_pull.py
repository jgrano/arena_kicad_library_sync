"""Tests for sync engine -- pull operations."""
from unittest.mock import MagicMock, patch

from arena_kicad_library_sync.arena_client import (
    ArenaAPI,
    ArenaClient,
    ConflictResult,
    KiCadPart,
)
from arena_kicad_library_sync.config import Config
from arena_kicad_library_sync.kicad_db import KiCadLibraryDB
from arena_kicad_library_sync.sync_engine import SyncEngine, PullResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_arena_api():
    """Create a mock ArenaAPI with all methods stubbed."""
    api = MagicMock(spec=ArenaAPI)
    api.client = MagicMock(spec=ArenaClient)
    api.client.allow_writes = False
    return api


def _make_db():
    """Create a mock KiCadLibraryDB with all methods stubbed."""
    db = MagicMock(spec=KiCadLibraryDB)
    db.get_last_sync_time.return_value = None
    db.get_part.return_value = None
    db.delete_obsolete.return_value = 0
    return db


def _make_config(tmp_path):
    """Create a real Config backed by a temp directory."""
    cfg = Config(config_dir=tmp_path)
    cfg.load()
    return cfg


def _make_kicad_part(guid="GUID-001", number="830-00042"):
    """Return a KiCadPart with sensible defaults."""
    return KiCadPart(
        arena_guid=guid,
        arena_number=number,
        description="10k Resistor 0402",
        category="Resistors",
        revision="A",
        lifecycle="Active",
        primary_mpn="RC0402FR-0710KL",
        primary_manufacturer="Yageo",
        kicad_symbol="Device:R",
        kicad_footprint="Resistor_SMD:R_0402_1005Metric",
        datasheet_url="https://example.com/10k.pdf",
        last_modified_arena="2026-03-15T10:30:00Z",
        custom_fields={"Tolerance": "1%"},
    )


def _raw_item(guid="GUID-001", number="830-00042", name="10k Resistor 0402"):
    """Return a raw Arena item dict."""
    return {
        "guid": guid,
        "number": number,
        "name": name,
        "description": "Thick film resistor, 10kohm, 1%, 0402",
        "revisionNumber": "A",
        "lifecyclePhase": {"name": "Production"},
        "category": {"guid": "CAT-001", "name": "Resistors"},
        "modifiedDateTime": "2026-03-15T10:30:00Z",
    }


# ===================================================================
# TestFullPull
# ===================================================================

class TestFullPull:
    def test_full_pull_inserts_items(self, tmp_path):
        """Full pull: items from Arena are inserted into DB."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        raw = _raw_item()
        part = _make_kicad_part()

        # get_items returns one page then empty
        api.get_items.side_effect = [
            {"results": [raw]},
            {"results": []},
        ]
        api.normalize_item.return_value = part
        api.enrich_with_sourcing.return_value = part
        db.get_part.return_value = None  # not existing

        engine = SyncEngine(api, db, cfg)
        result = engine.pull_full()

        assert isinstance(result, PullResult)
        assert result.added == 1
        assert result.updated == 0
        assert result.errors == []
        db.upsert_part.assert_called_once()
        db.log_sync_event.assert_called_once_with(
            direction="arena_to_kicad",
            arena_guid="GUID-001",
            operation="insert",
        )
        db.set_last_sync_time.assert_called_once_with("arena_to_kicad")

    def test_full_pull_updates_existing(self, tmp_path):
        """Full pull: existing items are counted as updated."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        raw = _raw_item()
        part = _make_kicad_part()

        api.get_items.side_effect = [{"results": [raw]}, {"results": []}]
        api.normalize_item.return_value = part
        api.enrich_with_sourcing.return_value = part
        db.get_part.return_value = {"id": "GUID-001"}  # existing

        engine = SyncEngine(api, db, cfg)
        result = engine.pull_full()

        assert result.added == 0
        assert result.updated == 1
        db.log_sync_event.assert_called_once_with(
            direction="arena_to_kicad",
            arena_guid="GUID-001",
            operation="update",
        )

    def test_full_pull_deletes_obsolete(self, tmp_path):
        """Full pull: items not in Arena are deleted from DB."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        raw = _raw_item(guid="GUID-001")
        part = _make_kicad_part(guid="GUID-001")

        api.get_items.side_effect = [{"results": [raw]}, {"results": []}]
        api.normalize_item.return_value = part
        api.enrich_with_sourcing.return_value = part
        db.get_part.return_value = None
        db.delete_obsolete.return_value = 3  # simulate 3 deleted

        engine = SyncEngine(api, db, cfg)
        result = engine.pull_full()

        assert result.deleted == 3
        db.delete_obsolete.assert_called_once_with({"GUID-001"})

    def test_progress_callback_called(self, tmp_path):
        """Verify progress callback is called for each item."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        items = [_raw_item(guid=f"G-{i}", number=f"830-{i:05d}") for i in range(3)]
        parts = [_make_kicad_part(guid=f"G-{i}", number=f"830-{i:05d}") for i in range(3)]

        api.get_items.side_effect = [{"results": items}, {"results": []}]
        api.normalize_item.side_effect = parts
        api.enrich_with_sourcing.side_effect = parts
        db.get_part.return_value = None

        callback = MagicMock()
        engine = SyncEngine(api, db, cfg)
        engine.pull_full(progress_callback=callback)

        assert callback.call_count == 3
        # First call: (1, 0, part_number), Second: (2, 0, ...) etc.
        callback.assert_any_call(1, 0, "830-00000")
        callback.assert_any_call(2, 0, "830-00001")
        callback.assert_any_call(3, 0, "830-00002")

    def test_pull_error_surfaces_in_result(self, tmp_path):
        """API error for one item doesn't stop sync, appears in result.errors."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        good_raw = _raw_item(guid="GUID-OK", number="830-00001")
        bad_raw = _raw_item(guid="GUID-BAD", number="830-00002")
        good_part = _make_kicad_part(guid="GUID-OK", number="830-00001")

        api.get_items.side_effect = [
            {"results": [good_raw, bad_raw]},
            {"results": []},
        ]
        # normalize succeeds for first, raises for second
        api.normalize_item.side_effect = [good_part, RuntimeError("bad data")]
        api.enrich_with_sourcing.return_value = good_part
        db.get_part.return_value = None

        engine = SyncEngine(api, db, cfg)
        result = engine.pull_full()

        assert result.added == 1
        assert len(result.errors) == 1
        assert "830-00002" in result.errors[0]

    def test_full_pull_multiple_pages(self, tmp_path):
        """Full pull paginates through multiple pages of items."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        # Create 400 items for page 1 (full page) and 1 for page 2
        page1_items = [_raw_item(guid=f"G-{i}", number=f"830-{i:05d}") for i in range(400)]
        page2_items = [_raw_item(guid="G-400", number="830-00400")]

        api.get_items.side_effect = [
            {"results": page1_items},
            {"results": page2_items},
        ]

        part = _make_kicad_part()
        api.normalize_item.return_value = part
        api.enrich_with_sourcing.return_value = part
        db.get_part.return_value = None

        engine = SyncEngine(api, db, cfg)
        result = engine.pull_full()

        assert result.added == 401
        assert api.get_items.call_count == 2


# ===================================================================
# TestDeltaPull
# ===================================================================

class TestDeltaPull:
    def test_delta_pull_only_modified(self, tmp_path):
        """Delta pull: only modified items are fetched and synced."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        db.get_last_sync_time.return_value = "2026-03-01T00:00:00Z"

        raw = _raw_item()
        part = _make_kicad_part()

        api.get_modified_since.return_value = [raw]
        api.normalize_item.return_value = part
        api.enrich_with_sourcing.return_value = part
        db.get_part.return_value = {"id": "GUID-001"}  # existing -> update

        engine = SyncEngine(api, db, cfg)
        result = engine.pull_delta()

        assert result.updated == 1
        assert result.added == 0
        api.get_modified_since.assert_called_once_with("2026-03-01T00:00:00Z")
        # get_items should NOT be called for delta
        api.get_items.assert_not_called()

    def test_delta_falls_back_to_full(self, tmp_path):
        """If no previous sync time, delta falls back to full pull."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        db.get_last_sync_time.return_value = None  # no prior sync

        raw = _raw_item()
        part = _make_kicad_part()

        api.get_items.side_effect = [{"results": [raw]}, {"results": []}]
        api.normalize_item.return_value = part
        api.enrich_with_sourcing.return_value = part
        db.get_part.return_value = None

        engine = SyncEngine(api, db, cfg)
        result = engine.pull_delta()

        # Should have called get_items (full pull), NOT get_modified_since
        api.get_items.assert_called()
        api.get_modified_since.assert_not_called()
        assert result.added == 1

    def test_delta_detects_conflict_on_dirty_part(self, tmp_path):
        """If local part is dirty and Arena also changed, conflict is detected."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        db.get_last_sync_time.return_value = "2026-03-01T00:00:00Z"

        raw = _raw_item(guid="GUID-001", number="830-00042", name="Changed in Arena")
        part = _make_kicad_part(guid="GUID-001")

        # Local part is dirty and has a different description
        existing_local = {
            "id": "GUID-001",
            "local_dirty": True,
            "description": "Changed locally",
            "arena_number": "830-00042",
        }

        api.get_modified_since.return_value = [raw]
        api.normalize_item.return_value = part
        api.enrich_with_sourcing.return_value = part
        db.get_part.return_value = existing_local

        # detect_conflict returns a real conflict
        conflict = ConflictResult(
            arena_guid="GUID-001",
            arena_number="830-00042",
            field_name="name",
            arena_value="Changed in Arena",
            local_value="Changed locally",
        )

        engine = SyncEngine(api, db, cfg)
        with patch(
            "arena_kicad_library_sync.sync_engine.detect_conflict",
            return_value=[conflict],
        ):
            result = engine.pull_delta()

        # The conflicting item should NOT be upserted
        db.upsert_part.assert_not_called()
        # But a conflict log event should be recorded
        db.log_sync_event.assert_any_call(
            direction="arena_to_kicad",
            arena_guid="GUID-001",
            operation="conflict",
        )
        # Conflict should be in pending conflicts
        assert len(engine.get_pending_conflicts()) == 1
        assert engine.get_pending_conflicts()[0].field_name == "name"
        # Result shows 0 added/updated (conflict skipped)
        assert result.added == 0
        assert result.updated == 0

    def test_delta_clean_part_no_conflict(self, tmp_path):
        """Delta pull: non-dirty existing part is updated without conflict check."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        db.get_last_sync_time.return_value = "2026-03-01T00:00:00Z"

        raw = _raw_item()
        part = _make_kicad_part()

        # Existing but NOT dirty
        existing_local = {
            "id": "GUID-001",
            "local_dirty": False,
            "description": "Old description",
        }

        api.get_modified_since.return_value = [raw]
        api.normalize_item.return_value = part
        api.enrich_with_sourcing.return_value = part
        db.get_part.return_value = existing_local

        engine = SyncEngine(api, db, cfg)
        result = engine.pull_delta()

        assert result.updated == 1
        db.upsert_part.assert_called_once()

    def test_delta_progress_callback(self, tmp_path):
        """Delta pull calls progress callback with (i+1, total, number)."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        db.get_last_sync_time.return_value = "2026-03-01T00:00:00Z"

        items = [_raw_item(guid=f"G-{i}", number=f"830-{i:05d}") for i in range(2)]
        parts = [_make_kicad_part(guid=f"G-{i}", number=f"830-{i:05d}") for i in range(2)]

        api.get_modified_since.return_value = items
        api.normalize_item.side_effect = parts
        api.enrich_with_sourcing.side_effect = parts
        db.get_part.return_value = None

        callback = MagicMock()
        engine = SyncEngine(api, db, cfg)
        engine.pull_delta(progress_callback=callback)

        assert callback.call_count == 2
        callback.assert_any_call(1, 2, "830-00000")
        callback.assert_any_call(2, 2, "830-00001")
