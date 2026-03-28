"""Tests for sync engine -- push operations."""
import pytest
from unittest.mock import MagicMock, patch

from arena_kicad_library_sync.arena_client import (
    ArenaAPI,
    ArenaClient,
    ConflictResult,
    KiCadPart,
)
from arena_kicad_library_sync.config import Config
from arena_kicad_library_sync.kicad_db import KiCadLibraryDB
from arena_kicad_library_sync.sync_engine import SyncEngine, PushResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_arena_api(allow_writes=True):
    """Create a mock ArenaAPI with all methods stubbed."""
    api = MagicMock(spec=ArenaAPI)
    api.client = MagicMock(spec=ArenaClient)
    api.client.allow_writes = allow_writes
    return api


def _make_db():
    """Create a mock KiCadLibraryDB with all methods stubbed."""
    db = MagicMock(spec=KiCadLibraryDB)
    db.get_last_sync_time.return_value = None
    db.get_part.return_value = None
    db.get_dirty_parts.return_value = []
    return db


def _make_config(tmp_path, conflict_strategy="prompt_user"):
    """Create a real Config backed by a temp directory."""
    cfg = Config(config_dir=tmp_path)
    cfg.load()
    cfg._data["sync"]["conflict_strategy"] = conflict_strategy
    return cfg


def _make_dirty_part(guid="GUID-001", number="830-00042"):
    """Return a local dirty part dict as get_dirty_parts would return."""
    return {
        "id": guid,
        "arena_guid": guid,
        "arena_number": number,
        "description": "10k Resistor 0402 (local edit)",
        "category": "Resistors",
        "mpn": "RC0402FR-0710KL",
        "manufacturer": "Yageo",
        "local_dirty": 1,
        "custom_fields": "{}",
    }


def _arena_current_item(guid="GUID-001", number="830-00042", name="10k Resistor 0402"):
    """Return a raw Arena item dict as get_item would return."""
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
# TestPushDirty
# ===================================================================

