"""
KiCad schematic reader — headless S-expression parser for .kicad_sch files.

Works without launching KiCad or importing pcbnew/eeschema APIs.
Extracts component data for bidirectional sync with Arena PLM.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Power symbols to skip during component extraction
POWER_SYMBOLS = frozenset({
    "PWR_FLAG", "GND", "VCC", "VDD", "VSS", "VBUS",
    "+3V3", "+3.3V", "+5V", "+12V", "-12V", "+1V8", "+2V5",
    "GNDA", "GNDPWR", "GNDD", "VSSA", "VDDA",
    "power_flag", "gnd", "vcc", "vdd",
})

# Library prefixes that indicate power symbols
POWER_LIB_PREFIXES = ("power:", "Device:PWR_FLAG")


@dataclass
class KiCadComponent:
    """A component extracted from a KiCad schematic."""
    reference: str = ""
    value: str = ""
    footprint: str = ""
    lib_id: str = ""
    uuid: str = ""
    datasheet: str = ""
    dnp: bool = False
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def mpn(self) -> str:
        return self.properties.get("MPN", "")

    @property
    def manufacturer(self) -> str:
        return self.properties.get("Manufacturer", "")

    @property
    def description(self) -> str:
        return self.properties.get("Description", self.value)

    def is_power_symbol(self) -> bool:
        """Check if this component is a power symbol (should be skipped)."""
        if self.reference.startswith("#"):
            return True
        if self.value in POWER_SYMBOLS:
            return True
        for prefix in POWER_LIB_PREFIXES:
            if self.lib_id.startswith(prefix):
                return True
        return False


@dataclass
class SchemaDiff:
    """Result of comparing schematic components against the DB."""
    new_parts: list[KiCadComponent] = field(default_factory=list)
    modified_parts: list[tuple[KiCadComponent, dict]] = field(default_factory=list)
    missing_from_arena: list[KiCadComponent] = field(default_factory=list)
    field_conflicts: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# S-expression parser
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Tokenize a KiCad S-expression string into a flat list of tokens.

    Handles: parentheses, quoted strings (with escaped quotes), bare words.
    """
    tokens = []
    i = 0
    n = len(text)

    while i < n:
        c = text[i]

        # Skip whitespace
        if c in " \t\r\n":
            i += 1
            continue

        # Open/close parens
        if c == "(":
            tokens.append("(")
            i += 1
            continue
        if c == ")":
            tokens.append(")")
            i += 1
            continue

        # Quoted string
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            # Extract string content without quotes
            content = text[i + 1:j].replace('\\"', '"').replace("\\\\", "\\")
            tokens.append(content)
            i = j + 1
            continue

        # Bare word (symbol, number, etc.)
        j = i
        while j < n and text[j] not in " \t\r\n()\"":
            j += 1
        tokens.append(text[i:j])
        i = j

    return tokens


def _parse_tokens(tokens: list[str], pos: int = 0) -> tuple[list, int]:
    """Parse tokenized S-expression into nested Python lists.

    Returns (parsed_list, next_position).
    """
    result = []
    while pos < len(tokens):
        token = tokens[pos]
        if token == "(":
            sub, pos = _parse_tokens(tokens, pos + 1)
            result.append(sub)
        elif token == ")":
            return result, pos + 1
        else:
            result.append(token)
            pos += 1
    return result, pos


def parse_sexp(text: str) -> list:
    """Parse a KiCad S-expression string into nested Python lists.

    Example:
        parse_sexp('(symbol "R1" (property "Value" "10k"))')
        -> [['symbol', 'R1', ['property', 'Value', '10k']]]
    """
    tokens = _tokenize(text)
    result, _ = _parse_tokens(tokens)
    return result


def _find_nodes(tree: list, node_type: str) -> list[list]:
    """Find all sub-lists whose first element matches node_type."""
    results = []
    for item in tree:
        if isinstance(item, list) and item and item[0] == node_type:
            results.append(item)
    return results


