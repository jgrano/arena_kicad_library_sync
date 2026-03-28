"""Tests for KiCad schematic S-expression reader."""
from arena_kicad_library_sync.kicad_reader import (
    read_schematic, read_project_bom, parse_sexp,
    get_title_block_info, KiCadComponent, _tokenize,
)


class TestSExpParser:
    def test_parse_basic(self):
        """Parse (a b c) -> [['a', 'b', 'c']]"""
        result = parse_sexp("(a b c)")
        assert result == [["a", "b", "c"]]

    def test_parse_nested(self):
        """Parse (a (b c) d) -> [['a', ['b', 'c'], 'd']]"""
        result = parse_sexp("(a (b c) d)")
        assert result == [["a", ["b", "c"], "d"]]

    def test_parse_quoted_strings(self):
        """Parse (a "hello world") -> [['a', 'hello world']]"""
        result = parse_sexp('(a "hello world")')
        assert result == [["a", "hello world"]]

    def test_parse_escaped_quotes(self):
        """Parse (a "say \\"hi\\"") correctly handles escaped quotes."""
        result = parse_sexp(r'(a "say \"hi\"")')
        assert result == [["a", 'say "hi"']]

    def test_parse_empty_parens(self):
        """Parse () -> [[]]"""
        result = parse_sexp("()")
        assert result == [[]]

    def test_tokenize(self):
        """Verify tokenizer output for a simple expression."""
        tokens = _tokenize('(symbol "R1" (property "Value" "10k"))')
        assert tokens == ["(", "symbol", "R1", "(", "property", "Value", "10k", ")", ")"]

    def test_parse_multiple_top_level(self):
        """Parse two top-level expressions."""
        result = parse_sexp("(a b) (c d)")
        assert result == [["a", "b"], ["c", "d"]]

    def test_parse_kicad_property(self):
        """Parse a realistic KiCad property node."""
        result = parse_sexp('(property "Reference" "R1" (at 100 95 0))')
        assert result == [["property", "Reference", "R1", ["at", "100", "95", "0"]]]


class TestReadSchematic:
    def test_extract_components(self, sample_sch_file):
        """Read sample .kicad_sch and verify correct components extracted."""
        components = read_schematic(sample_sch_file)
        refs = [c.reference for c in components]
        assert "R1" in refs
        assert "C1" in refs
        assert "R2" in refs  # from subsheet
        # Power symbols excluded
        assert "#PWR01" not in refs

    def test_skips_power_symbols(self, sample_sch_file):
        """Verify power symbols (GND, #PWR01) are excluded."""
        components = read_schematic(sample_sch_file)
        for comp in components:
            assert not comp.reference.startswith("#"), (
                f"Power symbol {comp.reference} should be excluded"
            )
            assert comp.value != "GND", "GND power symbol should be excluded"

    def test_extracts_properties(self, sample_sch_file):
        """Verify MPN, Manufacturer, Footprint extracted correctly."""
        components = read_schematic(sample_sch_file)
        by_ref = {c.reference: c for c in components}

        r1 = by_ref["R1"]
        assert r1.mpn == "RC0402FR-0710KL"
        assert r1.manufacturer == "Yageo"
        assert r1.footprint == "Resistor_SMD:R_0402_1005Metric"
        assert r1.value == "10k"
        assert r1.datasheet == "https://example.com/10k.pdf"

        c1 = by_ref["C1"]
        assert c1.mpn == "CL05B104KO5NNNC"
        assert c1.manufacturer == "Samsung"
        assert c1.footprint == "Capacitor_SMD:C_0402_1005Metric"
        assert c1.value == "100nF"

    def test_follows_hierarchical_sheets(self, sample_sch_file):
        """Verify sub-sheet components (R2) are included."""
        components = read_schematic(sample_sch_file)
        by_ref = {c.reference: c for c in components}
        assert "R2" in by_ref
        r2 = by_ref["R2"]
        assert r2.value == "4.7k"
        assert r2.mpn == "RC0603FR-074K7L"
        assert r2.footprint == "Resistor_SMD:R_0603_1608Metric"

    def test_nonexistent_file(self):
        """Verify empty list returned for nonexistent file."""
        result = read_schematic("/nonexistent/path/fake.kicad_sch")
        assert result == []

    def test_component_count(self, sample_sch_file):
        """Verify exactly 3 real components (R1, C1, R2) extracted."""
        components = read_schematic(sample_sch_file)
        assert len(components) == 3


