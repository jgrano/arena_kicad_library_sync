"""Tests for configuration management."""
import json

from arena_kicad_library_sync.config import Config, _deep_merge


# ===================================================================
# TestConfigLoadSave
# ===================================================================

class TestConfigLoadSave:
    def test_load_defaults(self, tmp_path):
        """Config loads defaults when no file exists."""
        cfg = Config(config_dir=tmp_path)
        cfg.load()

        assert cfg.arena_email == ""
        assert cfg.arena_api_url == "https://api.arenasolutions.com/v1"
        assert cfg.deployment_mode == "local"
        assert cfg.sync_direction == "arena_to_kicad"
        assert cfg.conflict_strategy == "prompt_user"
        # db_path should be auto-filled with a default
        assert cfg.db_path != ""

    def test_save_and_reload(self, tmp_path):
        """Config saves to JSON and reloads correctly."""
        cfg = Config(config_dir=tmp_path)
        cfg.load()
        cfg.arena_email = "test@example.com"
        cfg.arena_workspace_id = "900279607"
        cfg._data["sync"]["direction"] = "bidirectional"
        cfg.save()

        # Verify the file exists
        config_file = tmp_path / "config.json"
        assert config_file.exists()

        # Reload into a fresh Config
        cfg2 = Config(config_dir=tmp_path)
        cfg2.load()

        assert cfg2.arena_email == "test@example.com"
        assert cfg2.arena_workspace_id == "900279607"
        assert cfg2.sync_direction == "bidirectional"

    def test_password_not_in_json(self, tmp_path):
        """Password fields are never written to config.json."""
        cfg = Config(config_dir=tmp_path)
        cfg.load()
        # Manually inject a password into the live data
        cfg._data["arena"]["password"] = "super-secret"
        cfg._data["deployment"]["server_api_key_value"] = "api-key-secret"
        cfg.save()

        # Read the raw JSON and verify secrets are absent
        config_file = tmp_path / "config.json"
        raw = json.loads(config_file.read_text())

        assert "password" not in raw["arena"]
        assert "server_api_key_value" not in raw["deployment"]

    def test_load_merges_with_defaults(self, tmp_path):
        """Saved config with missing keys gets defaults merged in."""
        # Write a partial config
        config_file = tmp_path / "config.json"
        partial = {"arena": {"email": "partial@example.com"}}
        config_file.write_text(json.dumps(partial))

        cfg = Config(config_dir=tmp_path)
        cfg.load()

        # Saved value preserved
        assert cfg.arena_email == "partial@example.com"
        # Default values filled in
        assert cfg.arena_api_url == "https://api.arenasolutions.com/v1"
        assert cfg.deployment_mode == "local"
        assert cfg.conflict_strategy == "prompt_user"


# ===================================================================
# TestConfigValidation
# ===================================================================

class TestConfigValidation:
    def test_valid_config(self, config):
        """Default config with required fields validates without issues."""
        # Fill in required fields
        config._data["arena"]["email"] = "user@example.com"
        issues = config.validate()
        assert issues == []

    def test_missing_email(self, tmp_path):
        """Missing email is flagged."""
        cfg = Config(config_dir=tmp_path)
        cfg.load()
        # email is empty by default
        issues = cfg.validate()
        assert any("email" in i.lower() for i in issues)

    def test_invalid_mode(self, config):
        """Invalid deployment mode flagged."""
        config._data["deployment"]["mode"] = "cloud"
        issues = config.validate()
        assert any("deployment mode" in i.lower() or "mode" in i.lower() for i in issues)

    def test_invalid_direction(self, config):
        """Invalid sync direction flagged."""
        config._data["sync"]["direction"] = "upside_down"
        issues = config.validate()
        assert any("direction" in i.lower() for i in issues)

    def test_invalid_mechanism(self, config):
        """Invalid sync mechanism flagged."""
        config._data["sync"]["mechanism"] = "telepathy"
        issues = config.validate()
        assert any("mechanism" in i.lower() for i in issues)

    def test_invalid_conflict_strategy(self, config):
        """Invalid conflict strategy flagged."""
        config._data["sync"]["conflict_strategy"] = "flip_a_coin"
        issues = config.validate()
        assert any("conflict" in i.lower() or "strategy" in i.lower() for i in issues)

    def test_valid_modes(self, config):
        """All valid deployment modes pass validation."""
        config._data["arena"]["email"] = "user@example.com"
        for mode in ("local", "server", "both"):
            config._data["deployment"]["mode"] = mode
            issues = config.validate()
            assert not any("mode" in i.lower() for i in issues), f"mode={mode} should be valid"

    def test_valid_directions(self, config):
        """All valid sync directions pass validation."""
        config._data["arena"]["email"] = "user@example.com"
        for direction in ("arena_to_kicad", "kicad_to_arena", "bidirectional"):
            config._data["sync"]["direction"] = direction
            issues = config.validate()
            assert not any("direction" in i.lower() for i in issues), (
                f"direction={direction} should be valid"
            )


