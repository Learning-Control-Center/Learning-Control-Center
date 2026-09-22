"""Installer V2 Phase 1 source acquisition and archive safety contracts."""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts/bootstrap.sh"
LEGACY = ROOT / "scripts/bootstrap-ubuntu.sh"
SHA = "a" * 40
GITHUB_REF = (
    "https://api.github.com/repos/"
    "Learning-Control-Center/Learning-Control-Center/git/ref/heads/main"
)
GITHUB_RAW = (
    "https://raw.githubusercontent.com/Learning-Control-Center/"
    f"Learning-Control-Center/{SHA}/scripts/bootstrap.sh"
)
GITHUB_ARCHIVE = (
    f"https://api.github.com/repos/Learning-Control-Center/Learning-Control-Center/tarball/{SHA}"
)


def _member(
    name: str, data: bytes = b"", *, mode: int = 0o644, kind: bytes = tarfile.REGTYPE
) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.type = kind
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, data


def _archive(
    path: Path,
    *,
    extra: list[tuple[tarfile.TarInfo, bytes]] | None = None,
    omit: str = "",
    bootstrap_mode: int = 0o755,
    installer: bytes | None = None,
) -> None:
    root = f"Learning-Control-Center-Learning-Control-Center-{SHA[:7]}"
    entries = [
        _member(f"{root}/", kind=tarfile.DIRTYPE, mode=0o755),
        _member(f"{root}/scripts/", kind=tarfile.DIRTYPE, mode=0o755),
        _member(f"{root}/README.md", b"LCC\n"),
        _member(f"{root}/pyproject.toml", b"[project]\nname='lcc'\n"),
        _member(f"{root}/scripts/bootstrap.sh", BOOTSTRAP.read_bytes(), mode=bootstrap_mode),
        _member(
            f"{root}/scripts/install.sh",
            installer or b"#!/usr/bin/env bash\nexit 0\n",
            mode=0o755,
        ),
        _member(f"{root}/scripts/install/transition.sh", b"# transition fixture\n"),
        _member(
            f"{root}/scripts/install/platforms/ubuntu-24.04.sh",
            b"# platform fixture\n",
        ),
        _member(
            f"{root}/scripts/bootstrap-ubuntu.sh",
            (ROOT / "scripts/bootstrap-ubuntu.sh").read_bytes(),
            mode=0o755,
        ),
    ]
    with tarfile.open(path, "w:gz") as bundle:
        for info, data in [*entries, *(extra or [])]:
            if omit and info.name.rstrip("/").endswith(omit):
                continue
            bundle.addfile(info, io.BytesIO(data) if info.isfile() else None)


@pytest.fixture
def source_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "provider"
    fixture.mkdir()
    (fixture / "tmp").mkdir()
    (fixture / "bin").mkdir()
    (fixture / "ref.json").write_text(
        json.dumps({"ref": "refs/heads/main", "object": {"type": "commit", "sha": SHA}})
    )
    shutil.copy2(BOOTSTRAP, fixture / "bootstrap.sh")
    _archive(fixture / "source.tar.gz")
    curl = fixture / "bin/curl"
    curl.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
url = args[-1]
fixture = Path(os.environ["LCC_GITHUB_FIXTURE"])
with (fixture / "requests.log").open("a") as log:
    log.write(url + "\\n")
if url.endswith("/git/ref/heads/main"):
    source = fixture / "ref.json"
elif url.endswith("/scripts/bootstrap.sh") and "/" + "a" * 40 + "/" in url:
    source = fixture / "bootstrap.sh"
elif url.endswith("/main/scripts/bootstrap.sh"):
    source = fixture / "bootstrap.sh"
elif url.endswith("/tarball/" + "a" * 40):
    source = fixture / "source.tar.gz"
else:
    raise SystemExit("Unexpected provider request: " + url)
destination = Path(args[args.index("--output") + 1])
import shutil
shutil.copyfile(source, destination)
if url.endswith("/git/ref/heads/main") and os.environ.get("LCC_MOVE_MAIN_AFTER_REF") == "1":
    replacement = fixture / "next-ref.json"
    replacement.write_text(
        '{"ref":"refs/heads/main","object":{"type":"commit","sha":"' + "b" * 40 + '"}}'
    )
    replacement.replace(fixture / "ref.json")