class TestReadProjectBom:
    def test_deduplication(self, tmp_path, sample_kicad_sch_content):
        """Verify BOM deduplicates by MPN."""
        # Create a schematic where two components share the same MPN
        content = """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "dup-uuid")

  (symbol (lib_id "Device:R") (at 100 100 0) (unit 1)
    (uuid "dup-sym-1")
    (property "Reference" "R1" (at 100 95 0))
    (property "Value" "10k" (at 100 105 0))
    (property "Footprint" "Resistor_SMD:R_0402_1005Metric" (at 100 100 0))
    (property "Datasheet" "" (at 100 100 0))
    (property "MPN" "RC0402FR-0710KL" (at 100 100 0))
    (property "Manufacturer" "Yageo" (at 100 100 0))
  )

  (symbol (lib_id "Device:R") (at 200 100 0) (unit 1)
    (uuid "dup-sym-2")
    (property "Reference" "R2" (at 200 95 0))
    (property "Value" "10k" (at 200 105 0))
    (property "Footprint" "Resistor_SMD:R_0402_1005Metric" (at 200 100 0))
    (property "Datasheet" "" (at 200 100 0))
    (property "MPN" "RC0402FR-0710KL" (at 200 100 0))
    (property "Manufacturer" "Yageo" (at 200 100 0))
  )

  (symbol (lib_id "Device:C") (at 300 100 0) (unit 1)
    (uuid "dup-sym-3")
    (property "Reference" "C1" (at 300 95 0))
    (property "Value" "100nF" (at 300 105 0))
    (property "Footprint" "Capacitor_SMD:C_0402_1005Metric" (at 300 100 0))
    (property "Datasheet" "" (at 300 100 0))
    (property "MPN" "CL05B104KO5NNNC" (at 300 100 0))
    (property "Manufacturer" "Samsung" (at 300 100 0))
  )
)"""
        sch_path = tmp_path / "dedup_test.kicad_sch"
        sch_path.write_text(content)

        # Also create a matching .kicad_pro so read_project_bom finds the root
        pro_path = tmp_path / "dedup_test.kicad_pro"
        pro_path.write_text("{}")

        bom = read_project_bom(str(tmp_path))
        mpns = [c.mpn for c in bom]
        # Two R's with same MPN should collapse to one
        assert mpns.count("RC0402FR-0710KL") == 1
        assert mpns.count("CL05B104KO5NNNC") == 1
        assert len(bom) == 2

    def test_dnp_excluded(self, tmp_path):
        """Verify DNP components are excluded from BOM."""
        content = """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "dnp-uuid")

  (symbol (lib_id "Device:R") (at 100 100 0) (unit 1) dnp
    (uuid "dnp-sym-1")
    (property "Reference" "R1" (at 100 95 0))
    (property "Value" "10k" (at 100 105 0))
    (property "Footprint" "Resistor_SMD:R_0402_1005Metric" (at 100 100 0))
    (property "Datasheet" "" (at 100 100 0))
    (property "MPN" "RC0402FR-0710KL" (at 100 100 0))
  )

  (symbol (lib_id "Device:C") (at 200 100 0) (unit 1)
    (uuid "dnp-sym-2")
    (property "Reference" "C1" (at 200 95 0))
    (property "Value" "100nF" (at 200 105 0))
    (property "Footprint" "Capacitor_SMD:C_0402_1005Metric" (at 200 100 0))
    (property "Datasheet" "" (at 200 100 0))
    (property "MPN" "CL05B104KO5NNNC" (at 200 100 0))
  )
)"""
        sch_path = tmp_path / "dnp_test.kicad_sch"
        sch_path.write_text(content)
        pro_path = tmp_path / "dnp_test.kicad_pro"
        pro_path.write_text("{}")

        bom = read_project_bom(str(tmp_path))
        refs = [c.reference for c in bom]
        assert "R1" not in refs  # DNP excluded
        assert "C1" in refs
        assert len(bom) == 1


class TestTitleBlock:
    def test_parse_title_block(self, sample_sch_file):
        """Verify title block fields extracted."""
        info = get_title_block_info(sample_sch_file)
        assert info["title"] == "Test Board"
        assert info["rev"] == "1.0"
        assert info["date"] == "2026-01-01"
        assert info["company"] == "Test Corp"

    def test_title_block_nonexistent_file(self):
        """Verify empty dict returned for nonexistent file."""
        info = get_title_block_info("/nonexistent/path/fake.kicad_sch")
        assert info["title"] == ""
        assert info["rev"] == ""
        assert info["date"] == ""
        assert info["company"] == ""

    def test_title_block_empty_string(self):
        """Verify empty dict returned for empty string path."""
        info = get_title_block_info("")
        assert info["title"] == ""


class TestKiCadComponent:
    def test_is_power_symbol_hash_ref(self):
        """Components with # prefix references are power symbols."""
        comp = KiCadComponent(reference="#PWR01", value="GND", lib_id="power:GND")
        assert comp.is_power_symbol() is True

    def test_is_power_symbol_by_value(self):
        """Components with power symbol values are detected."""
        comp = KiCadComponent(reference="U1", value="VCC", lib_id="some:lib")
        assert comp.is_power_symbol() is True

    def test_is_power_symbol_by_lib(self):
        """Components from power library are detected."""
        comp = KiCadComponent(reference="U1", value="custom", lib_id="power:GND")
        assert comp.is_power_symbol() is True

    def test_not_power_symbol(self):
        """Regular components are not flagged as power symbols."""
        comp = KiCadComponent(reference="R1", value="10k", lib_id="Device:R")
        assert comp.is_power_symbol() is False

    def test_mpn_property(self):
        """MPN property accessor works."""
        comp = KiCadComponent(properties={"MPN": "ABC123"})
        assert comp.mpn == "ABC123"

    def test_mpn_missing(self):
        """MPN returns empty string when not set."""
        comp = KiCadComponent()
        assert comp.mpn == ""

    def test_description_fallback(self):
        """Description falls back to value when not in properties."""
        comp = KiCadComponent(value="10k")
        assert comp.description == "10k"

    def test_description_from_properties(self):
        """Description uses property when available."""
        comp = KiCadComponent(value="10k", properties={"Description": "Resistor 10k"})
        assert comp.description == "Resistor 10k"
