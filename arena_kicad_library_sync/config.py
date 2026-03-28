"""
Configuration management for Arena KiCad Library Sync.

Handles:
- Platform-appropriate config directory (macOS/Windows/Linux)
- JSON settings file with full schema and defaults
- Secure credential storage via keyring (passwords never in JSON)
- Configurable field mappings between Arena and KiCad
"""

import json
import logging
import os
import platform
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

APP_NAME = "arena_kicad_library_sync"
KEYRING_SERVICE = "com.emporiaenergy.arena-kicad-library-sync"

DEFAULT_CONFIG = {
    "arena": {
        "api_url": "https://api.arenasolutions.com/v1",
        "email": "",
        "workspace_id": "",
    },
    "deployment": {
        "mode": "local",
        "server_url": "http://openclaw.local:8765",
        "server_api_key": "",
    },
    "sync": {
        "direction": "arena_to_kicad",
        "mechanism": "both",
        "db_path": "",
        "httplib_port": 8765,
        "sync_interval_hours": 24,
        "last_sync_arena_to_kicad": None,
        "last_sync_kicad_to_arena": None,
        "conflict_strategy": "prompt_user",
    },
    "field_mappings": {
        "arena_to_kicad": {
            "number": "MPN",
            "name": "Description",
            "revisionNumber": "Revision",
            "lifecyclePhase": "Lifecycle",
            "category.name": "Category",
            "sourcing.primaryMPN": "MPN",
            "sourcing.primaryManufacturer": "Manufacturer",
        },
        "kicad_to_arena": {
            "MPN": "number",
            "Description": "name",
            "Footprint": "customAttributes.kicad_footprint",
            "Symbol": "customAttributes.kicad_symbol",
            "Datasheet": "customAttributes.datasheet",
        },
    },
    "kicad_library_path": "",
    "kicad_projects_root": "",
}


def _get_config_dir() -> Path:
    """Return platform-appropriate config directory."""
    try:
        from platformdirs import user_config_dir
        return Path(user_config_dir(APP_NAME))
    except ImportError:
        pass

    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / APP_NAME
    else:
        return Path.home() / ".config" / APP_NAME


def _get_default_db_path() -> str:
    """Return default SQLite DB path."""
    try:
        from platformdirs import user_data_dir
        return str(Path(user_data_dir(APP_NAME)) / "arena_library.db")
    except ImportError:
        pass

    system = platform.system()
    if system == "Darwin":
        return str(Path.home() / "Library" / "Application Support" / APP_NAME / "arena_library.db")
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return str(Path(appdata) / APP_NAME / "arena_library.db")
    else:
        return str(Path.home() / ".local" / "share" / APP_NAME / "arena_library.db")


# ---------------------------------------------------------------------------
# Keyring abstraction — never store passwords in JSON
# ---------------------------------------------------------------------------

def _save_secret(key: str, value: str) -> None:
    """Store a secret in the system keyring."""
    try:
        import keyring as kr
        kr.set_password(KEYRING_SERVICE, key, value)
        return
    except ImportError:
        pass

    # Fallback: macOS Keychain via security CLI
    if platform.system() == "Darwin":
        try:
            subprocess.run(
                ["security", "delete-generic-password", "-s", KEYRING_SERVICE, "-a", key],
                capture_output=True,
            )
            subprocess.run(
                ["security", "add-generic-password",
                 "-s", KEYRING_SERVICE, "-a", key, "-w", value, "-U"],
                capture_output=True, check=True,
            )
            return
        except Exception:
            pass

    # Last resort: base64-encoded file (not secure, but functional)
    import base64
    secrets_file = _get_config_dir() / ".secrets"
    secrets = {}
    if secrets_file.exists():
        try:
            raw = base64.b64decode(secrets_file.read_text()).decode()
            secrets = json.loads(raw)
        except Exception:
            pass
    secrets[key] = value
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    encoded = base64.b64encode(json.dumps(secrets).encode()).decode()
    secrets_file.write_text(encoded)


def _load_secret(key: str) -> Optional[str]:
    """Load a secret from the system keyring."""
    try:
        import keyring as kr
        val = kr.get_password(KEYRING_SERVICE, key)
        if val is not None:
            return val
    except ImportError:
        pass

    # Fallback: macOS Keychain
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password",
                 "-s", KEYRING_SERVICE, "-a", key, "-w"],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

    # Last resort: base64-encoded file
    import base64
    secrets_file = _get_config_dir() / ".secrets"
    if secrets_file.exists():
        try:
            raw = base64.b64decode(secrets_file.read_text()).decode()
            secrets = json.loads(raw)
            return secrets.get(key)
        except Exception:
            pass

    return None


