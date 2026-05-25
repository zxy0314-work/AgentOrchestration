import json
import os

import pytest
from src.common.config import Config, ConfigError


class TestConfig:
    def test_load_config(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"app": {"name": "test", "port": 8080}}')
        config = Config(str(config_file))
        assert config.get("app.name") == "test"
        assert config.get("app.port") == 8080

    def test_default_value(self):
        config = Config()
        assert config.get("nonexistent.key", "default") == "default"

    def test_set_value(self):
        config = Config()
        config.set("database.host", "localhost")
        assert config.get("database.host") == "localhost"

    def test_nested_set(self):
        config = Config()
        config.set("a.b.c.d", "value")
        assert config.get("a.b.c.d") == "value"

    def test_to_dict(self):
        config = Config()
        config.set("key1", "value1")
        config.set("key2", "value2")
        data = config.to_dict()
        assert data["key1"] == "value1"
        assert data["key2"] == "value2"

    # --- YAML support tests ---

    def test_load_yaml_config(self, tmp_path):
        """Config.load should support .yaml files."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("app:\n  name: test\n  port: 8080\n")
        config = Config(str(config_file))
        assert config.get("app.name") == "test"
        assert config.get("app.port") == 8080

    def test_load_yml_extension(self, tmp_path):
        """Config.load should support .yml extension."""
        config_file = tmp_path / "config.yml"
        config_file.write_text("database:\n  host: localhost\n  port: 5432\n")
        config = Config(str(config_file))
        assert config.get("database.host") == "localhost"
        assert config.get("database.port") == 5432

    def test_yaml_nested_structures(self, tmp_path):
        """YAML config should handle deeply nested structures."""
        config_file = tmp_path / "nested.yaml"
        config_file.write_text(
            "server:\n"
            "  host: 0.0.0.0\n"
            "  ports:\n"
            "    http: 8080\n"
            "    https: 8443\n"
            "  tls:\n"
            "    enabled: true\n"
            "    cert_path: /etc/certs/cert.pem\n"
        )
        config = Config(str(config_file))
        assert config.get("server.host") == "0.0.0.0"
        assert config.get("server.ports.http") == 8080
        assert config.get("server.ports.https") == 8443
        assert config.get("server.tls.enabled") is True
        assert config.get("server.tls.cert_path") == "/etc/certs/cert.pem"

    def test_yaml_list_values(self, tmp_path):
        """YAML config should handle list values."""
        config_file = tmp_path / "list.yaml"
        config_file.write_text(
            "features:\n"
            "  - auth\n"
            "  - logging\n"
            "  - metrics\n"
            "timeout: 30\n"
        )
        config = Config(str(config_file))
        assert config.get("features") == ["auth", "logging", "metrics"]
        assert config.get("timeout") == 30
        assert config.get("nonexistent") is None

    def test_yaml_and_json_equivalent(self, tmp_path):
        """Loading the same data from YAML and JSON should produce identical configs."""
        json_file = tmp_path / "config.json"
        yaml_file = tmp_path / "config.yaml"
        data = {"app": {"name": "test-app", "version": "1.0.0", "debug": False}}
        json_file.write_text(json.dumps(data))
        yaml_file.write_text("app:\n  name: test-app\n  version: \"1.0.0\"\n  debug: false\n")

        json_config = Config(str(json_file))
        yaml_config = Config(str(yaml_file))
        assert json_config.to_dict() == yaml_config.to_dict()
        assert json_config.get("app.name") == yaml_config.get("app.name")

    def test_load_rejects_unsupported_extension(self, tmp_path):
        """Config.load should raise ConfigError for unsupported file extensions."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[app]\nname = \"test\"\n")
        with pytest.raises(ConfigError, match="Unsupported config format"):
            Config(str(config_file))

    def test_load_rejects_no_extension(self, tmp_path):
        """Config.load should raise ConfigError for files without recognized extensions."""
        config_file = tmp_path / "config"
        config_file.write_text("{}")
        with pytest.raises(ConfigError, match="Unsupported config format"):
            Config(str(config_file))

    def test_yaml_bad_syntax_raises_error(self, tmp_path):
        """Config.load should propagate YAML parse errors."""
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("key: [unclosed list\n")
        with pytest.raises(Exception):
            Config(str(config_file))

    def test_ao_env_overrides_with_yaml(self, tmp_path):
        """AO_ env overrides should work on top of YAML-loaded config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("app:\n  name: base\n  port: 3000\n")
        os.environ["AO_APP_NAME"] = "overridden"
        try:
            config = Config(str(config_file))
            assert config.get("app.name") == "overridden"
            assert config.get("app.port") == 3000
        finally:
            del os.environ["AO_APP_NAME"]

    def test_empty_yaml_config(self, tmp_path):
        """Loading an empty YAML file should not crash."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        config = Config(str(config_file))
        assert config.to_dict() is None or config.to_dict() == {}

    def test_boolean_in_yaml(self, tmp_path):
        """YAML boolean values should be loaded as Python bools."""
        config_file = tmp_path / "bool.yaml"
        config_file.write_text(
            "debug: true\n"
            "production: false\n"
            "rate: 0.75\n"
        )
        config = Config(str(config_file))
        assert config.get("debug") is True
        assert config.get("production") is False
        assert config.get("rate") == 0.75