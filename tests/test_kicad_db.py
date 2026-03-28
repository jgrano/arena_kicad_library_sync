"""Tests for KiCad SQLite database manager."""
import json
from arena_kicad_library_sync.kicad_db import SCHEMA_VERSION


def _make_part(id="GUID-001", arena_number="830-00042", category="Resistors",
               description="10k Resistor", value="10k", kicad_symbol="Device:R",
               kicad_footprint="R_0402", lifecycle="Active", revision="A",
               mpn="RC0402FR-0710KL", manufacturer="Yageo",
               custom_fields=None):
    """Helper to build a part dict with sensible defaults."""
    return {
        "id": id,
        "arena_number": arena_number,
        "category": category,
        "description": description,
        "value": value,
        "kicad_symbol": kicad_symbol,
        "kicad_footprint": kicad_footprint,
        "lifecycle": lifecycle,
        "revision": revision,
        "mpn": mpn,
        "manufacturer": manufacturer,
        "custom_fields": custom_fields or {"Tolerance": "1%"},
    }


class TestDatabaseSetup:
    def test_creates_tables(self, tmp_db):
        """Verify schema tables are created on connect."""
        cursor = tmp_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = sorted(row[0] for row in cursor.fetchall())
        assert "parts" in tables
        assert "sync_log" in tables
        assert "sync_state" in tables
        assert "schema_version" in tables

    def test_schema_version(self, tmp_db):
        """Verify schema version is set."""
        row = tmp_db.conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == SCHEMA_VERSION


class TestPartsCRUD:
    def test_upsert_and_retrieve(self, tmp_db):
        """Insert a part, retrieve it, verify all fields."""
        part = _make_part()
        tmp_db.upsert_part(part)

        result = tmp_db.get_part("GUID-001")
        assert result is not None
        assert result["id"] == "GUID-001"
        assert result["arena_number"] == "830-00042"
        assert result["category"] == "Resistors"
        assert result["description"] == "10k Resistor"
        assert result["value"] == "10k"
        assert result["kicad_symbol"] == "Device:R"
        assert result["kicad_footprint"] == "R_0402"
        assert result["lifecycle"] == "Active"
        assert result["revision"] == "A"
        assert result["mpn"] == "RC0402FR-0710KL"
        assert result["manufacturer"] == "Yageo"
        assert json.loads(result["custom_fields"]) == {"Tolerance": "1%"}
        # Source is "arena" by default, so local_dirty should be 0
        assert result["local_dirty"] == 0

    def test_upsert_update(self, tmp_db):
        """Insert then update same part, verify update applied."""
        part = _make_part()
        tmp_db.upsert_part(part)

        part["description"] = "10k Resistor Updated"
        part["revision"] = "B"
        tmp_db.upsert_part(part)

        result = tmp_db.get_part("GUID-001")
        assert result["description"] == "10k Resistor Updated"
        assert result["revision"] == "B"
        # Still only one row
        assert tmp_db.get_part_count() == 1

    def test_get_part_by_number(self, tmp_db):
        """Verify lookup by arena_number works."""
        tmp_db.upsert_part(_make_part())

        result = tmp_db.get_part_by_number("830-00042")
        assert result is not None
        assert result["id"] == "GUID-001"
        assert result["arena_number"] == "830-00042"

        # Non-existent number returns None
        assert tmp_db.get_part_by_number("999-99999") is None

    def test_get_all_parts(self, tmp_db):
        """Insert 3 parts, get_all returns all 3 sorted."""
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00003"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="830-00001"))
        tmp_db.upsert_part(_make_part(id="G3", arena_number="830-00002"))

        parts = tmp_db.get_all_parts()
        assert len(parts) == 3
        # Should be sorted by arena_number
        assert parts[0]["arena_number"] == "830-00001"
        assert parts[1]["arena_number"] == "830-00002"
        assert parts[2]["arena_number"] == "830-00003"

    def test_get_parts_by_category(self, tmp_db):
        """Filter parts by category."""
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00001", category="Resistors"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="831-00001", category="Capacitors"))
        tmp_db.upsert_part(_make_part(id="G3", arena_number="830-00002", category="Resistors"))

        resistors = tmp_db.get_parts_by_category("Resistors")
        assert len(resistors) == 2
        assert all(p["category"] == "Resistors" for p in resistors)

        capacitors = tmp_db.get_parts_by_category("Capacitors")
        assert len(capacitors) == 1
        assert capacitors[0]["category"] == "Capacitors"

        # Non-existent category returns empty list
        assert tmp_db.get_parts_by_category("Inductors") == []

    def test_get_categories(self, tmp_db):
        """Get distinct categories."""
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00001", category="Resistors"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="831-00001", category="Capacitors"))
        tmp_db.upsert_part(_make_part(id="G3", arena_number="830-00002", category="Resistors"))

        categories = tmp_db.get_categories()
        assert sorted(categories) == ["Capacitors", "Resistors"]


