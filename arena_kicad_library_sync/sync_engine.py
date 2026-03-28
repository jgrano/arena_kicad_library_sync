"""
Bidirectional sync engine for Arena PLM <-> KiCad library.

Orchestrates:
- Pull: Arena -> KiCad (full and delta)
- Push: KiCad -> Arena (dirty parts and new parts)
- Conflict detection and resolution
- SyncClient interface for deployment-mode abstraction
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from .arena_client import ArenaAPI, ConflictResult, KiCadPart, detect_conflict
from .config import Config
from .kicad_db import KiCadLibraryDB

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[int, int, str], None]]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PullResult:
    """Result of a pull (Arena -> KiCad) operation."""
    added: int = 0
    updated: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)
    duration_sec: float = 0.0


@dataclass
class PushResult:
    """Result of a push (KiCad -> Arena) operation."""
    created: int = 0
    updated: int = 0
    conflicts: list[ConflictResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_sec: float = 0.0


# ---------------------------------------------------------------------------
# Sync Engine
# ---------------------------------------------------------------------------

class SyncEngine:
    """Bidirectional sync engine between Arena PLM and local KiCad library."""

    def __init__(self, arena_api: ArenaAPI, db: KiCadLibraryDB, config: Config):
        self.arena = arena_api
        self.db = db
        self.config = config
        self._pending_conflicts: list[ConflictResult] = []

    # -- Pull (Arena -> KiCad) ----------------------------------------------

    def pull_full(self, progress_callback: ProgressCallback = None) -> PullResult:
        """Full pull: fetch all Arena items and sync to local DB."""
        start = time.time()
        result = PullResult()
        field_map = self.config.get_field_map("arena_to_kicad")
        cat_defaults = self.config.get_category_defaults()

        try:
            # Paginate through all items
            all_guids: set[str] = set()
            offset = 0
            limit = 400
            total_fetched = 0

            while True:
                data = self.arena.get_items(page=offset // limit, limit=limit)
                items = data.get("results", [])
                if not items:
                    break

                for i, raw_item in enumerate(items):
                    try:
                        part = self.arena.normalize_item(raw_item, field_map, cat_defaults)
                        part = self.arena.enrich_with_sourcing(part)
                        guid = part.arena_guid
                        all_guids.add(guid)

                        existing = self.db.get_part(guid)
                        part_dict = _part_to_dict(part)

                        self.db.upsert_part(part_dict, source="arena")
                        self.db.log_sync_event(
                            direction="arena_to_kicad",
                            arena_guid=guid,
                            operation="update" if existing else "insert",
                        )

                        if existing:
                            result.updated += 1
                        else:
                            result.added += 1

                        total_fetched += 1
                        if progress_callback:
                            progress_callback(total_fetched, 0, part.arena_number)

                    except Exception as e:
                        err = f"Failed to sync {raw_item.get('number', '?')}: {e}"
                        result.errors.append(err)
                        logger.error(err)

                offset += limit
                if len(items) < limit:
                    break

            # Delete obsolete parts
            if all_guids:
                deleted = self.db.delete_obsolete(all_guids)
                result.deleted = deleted

            self.db.set_last_sync_time("arena_to_kicad")

        except Exception as e:
            result.errors.append(f"Pull failed: {e}")
            logger.error("Full pull failed: %s", e)

        result.duration_sec = time.time() - start
        logger.info("Full pull complete: +%d ~%d -%d (%d errors) in %.1fs",
                     result.added, result.updated, result.deleted,
                     len(result.errors), result.duration_sec)
        return result

    def pull_delta(self, progress_callback: ProgressCallback = None) -> PullResult:
        """Delta pull: fetch only items modified since last sync."""
        start = time.time()
        result = PullResult()
        field_map = self.config.get_field_map("arena_to_kicad")
        cat_defaults = self.config.get_category_defaults()

        last_sync = self.db.get_last_sync_time("arena_to_kicad")
        if not last_sync:
            logger.info("No previous sync — falling back to full pull")
            return self.pull_full(progress_callback)

        try:
            modified_items = self.arena.get_modified_since(last_sync)
            total = len(modified_items)
            logger.info("Delta pull: %d items modified since %s", total, last_sync)

            for i, raw_item in enumerate(modified_items):
                try:
                    part = self.arena.normalize_item(raw_item, field_map, cat_defaults)
                    part = self.arena.enrich_with_sourcing(part)
                    guid = part.arena_guid

                    existing = self.db.get_part(guid)

                    # Check for conflict if part is locally dirty
                    if existing and existing.get("local_dirty"):
                        conflicts = detect_conflict(raw_item, existing, last_sync)
                        if conflicts:
                            for c in conflicts:
                                self._pending_conflicts.append(c)
                            self.db.log_sync_event(
                                direction="arena_to_kicad",
                                arena_guid=guid,
                                operation="conflict",
                            )
                            continue

                    part_dict = _part_to_dict(part)
                    self.db.upsert_part(part_dict, source="arena")
                    self.db.log_sync_event(
                        direction="arena_to_kicad",
                        arena_guid=guid,
                        operation="update" if existing else "insert",
                    )

                    if existing:
                        result.updated += 1
                    else:
                        result.added += 1

                    if progress_callback:
                        progress_callback(i + 1, total, part.arena_number)

                except Exception as e:
                    err = f"Failed to sync {raw_item.get('number', '?')}: {e}"
                    result.errors.append(err)
                    logger.error(err)

            self.db.set_last_sync_time("arena_to_kicad")

        except Exception as e:
            result.errors.append(f"Delta pull failed: {e}")
            logger.error("Delta pull failed: %s", e)

        result.duration_sec = time.time() - start
        logger.info("Delta pull complete: +%d ~%d (%d errors) in %.1fs",
                     result.added, result.updated,
                     len(result.errors), result.duration_sec)
        return result

    # -- Push (KiCad -> Arena) ----------------------------------------------

    def push_dirty(self, progress_callback: ProgressCallback = None) -> PushResult:
        """Push locally modified parts back to Arena."""
        start = time.time()
        result = PushResult()

        if not self.arena.client.allow_writes:
            result.errors.append("Write operations not enabled (allow_writes=False)")
            return result

        dirty_parts = self.db.get_dirty_parts()
        total = len(dirty_parts)
        logger.info("Push: %d dirty parts to sync", total)

        strategy = self.config.conflict_strategy
        k2a_map = self.config.get_field_map("kicad_to_arena")

        for i, local in enumerate(dirty_parts):
            guid = local["id"]
            try:
                # Fetch current Arena state for conflict check
                arena_current = self.arena.get_item(guid)
                conflicts = detect_conflict(arena_current, local)

                if conflicts:
                    if strategy == "arena_wins":
                        # Revert local — re-pull from Arena
                        field_map = self.config.get_field_map("arena_to_kicad")
                        part = self.arena.normalize_item(arena_current, field_map)
                        self.db.upsert_part(_part_to_dict(part), source="arena")
                        self.db.log_sync_event(
                            direction="kicad_to_arena", arena_guid=guid,
                            operation="conflict", resolved_by="arena_wins",
                        )
                        logger.info("Conflict on %s — arena wins, reverted local", guid)

                    elif strategy == "kicad_wins":
                        # Push local values to Arena
                        self._push_part_to_arena(local, k2a_map)
                        self.db.clear_dirty(guid)
                        self.db.log_sync_event(
                            direction="kicad_to_arena", arena_guid=guid,
                            operation="conflict", resolved_by="kicad_wins",
                        )
                        result.updated += 1
                        logger.info("Conflict on %s — kicad wins, pushed local", guid)

                    else:
                        # prompt_user — add to pending conflicts
                        for c in conflicts:
                            self._pending_conflicts.append(c)
                            result.conflicts.append(c)
                        self.db.log_sync_event(
                            direction="kicad_to_arena", arena_guid=guid,
                            operation="conflict", resolved_by="pending",
                        )
                else:
                    # No conflict — push changes
                    self._push_part_to_arena(local, k2a_map)
                    self.db.clear_dirty(guid)
                    self.db.log_sync_event(
                        direction="kicad_to_arena", arena_guid=guid,
                        operation="update",
                    )
                    result.updated += 1

                if progress_callback:
                    progress_callback(i + 1, total, local.get("arena_number", ""))

            except Exception as e:
                err = f"Failed to push {local.get('arena_number', guid)}: {e}"
                result.errors.append(err)
                logger.error(err)

        self.db.set_last_sync_time("kicad_to_arena")
        result.duration_sec = time.time() - start
        logger.info("Push complete: %d updated, %d conflicts, %d errors in %.1fs",
                     result.updated, len(result.conflicts),
                     len(result.errors), result.duration_sec)
        return result

    def push_new_parts(self, parts: list[dict],
                       progress_callback: ProgressCallback = None) -> PushResult:
        """Create new items in Arena for parts that have no Arena GUID."""
        start = time.time()
        result = PushResult()

        if not self.arena.client.allow_writes:
            result.errors.append("Write operations not enabled")
            return result

        total = len(parts)
        for i, part_data in enumerate(parts):
            try:
                arena_result = self.arena.create_item(part_data)
                guid = arena_result.get("guid", "")
                number = arena_result.get("number", "")

                # Update local DB with Arena GUID
                if guid:
                    part_data["id"] = guid
                    part_data["arena_guid"] = guid
                    part_data["arena_number"] = number
                    self.db.upsert_part(part_data, source="arena")
                    self.db.log_sync_event(
                        direction="kicad_to_arena", arena_guid=guid,
                        operation="insert",
                    )
                    result.created += 1

                if progress_callback:
                    progress_callback(i + 1, total, number)

            except Exception as e:
                err = f"Failed to create item: {e}"
                result.errors.append(err)
                logger.error(err)

        result.duration_sec = time.time() - start
        return result

    def push_schematic(self, sch_path: str,
                       progress_callback: ProgressCallback = None) -> PushResult:
        """Read a schematic, diff against DB, and push changes to Arena."""
        from .kicad_reader import read_schematic, diff_against_db

        components = read_schematic(sch_path)
        diff = diff_against_db(components, self.db)

        # Mark modified parts as dirty
        for comp, changes in diff.modified_parts:
            part = self.db.get_part_by_number(comp.mpn)
            if part:
                self.db.mark_dirty(part["id"])

        # Push dirty parts
        return self.push_dirty(progress_callback)

    def _push_part_to_arena(self, local_part: dict,
                            field_mappings: dict[str, str]) -> None:
        """Push a single local part's changes to Arena."""
        guid = local_part["id"]

        for kicad_field, arena_field in field_mappings.items():
            local_value = local_part.get(kicad_field.lower(), "")
            if not local_value:
                # Check custom_fields
                import json
                custom = local_part.get("custom_fields", "{}")
                if isinstance(custom, str):
                    custom = json.loads(custom)
                local_value = custom.get(kicad_field, "")

            if local_value:
                try:
                    self.arena.update_item_attribute(guid, arena_field, local_value)
                except Exception as e:
                    logger.warning("Failed to update %s.%s: %s", guid, arena_field, e)

    # -- Conflict resolution ------------------------------------------------

    def get_pending_conflicts(self) -> list[ConflictResult]:
        """Get all unresolved conflicts."""
        return [c for c in self._pending_conflicts if c.resolution is None]

    def resolve_conflict(self, conflict: ConflictResult, resolution: str) -> None:
        """Resolve a specific conflict.

        Args:
            conflict: The ConflictResult to resolve
            resolution: "arena_wins" or "kicad_wins"
        """
        conflict.resolution = resolution

        if resolution == "kicad_wins":
            # Push local value to Arena
            try:
                self.arena.update_item_attribute(
                    conflict.arena_guid, conflict.field_name, conflict.local_value
                )
            except Exception as e:
                logger.error("Failed to push resolution: %s", e)
                return
        elif resolution == "arena_wins":
            # Revert local DB
            part = self.db.get_part(conflict.arena_guid)
            if part:
                self.db.clear_dirty(conflict.arena_guid)

        self.db.log_sync_event(
            direction="kicad_to_arena",
            arena_guid=conflict.arena_guid,
            operation="conflict",
            field_changed=conflict.field_name,
            old_value=conflict.local_value if resolution == "arena_wins" else conflict.arena_value,
            new_value=conflict.arena_value if resolution == "arena_wins" else conflict.local_value,
            resolved_by=resolution,
        )

    def resolve_conflict_by_guid(self, arena_guid: str, field_name: str,
                                  resolution: str) -> None:
        """Resolve a conflict by GUID and field name."""
        for c in self._pending_conflicts:
            if c.arena_guid == arena_guid and c.field_name == field_name:
                self.resolve_conflict(c, resolution)
                return
        raise ValueError(f"No pending conflict for {arena_guid}/{field_name}")

    def auto_resolve_all(self, strategy: str) -> int:
        """Auto-resolve all pending conflicts with given strategy.

        Returns number of conflicts resolved.
        """
        pending = self.get_pending_conflicts()
        for c in pending:
            self.resolve_conflict(c, strategy)
        return len(pending)