class TestPushDirty:
    def test_push_dirty_no_conflict(self, tmp_path):
        """Dirty parts pushed to Arena when no conflict."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path, conflict_strategy="prompt_user")

        dirty = _make_dirty_part()
        db.get_dirty_parts.return_value = [dirty]

        arena_current = _arena_current_item()
        api.get_item.return_value = arena_current

        engine = SyncEngine(api, db, cfg)

        with patch(
            "arena_kicad_library_sync.sync_engine.detect_conflict",
            return_value=[],  # no conflict
        ):
            result = engine.push_dirty()

        assert isinstance(result, PushResult)
        assert result.updated == 1
        assert result.conflicts == []
        assert result.errors == []
        db.clear_dirty.assert_called_once_with("GUID-001")
        db.log_sync_event.assert_any_call(
            direction="kicad_to_arena",
            arena_guid="GUID-001",
            operation="update",
        )

    def test_push_dirty_conflict_arena_wins(self, tmp_path):
        """Conflict with arena_wins strategy reverts local."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path, conflict_strategy="arena_wins")

        dirty = _make_dirty_part()
        db.get_dirty_parts.return_value = [dirty]

        arena_current = _arena_current_item(name="Arena version")
        api.get_item.return_value = arena_current

        conflict = ConflictResult(
            arena_guid="GUID-001",
            field_name="name",
            arena_value="Arena version",
            local_value="Local version",
        )

        part = KiCadPart(arena_guid="GUID-001", arena_number="830-00042")
        api.normalize_item.return_value = part

        engine = SyncEngine(api, db, cfg)
        with patch(
            "arena_kicad_library_sync.sync_engine.detect_conflict",
            return_value=[conflict],
        ):
            result = engine.push_dirty()

        # arena_wins: local should be reverted via upsert from arena data
        api.normalize_item.assert_called_once()
        db.upsert_part.assert_called_once()
        db.log_sync_event.assert_any_call(
            direction="kicad_to_arena",
            arena_guid="GUID-001",
            operation="conflict",
            resolved_by="arena_wins",
        )
        # Should NOT have updated Arena
        assert result.updated == 0
        assert result.conflicts == []

    def test_push_dirty_conflict_kicad_wins(self, tmp_path):
        """Conflict with kicad_wins strategy pushes local."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path, conflict_strategy="kicad_wins")

        dirty = _make_dirty_part()
        db.get_dirty_parts.return_value = [dirty]

        arena_current = _arena_current_item()
        api.get_item.return_value = arena_current

        conflict = ConflictResult(
            arena_guid="GUID-001",
            field_name="name",
            arena_value="Arena value",
            local_value="Local value",
        )

        engine = SyncEngine(api, db, cfg)
        with patch(
            "arena_kicad_library_sync.sync_engine.detect_conflict",
            return_value=[conflict],
        ):
            result = engine.push_dirty()

        # kicad_wins: local changes pushed to Arena
        assert result.updated == 1
        db.clear_dirty.assert_called_once_with("GUID-001")
        db.log_sync_event.assert_any_call(
            direction="kicad_to_arena",
            arena_guid="GUID-001",
            operation="conflict",
            resolved_by="kicad_wins",
        )

    def test_push_dirty_conflict_prompt_user(self, tmp_path):
        """Conflict with prompt_user adds to pending conflicts."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path, conflict_strategy="prompt_user")

        dirty = _make_dirty_part()
        db.get_dirty_parts.return_value = [dirty]

        arena_current = _arena_current_item()
        api.get_item.return_value = arena_current

        conflict = ConflictResult(
            arena_guid="GUID-001",
            field_name="name",
            arena_value="Arena value",
            local_value="Local value",
        )

        engine = SyncEngine(api, db, cfg)
        with patch(
            "arena_kicad_library_sync.sync_engine.detect_conflict",
            return_value=[conflict],
        ):
            result = engine.push_dirty()

        # prompt_user: conflict not auto-resolved
        assert result.updated == 0
        assert len(result.conflicts) == 1
        assert result.conflicts[0].field_name == "name"
        # dirty flag NOT cleared
        db.clear_dirty.assert_not_called()
        # Conflict should be in pending list
        assert len(engine.get_pending_conflicts()) == 1

    def test_push_dirty_writes_disabled(self, tmp_path):
        """Push returns error when allow_writes is False."""
        api = _make_arena_api(allow_writes=False)
        db = _make_db()
        cfg = _make_config(tmp_path)

        engine = SyncEngine(api, db, cfg)
        result = engine.push_dirty()

        assert len(result.errors) == 1
        assert "allow_writes" in result.errors[0]
        db.get_dirty_parts.assert_not_called()


# ===================================================================
# TestPushNew
# ===================================================================