"""
    )
    curl.chmod(0o755)
    for command in ("apt-get", "systemctl", "useradd"):
        spy = fixture / "bin" / command
        spy.write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$0 $*" >> "$LCC_GITHUB_FIXTURE/mutations.log"\n'
        )
        spy.chmod(0o755)
    return fixture


def _run(
    fixture: Path,
    *args: str,
    piped: bool = False,
    move_main: bool = False,
    acquire_only: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{fixture / 'bin'}:{os.environ['PATH']}",
        "LCC_GITHUB_FIXTURE": str(fixture),
        "TMPDIR": str(fixture / "tmp"),
        "LCC_MOVE_MAIN_AFTER_REF": "1" if move_main else "0",
        "LCC_BOOTSTRAP_TESTING": "1",
        "LCC_BOOTSTRAP_TEST_ACQUIRE_ONLY": "1" if acquire_only else "0",
    }
    env.pop("LCC_V2_PINNED_SHA", None)
    command = ["bash", "-s", "--", *args] if piped else ["bash", str(BOOTSTRAP), *args]
    return subprocess.run(
        command,
        input=BOOTSTRAP.read_text() if piped else None,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _requests(fixture: Path) -> list[str]:
    log = fixture / "requests.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("piped", [False, True])
def test_exact_sha_source_acquisition_and_no_host_mutation(
    source_fixture: Path, piped: bool
) -> None:
    result = _run(
        source_fixture,
        "--commit",
        SHA,
        "--non-interactive",
        "--domain",
        "lcc.example.test",
        "--app-port",
        "8123",
        piped=piped,
    )
    assert result.returncode == 0, result.stderr
    assert _requests(source_fixture) == [GITHUB_REF, GITHUB_RAW, GITHUB_ARCHIVE]
    assert f"Validated exact-SHA source: {SHA}" in result.stdout
    assert "Validated source acquisition only" in result.stdout
    assert not (source_fixture / "mutations.log").exists()
    assert not list((source_fixture / "tmp").iterdir())


@pytest.mark.parametrize("piped", [False, True])
@pytest.mark.parametrize("gateway_args", [[], ["--gateway", "external"]])
def test_deliberate_core_handoff_preserves_pinned_identity_and_arguments(
    source_fixture: Path,
    piped: bool,
    gateway_args: list[str],
) -> None:
    archive = source_fixture / "source.tar.gz"
    _archive(
        archive,
        installer=b"#!/usr/bin/env bash\n"
        b'printf \'%s\\n\' "$LCC_V2_SOURCE_ROOT" "$LCC_V2_SOURCE_REPOSITORY" '
        b'"$LCC_V2_SOURCE_REF" "$LCC_V2_SOURCE_SHA" "$@" '
        b'> "$LCC_GITHUB_FIXTURE/handoff.log"\n',
    )
    result = _run(
        source_fixture,
        "--commit",
        SHA,
        "--non-interactive",
        "--domain",
        "lcc.example.test",
        "--app-port",
        "8123",
        *gateway_args,
        piped=piped,
        acquire_only=False,
    )
    assert result.returncode == 0, result.stderr
    handoff = (source_fixture / "handoff.log").read_text().splitlines()
    assert handoff[0].endswith("/scripts") is False
    assert handoff[1:4] == [
        "https://github.com/Learning-Control-Center/Learning-Control-Center.git",
        "refs/heads/main",
        SHA,
    ]
    assert handoff[4:] == [
        "--commit",
        SHA,
        "--non-interactive",
        "--domain",
        "lcc.example.test",
        "--app-port",
        "8123",
        *gateway_args,
    ]
    assert _requests(source_fixture) == [GITHUB_REF, GITHUB_RAW, GITHUB_ARCHIVE]
    assert not (source_fixture / "mutations.log").exists()


def test_main_movement_does_not_change_selected_source(source_fixture: Path) -> None:
    result = _run(source_fixture, move_main=True)
    assert result.returncode == 0, result.stderr
    assert _requests(source_fixture) == [GITHUB_REF, GITHUB_RAW, GITHUB_ARCHIVE]
    assert f"Validated exact-SHA source: {SHA}" in result.stdout
    assert '"' + "b" * 40 + '"' in (source_fixture / "ref.json").read_text()


def test_github_failure_does_not_contact_another_provider(source_fixture: Path) -> None:
    (source_fixture / "ref.json").unlink()
    result = _run(source_fixture)
    assert result.returncode != 0
    assert _requests(source_fixture) == [GITHUB_REF]
    assert not (source_fixture / "mutations.log").exists()


@pytest.mark.parametrize(
    "ref_body",
    [
        "garbage",
        "{}",
        '{"ref":"refs/heads/main","object":{"type":"commit","sha":"abc"}}',
        '{"ref":"refs/heads/main","object":{"type":"commit","sha":"' + "A" * 40 + '"}}',
        '{"ref":"refs/heads/other","object":{"type":"commit","sha":"' + SHA + '"}}',
        '{"ref":"refs/heads/main","object":{"type":"tag","sha":"' + SHA + '"}}',
        '{"ref":"refs/heads/main","ref":"refs/heads/main","object":{"type":"commit","sha":"'
        + SHA
        + '"}}',
        "[]",
    ],
)
def test_invalid_or_ambiguous_ref_fails_before_pinned_code(
    source_fixture: Path, ref_body: str
) -> None:
    (source_fixture / "ref.json").write_text(ref_body)
    result = _run(source_fixture)
    assert result.returncode != 0
    assert _requests(source_fixture) == [GITHUB_REF]
    assert not (source_fixture / "mutations.log").exists()
    assert not list((source_fixture / "tmp").iterdir())


def test_explicit_revision_must_match_selected_main(source_fixture: Path) -> None:
    result = _run(source_fixture, "--commit", "b" * 40)
    assert result.returncode != 0
    assert _requests(source_fixture) == [GITHUB_REF]


@pytest.mark.parametrize("arguments", [("--commit=abc",), ("--commit", SHA, "--commit", SHA)])
def test_noncanonical_or_duplicate_explicit_revision_fails_before_network(
    source_fixture: Path, arguments: tuple[str, ...]
) -> None:
    result = _run(source_fixture, *arguments)
    assert result.returncode != 0
    assert _requests(source_fixture) == []


@pytest.mark.parametrize(
    "option",
    [
        "--repository-url",
        "--repository-url=https://forgejo.invalid",
        "--asset-base-url",
        "--ref",
    ],
)
def test_v1_source_overrides_are_rejected_without_provider_fallback(
    source_fixture: Path, option: str
) -> None:
    result = _run(source_fixture, option, "https://forgejo.waqsea.com/")
    assert result.returncode != 0
    assert "fixed to public GitHub" in result.stderr
    assert _requests(source_fixture) == []


@pytest.mark.parametrize(
    "kind", ["truncated", "truncated-tail", "corrupt", "oversized", "missing", "nonexecutable"]
)
def test_invalid_archive_fails_and_cleans_up(source_fixture: Path, kind: str) -> None:
    archive = source_fixture / "source.tar.gz"
    if kind == "truncated":
        archive.write_bytes(archive.read_bytes()[:100])
    elif kind == "truncated-tail":
        archive.write_bytes(archive.read_bytes()[:-1])
    elif kind == "corrupt":
        archive.write_bytes(b"not a gzip archive")
    elif kind == "oversized":
        with archive.open("wb") as stream:
            stream.truncate(268435457)
    elif kind == "missing":
        _archive(archive, omit="scripts/bootstrap.sh")
    else:
        _archive(archive, bootstrap_mode=0o644)
    result = _run(source_fixture)
    assert result.returncode != 0
    assert _requests(source_fixture) == [GITHUB_REF, GITHUB_RAW, GITHUB_ARCHIVE]
    assert not (source_fixture / "mutations.log").exists()
    assert not list((source_fixture / "tmp").iterdir())


@pytest.mark.parametrize(
    "bad_member",
    [
        "../outside",
        "/absolute",
        f"Learning-Control-Center-Learning-Control-Center-{SHA[:7]}/../escape",
        f"Learning-Control-Center-Learning-Control-Center-{SHA[:7]}/README.md",
    ],
)
def test_archive_rejects_unsafe_paths_and_duplicates(source_fixture: Path, bad_member: str) -> None:
    _archive(source_fixture / "source.tar.gz", extra=[_member(bad_member, b"bad")])
    result = _run(source_fixture)
    assert result.returncode != 0
    assert not list((source_fixture / "tmp").iterdir())


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_archive_rejects_links_and_special_members(source_fixture: Path, kind: bytes) -> None:
    root = f"Learning-Control-Center-Learning-Control-Center-{SHA[:7]}"
    _archive(source_fixture / "source.tar.gz", extra=[_member(f"{root}/unsafe", kind=kind)])
    result = _run(source_fixture)
    assert result.returncode != 0
    assert not list((source_fixture / "tmp").iterdir())


def test_archive_rejects_privileged_modes_and_file_directory_collision(
    source_fixture: Path,
) -> None:
    root = f"Learning-Control-Center-Learning-Control-Center-{SHA[:7]}"
    _archive(source_fixture / "source.tar.gz", bootstrap_mode=0o4755)
    privileged = _run(source_fixture)
    assert privileged.returncode != 0
    assert "privileged mode" in privileged.stderr

    (source_fixture / "requests.log").unlink()
    _archive(
        source_fixture / "source.tar.gz",
        extra=[_member(f"{root}/scripts/bootstrap.sh/child", b"bad")],
    )
    collision = _run(source_fixture)
    assert collision.returncode != 0
    assert "file/directory collision" in collision.stderr
    assert not list((source_fixture / "tmp").iterdir())


def test_archive_rejects_claimed_expanded_size_before_extraction(source_fixture: Path) -> None:
    root = f"Learning-Control-Center-Learning-Control-Center-{SHA[:7]}"
    large = tarfile.TarInfo(f"{root}/large.bin")
    large.mode = 0o644
    large.size = 536870913
    with gzip.open(source_fixture / "source.tar.gz", "wb") as compressed:
        compressed.write(large.tobuf())
        compressed.write(b"\0" * 1024)
    result = _run(source_fixture)
    assert result.returncode != 0
    assert "expanded size exceeds limit" in result.stderr
    assert not list((source_fixture / "tmp").iterdir())


def test_untrusted_inherited_pin_cannot_skip_ref_resolution(source_fixture: Path) -> None:
    environment = {
        **os.environ,
        "LCC_V2_PINNED_SHA": SHA,
        "TMPDIR": str(source_fixture / "tmp"),
    }
    result = subprocess.run(
        ["bash", str(BOOTSTRAP)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Pinned state is valid only" in result.stderr
    assert not list((source_fixture / "tmp").iterdir())


def test_pinned_handoff_preserves_argument_array(source_fixture: Path) -> None:
    pinned = source_fixture / "bootstrap.sh"
    pinned.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$LCC_V2_PINNED_SHA" "$@" '
        '> "$LCC_GITHUB_FIXTURE/args.log"\n'
    )
    arguments = ["--non-interactive", "--domain", "lcc.example.test", "--app-port", "8123"]
    result = _run(source_fixture, *arguments, piped=True)
    assert result.returncode == 0, result.stderr
    assert (source_fixture / "args.log").read_text().splitlines() == [SHA, *arguments]
    assert _requests(source_fixture) == [GITHUB_REF, GITHUB_RAW]


def test_ubuntu_bootstrap_is_a_thin_generic_compatibility_entry() -> None:
    legacy = ROOT / "scripts/bootstrap-ubuntu.sh"
    assert legacy.is_file() and os.access(legacy, os.X_OK)
    assert "bootstrap.sh" in legacy.read_text()
    assert "apt-get" not in legacy.read_text()


@pytest.mark.parametrize("piped", [False, True])
def test_ubuntu_compatibility_entry_forwards_to_generic_bootstrap(
    source_fixture: Path, piped: bool
) -> None:
    env = {
        **os.environ,
        "PATH": f"{source_fixture / 'bin'}:{os.environ['PATH']}",
        "LCC_GITHUB_FIXTURE": str(source_fixture),
        "TMPDIR": str(source_fixture / "tmp"),
        "LCC_BOOTSTRAP_TESTING": "1",
        "LCC_BOOTSTRAP_TEST_ACQUIRE_ONLY": "1",
    }
    command = (
        ["bash", "-s", "--", "--non-interactive"]
        if piped
        else ["bash", str(LEGACY), "--non-interactive"]
    )
    result = subprocess.run(
        command,
        input=LEGACY.read_text() if piped else None,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    expected = [GITHUB_REF, GITHUB_RAW, GITHUB_ARCHIVE]
    if piped:
        expected.insert(
            0,
            "https://raw.githubusercontent.com/Learning-Control-Center/"
            "Learning-Control-Center/main/scripts/bootstrap.sh",
        )
    assert _requests(source_fixture) == expected


def test_piped_ubuntu_compatibility_rejects_old_source_before_network(
    source_fixture: Path,
) -> None:
    result = subprocess.run(
        ["bash", "-s", "--", "--repository-url", "https://forgejo.example.invalid/repo.git"],
        input=LEGACY.read_text(),
        env={
            **os.environ,
            "PATH": f"{source_fixture / 'bin'}:{os.environ['PATH']}",
            "LCC_GITHUB_FIXTURE": str(source_fixture),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "GitHub-only V2 installer" in result.stderr
    assert _requests(source_fixture) == []
