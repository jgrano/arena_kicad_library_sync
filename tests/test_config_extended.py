"""Extended tests for config.py — credential storage and accessors."""

import json
from pathlib import Path
from unittest.mock import patch
from arena_kicad_library_sync.config import (
    Config, _get_config_dir, _get_default_db_path,
    _save_secret, _load_secret, APP_NAME,
)


class TestConfigDir:
    def test_get_config_dir_returns_path(self):
        result = _get_config_dir()
        assert isinstance(result, Path)
        assert APP_NAME in str(result)

    def test_get_default_db_path(self):
        result = _get_default_db_path()
        assert "arena_library.db" in result


class TestConfigAccessors:
    def test_arena_email(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.arena_email == ""
        config.arena_email = "test@example.com"
        assert config.arena_email == "test@example.com"

    def test_arena_api_url(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert "arenasolutions.com" in config.arena_api_url

    def test_arena_workspace_id(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        config.arena_workspace_id = "12345"
        assert config.arena_workspace_id == "12345"

    def test_deployment_mode(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.deployment_mode == "local"

    def test_server_url(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert "openclaw" in config.server_url

    def test_sync_direction(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.sync_direction == "arena_to_kicad"

    def test_sync_mechanism(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.sync_mechanism == "both"

    def test_db_path(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.db_path != ""

    def test_httplib_port(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.httplib_port == 8765

    def test_sync_interval_hours(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.sync_interval_hours == 24

    def test_conflict_strategy(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.conflict_strategy == "prompt_user"

    def test_kicad_library_path(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.kicad_library_path == ""

    def test_kicad_projects_root(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert config.kicad_projects_root == ""

    def test_config_dir_property(self, tmp_path):
        config = Config(config_dir=tmp_path)
        assert config.config_dir == tmp_path

    def test_data_property(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        assert isinstance(config.data, dict)
        assert "arena" in config.data


class TestConfigSaveLoad:
    def test_save_creates_file(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        config.arena_email = "user@test.com"
        config.save()
        assert (tmp_path / "config.json").exists()

    def test_saved_json_has_no_password(self, tmp_path):
        config = Config(config_dir=tmp_path).load()
        config._data["arena"]["password"] = "secret123"
        config.save()
        with open(tmp_path / "config.json") as f:
            data = json.load(f)
        assert "password" not in data["arena"]

    def test_corrupted_file_uses_defaults(self, tmp_path):
        (tmp_path / "config.json").write_text("not valid json")
        config = Config(config_dir=tmp_path).load()
        assert config.arena_email == ""  # Default value

    def test_partial_config_merges_defaults(self, tmp_path):
        partial = {"arena": {"email": "partial@test.com"}}
        (tmp_path / "config.json").write_text(json.dumps(partial))
        config = Config(config_dir=tmp_path).load()
        assert config.arena_email == "partial@test.com"
        assert config.deployment_mode == "local"  # From defaults


class TestCredentialStorage:
    @patch("arena_kicad_library_sync.config._save_secret")
    def test_set_arena_password(self, mock_save, tmp_path):
        config = Config(config_dir=tmp_path).load()
        config.set_arena_password("secret")
        mock_save.assert_called_once_with("arena_password", "secret")

    @patch("arena_kicad_library_sync.config._load_secret")
    def test_get_arena_password(self, mock_load, tmp_path):
        mock_load.return_value = "secret"
        config = Config(config_dir=tmp_path).load()
        assert config.get_arena_password() == "secret"

    @patch("arena_kicad_library_sync.config._save_secret")
    def test_set_server_api_key(self, mock_save, tmp_path):
        config = Config(config_dir=tmp_path).load()
        config.set_server_api_key("key123")
        mock_save.assert_called_once_with("server_api_key", "key123")

    @patch("arena_kicad_library_sync.config._load_secret")
    def test_get_server_api_key(self, mock_load, tmp_path):
        mock_load.return_value = "key123"
        config = Config(config_dir=tmp_path).load()
        assert config.get_server_api_key() == "key123"


class TestSecretFallback:
    """Test the base64 file fallback for secret storage."""

    @patch("arena_kicad_library_sync.config.platform")
    def test_save_load_fallback(self, mock_platform, tmp_path):
        """Test base64 file fallback when keyring not available."""
        mock_platform.system.return_value = "Linux"

        # Patch _get_config_dir to return tmp_path
        with patch("arena_kicad_library_sync.config._get_config_dir", return_value=tmp_path):
            # Remove keyring availability
            with patch.dict("sys.modules", {"keyring": None}):
                _save_secret("test_key", "test_value")
                result = _load_secret("test_key")
                assert result == "test_value"


class TestConfigValidation:
    def test_all_valid_conflict_strategies(self, tmp_path):
        for strategy in ("arena_wins", "kicad_wins", "prompt_user"):
            config = Config(config_dir=tmp_path).load()
            config._data["arena"]["email"] = "test@test.com"
            config._data["sync"]["conflict_strategy"] = strategy
            assert not config.validate()

    def test_all_valid_mechanisms(self, tmp_path):
        for mech in ("http_lib", "db_lib", "both"):
            config = Config(config_dir=tmp_path).load()
            config._data["arena"]["email"] = "test@test.com"
            config._data["sync"]["mechanism"] = mech
            assert not config.validate()
