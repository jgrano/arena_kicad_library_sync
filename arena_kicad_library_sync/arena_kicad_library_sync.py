"""
KiCad Action Plugin entry point for Arena PLM Library Sync.

Registers a toolbar button in KiCad's PCB Editor that opens
the bidirectional library sync dialog.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)


try:
    import pcbnew

    class ArenaKiCadLibrarySyncPlugin(pcbnew.ActionPlugin):
        """KiCad Action Plugin for Arena PLM Library Sync."""

        def defaults(self):
            self.name = "Arena PLM Library Sync"
            self.category = "Library Management"
            self.description = (
                "Bidirectional library sync between KiCad and Arena PLM — "
                "supports HTTP lib, DB lib, and server middleware deployment"
            )
            self.show_toolbar_button = True
            icon_path = os.path.join(os.path.dirname(__file__), "resources", "icon.png")
            if os.path.exists(icon_path):
                self.icon_file_name = icon_path
                self.dark_icon_file_name = icon_path

        def Run(self):
            """Called when the user clicks the plugin button."""
            import wx
            from .config import Config
            from .sync_engine import LocalSyncClient, ServerSyncClient, SyncEngine
            from .kicad_db import KiCadLibraryDB
            from .arena_client import ArenaClient, ArenaAPI

            # Load config
            config = Config().load()
            mode = config.deployment_mode

            # Create sync client based on deployment mode
            sync_client = None

            if mode in ("server", "both"):
                try:
                    api_key = config.get_server_api_key() or ""
                    sync_client = ServerSyncClient(config.server_url, api_key)
                    # Test connection
                    sync_client.get_status()
                    logger.info("Connected to sync server at %s", config.server_url)
                except Exception as e:
                    logger.warning("Server connection failed: %s", e)
                    if mode == "server":
                        wx.MessageBox(
                            f"Cannot connect to sync server at {config.server_url}.\n\n"
                            f"Error: {e}\n\n"
                            "Check server URL in settings.",
                            "Arena PLM Library Sync",
                            wx.OK | wx.ICON_WARNING,
                        )
                        return
                    sync_client = None

            if sync_client is None and mode in ("local", "both"):
                try:
                    db = KiCadLibraryDB(config.db_path).connect()
                    client = ArenaClient(
                        base_url=config.arena_api_url,
                        allow_writes=(config.sync_direction != "arena_to_kicad"),
                    )

                    # Try to login with stored credentials
                    email = config.arena_email
                    password = config.get_arena_password()
                    arena_api = None

                    if email and password:
                        try:
                            client.login(email, password, config.arena_workspace_id)
                            arena_api = ArenaAPI(client)
                        except Exception as e:
                            logger.warning("Arena login failed: %s", e)

                    if arena_api:
                        engine = SyncEngine(arena_api, db, config)
                        sync_client = LocalSyncClient(engine)

                        # Auto delta pull if interval elapsed
                        _maybe_auto_sync(engine, config)
                    else:
                        # Open settings dialog for initial setup
                        sync_client = None

                except Exception as e:
                    logger.error("Local mode init failed: %s", e)

            # Open the main sync dialog
            from .ui.sync_dialog import SyncDialog
            dlg = SyncDialog(None, sync_client=sync_client, config=config)
            dlg.ShowModal()
            dlg.Destroy()

        # -- HTTP lib server management ---

        _httplib_server_thread = None
        _httplib_server = None

        @classmethod
        def start_httplib_server(cls, config):
            """Start the HTTP lib server in a daemon thread."""
            if cls._httplib_server_thread and cls._httplib_server_thread.is_alive():
                return

            port = config.httplib_port

            def _run_server():
                try:
                    import uvicorn
                    from .server.middleware_server import create_app
                    app = create_app()
                    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
                except Exception as e:
                    logger.error("HTTP lib server failed: %s", e)

            cls._httplib_server_thread = threading.Thread(
                target=_run_server, daemon=True, name="httplib-server"
            )
            cls._httplib_server_thread.start()
            logger.info("HTTP lib server started on port %d", port)

        @classmethod
        def generate_httplib_file(cls, config):
            """Generate a .kicad_httplib file pointing at the local server."""
            lib_path = config.kicad_library_path
            if not lib_path:
                return

            httplib_path = os.path.join(lib_path, "Arena_PLM.kicad_httplib")
            if os.path.exists(httplib_path):
                return

            import json
            httplib = {
                "meta": {"version": 1},
                "name": "Arena PLM Library",
                "description": "Components synced from Arena PLM",
                "source": {
                    "type": "REST_API",
                    "api_version": "v1",
                    "root_url": f"http://127.0.0.1:{config.httplib_port}",
                },
            }

            os.makedirs(lib_path, exist_ok=True)
            with open(httplib_path, "w", encoding="utf-8") as f:
                json.dump(httplib, f, indent=2)
            logger.info("Generated .kicad_httplib at %s", httplib_path)

except ImportError:
    # pcbnew not available — provide a stub for non-KiCad environments
    class ArenaKiCadLibrarySyncPlugin:
        """Stub plugin class for non-KiCad environments."""

        def register(self):
            pass

        def defaults(self):
            pass

        def Run(self):
            raise RuntimeError("This plugin requires KiCad (pcbnew)")


def _maybe_auto_sync(engine, config):
    """Run a background delta pull if the sync interval has elapsed."""
    from datetime import datetime, timezone

    interval = config.sync_interval_hours
    if interval <= 0:
        return

    last_sync = engine.db.get_last_sync_time("arena_to_kicad")
    if last_sync:
        try:
            last_dt = datetime.fromisoformat(last_sync)
            now = datetime.now(timezone.utc)
            hours_since = (now - last_dt).total_seconds() / 3600
            if hours_since < interval:
                return
        except Exception:
            pass

    def _bg_sync():
        try:
            result = engine.pull_delta()
            logger.info("Background sync: +%d ~%d -%d",
                         result.added, result.updated, result.deleted)
        except Exception as e:
            logger.error("Background sync failed: %s", e)

    t = threading.Thread(target=_bg_sync, daemon=True, name="auto-sync")
    t.start()