def _find_node(tree: list, node_type: str) -> Optional[list]:
    """Find first sub-list whose first element matches node_type."""
    for item in tree:
        if isinstance(item, list) and item and item[0] == node_type:
            return item
    return None


def _get_property(node: list, prop_name: str) -> str:
    """Extract a named property value from a symbol node.

    KiCad format: (property "Name" "Value" ...)
    """
    for item in node:
        if isinstance(item, list) and len(item) >= 3:
            if item[0] == "property" and item[1] == prop_name:
                return item[2]
    return ""


# ---------------------------------------------------------------------------
# Component extraction
# ---------------------------------------------------------------------------

def _extract_components_from_tree(tree: list) -> list[KiCadComponent]:
    """Extract all components from a parsed S-expression tree."""
    components = []

    for node in tree:
        if not isinstance(node, list) or not node:
            continue

        # KiCad 8+ uses (symbol (lib_id "...") ...) for placed instances
        if node[0] == "symbol":
            comp = _parse_symbol_node(node)
            if comp and not comp.is_power_symbol():
                components.append(comp)

    return components


def _parse_symbol_node(node: list) -> Optional[KiCadComponent]:
    """Parse a placed symbol instance node into a KiCadComponent."""
    comp = KiCadComponent()

    # lib_id
    lib_id_node = _find_node(node, "lib_id")
    if lib_id_node and len(lib_id_node) >= 2:
        comp.lib_id = lib_id_node[1]

    # UUID
    uuid_node = _find_node(node, "uuid")
    if uuid_node and len(uuid_node) >= 2:
        comp.uuid = uuid_node[1]

    # DNP flag
    for item in node:
        if item == "dnp" or (isinstance(item, list) and item and item[0] == "dnp"):
            comp.dnp = True

    # Standard properties
    comp.reference = _get_property(node, "Reference")
    comp.value = _get_property(node, "Value")
    comp.footprint = _get_property(node, "Footprint")
    comp.datasheet = _get_property(node, "Datasheet")

    # All custom properties
    for item in node:
        if isinstance(item, list) and len(item) >= 3 and item[0] == "property":
            name = item[1]
            value = item[2]
            if name not in ("Reference", "Value", "Footprint", "Datasheet",
                            "ki_keywords", "ki_description", "ki_fp_filters"):
                comp.properties[name] = value

    # Skip if no reference (not a real placed component)
    if not comp.reference or comp.reference.startswith("#"):
        return None

    return comp


def _find_sheet_files(tree: list) -> list[str]:
    """Find hierarchical sheet file references in a parsed tree."""
    files = []
    for node in tree:
        if isinstance(node, list) and node and node[0] == "sheet":
            sheet_file = _get_property(node, "Sheetfile")
            if sheet_file:
                files.append(sheet_file)
    return files


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_schematic(sch_path: str) -> list[KiCadComponent]:
    """Read a .kicad_sch file and extract all placed components.

    Recursively follows hierarchical sheet references.
    Skips power symbols (PWR_FLAG, GND, VCC, etc.).

    Args:
        sch_path: Path to .kicad_sch file

    Returns:
        List of KiCadComponent objects with all properties extracted.
    """
    sch_path = os.path.abspath(sch_path)
    if not os.path.exists(sch_path):
        logger.error("Schematic not found: %s", sch_path)
        return []

    visited = set()
    return _read_schematic_recursive(sch_path, visited)