class TestDirtyTracking:
    def test_mark_dirty_and_clear(self, tmp_db):
        """Mark a part as dirty, verify, then clear."""
        part = _make_part()
        tmp_db.upsert_part(part)  # source="arena" so local_dirty=0

        result = tmp_db.get_part("GUID-001")
        assert result["local_dirty"] == 0

        tmp_db.mark_dirty("GUID-001")
        result = tmp_db.get_part("GUID-001")
        assert result["local_dirty"] == 1

        tmp_db.clear_dirty("GUID-001")
        result = tmp_db.get_part("GUID-001")
        assert result["local_dirty"] == 0
        # clear_dirty should also set last_synced_to_arena
        assert result["last_synced_to_arena"] is not None

    def test_get_dirty_parts(self, tmp_db):
        """Only dirty parts are returned."""
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00001"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="830-00002"))
        tmp_db.upsert_part(_make_part(id="G3", arena_number="830-00003"))

        # None dirty yet
        assert tmp_db.get_dirty_parts() == []

        tmp_db.mark_dirty("G1")
        tmp_db.mark_dirty("G3")

        dirty = tmp_db.get_dirty_parts()
        assert len(dirty) == 2
        dirty_ids = {p["id"] for p in dirty}
        assert dirty_ids == {"G1", "G3"}

    def test_dirty_count(self, tmp_db):
        """get_dirty_count returns correct count."""
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00001"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="830-00002"))

        assert tmp_db.get_dirty_count() == 0

        tmp_db.mark_dirty("G1")
        assert tmp_db.get_dirty_count() == 1

        tmp_db.mark_dirty("G2")
        assert tmp_db.get_dirty_count() == 2

        tmp_db.clear_dirty("G1")
        assert tmp_db.get_dirty_count() == 1


class TestObsolete:
    def test_delete_obsolete(self, tmp_db):
        """Parts not in active_guids set are deleted."""
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00001"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="830-00002"))
        tmp_db.upsert_part(_make_part(id="G3", arena_number="830-00003"))

        assert tmp_db.get_part_count() == 3

        # Only G1 and G3 are still active
        deleted = tmp_db.delete_obsolete({"G1", "G3"})
        assert deleted == 1
        assert tmp_db.get_part_count() == 2
        assert tmp_db.get_part("G2") is None
        assert tmp_db.get_part("G1") is not None
        assert tmp_db.get_part("G3") is not None

    def test_delete_obsolete_all_active(self, tmp_db):
        """No deletions when all parts are active."""
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00001"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="830-00002"))

        deleted = tmp_db.delete_obsolete({"G1", "G2"})
        assert deleted == 0
        assert tmp_db.get_part_count() == 2

    def test_delete_obsolete_empty_active(self, tmp_db):
        """All parts deleted when active set is empty."""
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00001"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="830-00002"))

        deleted = tmp_db.delete_obsolete(set())
        assert deleted == 2
        assert tmp_db.get_part_count() == 0


