"""
SQLite database manager for the KiCad component library.

Manages the local parts database that backs both:
- KiCad Database Library (.kicad_dbl) via ODBC
- KiCad HTTP Library via the FastAPI server

Schema supports bidirectional sync with Arena PLM including
dirty tracking, conflict detection, and audit logging.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS parts (
    id TEXT PRIMARY KEY,
    arena_number TEXT UNIQUE NOT NULL,
    category TEXT DEFAULT '',
    description TEXT DEFAULT '',
    value TEXT DEFAULT '',
    reference TEXT DEFAULT '',
    kicad_symbol TEXT DEFAULT '',
    kicad_footprint TEXT DEFAULT '',
    datasheet TEXT DEFAULT '',
    lifecycle TEXT DEFAULT '',
    revision TEXT DEFAULT '',
    mpn TEXT DEFAULT '',
    manufacturer TEXT DEFAULT '',
    last_modified_arena TEXT,
    last_synced_from_arena TEXT,
    last_synced_to_arena TEXT,
    local_dirty INTEGER DEFAULT 0,
    custom_fields TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,
    arena_guid TEXT,
    operation TEXT NOT NULL,
    field_changed TEXT,
    old_value TEXT,
    new_value TEXT,
    resolved_by TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parts_category ON parts(category);
CREATE INDEX IF NOT EXISTS idx_parts_lifecycle ON parts(lifecycle);
CREATE INDEX IF NOT EXISTS idx_parts_dirty ON parts(local_dirty);
CREATE INDEX IF NOT EXISTS idx_parts_mpn ON parts(mpn);
CREATE INDEX IF NOT EXISTS idx_sync_log_timestamp ON sync_log(timestamp);
"""


