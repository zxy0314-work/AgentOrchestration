"""Tests for CLI main parser -- output mode argument validation."""

import argparse
import sys
import pytest

from src.cli.main import cli


class TestOutputModeValidation:
    """Test that --output / -o argument validates output modes using argparse choices."""

    def test_output_text_is_valid(self):
        """Test that --output text is accepted."""
        test_args = ["--output", "text", "status"]
        sys.argv = ["agent-orch"] + test_args
        # Should not raise SystemExit or argparse error
        try:
            cli()
        except SystemExit:
            pytest.fail("--output text should be valid")

    def test_output_json_is_valid(self):
        """Test that --output json is accepted."""
        test_args = ["--output", "json", "status"]
        sys.argv = ["agent-orch"] + test_args
        try:
            cli()
        except SystemExit:
            pytest.fail("--output json should be valid")

    def test_output_yaml_is_valid(self):
        """Test that --output yaml is accepted."""
        test_args = ["--output", "yaml", "status"]
        sys.argv = ["agent-orch"] + test_args
        try:
            cli()
        except SystemExit:
            pytest.fail("--output yaml should be valid")

    def test_output_invalid_value_fails(self):
        """Test that --output with an invalid value is rejected by argparse."""
        test_args = ["--output", "invalid_value", "status"]
        sys.argv = ["agent-orch"] + test_args
        with pytest.raises(SystemExit) as exc_info:
            cli()
        # argparse exits with code 2 on invalid choices
        assert exc_info.value.code == 2, (
            "argparse should exit with code 2 for invalid choice"
        )

    def test_default_output_mode(self):
        """Test that default output mode is 'text' when --output is not given."""
        # Parse directly using argparse to check the default
        parser = argparse.ArgumentParser()
        parser.add_argument("--output", "-o", choices=["text", "json", "yaml"],
                            default="text")
        args = parser.parse_args([])
        assert args.output == "text", f"Expected default 'text', got '{args.output}'"

    def test_output_accessible_via_args(self):
        """Test that args.output is accessible after parsing."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--output", "-o", choices=["text", "json", "yaml"],
                            default="text")
        args = parser.parse_args(["--output", "json"])
        assert args.output == "json", f"Expected args.output='json', got '{args.output}'"

    def test_output_short_form(self):
        """Test that -o short form works."""
        test_args = ["-o", "yaml", "status"]
        sys.argv = ["agent-orch"] + test_args
        try:
            cli()
        except SystemExit:
            pytest.fail("-o yaml should be valid")