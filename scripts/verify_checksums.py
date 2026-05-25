#!/usr/bin/env python3
"""
Release checksum verification script.

Generates a checksum manifest for all packaged source files after normalizing
line endings. Run this during CI on multiple runner types to verify that
checksums are stable across platforms.

Usage:
    python scripts/verify_checksums.py [--generate] [--verify MANIFEST_FILE]
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

RELEASE_DIRS = ["src", "tests"]
RELEASE_FILES = [
    "pyproject.toml",
    "Makefile",
    ".gitattributes",
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
]
MANIFEST_FILE = "release_checksums.json"

# Files that should be identical across platforms after line-ending normalization
CHECKSUMED_EXTENSIONS = {".py", ".md", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini", ".txt", ".sh"}
CHECKSUMED_FILES = {"Makefile", "Dockerfile"}


def normalize_content(content: bytes) -> bytes:
    """Normalize line endings to LF for consistent checksums."""
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def file_checksum(path: Path) -> str:
    """Compute SHA-256 of a file after normalizing line endings."""
    content = path.read_bytes()
    normalized = normalize_content(content)
    return hashlib.sha256(normalized).hexdigest()


def collect_files() -> list[Path]:
    """Collect all release files that should be checksummed."""
    files = []
    for d in RELEASE_DIRS:
        base = Path(d)
        if base.exists():
            for f in base.rglob("*"):
                if f.is_file() and (f.suffix in CHECKSUMED_EXTENSIONS or f.name in CHECKSUMED_FILES):
                    files.append(f)
    for f in RELEASE_FILES:
        p = Path(f)
        if p.exists() and p.is_file():
            files.append(p)
    return sorted(files)


def generate_manifest() -> dict[str, str]:
    """Generate checksum manifest for all release files."""
    manifest = {}
    for f in collect_files():
        manifest[str(f)] = file_checksum(f)
    return manifest


def verify_manifest(manifest: dict[str, str]) -> bool:
    """Verify current files against a manifest. Returns True if all match."""
    success = True
    for path_str, expected in manifest.items():
        p = Path(path_str)
        if not p.exists():
            print(f"  MISSING: {path_str}")
            success = False
            continue
        actual = file_checksum(p)
        if actual != expected:
            print(f"  FAIL: {path_str}")
            print(f"    expected: {expected}")
            print(f"    actual:   {actual}")
            success = False
        else:
            print(f"  PASS: {path_str}")
    return success


def main():
    parser = argparse.ArgumentParser(description="Release checksum verification")
    parser.add_argument("--generate", action="store_true", help="Generate checksum manifest")
    parser.add_argument("--verify", type=str, default=None, help="Verify against manifest file")
    args = parser.parse_args()

    if args.generate:
        manifest = generate_manifest()
        with open(MANIFEST_FILE, "w") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        print(f"Generated {MANIFEST_FILE} with {len(manifest)} entries")
        for path_str, cksum in sorted(manifest.items()):
            print(f"  {cksum[:16]}  {path_str}")

    elif args.verify:
        manifest_path = args.verify
        if not os.path.exists(manifest_path):
            print(f"Manifest file not found: {manifest_path}")
            sys.exit(1)
        with open(manifest_path) as f:
            manifest = json.load(f)
        print(f"Verifying {len(manifest)} files against {manifest_path}...")
        if verify_manifest(manifest):
            print("\n✅ All checksums match — release is reproducible across platforms.")
        else:
            print("\n❌ Checksum drift detected — some files differ from the reference manifest.")
            print("   This may be caused by platform-specific line endings.")
            sys.exit(1)

    else:
        # Default: generate + self-verify
        manifest = generate_manifest()
        print(f"Generated manifest with {len(manifest)} entries (dry run)")
        for path_str, cksum in sorted(manifest.items()):
            print(f"  {cksum[:16]}  {path_str}")
        print("\nRun with --generate to save the manifest, or --verify to check against a saved one.")


if __name__ == "__main__":
    main()