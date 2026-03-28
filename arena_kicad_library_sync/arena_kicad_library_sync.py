"""
KiCad Action Plugin entry point for Arena PLM Library Sync.

Registers a toolbar button in KiCad's PCB Editor that opens
the bidirectional library sync dialog.
"""

import logging
import os

logger = logging.getLogger(__name__)


try:
    import pcbnew

    class ArenaKiCadLibrarySyncPlugin(pcbnew.ActionPlugin):
        """KiCad Action Plugin for Arena PLM Library Sync."""

        def defaults(self):
            self.name = "Arena PLM Library Sync"
            self.category = "Library Management"
            self.description = (
                "Bidirectional library sync between KiCad and Arena PLM"
            )
            self.show_toolbar_button = True
            icon_path = os.path.join(os.path.dirname(__file__), "resources", "icon.png")
            if os.path.exists(icon_path):
                self.icon_file_name = icon_path
                self.dark_icon_file_name = icon_path

        def Run(self):
            """Called when the user clicks the plugin button."""
            from .config import Config

            # Load config — the dialog handles connection inline
            config = Config().load()

            from .ui.sync_dialog import SyncDialog
            dlg = SyncDialog(None, config=config)
            dlg.ShowModal()
            dlg.Destroy()

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
