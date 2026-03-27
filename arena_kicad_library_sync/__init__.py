"""
Arena KiCad Library Sync — Action Plugin for bidirectional library sync with Arena PLM.
"""

__version__ = "1.0.0"

try:
    from .arena_kicad_library_sync import ArenaKiCadLibrarySyncPlugin
    ArenaKiCadLibrarySyncPlugin().register()
except ImportError:
    # pcbnew not available (running outside KiCad, e.g. server mode or tests)
    pass