class KiCadLibraryDB:
    """SQLite database manager for the KiCad component library."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> "KiCadLibraryDB":
        """Open database connection and ensure schema exists."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        return self

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "KiCadLibraryDB":
        return self.connect()

    def __exit__(self, *args: Any) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    # -- Schema management --------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables if they don't exist, run migrations."""
        self.conn.executescript(SCHEMA_SQL)

        # Check schema version
        row = self.conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()

        if row is None:
            self.conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()
        else:
            current = row[0]
            if current < SCHEMA_VERSION:
                self._migrate(current)

    def _migrate(self, from_version: int) -> None:
        """Run schema migrations from from_version to SCHEMA_VERSION."""
        # Future migrations go here
        self.conn.execute(
            "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
        )
        self.conn.commit()

    # -- Parts CRUD ---------------------------------------------------------

    def upsert_part(self, part: dict, source: str = "arena") -> None:
        """Insert or update a part in the database.

        Args:
            part: Dict with part fields (id/arena_guid, arena_number, etc.)
            source: "arena" or "kicad" — determines which timestamps to set
        """
        now = _now_iso()
        guid = part.get("id") or part.get("arena_guid", "")
        arena_number = part.get("arena_number", "")

        if not guid:
            raise ValueError("Part must have an id or arena_guid")

        custom = part.get("custom_fields", {})
        if isinstance(custom, dict):
            custom = json.dumps(custom)

        existing = self.conn.execute(
            "SELECT id FROM parts WHERE id = ?", (guid,)
        ).fetchone()

        if existing:
            # Update
            fields = {
                "arena_number": arena_number,
                "category": part.get("category", ""),
                "description": part.get("description", ""),
                "value": part.get("value", ""),
                "reference": part.get("reference", ""),
                "kicad_symbol": part.get("kicad_symbol", ""),
                "kicad_footprint": part.get("kicad_footprint", ""),
                "datasheet": part.get("datasheet", part.get("datasheet_url", "")),
                "lifecycle": part.get("lifecycle", ""),
                "revision": part.get("revision", ""),
                "mpn": part.get("mpn", part.get("primary_mpn", "")),
                "manufacturer": part.get("manufacturer", part.get("primary_manufacturer", "")),
                "custom_fields": custom,
            }

            if source == "arena":
                fields["last_modified_arena"] = part.get("last_modified_arena", now)
                fields["last_synced_from_arena"] = now
                fields["local_dirty"] = 0
            else:
                fields["local_dirty"] = 1

            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [guid]
            self.conn.execute(
                f"UPDATE parts SET {set_clause} WHERE id = ?", values
            )
        else:
            # Insert
            self.conn.execute(
                """INSERT INTO parts
                   (id, arena_number, category, description, value, reference,
                    kicad_symbol, kicad_footprint, datasheet, lifecycle, revision,
                    mpn, manufacturer, last_modified_arena,
                    last_synced_from_arena, last_synced_to_arena,
                    local_dirty, custom_fields)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    guid,
                    arena_number,
                    part.get("category", ""),
                    part.get("description", ""),
                    part.get("value", ""),
                    part.get("reference", ""),
                    part.get("kicad_symbol", ""),
                    part.get("kicad_footprint", ""),
                    part.get("datasheet", part.get("datasheet_url", "")),
                    part.get("lifecycle", ""),
                    part.get("revision", ""),
                    part.get("mpn", part.get("primary_mpn", "")),
                    part.get("manufacturer", part.get("primary_manufacturer", "")),
                    part.get("last_modified_arena", now) if source == "arena" else None,
                    now if source == "arena" else None,
                    now if source == "kicad" else None,
                    0 if source == "arena" else 1,
                    custom,
                ),
            )

        self.conn.commit()

    def get_part(self, guid: str) -> Optional[dict]:
        """Get a single part by GUID."""
        row = self.conn.execute(
            "SELECT * FROM parts WHERE id = ?", (guid,)
        ).fetchone()
        return dict(row) if row else None

    def get_part_by_number(self, arena_number: str) -> Optional[dict]:
        """Get a single part by Arena part number."""
        row = self.conn.execute(
            "SELECT * FROM parts WHERE arena_number = ?", (arena_number,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_parts(self) -> list[dict]:
        """Get all parts in the database."""
        rows = self.conn.execute("SELECT * FROM parts ORDER BY arena_number").fetchall()
        return [dict(r) for r in rows]

    def get_parts_by_category(self, category: str) -> list[dict]:
        """Get all parts in a specific category."""
        rows = self.conn.execute(
            "SELECT * FROM parts WHERE category = ? ORDER BY arena_number",
            (category,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_categories(self) -> list[str]:
        """Get distinct category names."""
        rows = self.conn.execute(
            "SELECT DISTINCT category FROM parts WHERE category != '' ORDER BY category"
        ).fetchall()
        return [r[0] for r in rows]

    def get_part_count(self) -> int:
        """Get total number of parts."""
        row = self.conn.execute("SELECT COUNT(*) FROM parts").fetchone()
        return row[0] if row else 0

    # -- Dirty tracking -----------------------------------------------------

    def mark_dirty(self, arena_guid: str) -> None:
        """Mark a part as locally modified (pending push to Arena)."""
        self.conn.execute(
            "UPDATE parts SET local_dirty = 1 WHERE id = ?", (arena_guid,)
        )
        self.conn.commit()

    def get_dirty_parts(self) -> list[dict]:
        """Get all parts with local modifications not yet pushed."""
        rows = self.conn.execute(
            "SELECT * FROM parts WHERE local_dirty = 1 ORDER BY arena_number"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_dirty_count(self) -> int:
        """Get count of dirty parts."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM parts WHERE local_dirty = 1"
        ).fetchone()
        return row[0] if row else 0

    def clear_dirty(self, arena_guid: str) -> None:
        """Clear the dirty flag after successful push to Arena."""
        now = _now_iso()
        self.conn.execute(
            "UPDATE parts SET local_dirty = 0, last_synced_to_arena = ? WHERE id = ?",
            (now, arena_guid),
        )
        self.conn.commit()

    # -- Obsolete cleanup ---------------------------------------------------

    def delete_obsolete(self, active_guids: set[str]) -> int:
        """Remove parts whose GUIDs are not in the active set.

        Returns number of parts deleted.
        """
        all_parts = self.conn.execute("SELECT id FROM parts").fetchall()
        to_delete = [r[0] for r in all_parts if r[0] not in active_guids]

        if to_delete:
            placeholders = ",".join("?" * len(to_delete))
            self.conn.execute(
                f"DELETE FROM parts WHERE id IN ({placeholders})", to_delete
            )
            self.conn.commit()
            logger.info("Deleted %d obsolete parts", len(to_delete))

        return len(to_delete)

    # -- Sync log -----------------------------------------------------------

    def log_sync_event(self, direction: str, arena_guid: str,
                       operation: str, field_changed: str = "",
                       old_value: str = "", new_value: str = "",
                       resolved_by: str = "") -> None:
        """Record a sync event in the audit log."""
        self.conn.execute(
            """INSERT INTO sync_log
               (direction, arena_guid, operation, field_changed,
                old_value, new_value, resolved_by, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (direction, arena_guid, operation, field_changed,
             old_value, new_value, resolved_by, _now_iso()),
        )
        self.conn.commit()

    def get_sync_log(self, limit: int = 100) -> list[dict]:
        """Get recent sync log entries."""
        rows = self.conn.execute(
            "SELECT * FROM sync_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Sync state ---------------------------------------------------------

    def get_last_sync_time(self, direction: str) -> Optional[str]:
        """Get the last successful sync timestamp for a direction."""
        row = self.conn.execute(
            "SELECT value FROM sync_state WHERE key = ?",
            (f"last_sync_{direction}",),
        ).fetchone()
        return row[0] if row else None

    def set_last_sync_time(self, direction: str, dt: Optional[str] = None) -> None:
        """Set the last successful sync timestamp."""
        if dt is None:
            dt = _now_iso()
        self.conn.execute(
            """INSERT INTO sync_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (f"last_sync_{direction}", dt),
        )
        self.conn.commit()

    # -- KiCad DBL export ---------------------------------------------------

    def export_kicad_dbl_config(self, output_path: str) -> None:
        """Generate a .kicad_dbl JSON file pointing at this SQLite database.

        Groups parts by category into separate library entries.
        """
        categories = self.get_categories()
        if not categories:
            categories = ["All"]

        libraries = []
        for cat in categories:
            safe_name = cat.replace(" ", "_").replace("/", "_")
            libraries.append({
                "name": f"Arena_{safe_name}",
                "table": "parts",
                "key": "id",
                "symbols": "kicad_symbol",
                "footprints": "kicad_footprint",
                "fields": [
                    {"column": "arena_number", "name": "Arena PN", "visible_on_add": False, "visible_in_chooser": True},
                    {"column": "value", "name": "Value", "visible_on_add": True, "visible_in_chooser": True},
                    {"column": "description", "name": "Description", "visible_on_add": True, "visible_in_chooser": True},
                    {"column": "mpn", "name": "MPN", "visible_on_add": True, "visible_in_chooser": True},
                    {"column": "manufacturer", "name": "Manufacturer", "visible_on_add": True, "visible_in_chooser": True},
                    {"column": "datasheet", "name": "Datasheet", "visible_on_add": True, "visible_in_chooser": False},
                    {"column": "lifecycle", "name": "Lifecycle", "visible_on_add": False, "visible_in_chooser": True},
                    {"column": "revision", "name": "Revision", "visible_on_add": False, "visible_in_chooser": False},
                    {"column": "category", "name": "Category", "visible_on_add": False, "visible_in_chooser": True},
                ],
                "properties": {
                    "description": "description",
                },
            })

            # Add category filter if not "All"
            if cat != "All":
                libraries[-1]["query"] = f"SELECT * FROM parts WHERE category = '{cat}'"

        dbl = {
            "meta": {"version": 0},
            "name": "Arena PLM Library",
            "description": "Components synced from Arena PLM",
            "source": {
                "type": "odbc",
                "dsn": "",
                "connection_string": f"Driver={{SQLite3}};Database={self.db_path}",
            },
            "libraries": libraries,
        }

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(dbl, f, indent=2)

        logger.info("Exported .kicad_dbl config to %s", output_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()