# ===================================================================
# TestFieldMappings
# ===================================================================

class TestFieldMappings:
    def test_get_field_map(self, config):
        """get_field_map returns correct mapping dict."""
        a2k = config.get_field_map("arena_to_kicad")

        assert isinstance(a2k, dict)
        assert a2k["number"] == "MPN"
        assert a2k["name"] == "Description"
        assert a2k["revisionNumber"] == "Revision"
        assert a2k["lifecyclePhase"] == "Lifecycle"
        assert a2k["category.name"] == "Category"

    def test_get_field_map_kicad_to_arena(self, config):
        """get_field_map('kicad_to_arena') returns the push mapping."""
        k2a = config.get_field_map("kicad_to_arena")

        assert isinstance(k2a, dict)
        assert k2a["MPN"] == "number"
        assert k2a["Description"] == "name"

    def test_get_field_map_unknown_direction(self, config):
        """get_field_map with unknown direction returns empty dict."""
        result = config.get_field_map("nonexistent")
        assert result == {}

    def test_get_reverse_map(self, config):
        """get_reverse_map inverts arena_to_kicad mapping."""
        reverse = config.get_reverse_map()

        assert isinstance(reverse, dict)
        # The reverse of "number" -> "MPN" is "MPN" -> "number"
        assert reverse["MPN"] == "sourcing.primaryMPN"  # last wins for duplicate values
        assert reverse["Description"] == "name"
        assert reverse["Revision"] == "revisionNumber"
        assert reverse["Lifecycle"] == "lifecyclePhase"
        assert reverse["Category"] == "category.name"

    def test_get_field_map_returns_copy(self, config):
        """get_field_map returns a copy, not a reference to internal data."""
        a2k = config.get_field_map("arena_to_kicad")
        a2k["injected_key"] = "injected_value"

        # Internal data should be unmodified
        a2k_again = config.get_field_map("arena_to_kicad")
        assert "injected_key" not in a2k_again


# ===================================================================
# TestDeepMerge
# ===================================================================

class TestDeepMerge:
    def test_deep_merge_nested(self):
        """Nested dicts are merged recursively."""
        base = {
            "a": {"x": 1, "y": 2},
            "b": "base_b",
        }
        override = {
            "a": {"y": 99, "z": 3},
        }
        result = _deep_merge(base, override)

        assert result["a"]["x"] == 1       # kept from base
        assert result["a"]["y"] == 99      # overridden
        assert result["a"]["z"] == 3       # added from override
        assert result["b"] == "base_b"     # untouched

    def test_deep_merge_override(self):
        """Override values replace base values."""
        base = {"a": 1, "b": 2, "c": 3}
        override = {"b": 20, "d": 40}
        result = _deep_merge(base, override)

        assert result["a"] == 1
        assert result["b"] == 20
        assert result["c"] == 3
        assert result["d"] == 40

    def test_deep_merge_override_dict_with_scalar(self):
        """Override can replace a nested dict with a scalar."""
        base = {"a": {"nested": True}}
        override = {"a": "flat_now"}
        result = _deep_merge(base, override)

        assert result["a"] == "flat_now"

    def test_deep_merge_does_not_modify_override(self):
        """The override dict should not be mutated."""
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        import copy
        override_copy = copy.deepcopy(override)

        _deep_merge(base, override)

        assert override == override_copy

    def test_deep_merge_empty_override(self):
        """Empty override returns base unchanged."""
        base = {"a": 1, "b": {"c": 2}}
        result = _deep_merge(base, {})

        assert result == {"a": 1, "b": {"c": 2}}

    def test_deep_merge_empty_base(self):
        """Empty base gets all override values."""
        override = {"a": 1, "b": {"c": 2}}
        result = _deep_merge({}, override)

        assert result == {"a": 1, "b": {"c": 2}}
