"""Tests for the release checksum verification script."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from scripts.verify_checksums import (
    CHECKSUMED_EXTENSIONS,
    file_checksum,
    generate_manifest,
    normalize_content,
    verify_manifest,
)


class TestNormalizeContent:
    def test_lf_stays_lf(self):
        assert normalize_content(b"hello\nworld\n") == b"hello\nworld\n"

    def test_crlf_to_lf(self):
        assert normalize_content(b"hello\r\nworld\r\n") == b"hello\nworld\n"

    def test_cr_to_lf(self):
        assert normalize_content(b"hello\rworld\r") == b"hello\nworld\n"

    def test_mixed_line_endings(self):
        result = normalize_content(b"a\r\nb\nc\rd")
        assert result == b"a\nb\nc\nd"


class TestFileChecksum:
    def test_same_content_different_endings(self):
        """Files with same content but different line endings should have the same checksum."""
        lf_content = b"line1\nline2\nline3\n"
        crlf_content = b"line1\r\nline2\r\nline3\r\n"

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f1:
            f1.write(lf_content)
            p1 = f1.name

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f2:
            f2.write(crlf_content)
            p2 = f2.name

        try:
            cksum1 = file_checksum(Path(p1))
            cksum2 = file_checksum(Path(p2))
            assert cksum1 == cksum2, "Checksums should match after normalization"
        finally:
            os.unlink(p1)
            os.unlink(p2)

    def test_different_content(self):
        """Files with truly different content should have different checksums."""
        content_a = b"print('hello')\n"
        content_b = b"print('world')\n"

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f1:
            f1.write(content_a)
            p1 = f1.name

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f2:
            f2.write(content_b)
            p2 = f2.name

        try:
            cksum_a = file_checksum(Path(p1))
            cksum_b = file_checksum(Path(p2))
            assert cksum_a != cksum_b
        finally:
            os.unlink(p1)
            os.unlink(p2)


class TestGenerateAndVerify:
    def test_generate_and_verify_manifest(self, tmp_path):
        """Generate a manifest and verify it passes."""
        original_dir = os.getcwd()
        os.chdir(tmp_path)

        try:
            # Create a minimal project structure
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "__init__.py").write_text("# test\n")
            (tmp_path / "tests").mkdir()
            (tmp_path / "tests" / "__init__.py").write_text("# test\n")
            (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
            (tmp_path / "README.md").write_text("# Test\n")

            manifest = generate_manifest()
            assert len(manifest) > 0

            # Save and re-verify
            manifest_path = tmp_path / "release_checksums.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2, sort_keys=True)

            with open(manifest_path) as f:
                loaded = json.load(f)

            assert verify_manifest(loaded) is True
        finally:
            os.chdir(original_dir)

    def test_verify_fails_on_modified_file(self, tmp_path):
        """Verification should fail if a file's content changes."""
        original_dir = os.getcwd()
        os.chdir(tmp_path)

        try:
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "main.py").write_text("version = 1\n")
            (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")

            manifest = generate_manifest()
            manifest_path = tmp_path / "release_checksums.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2, sort_keys=True)

            # Modify a file
            (tmp_path / "src" / "main.py").write_text("version = 2\n")

            with open(manifest_path) as f:
                loaded = json.load(f)

            assert verify_manifest(loaded) is False
        finally:
            os.chdir(original_dir)