"""Tests for conflict detection and resolution."""

from arena_kicad_library_sync.arena_client import (
    detect_conflict, ConflictResult, KiCadPart,
    _extract_nested, _set_nested, ArenaAPIError,
    LIFECYCLE_MAP, FORBIDDEN_WRITE_FIELDS,
)


class TestDetectConflict:
    def test_no_conflict_same_values(self):
        arena = {"guid": "G1", "number": "830-001", "name": "Same", "description": "Same"}
        local = {"arena_number": "830-001", "description": "Same"}
        conflicts = detect_conflict(arena, local)
        assert len(conflicts) == 0

    def test_conflict_on_name_vs_description(self):
        arena = {"guid": "G1", "number": "830-001", "name": "Arena Name",
                 "description": "Arena Desc", "modifiedDateTime": "2026-01-01"}
        local = {"arena_number": "830-001", "description": "Local Desc"}
        conflicts = detect_conflict(arena, local)
        # description field maps to description in both
        desc_conflicts = [c for c in conflicts if c.field_name == "description"]
        assert len(desc_conflicts) == 1
        assert desc_conflicts[0].arena_value == "Arena Desc"
        assert desc_conflicts[0].local_value == "Local Desc"

    def test_conflict_result_has_conflict_property(self):
        cr = ConflictResult(arena_value="A", local_value="B")
        assert cr.has_conflict is True

    def test_conflict_result_no_conflict(self):
        cr = ConflictResult(arena_value="Same", local_value="Same")
        assert cr.has_conflict is False

    def test_conflict_includes_timestamps(self):
        arena = {"guid": "G1", "number": "830-001", "name": "A",
                 "description": "A", "modifiedDateTime": "2026-03-15"}
        local = {"arena_number": "830-001", "description": "B",
                 "last_modified_local": "2026-03-10"}
        conflicts = detect_conflict(arena, local)
        if conflicts:
            assert conflicts[0].arena_modified_at == "2026-03-15"


class TestConflictResultDataclass:
    def test_default_values(self):
        cr = ConflictResult()
        assert cr.arena_guid == ""
        assert cr.resolution is None
        assert cr.recommended == "arena_wins"

    def test_has_conflict_different(self):
        cr = ConflictResult(arena_value="A", local_value="B")
        assert cr.has_conflict is True

    def test_has_conflict_same(self):
        cr = ConflictResult(arena_value="X", local_value="X")
        assert cr.has_conflict is False


class TestHelperFunctions:
    def test_extract_nested_simple(self):
        assert _extract_nested({"a": "b"}, "a") == "b"

    def test_extract_nested_deep(self):
        assert _extract_nested({"a": {"b": {"c": "d"}}}, "a.b.c") == "d"

    def test_extract_nested_missing(self):
        assert _extract_nested({"a": "b"}, "x") == ""

    def test_extract_nested_partial(self):
        assert _extract_nested({"a": {"b": "c"}}, "a.x") == ""

    def test_set_nested_simple(self):
        d = {}
        _set_nested(d, "a", "b")
        assert d == {"a": "b"}

    def test_set_nested_deep(self):
        d = {}
        _set_nested(d, "a.b.c", "d")
        assert d == {"a": {"b": {"c": "d"}}}


class TestKiCadPart:
    def test_default_values(self):
        p = KiCadPart()
        assert p.arena_guid == ""
        assert p.custom_fields == {}
        assert p.alternates == []

    def test_all_fields(self):
        p = KiCadPart(
            arena_guid="G1", arena_number="830-001",
            description="Test", category="Resistors",
            revision="A", lifecycle="Active",
            primary_mpn="MPN1", primary_manufacturer="Mfr1",
        )
        assert p.arena_guid == "G1"
        assert p.lifecycle == "Active"


class TestArenaAPIError:
    def test_error_message(self):
        e = ArenaAPIError("test error", status_code=404, arena_guid="G1")
        assert str(e) == "test error"
        assert e.status_code == 404
        assert e.arena_guid == "G1"

    def test_default_values(self):
        e = ArenaAPIError("msg")
        assert e.status_code == 0
        assert e.arena_guid == ""


class TestLifecycleMap:
    def test_all_mappings(self):
        assert LIFECYCLE_MAP["DESIGN"] == "Development"
        assert LIFECYCLE_MAP["Design"] == "Development"
        assert LIFECYCLE_MAP["PRODUCTION"] == "Active"
        assert LIFECYCLE_MAP["Production"] == "Active"
        assert LIFECYCLE_MAP["OBSOLETE"] == "Obsolete"
        assert LIFECYCLE_MAP["Obsolete"] == "Obsolete"
        assert LIFECYCLE_MAP["UNRELEASED"] == "NPI"
        assert LIFECYCLE_MAP["Unreleased"] == "NPI"


class TestForbiddenFields:
    def test_forbidden_set(self):
        assert "lifecyclePhase" in FORBIDDEN_WRITE_FIELDS
        assert "revisionNumber" in FORBIDDEN_WRITE_FIELDS
        assert "workflowStatus" in FORBIDDEN_WRITE_FIELDS
        assert "description" not in FORBIDDEN_WRITE_FIELDS
