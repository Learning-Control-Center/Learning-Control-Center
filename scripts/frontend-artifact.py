#!/usr/bin/env python3
"""Create or verify the frontend artifact shipped to production hosts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "LCC_FRONTEND_ARTIFACT.json"
SCHEMA_VERSION = 1
ROOT_FILES = (
    "docs/IMPORT_EXPORT_FORMAT.md",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/postcss.config.js",
    "frontend/tailwind.config.js",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
)


class ArtifactError(RuntimeError):
    """A frontend source or artifact violates the production contract."""


def _regular_files(root: Path, relative_roots: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for relative in relative_roots:
        candidate = root / relative
        if not candidate.exists():
            raise ArtifactError(f"required frontend input is missing: {relative}")
        if candidate.is_symlink():
            raise ArtifactError(f"frontend input must not be a symlink: {relative}")
        if candidate.is_file():
            files.append(candidate)
            continue
        for path in candidate.rglob("*"):
            if path.is_symlink():
                raise ArtifactError(
                    f"frontend input must not contain symlinks: {path.relative_to(root)}"
                )
            if path.is_file():
                files.append(path)
    return sorted(set(files), key=lambda path: path.relative_to(root).as_posix())


def _source_files(root: Path) -> list[Path]:
    files = _regular_files(root, ROOT_FILES + ("frontend/public", "frontend/src"))
    return [
        path
        for path in files
        if ".test." not in path.name
        and "frontend/src/test/" not in path.relative_to(root).as_posix()
    ]


def _artifact_files(root: Path) -> list[Path]:
    dist = root / "frontend" / "dist"
    if not dist.is_dir() or dist.is_symlink():
        raise ArtifactError("frontend/dist must be a real directory")
    files: list[Path] = []
    for path in dist.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ArtifactError(f"frontend artifact must not contain symlinks: {relative}")
        if path.is_file() and path.name != MANIFEST_NAME:
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                raise ArtifactError(f"frontend artifact member is not a regular file: {relative}")
            files.append(path)
    if not files or not (dist / "index.html").is_file():
        raise ArtifactError("frontend artifact is missing index.html")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _tree_digest(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = PurePosixPath(path.relative_to(root).as_posix()).as_posix().encode()
        content = path.read_bytes()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(len(content)).encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _current(root: Path) -> dict[str, object]:
    source_files = _source_files(root)
    artifact_files = _artifact_files(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_sha256": _tree_digest(root, source_files),
        "artifact_sha256": _tree_digest(root, artifact_files),
        "artifact_file_count": len(artifact_files),
    }


def write_manifest(root: Path) -> None:
    manifest = root / "frontend" / "dist" / MANIFEST_NAME
    manifest.write_text(json.dumps(_current(root), indent=2, sort_keys=True) + "\n")
    os.chmod(manifest, 0o644)
    print(f"frontend-artifact: wrote {manifest}")


def verify_manifest(root: Path) -> None:
    manifest = root / "frontend" / "dist" / MANIFEST_NAME
    if not manifest.is_file() or manifest.is_symlink():
        raise ArtifactError(f"frontend artifact manifest is missing or unsafe: {manifest}")
    try:
        recorded = json.loads(manifest.read_text())
    except (json.JSONDecodeError, OSError) as error:
        raise ArtifactError(f"frontend artifact manifest is invalid: {error}") from error
    expected_keys = {
        "schema_version",
        "source_sha256",
        "artifact_sha256",
        "artifact_file_count",
    }
    if set(recorded) != expected_keys:
        raise ArtifactError("frontend artifact manifest has unexpected fields")
    current = _current(root)
    if recorded != current:
        raise ArtifactError(
            "frontend artifact is stale or modified; rebuild it from the locked frontend source"
        )
    print(f"frontend-artifact: verified {current['artifact_sha256']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("write", "verify"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    try:
        if arguments.action == "write":
            write_manifest(root)
        else:
            verify_manifest(root)
    except ArtifactError as error:
        parser.exit(1, f"frontend-artifact: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