def _delete_secret(key: str) -> None:
    """Remove a secret from the system keyring."""
    try:
        import keyring as kr
        kr.delete_password(KEYRING_SERVICE, key)
        return
    except (ImportError, Exception):
        pass

    if platform.system() == "Darwin":
        try:
            subprocess.run(
                ["security", "delete-generic-password", "-s", KEYRING_SERVICE, "-a", key],
                capture_output=True,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Config class
# ---------------------------------------------------------------------------

class Config:
    """Application configuration with secure credential management."""

    def __init__(self, config_dir: Optional[Path] = None):
        self._config_dir = config_dir or _get_config_dir()
        self._config_file = self._config_dir / "config.json"
        self._data: dict = deepcopy(DEFAULT_CONFIG)

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def data(self) -> dict:
        return self._data

    # -- Persistence --------------------------------------------------------

    def load(self) -> "Config":
        """Load config from disk, merging with defaults for missing keys."""
        if self._config_file.exists():
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data = _deep_merge(deepcopy(DEFAULT_CONFIG), saved)
                logger.debug("Config loaded from %s", self._config_file)
            except Exception as e:
                logger.warning("Failed to load config, using defaults: %s", e)
                self._data = deepcopy(DEFAULT_CONFIG)
        else:
            self._data = deepcopy(DEFAULT_CONFIG)

        # Fill in default db_path if empty
        if not self._data["sync"]["db_path"]:
            self._data["sync"]["db_path"] = _get_default_db_path()

        return self

    def save(self) -> None:
        """Save config to disk. Passwords are NEVER written to JSON."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        # Ensure no password fields leak into the JSON
        data_to_save = deepcopy(self._data)
        data_to_save["arena"].pop("password", None)
        data_to_save["deployment"].pop("server_api_key_value", None)

        with open(self._config_file, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=2)
        logger.debug("Config saved to %s", self._config_file)

    def validate(self) -> list[str]:
        """Validate config and return list of issues (empty = valid)."""
        issues = []

        if not self._data["arena"]["api_url"]:
            issues.append("Arena API URL is required")
        if not self._data["arena"]["email"]:
            issues.append("Arena email is required")

        mode = self._data["deployment"]["mode"]
        if mode not in ("local", "server", "both"):
            issues.append(f"Invalid deployment mode: {mode}")

        direction = self._data["sync"]["direction"]
        if direction not in ("arena_to_kicad", "kicad_to_arena", "bidirectional"):
            issues.append(f"Invalid sync direction: {direction}")

        mechanism = self._data["sync"]["mechanism"]
        if mechanism not in ("http_lib", "db_lib", "both"):
            issues.append(f"Invalid sync mechanism: {mechanism}")

        strategy = self._data["sync"]["conflict_strategy"]
        if strategy not in ("arena_wins", "kicad_wins", "prompt_user"):
            issues.append(f"Invalid conflict strategy: {strategy}")

        return issues

    # -- Arena credentials --------------------------------------------------

    @property
    def arena_email(self) -> str:
        return self._data["arena"]["email"]

    @arena_email.setter
    def arena_email(self, value: str) -> None:
        self._data["arena"]["email"] = value

    @property
    def arena_api_url(self) -> str:
        return self._data["arena"]["api_url"]

    @property
    def arena_workspace_id(self) -> str:
        return self._data["arena"]["workspace_id"]

    @arena_workspace_id.setter
    def arena_workspace_id(self, value: str) -> None:
        self._data["arena"]["workspace_id"] = value

    def get_arena_password(self) -> Optional[str]:
        """Retrieve Arena password from keyring."""
        return _load_secret("arena_password")

    def set_arena_password(self, password: str) -> None:
        """Store Arena password in keyring."""
        _save_secret("arena_password", password)

    def get_server_api_key(self) -> Optional[str]:
        """Retrieve server API key from keyring."""
        return _load_secret("server_api_key")

    def set_server_api_key(self, key: str) -> None:
        """Store server API key in keyring."""
        _save_secret("server_api_key", key)

    # -- Field mappings -----------------------------------------------------

    def get_field_map(self, direction: str) -> dict[str, str]:
        """Get field mapping for given direction.

        Args:
            direction: "arena_to_kicad" or "kicad_to_arena"

        Returns:
            Dict mapping source field names to target field names.
        """
        return dict(self._data["field_mappings"].get(direction, {}))

    def get_reverse_map(self) -> dict[str, str]:
        """Auto-invert arena_to_kicad mapping for kicad_to_arena use.

        Returns the inverse mapping, where KiCad field names map back
        to Arena field names.
        """
        a2k = self._data["field_mappings"].get("arena_to_kicad", {})
        return {v: k for k, v in a2k.items()}

    # -- Convenience accessors ----------------------------------------------

    @property
    def deployment_mode(self) -> str:
        return self._data["deployment"]["mode"]

    @property
    def server_url(self) -> str:
        return self._data["deployment"]["server_url"]

    @property
    def sync_direction(self) -> str:
        return self._data["sync"]["direction"]

    @property
    def sync_mechanism(self) -> str:
        return self._data["sync"]["mechanism"]

    @property
    def db_path(self) -> str:
        return self._data["sync"]["db_path"]

    @property
    def httplib_port(self) -> int:
        return self._data["sync"]["httplib_port"]

    @property
    def sync_interval_hours(self) -> int:
        return self._data["sync"]["sync_interval_hours"]

    @property
    def conflict_strategy(self) -> str:
        return self._data["sync"]["conflict_strategy"]

    @property
    def kicad_library_path(self) -> str:
        return self._data["kicad_library_path"]

    @property
    def kicad_projects_root(self) -> str:
        return self._data["kicad_projects_root"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
