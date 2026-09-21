from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from app.database import run_migrations


def _release_root(repository_root: Path, temporary_root: Path) -> Path:
    release_root = temporary_root / "release"
    release_root.mkdir()
    (release_root / ".venv").symlink_to(repository_root / ".venv", target_is_directory=True)
    (release_root / "backend").symlink_to(repository_root / "backend", target_is_directory=True)
    (release_root / "RELEASE_ID").write_text("test-release\n")
    (release_root / "RELEASE_CHANNEL").write_text("stable\n")
    (release_root / "SOURCE_REVISION").write_text("1" * 40 + "\n")
    (release_root / "RELEASE_MANIFEST").write_text(
        "metadata_version=1\n"
        "channel=stable\n"
        "release_id=test-release\n"
        "source_repository=https://example.invalid/repository.git\n"
        "source_ref=refs/tags/test-release\n"
        f"source_revision={'1' * 40}\n"
        "source_origin=https://example.invalid/releases/download\n"
    )
    return release_root


def _run_backup(
    repository_root: Path,
    database_path: Path,
    backup_directory: Path,
    release_root: Path,
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "LCC_DATABASE_URL": f"sqlite:///{database_path}",
        "LCC_BACKUP_DIRECTORY": str(backup_directory),
        "LCC_BACKUP_RETENTION_COUNT": "2",
        "LCC_RELEASE_ROOT": str(release_root),
    }
    return subprocess.run(
        [str(repository_root / "scripts" / "operational-backup.sh")],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_scheduled_backup_manifest_permissions_and_retention(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "data" / "lcc.sqlite3"
    backup_directory = tmp_path / "backups"
    release_root = _release_root(repository_root, tmp_path)
    run_migrations(f"sqlite:///{database_path}")
    for _ in range(3):
        result = _run_backup(repository_root, database_path, backup_directory, release_root)
        reported_backup = Path(result.stdout.strip())
        assert reported_backup.is_file()
        assert reported_backup.parent == backup_directory
    backups = sorted(backup_directory.glob("lcc-scheduled-*.sqlite3"))
    assert len(backups) == 2
    assert backup_directory.stat().st_mode & 0o777 == 0o700
    for backup in backups:
        manifest = backup.with_suffix(backup.suffix + ".manifest")
        values = dict(line.split("=", 1) for line in manifest.read_text().splitlines())
        assert values == {
            "checksum_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "schema_revision": "0019_remove_legacy_roadmap_pointer_cycle",
            "channel": "stable",
            "release_id": "test-release",
            "source_repository": "https://example.invalid/repository.git",
            "source_ref": "refs/tags/test-release",
            "source_revision": "1" * 40,
            "source_origin": "https://example.invalid/releases/download",
            "database_path": str(database_path),
        }
        assert backup.stat().st_mode & 0o777 == 0o600
        assert manifest.stat().st_mode & 0o777 == 0o600


def test_scheduled_backup_failure_is_nonzero_and_leaves_no_artifact(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "missing" / "lcc.sqlite3"
    backup_directory = tmp_path / "backups"
    release_root = _release_root(repository_root, tmp_path)
    environment = {
        **os.environ,
        "LCC_DATABASE_URL": f"sqlite:///{database_path}",
        "LCC_BACKUP_DIRECTORY": str(backup_directory),
        "LCC_RELEASE_ROOT": str(release_root),
    }
    result = subprocess.run(
        [str(repository_root / "scripts" / "operational-backup.sh")],
        cwd=repository_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert list(backup_directory.glob("lcc-scheduled-*")) == []