class TestPushNew:
    def test_push_new_parts_creates_in_arena(self, tmp_path):
        """New parts are created in Arena and GUID stored locally."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        new_part = {
            "name": "New Capacitor 100nF",
            "category": {"guid": "CAT-002"},
        }

        api.create_item.return_value = {
            "guid": "NEW-GUID-001",
            "number": "831-00001",
        }

        engine = SyncEngine(api, db, cfg)
        result = engine.push_new_parts([new_part])

        assert result.created == 1
        assert result.errors == []
        api.create_item.assert_called_once_with(new_part)
        # Local DB should be updated with the Arena GUID and number
        db.upsert_part.assert_called_once()
        upserted = db.upsert_part.call_args[0][0]
        assert upserted["id"] == "NEW-GUID-001"
        assert upserted["arena_guid"] == "NEW-GUID-001"
        assert upserted["arena_number"] == "831-00001"

    def test_push_new_parts_writes_disabled(self, tmp_path):
        """Push new parts returns error when allow_writes is False."""
        api = _make_arena_api(allow_writes=False)
        db = _make_db()
        cfg = _make_config(tmp_path)

        engine = SyncEngine(api, db, cfg)
        result = engine.push_new_parts([{"name": "Test"}])

        assert len(result.errors) == 1
        assert result.created == 0

    def test_push_new_parts_api_error(self, tmp_path):
        """API error during create is captured in result.errors."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        api.create_item.side_effect = RuntimeError("Arena 500 error")

        engine = SyncEngine(api, db, cfg)
        result = engine.push_new_parts([{"name": "Bad part"}])

        assert result.created == 0
        assert len(result.errors) == 1
        assert "Arena 500 error" in result.errors[0]

    def test_push_new_parts_progress_callback(self, tmp_path):
        """Progress callback called for each new part."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        api.create_item.side_effect = [
            {"guid": "G-1", "number": "830-00001"},
            {"guid": "G-2", "number": "830-00002"},
        ]

        callback = MagicMock()
        engine = SyncEngine(api, db, cfg)
        engine.push_new_parts(
            [{"name": "Part A"}, {"name": "Part B"}],
            progress_callback=callback,
        )

        assert callback.call_count == 2
        callback.assert_any_call(1, 2, "830-00001")
        callback.assert_any_call(2, 2, "830-00002")


# ===================================================================
# TestConflictResolution
# ===================================================================

class TestConflictResolution:
    def _engine_with_conflict(self, tmp_path):
        """Return an engine that already has one pending conflict."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)

        engine = SyncEngine(api, db, cfg)

        conflict = ConflictResult(
            arena_guid="GUID-001",
            arena_number="830-00042",
            field_name="name",
            arena_value="Arena value",
            local_value="Local value",
        )
        engine._pending_conflicts.append(conflict)
        return engine, api, db, conflict

    def test_resolve_kicad_wins_calls_arena_write(self, tmp_path):
        """Resolving kicad_wins pushes local value to Arena."""
        engine, api, db, conflict = self._engine_with_conflict(tmp_path)

        engine.resolve_conflict(conflict, "kicad_wins")

        assert conflict.resolution == "kicad_wins"
        api.update_item_attribute.assert_called_once_with(
            "GUID-001", "name", "Local value",
        )
        db.log_sync_event.assert_called_once()

    def test_resolve_arena_wins_reverts_local(self, tmp_path):
        """Resolving arena_wins clears dirty flag."""
        engine, api, db, conflict = self._engine_with_conflict(tmp_path)
        db.get_part.return_value = {"id": "GUID-001", "description": "Local value"}

        engine.resolve_conflict(conflict, "arena_wins")

        assert conflict.resolution == "arena_wins"
        db.clear_dirty.assert_called_once_with("GUID-001")
        # Should NOT push to Arena
        api.update_item_attribute.assert_not_called()

    def test_auto_resolve_all(self, tmp_path):
        """auto_resolve_all resolves all pending conflicts."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)
        engine = SyncEngine(api, db, cfg)

        conflicts = [
            ConflictResult(
                arena_guid=f"GUID-{i}",
                arena_number=f"830-{i:05d}",
                field_name="name",
                arena_value=f"Arena {i}",
                local_value=f"Local {i}",
            )
            for i in range(3)
        ]
        engine._pending_conflicts.extend(conflicts)

        db.get_part.return_value = {"id": "GUID-0"}

        resolved = engine.auto_resolve_all("arena_wins")

        assert resolved == 3
        # All conflicts should now have a resolution
        assert all(c.resolution == "arena_wins" for c in conflicts)
        # No more pending
        assert len(engine.get_pending_conflicts()) == 0

    def test_resolve_conflict_by_guid(self, tmp_path):
        """resolve_conflict_by_guid finds and resolves the right conflict."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)
        engine = SyncEngine(api, db, cfg)

        c1 = ConflictResult(arena_guid="G-1", field_name="name",
                            arena_value="A", local_value="B")
        c2 = ConflictResult(arena_guid="G-2", field_name="description",
                            arena_value="C", local_value="D")
        engine._pending_conflicts.extend([c1, c2])

        db.get_part.return_value = {"id": "G-2"}
        engine.resolve_conflict_by_guid("G-2", "description", "arena_wins")

        assert c2.resolution == "arena_wins"
        assert c1.resolution is None  # untouched

    def test_resolve_conflict_by_guid_not_found(self, tmp_path):
        """resolve_conflict_by_guid raises ValueError for unknown conflict."""
        api = _make_arena_api()
        db = _make_db()
        cfg = _make_config(tmp_path)
        engine = SyncEngine(api, db, cfg)

        with pytest.raises(ValueError, match="No pending conflict"):
            engine.resolve_conflict_by_guid("NOPE", "name", "arena_wins")
