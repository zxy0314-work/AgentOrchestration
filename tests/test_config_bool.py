"""Tests for Config.get_bool()."""

import pytest
from src.common.config import Config


class TestConfigBool:
    def test_true_string(self):
        config = Config()
        config.set("flag", "true")
        assert config.get_bool("flag") is True

    def test_false_string(self):
        config = Config()
        config.set("flag", "false")
        assert config.get_bool("flag") is False

    def test_true_string_uppercase(self):
        config = Config()
        config.set("flag", "TRUE")
        assert config.get_bool("flag") is True

    def test_false_string_mixed_case(self):
        config = Config()
        config.set("flag", "False")
        assert config.get_bool("flag") is False

    def test_one_string(self):
        config = Config()
        config.set("flag", "1")
        assert config.get_bool("flag") is True

    def test_zero_string(self):
        config = Config()
        config.set("flag", "0")
        assert config.get_bool("flag") is False

    def test_bool_true(self):
        config = Config()
        config.set("flag", True)
        assert config.get_bool("flag") is True

    def test_bool_false(self):
        config = Config()
        config.set("flag", False)
        assert config.get_bool("flag") is False

    def test_default_when_missing(self):
        config = Config()
        assert config.get_bool("nonexistent") is False

    def test_default_true_when_missing(self):
        config = Config()
        assert config.get_bool("nonexistent", True) is True

    def test_nested_bool(self):
        config = Config()
        config.set("app.features.debug", "true")
        assert config.get_bool("app.features.debug") is True

    def test_nested_bool_false(self):
        config = Config()
        config.set("app.features.debug", "false")
        assert config.get_bool("app.features.debug") is False

    def test_arbitrary_string_is_false(self):
        config = Config()
        config.set("flag", "yes")
        assert config.get_bool("flag") is False

    def test_integer_is_default(self):
        config = Config()
        config.set("flag", 42)
        assert config.get_bool("flag") is False

# 2026-05-25T09:25:00 update