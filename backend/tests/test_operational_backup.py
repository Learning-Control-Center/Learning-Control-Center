from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from app.database import run_migrations


def _run_backup(repository_root: Path, database_path: Path, backup_directory: Path) -> None:
    environment = {
        **os.environ,
        "LCC_DATABASE_URL": f"sqlite:///{database_path}",
        "LCC_BACKUP_DIRECTORY": str(backup_directory),
        "LCC_BACKUP_RETENTION_COUNT": "2",
        "LCC_APP_VERSION": "test-version",
    }
    subprocess.run(
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
    run_migrations(f"sqlite:///{database_path}")
    for _ in range(3):
        _run_backup(repository_root, database_path, backup_directory)
    backups = sorted(backup_directory.glob("lcc-scheduled-*.sqlite3"))
    assert len(backups) == 2
    assert backup_directory.stat().st_mode & 0o777 == 0o700
    for backup in backups:
        manifest = backup.with_suffix(backup.suffix + ".manifest")
        values = dict(line.split("=", 1) for line in manifest.read_text().splitlines())
        assert values == {
            "checksum_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "schema_revision": "0009_unified_evidence_verification",
            "app_version": "test-version",
            "database_path": str(database_path),
        }
        assert backup.stat().st_mode & 0o777 == 0o600
        assert manifest.stat().st_mode & 0o777 == 0o600


def test_scheduled_backup_failure_is_nonzero_and_leaves_no_artifact(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "missing" / "lcc.sqlite3"
    backup_directory = tmp_path / "backups"
    environment = {
        **os.environ,
        "LCC_DATABASE_URL": f"sqlite:///{database_path}",
        "LCC_BACKUP_DIRECTORY": str(backup_directory),
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
