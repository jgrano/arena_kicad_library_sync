"""
Shared test fixtures for Arena KiCad Library Sync.
"""


import pytest


# ---------------------------------------------------------------------------
# Sample Arena API responses
# ---------------------------------------------------------------------------

@pytest.fixture
def arena_login_response():
    return {"arenaSessionId": "test-session-id-12345"}


@pytest.fixture
def arena_item_raw():
    """A sample raw Arena item response."""
    return {
        "guid": "GUID-001",
        "number": "830-00042",
        "name": "10k Resistor 0402",
        "description": "Thick film resistor, 10kohm, 1%, 0402",
        "revisionNumber": "A",
        "lifecyclePhase": {"name": "Production"},
        "category": {"guid": "CAT-001", "name": "Resistors"},
        "modifiedDateTime": "2026-03-15T10:30:00Z",
        "additionalAttributes": [
            {"apiName": "kicad_footprint", "value": "Resistor_SMD:R_0402_1005Metric"},
            {"apiName": "kicad_symbol", "value": "Device:R"},
        ],
    }


@pytest.fixture
def arena_item_list(arena_item_raw):
    """A paginated item list response."""
    return {
        "count": 1,
        "results": [arena_item_raw],
    }


@pytest.fixture
def arena_sourcing_response():
    return {
        "count": 2,
        "results": [
            {
                "manufacturer": {"name": "Yageo"},
                "mpn": "RC0402FR-0710KL",
            },
            {
                "manufacturer": {"name": "Samsung"},
                "mpn": "RC1005F1002CS",
            },
        ],
    }


@pytest.fixture
def arena_categories_response():
    return {
        "results": [
            {
                "guid": "CAT-001",
                "name": "Resistors",
                "path": "Components/Passive/Resistors",
                "assignable": True,
                "activated": True,
                "numberFormat": {
                    "guid": "NF-001",
                    "fields": [{"value": "830"}],
                },
                "parentCategory": {"name": "Passive"},
            },
            {
                "guid": "CAT-002",
                "name": "Capacitors",
                "path": "Components/Passive/Capacitors",
                "assignable": True,
                "activated": True,
                "numberFormat": {
                    "guid": "NF-002",
                    "fields": [{"value": "831"}],
                },
                "parentCategory": {"name": "Passive"},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Sample KiCad schematic content
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_kicad_sch_content():
    """Minimal .kicad_sch content with components."""
    return """(kicad_sch (version 20230121) (generator eeschema)

  (uuid "root-uuid")

  (title_block
    (title "Test Board")
    (date "2026-01-01")
    (rev "1.0")
    (company "Test Corp")
  )

  (symbol (lib_id "Device:R") (at 100 100 0) (unit 1)
    (uuid "sym-uuid-1")
    (property "Reference" "R1" (at 100 95 0))
    (property "Value" "10k" (at 100 105 0))
    (property "Footprint" "Resistor_SMD:R_0402_1005Metric" (at 100 100 0))
    (property "Datasheet" "https://example.com/10k.pdf" (at 100 100 0))
    (property "MPN" "RC0402FR-0710KL" (at 100 100 0))
    (property "Manufacturer" "Yageo" (at 100 100 0))
  )

  (symbol (lib_id "Device:C") (at 200 100 0) (unit 1)
    (uuid "sym-uuid-2")
    (property "Reference" "C1" (at 200 95 0))
    (property "Value" "100nF" (at 200 105 0))
    (property "Footprint" "Capacitor_SMD:C_0402_1005Metric" (at 200 100 0))
    (property "Datasheet" "" (at 200 100 0))
    (property "MPN" "CL05B104KO5NNNC" (at 200 100 0))
    (property "Manufacturer" "Samsung" (at 200 100 0))
  )

  (symbol (lib_id "power:GND") (at 150 200 0) (unit 1)
    (uuid "sym-uuid-3")
    (property "Reference" "#PWR01" (at 150 200 0))
    (property "Value" "GND" (at 150 210 0))
    (property "Footprint" "" (at 150 200 0))
  )

  (sheet (at 300 100) (size 50 50)
    (property "Sheetname" "SubSheet" (at 300 90 0))
    (property "Sheetfile" "subsheet.kicad_sch" (at 300 200 0))
  )
)"""


@pytest.fixture
def sample_subsheet_content():
    """Minimal sub-sheet content."""
    return """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "sub-uuid")

  (symbol (lib_id "Device:R") (at 50 50 0) (unit 1)
    (uuid "sym-uuid-4")
    (property "Reference" "R2" (at 50 45 0))
    (property "Value" "4.7k" (at 50 55 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 50 50 0))
    (property "Datasheet" "" (at 50 50 0))
    (property "MPN" "RC0603FR-074K7L" (at 50 50 0))
    (property "Manufacturer" "Yageo" (at 50 50 0))
  )
)"""


# ---------------------------------------------------------------------------
# Temp files and database
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db_path(tmp_path):
    """Return a temporary SQLite database path."""
    return str(tmp_path / "test_arena_library.db")


@pytest.fixture
def tmp_db(tmp_db_path):
    """Create and return a connected KiCadLibraryDB."""
    from arena_kicad_library_sync.kicad_db import KiCadLibraryDB
    db = KiCadLibraryDB(tmp_db_path)
    db.connect()
    yield db
    db.close()


@pytest.fixture
def sample_kicad_part():
    """A sample KiCadPart object."""
    from arena_kicad_library_sync.arena_client import KiCadPart
    return KiCadPart(
        arena_guid="GUID-001",
        arena_number="830-00042",
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


@pytest.fixture
def sample_sch_file(tmp_path, sample_kicad_sch_content, sample_subsheet_content):
    """Create a temp .kicad_sch file with sub-sheet."""
    sch_path = tmp_path / "test.kicad_sch"
    sch_path.write_text(sample_kicad_sch_content)

    sub_path = tmp_path / "subsheet.kicad_sch"
    sub_path.write_text(sample_subsheet_content)

    return str(sch_path)


@pytest.fixture
def config(tmp_path):
    """Create a Config with temp directory."""
    from arena_kicad_library_sync.config import Config
    return Config(config_dir=tmp_path).load()


@pytest.fixture
def field_mappings():
    """Default field mappings."""
    return {
        "number": "MPN",
        "name": "Description",
        "revisionNumber": "Revision",
        "lifecyclePhase": "Lifecycle",
        "category.name": "Category",
    }