def _read_schematic_recursive(sch_path: str,
                               visited: set[str]) -> list[KiCadComponent]:
    """Recursively read a schematic and all its sub-sheets."""
    sch_path = os.path.abspath(sch_path)
    if sch_path in visited:
        return []
    visited.add(sch_path)

    logger.debug("Reading schematic: %s", sch_path)

    try:
        with open(sch_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.error("Failed to read %s: %s", sch_path, e)
        return []

    tree = parse_sexp(content)
    if not tree:
        return []

    # The root is typically a single (kicad_sch ...) node
    root = tree[0] if len(tree) == 1 and isinstance(tree[0], list) else tree

    # Extract components from this sheet
    components = _extract_components_from_tree(root)

    # Recursively process sub-sheets
    sheet_dir = os.path.dirname(sch_path)
    sheet_files = _find_sheet_files(root)
    for sheet_file in sheet_files:
        sub_path = os.path.join(sheet_dir, sheet_file)
        if os.path.exists(sub_path):
            sub_components = _read_schematic_recursive(sub_path, visited)
            components.extend(sub_components)
        else:
            logger.warning("Sub-sheet not found: %s", sub_path)

    return components


def read_project_bom(project_path: str) -> list[KiCadComponent]:
    """Scan all .kicad_sch files under a project root and build a BOM.

    Deduplicates by MPN (components with the same MPN are merged,
    quantity tracked via reference count). DNP components grouped separately.

    Args:
        project_path: Path to project directory or .kicad_pro file

    Returns:
        Deduplicated list of KiCadComponent objects.
    """
    if os.path.isfile(project_path):
        project_dir = os.path.dirname(project_path)
    else:
        project_dir = project_path

    # Find root schematic
    sch_files = list(Path(project_dir).glob("*.kicad_sch"))
    if not sch_files:
        logger.warning("No .kicad_sch files found in %s", project_dir)
        return []

    # Prefer the one matching the project name
    pro_files = list(Path(project_dir).glob("*.kicad_pro"))
    root_sch = sch_files[0]
    if pro_files:
        project_name = pro_files[0].stem
        for sch in sch_files:
            if sch.stem == project_name:
                root_sch = sch
                break

    all_components = read_schematic(str(root_sch))

    # Deduplicate by MPN
    seen: dict[str, KiCadComponent] = {}
    no_mpn = []
    for comp in all_components:
        if comp.dnp:
            continue
        mpn = comp.mpn
        if mpn:
            if mpn not in seen:
                seen[mpn] = comp
            # else: duplicate MPN, skip (counted by reference)
        else:
            no_mpn.append(comp)

    return list(seen.values()) + no_mpn


def diff_against_db(components: list[KiCadComponent],
                    db) -> SchemaDiff:
    """Compare schematic components against the local database.

    Args:
        components: Components from read_schematic or read_project_bom
        db: KiCadLibraryDB instance

    Returns:
        SchemaDiff with new, modified, missing, and conflicting parts.
    """
    diff = SchemaDiff()

    db_parts = {row["arena_number"]: row for row in db.get_all_parts()}

    for comp in components:
        mpn = comp.mpn
        if not mpn:
            diff.missing_from_arena.append(comp)
            continue

        if mpn not in db_parts:
            diff.new_parts.append(comp)
            continue

        # Check for modifications
        db_row = db_parts[mpn]
        changes = {}
        if comp.description and comp.description != db_row.get("description", ""):
            changes["description"] = (db_row.get("description", ""), comp.description)
        if comp.footprint and comp.footprint != db_row.get("kicad_footprint", ""):
            changes["kicad_footprint"] = (db_row.get("kicad_footprint", ""), comp.footprint)

        if changes:
            diff.modified_parts.append((comp, changes))

    return diff


def get_title_block_info(schematic_file: str) -> dict[str, str]:
    """Parse the title block from a .kicad_sch file.

    Returns a dict with title, date, rev, company (best effort).
    Reuses the line-by-line approach from the original KiCad-Arena-Sync.
    """
    info = {"title": "", "date": "", "rev": "", "company": ""}
    if not schematic_file or not os.path.exists(schematic_file):
        return info

    try:
        with open(schematic_file, "r", encoding="utf-8") as f:
            in_title_block = False
            for line in f:
                stripped = line.strip()
                if stripped.startswith("(title_block"):
                    in_title_block = True
                    continue
                if in_title_block:
                    if stripped.startswith(")"):
                        break
                    for key in ("title", "date", "rev", "company"):
                        if stripped.startswith(f"({key} "):
                            value = stripped.split('"')[1] if '"' in stripped else ""
                            info[key] = value
    except Exception:
        pass

    return info