# ---------------------------------------------------------------------------
# SyncClient interface (deployment-mode abstraction)
# ---------------------------------------------------------------------------

class SyncClient(ABC):
    """Abstract interface for sync operations.

    UI dialogs use this interface — completely agnostic to deployment mode.
    """

    @abstractmethod
    def trigger_pull(self, mode: str = "delta") -> PullResult:
        ...

    @abstractmethod
    def trigger_push(self) -> PushResult:
        ...

    @abstractmethod
    def trigger_bidirectional(self, mode: str = "delta") -> dict:
        ...

    @abstractmethod
    def get_status(self) -> dict:
        ...

    @abstractmethod
    def get_conflicts(self) -> list[ConflictResult]:
        ...

    @abstractmethod
    def resolve_conflict(self, arena_guid: str, field_name: str,
                         resolution: str) -> None:
        ...

    @abstractmethod
    def subscribe_progress(self, callback: Callable[[int, int, str], None]) -> None:
        ...


class LocalSyncClient(SyncClient):
    """SyncClient that calls the SyncEngine directly in-process."""

    def __init__(self, engine: SyncEngine):
        self.engine = engine
        self._progress_callback: ProgressCallback = None

    def trigger_pull(self, mode: str = "delta") -> PullResult:
        cb = self._progress_callback
        if mode == "full":
            return self.engine.pull_full(progress_callback=cb)
        return self.engine.pull_delta(progress_callback=cb)

    def trigger_push(self) -> PushResult:
        return self.engine.push_dirty(progress_callback=self._progress_callback)

    def trigger_bidirectional(self, mode: str = "delta") -> dict:
        pull = self.trigger_pull(mode)
        push = self.trigger_push()
        return {"pull": pull, "push": push}

    def get_status(self) -> dict:
        db = self.engine.db
        return {
            "last_pull": db.get_last_sync_time("arena_to_kicad"),
            "last_push": db.get_last_sync_time("kicad_to_arena"),
            "total_parts": db.get_part_count(),
            "dirty_parts": db.get_dirty_count(),
        }

    def get_conflicts(self) -> list[ConflictResult]:
        return self.engine.get_pending_conflicts()

    def resolve_conflict(self, arena_guid: str, field_name: str,
                         resolution: str) -> None:
        self.engine.resolve_conflict_by_guid(arena_guid, field_name, resolution)

    def subscribe_progress(self, callback: Callable[[int, int, str], None]) -> None:
        self._progress_callback = callback


