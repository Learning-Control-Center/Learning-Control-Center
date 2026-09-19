#!/usr/bin/env python3
"""Emit deterministic Roadmap/shared chunk sizes from a Vite production manifest."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any, cast


def _gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


def _entry_key(manifest: dict[str, Any], *, source: str | None = None, entry: bool = False) -> str:
    matches = [
        key
        for key, value in manifest.items()
        if isinstance(value, dict)
        and ((source is not None and key == source) or (entry and value.get("isEntry") is True))
    ]
    if len(matches) != 1:
        description = source or "the application entry"
        raise RuntimeError(
            f"Expected one Vite manifest record for {description}; got {len(matches)}."
        )
    return matches[0]


def _static_closure(manifest: dict[str, Any], entry_key: str) -> set[str]:
    closure: set[str] = set()
    pending = [entry_key]
    while pending:
        key = pending.pop()
        if key in closure:
            continue
        item = manifest.get(key)
        if not isinstance(item, dict):
            raise RuntimeError(f"Vite manifest import {key!r} is missing.")
        closure.add(key)
        pending.extend(str(imported) for imported in item.get("imports", ()))
    return closure


def inventory(dist: Path, *, roadmap_source: str) -> dict[str, object]:
    manifest_path = dist / ".vite" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("Vite manifest must be an object.")
    roadmap_key = _entry_key(manifest, source=roadmap_source)
    application_key = _entry_key(manifest, entry=True)
    roadmap = manifest[roadmap_key]
    if roadmap.get("isDynamicEntry") is not True:
        raise RuntimeError("The Roadmap feature must remain a lazy Vite dynamic entry.")
    application_keys = _static_closure(manifest, application_key)
    roadmap_keys = _static_closure(manifest, roadmap_key) - application_keys

    def facts(keys: set[str]) -> dict[str, object]:
        paths = sorted({dist / str(manifest[key]["file"]) for key in keys})
        return {
            "files": [path.relative_to(dist).as_posix() for path in paths],
            "rawBytes": sum(path.stat().st_size for path in paths),
            "gzipBytes": sum(_gzip_size(path) for path in paths),
        }

    roadmap_facts = facts(roadmap_keys)
    application_facts = facts(application_keys)
    return {
        "schemaVersion": 1,
        "manifest": manifest_path.relative_to(dist).as_posix(),
        "compression": {"format": "gzip", "level": 9, "mtime": 0},
        "roadmapRoute": roadmap_facts,
        "sharedApplication": application_facts,
        "combinedGzipBytes": cast(int, roadmap_facts["gzipBytes"])
        + cast(int, application_facts["gzipBytes"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("frontend/dist"))
    parser.add_argument("--roadmap-source", default="src/features/roadmap/v2.ts")
    arguments = parser.parse_args()
    print(
        json.dumps(
            inventory(arguments.dist.resolve(), roadmap_source=arguments.roadmap_source),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
