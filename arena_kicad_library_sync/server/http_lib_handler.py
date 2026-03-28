"""
KiCad HTTP Library API endpoints.

Implements the KiCad HTTP lib specification so KiCad can fetch
component data directly from the Arena-synced SQLite database.

Endpoints:
  GET /v1/categories
  GET /v1/parts/{category_id}
  GET /v1/parts/{category_id}/{part_id}
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["httplib"])

# Lifecycle -> color hint for KiCad display
LIFECYCLE_COLORS = {
    "Active": "#00AA00",
    "Development": "#FFAA00",
    "NPI": "#FFAA00",
    "Obsolete": "#FF0000",
    "Unknown": "#888888",
}


def _get_db():
    """Dependency: get the shared KiCadLibraryDB instance.

    This is overridden at app startup via app.dependency_overrides.
    """
    raise RuntimeError("DB dependency not configured")


@router.get("/categories")
def list_categories(db=Depends(_get_db)) -> list[dict[str, str]]:
    """Return all component categories in the library."""
    categories = db.get_categories()
    return [
        {"id": cat, "name": cat, "description": f"Arena PLM — {cat}"}
        for cat in categories
    ]


@router.get("/parts/{category_id}")
def list_parts(category_id: str, db=Depends(_get_db)) -> list[dict[str, Any]]:
    """Return all parts in a category, formatted for KiCad HTTP lib."""
    parts = db.get_parts_by_category(category_id)
    return [_format_part_for_kicad(p) for p in parts]


@router.get("/parts/{category_id}/{part_id}")
def get_part(category_id: str, part_id: str,
             db=Depends(_get_db)) -> dict[str, Any]:
    """Return a single part with all fields."""
    part = db.get_part(part_id)
    if not part:
        # Try by arena_number
        part = db.get_part_by_number(part_id)
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return _format_part_for_kicad(part)


def _format_part_for_kicad(part: dict) -> dict[str, Any]:
    """Format a DB part record for KiCad HTTP lib consumption."""
    lifecycle = part.get("lifecycle", "")

    fields: dict[str, dict[str, Any]] = {
        "Value": {"value": part.get("value", part.get("description", "")), "visible": True},
        "Reference": {"value": part.get("reference", ""), "visible": False},
        "Datasheet": {"value": part.get("datasheet", ""), "visible": False},
        "Arena PN": {"value": part.get("arena_number", ""), "visible": True},
        "MPN": {"value": part.get("mpn", ""), "visible": True},
        "Manufacturer": {"value": part.get("manufacturer", ""), "visible": True},
        "Description": {"value": part.get("description", ""), "visible": True},
        "Lifecycle": {"value": f"{lifecycle}", "visible": True},
        "Revision": {"value": part.get("revision", ""), "visible": False},
        "Category": {"value": part.get("category", ""), "visible": False},
    }

    # Add custom fields
    custom = part.get("custom_fields", "{}")
    if isinstance(custom, str):
        import json
        try:
            custom = json.loads(custom)
        except Exception:
            custom = {}
    for key, value in custom.items():
        if key not in fields:
            fields[key] = {"value": str(value), "visible": False}

    footprints = []
    fp = part.get("kicad_footprint", "")
    if fp:
        footprints = [fp]

    return {
        "id": part.get("id", ""),
        "name": part.get("description", part.get("arena_number", "")),
        "symbolIdStr": part.get("kicad_symbol", ""),
        "footprints": footprints,
        "fields": fields,
    }