class ServerSyncClient(SyncClient):
    """SyncClient that calls the middleware REST API."""

    def __init__(self, server_url: str, api_key: str = ""):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self._progress_callback: ProgressCallback = None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _get(self, path: str) -> dict:
        import requests
        resp = requests.get(f"{self.server_url}{path}",
                            headers=self._headers(), timeout=300)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: Optional[dict] = None) -> dict:
        import requests
        resp = requests.post(f"{self.server_url}{path}",
                             json=body or {}, headers=self._headers(), timeout=300)
        resp.raise_for_status()
        return resp.json()

    def trigger_pull(self, mode: str = "delta") -> PullResult:
        data = self._post("/v1/sync/pull", {"mode": mode})
        return PullResult(
            added=data.get("added", 0),
            updated=data.get("updated", 0),
            deleted=data.get("deleted", 0),
            errors=data.get("errors", []),
            duration_sec=data.get("duration_sec", 0),
        )

    def trigger_push(self) -> PushResult:
        data = self._post("/v1/sync/push")
        return PushResult(
            created=data.get("created", 0),
            updated=data.get("updated", 0),
            errors=data.get("errors", []),
            duration_sec=data.get("duration_sec", 0),
        )

    def trigger_bidirectional(self, mode: str = "delta") -> dict:
        return self._post("/v1/sync/bidirectional", {"mode": mode})

    def get_status(self) -> dict:
        return self._get("/v1/sync/status")

    def get_conflicts(self) -> list[ConflictResult]:
        data = self._get("/v1/conflicts")
        return [
            ConflictResult(
                arena_guid=c["arena_guid"],
                arena_number=c.get("arena_number", ""),
                field_name=c["field_name"],
                arena_value=c.get("arena_value", ""),
                local_value=c.get("local_value", ""),
                recommended=c.get("recommended", "arena_wins"),
            )
            for c in data
        ]

    def resolve_conflict(self, arena_guid: str, field_name: str,
                         resolution: str) -> None:
        self._post("/v1/conflicts/resolve", {
            "arena_guid": arena_guid,
            "field_name": field_name,
            "resolution": resolution,
        })

    def subscribe_progress(self, callback: Callable[[int, int, str], None]) -> None:
        self._progress_callback = callback
        # TODO: WebSocket subscription to /ws/sync-progress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _part_to_dict(part: KiCadPart) -> dict:
    """Convert a KiCadPart dataclass to a dict for DB upsert."""
    return {
        "id": part.arena_guid,
        "arena_guid": part.arena_guid,
        "arena_number": part.arena_number,
        "description": part.description,
        "category": part.category,
        "revision": part.revision,
        "lifecycle": part.lifecycle,
        "mpn": part.primary_mpn,
        "manufacturer": part.primary_manufacturer,
        "kicad_symbol": part.kicad_symbol,
        "kicad_footprint": part.kicad_footprint,
        "datasheet_url": part.datasheet_url,
        "last_modified_arena": part.last_modified_arena,
        "custom_fields": part.custom_fields,
    }