class TestSyncLog:
    def test_log_sync_event(self, tmp_db):
        """Log an event and retrieve it."""
        tmp_db.log_sync_event(
            direction="pull",
            arena_guid="GUID-001",
            operation="create",
            field_changed="description",
            old_value="",
            new_value="10k Resistor",
        )

        log = tmp_db.get_sync_log()
        assert len(log) == 1
        entry = log[0]
        assert entry["direction"] == "pull"
        assert entry["arena_guid"] == "GUID-001"
        assert entry["operation"] == "create"
        assert entry["field_changed"] == "description"
        assert entry["old_value"] == ""
        assert entry["new_value"] == "10k Resistor"
        assert entry["timestamp"] is not None

    def test_sync_log_ordering(self, tmp_db):
        """Newest entries first."""
        tmp_db.log_sync_event(direction="pull", arena_guid="G1", operation="create")
        tmp_db.log_sync_event(direction="pull", arena_guid="G2", operation="update")
        tmp_db.log_sync_event(direction="push", arena_guid="G3", operation="delete")

        log = tmp_db.get_sync_log()
        assert len(log) == 3
        # Newest first: timestamps are monotonically increasing,
        # so the last inserted should appear first
        assert log[0]["arena_guid"] == "G3"
        assert log[1]["arena_guid"] == "G2"
        assert log[2]["arena_guid"] == "G1"


class TestSyncState:
    def test_last_sync_time(self, tmp_db):
        """Set and get last sync time."""
        tmp_db.set_last_sync_time("pull", "2026-03-15T10:30:00Z")
        result = tmp_db.get_last_sync_time("pull")
        assert result == "2026-03-15T10:30:00Z"

        # Update with new time
        tmp_db.set_last_sync_time("pull", "2026-03-16T12:00:00Z")
        result = tmp_db.get_last_sync_time("pull")
        assert result == "2026-03-16T12:00:00Z"

    def test_last_sync_time_default_none(self, tmp_db):
        """Returns None when not set."""
        assert tmp_db.get_last_sync_time("pull") is None
        assert tmp_db.get_last_sync_time("push") is None

    def test_last_sync_time_auto_generates(self, tmp_db):
        """When no dt argument is passed, a timestamp is auto-generated."""
        tmp_db.set_last_sync_time("push")
        result = tmp_db.get_last_sync_time("push")
        assert result is not None
        # Should be an ISO timestamp string
        assert "T" in result


class TestExport:
    def test_export_kicad_dbl_config(self, tmp_db, tmp_path):
        """Generate .kicad_dbl and verify JSON structure."""
        # Insert parts in two categories so the export has real data
        tmp_db.upsert_part(_make_part(id="G1", arena_number="830-00001", category="Resistors"))
        tmp_db.upsert_part(_make_part(id="G2", arena_number="831-00001", category="Capacitors"))

        output_path = str(tmp_path / "output" / "arena.kicad_dbl")
        tmp_db.export_kicad_dbl_config(output_path)

        with open(output_path, "r", encoding="utf-8") as f:
            dbl = json.load(f)

        # Top-level structure
        assert dbl["meta"]["version"] == 0
        assert dbl["name"] == "Arena PLM Library"
        assert dbl["source"]["type"] == "odbc"
        assert "Database=" in dbl["source"]["connection_string"]

        # Libraries: one per category
        libs = dbl["libraries"]
        assert len(libs) == 2
        lib_names = {lib["name"] for lib in libs}
        assert "Arena_Resistors" in lib_names
        assert "Arena_Capacitors" in lib_names

        # Each library has expected keys
        for lib in libs:
            assert lib["table"] == "parts"
            assert lib["key"] == "id"
            assert lib["symbols"] == "kicad_symbol"
            assert lib["footprints"] == "kicad_footprint"
            assert len(lib["fields"]) > 0
            # Category-specific libraries should have a query filter
            assert "query" in lib

    def test_export_kicad_dbl_empty_db(self, tmp_db, tmp_path):
        """Export with no parts produces a valid file with 'All' library."""
        output_path = str(tmp_path / "empty.kicad_dbl")
        tmp_db.export_kicad_dbl_config(output_path)

        with open(output_path, "r", encoding="utf-8") as f:
            dbl = json.load(f)

        assert len(dbl["libraries"]) == 1
        assert dbl["libraries"][0]["name"] == "Arena_All"
        # "All" category should not have a query filter
        assert "query" not in dbl["libraries"][0]
