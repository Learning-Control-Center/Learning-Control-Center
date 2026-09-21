"""Disposable source and APT fixtures for bootstrap prerequisite isolation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts" / "bootstrap-ubuntu.sh"
UBUNTU_KEYRING = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
UBUNTU_SOURCE = (
    "Types: deb\n"
    "URIs: http://archive.ubuntu.com/ubuntu\n"
    "Suites: noble noble-updates\n"
    "Components: main universe\n"
    f"Signed-By: {UBUNTU_KEYRING}\n"
)
FAKE_APT = r"""#!/usr/bin/env python3
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
options = {}
while arguments[:1] == ["-o"]:
    key, value = arguments[1].split("=", 1)
    options[key] = value
    arguments = arguments[2:]

sourceparts = Path(options["Dir::Etc::sourceparts"])
view_root = sourceparts.parent
required = {
    "Dir::Etc::sourcelist": str(view_root / "empty.list"),
    "Dir::Etc::main": str(view_root / "empty.conf"),
    "Dir::Etc::preferences": str(view_root / "empty.pref"),
    "Dir::Etc::trusted": str(view_root / "empty.gpg"),
    "Dir::State::status": "/var/lib/dpkg/status",
    "APT::Update::Error-Mode": "any",
    "APT::Get::AllowUnauthenticated": "false",
    "Acquire::AllowInsecureRepositories": "false",
    "DPkg::Lock::Timeout": "0",
}
for key, value in required.items():
    if options.get(key) != value:
        raise SystemExit(f"Missing isolated APT option {key}={value}")
if os.environ.get("APT_CONFIG") != str(view_root / "apt.conf"):
    raise SystemExit("APT_CONFIG did not exclude host configuration")
if f'Dir::Etc::parts "{view_root / "empty.d"}";' not in (view_root / "apt.conf").read_text():
    raise SystemExit("APT_CONFIG did not preclude host configuration fragments")

sources = list(sourceparts.iterdir())
if len(sources) != 1 or sources[0].name != "ubuntu.sources":
    raise SystemExit("APT sourceparts includes an unselected source")
source = sources[0].read_text()
if any(host in source for host in ("docker.com", "cloudsmith.io", "cloudflare.com")):
    raise SystemExit("A third-party repository entered the isolated APT view")
lists = Path(options["Dir::State::lists"])
archives = Path(options["Dir::Cache::archives"])
if lists == Path("/var/lib/apt/lists") or archives == Path("/var/cache/apt/archives"):
    raise SystemExit("APT uses global package lists or archives")
if lists.parent != sourceparts.parent or archives.parent != sourceparts.parent:
    raise SystemExit("APT view state does not share the temporary source root")
if options["Dir::Etc::preferencesparts"] != str(sourceparts.parent / "empty.d"):
    raise SystemExit("Host package preferences entered the APT view")
if options["Dir::Etc::trustedparts"] != str(sourceparts.parent / "empty.d"):
    raise SystemExit("Host trusted keys entered the APT view")
Path(os.environ["LCC_FAKE_APT_VIEW_ROOT_CAPTURE"]).write_text(str(view_root))

commands = {"update", "indextargets", "check", "policy", "install"}
command = next((item for item in arguments if item in commands), "")
command_index = arguments.index(command)
remaining = arguments[:command_index] + arguments[command_index + 1:]
log = Path(os.environ["LCC_FAKE_APT_LOG"])
with log.open("a") as stream:
    stream.write(f"{Path(sys.argv[0]).name} {command} {' '.join(remaining)}\n")

if command == "update":
    if os.environ.get("LCC_FAKE_APT_LOCKED") == "1":
        print("Could not get APT lock", file=sys.stderr)
        raise SystemExit(100)
    if os.environ.get("LCC_FAKE_APT_BROKEN_VENDOR") == "1" and "docker.com" in source:
        print("Unrelated vendor source is unreachable", file=sys.stderr)
        raise SystemExit(100)
    if os.environ.get("LCC_FAKE_APT_BAD_SIGNATURE") == "1":
        print("Ubuntu InRelease signature verification failed", file=sys.stderr)
        raise SystemExit(100)
    lists.joinpath("ubuntu_Packages").write_text("Ubuntu package metadata\n")
elif command == "indextargets":
    for paragraph in source.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines())
        for suite in fields["Suites"].split():
            origin = "Vendor" if os.environ.get("LCC_FAKE_APT_BAD_ORIGIN") == "1" else "Ubuntu"
            print(
                "|".join(
                    ("Packages", origin, "Ubuntu", suite, fields["URIs"], fields["Signed-By"])
                )
            )
elif command == "check":
    if os.environ.get("LCC_FAKE_APT_BROKEN_DPKG") == "1":
        print("Broken dependency state", file=sys.stderr)
        raise SystemExit(100)
elif command == "policy":
    global_lists = Path(os.environ["LCC_FAKE_GLOBAL_LISTS"])
    if lists == global_lists and global_lists.joinpath("vendor_Packages").exists():
        print("  Candidate: 99:vendor")
    elif arguments[-1] == "caddy" and "universe" not in source:
        print("  Candidate: (none)")
    else:
        print("  Candidate: 1:ubuntu")
elif command == "install":
    if os.environ.get("LCC_FAKE_APT_INSTALL_FAILURE") == "1":
        print("Ubuntu dependency resolution failed", file=sys.stderr)
        raise SystemExit(100)
else:
    raise SystemExit(f"Unexpected APT command: {command}")
"""


def _host(tmp_path: Path, third_party: dict[str, str] | None = None) -> dict[str, Path]:
    apt_root = tmp_path / "apt"
    sources = apt_root / "sources.list.d"
    sources.mkdir(parents=True)
    (sources / "ubuntu.sources").write_text(UBUNTU_SOURCE)
    for name, content in (third_party or {}).items():
        (sources / name).write_text(content)
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_get = binaries / "apt-get"
    fake_get.write_text(FAKE_APT)
    fake_get.chmod(0o755)
    (binaries / "apt-cache").symlink_to(fake_get)
    global_lists = tmp_path / "global-lists"
    global_lists.mkdir()
    return {
        "apt_root": apt_root,
        "sources": sources,
        "os_release": os_release,
        "binaries": binaries,
        "global_lists": global_lists,
        "fake_log": tmp_path / "fake-apt.log",
        "source_capture": tmp_path / "isolated-ubuntu.sources",
        "view_root_capture": tmp_path / "apt-view-root.txt",
    }


def _run(
    tmp_path: Path,
    host: dict[str, Path],
    *,
    missing: str = "python3-venv,sqlite3",
    overrides: dict[str, str] | None = None,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PATH": f"{host['binaries']}:{os.environ['PATH']}",
        "LCC_BOOTSTRAP_TESTING": "1",
        "LCC_BOOTSTRAP_TEST_EXECUTE_APT": "1",
        "LCC_BOOTSTRAP_OS_RELEASE": str(host["os_release"]),
        "LCC_BOOTSTRAP_TEST_APT_SOURCE_ROOT": str(host["apt_root"]),
        "LCC_BOOTSTRAP_TEST_APT_SOURCE_CAPTURE": str(host["source_capture"]),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": missing,
        "LCC_FAKE_APT_LOG": str(host["fake_log"]),
        "LCC_FAKE_APT_VIEW_ROOT_CAPTURE": str(host["view_root_capture"]),
        "LCC_FAKE_GLOBAL_LISTS": str(host["global_lists"]),
        "LCC_BOOTSTRAP_ASSET_BASE_URL": (tmp_path / "missing-assets").as_uri(),
        **(overrides or {}),
    }
    command = [
        str(BOOTSTRAP),
        "--channel",
        "stable",
        "--ref",
        "v1.0.1",
        "--domain",
        "lcc.example.test",
        "--timezone",
        "UTC",
        "--non-interactive",
    ]
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(command, capture_output=True, text=True, env=environment, check=False)


DOCKER = (
    "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] "
    "https://download.docker.com/linux/ubuntu noble stable\n"
)
CADDY = (
    "Types: deb\nURIs: https://dl.cloudsmith.io/public/caddy/stable/deb/debian\n"
    "Suites: any-version\nComponents: main\n"
    "Signed-By: /usr/share/keyrings/caddy-stable-archive-keyring.gpg\n"
)
CLOUDFLARE = (
    "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
    "https://pkg.cloudflare.com/cloudflared any main\n"
)


@pytest.mark.parametrize(
    "third_party",
    [
        {},
        {"docker.list": DOCKER},
        {"caddy.sources": CADDY},
        {"cloudflare.list": CLOUDFLARE},
        {
            "docker.list": DOCKER,
            "caddy.sources": CADDY,
            "cloudflare.list": CLOUDFLARE,
        },
    ],
    ids=["ubuntu-only", "docker", "cloudsmith-caddy", "cloudflare", "all-vendors"],
)
def test_only_selected_ubuntu_sources_supply_missing_prerequisites(
    tmp_path: Path, third_party: dict[str, str]
) -> None:
    host = _host(tmp_path, third_party)
    original = {path.name: path.read_bytes() for path in host["sources"].iterdir()}
    result = _run(tmp_path, host)
    assert "failed during phase: stable release acquisition" in result.stderr
    assert "isolated Ubuntu 24.04 signed repositories" in result.stdout
    assert [line.split(" ", 2)[:2] for line in host["fake_log"].read_text().splitlines()] == [
        ["apt-get", "update"],
        ["apt-get", "indextargets"],
        ["apt-get", "check"],
        ["apt-cache", "policy"],
        ["apt-cache", "policy"],
        ["apt-get", "install"],
    ]
    assert (
        "install --no-install-recommends --yes python3-venv sqlite3" in host["fake_log"].read_text()
    )
    assert "upgrade" not in host["fake_log"].read_text()
    assert "http://archive.ubuntu.com/ubuntu" in host["source_capture"].read_text()
    assert not any(
        vendor in host["source_capture"].read_text()
        for vendor in ("docker.com", "cloudsmith.io", "cloudflare.com")
    )
    assert {path.name: path.read_bytes() for path in host["sources"].iterdir()} == original
    assert not Path(host["view_root_capture"].read_text()).exists()


def test_broken_unrelated_repository_and_stale_vendor_metadata_are_ignored(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path, {"docker.list": DOCKER})
    (host["global_lists"] / "vendor_Packages").write_text("python3-venv 99:vendor\n")
    result = _run(tmp_path, host, overrides={"LCC_FAKE_APT_BROKEN_VENDOR": "1"})
    assert "failed during phase: stable release acquisition" in result.stderr
    assert "apt-cache policy python3-venv" in host["fake_log"].read_text()
    assert (
        "install --no-install-recommends --yes python3-venv sqlite3" in host["fake_log"].read_text()
    )
    assert (host["global_lists"] / "vendor_Packages").read_text() == "python3-venv 99:vendor\n"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("", "No trusted Ubuntu 24.04 APT source"),
        (
            UBUNTU_SOURCE.replace(UBUNTU_KEYRING, "/usr/share/keyrings/vendor.gpg"),
            "package-owned archive keyring",
        ),
        (
            UBUNTU_SOURCE.replace("archive.ubuntu.com", "spoofed.example.test"),
            "No trusted Ubuntu 24.04 APT source",
        ),
        (UBUNTU_SOURCE.replace("noble noble-updates", "jammy"), "Ubuntu 24.04 (Noble)"),
        ("Enabled: no\n" + UBUNTU_SOURCE, "No trusted Ubuntu 24.04 APT source"),
    ],
)
def test_missing_or_invalid_official_source_fails_before_apt(
    tmp_path: Path, source: str, message: str
) -> None:
    host = _host(tmp_path)
    (host["sources"] / "ubuntu.sources").write_text(source)
    result = _run(tmp_path, host)
    assert message in result.stderr
    assert not host["fake_log"].exists()


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("LCC_FAKE_APT_BAD_SIGNATURE", "metadata refresh failed"),
        ("LCC_FAKE_APT_BAD_ORIGIN", "unexpected package index"),
        ("LCC_FAKE_APT_BROKEN_DPKG", "APT dependency state is broken"),
        ("LCC_FAKE_APT_LOCKED", "metadata refresh failed"),
        ("LCC_FAKE_APT_INSTALL_FAILURE", "prerequisite installation failed"),
    ],
)
def test_real_ubuntu_apt_failures_are_not_ignored(
    tmp_path: Path, failure: str, message: str
) -> None:
    host = _host(tmp_path, {"docker.list": DOCKER})
    result = _run(tmp_path, host, overrides={failure: "1"})
    assert message in result.stderr
    assert not Path(host["view_root_capture"].read_text()).exists()


def test_existing_caddy_is_not_reinstalled_and_missing_caddy_uses_ubuntu_view(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path, {"caddy.sources": CADDY})
    existing = _run(tmp_path, host, missing="sqlite3")
    assert "stable release acquisition" in existing.stderr
    assert "install --no-install-recommends --yes sqlite3" in host["fake_log"].read_text()
    assert "install --no-install-recommends --yes caddy" not in host["fake_log"].read_text()

    host["fake_log"].unlink()
    missing = _run(tmp_path, host, missing="caddy")
    assert "stable release acquisition" in missing.stderr
    assert "install --no-install-recommends --yes caddy" in host["fake_log"].read_text()
    assert "cloudsmith.io" not in host["source_capture"].read_text()


def test_missing_caddy_requires_ubuntu_universe(tmp_path: Path) -> None:
    host = _host(tmp_path, {"caddy.sources": CADDY})
    (host["sources"] / "ubuntu.sources").write_text(UBUNTU_SOURCE.replace("main universe", "main"))
    result = _run(tmp_path, host, missing="caddy")
    assert "caddy has no installable candidate" in result.stderr
    assert "apt-get install" not in host["fake_log"].read_text()


def test_legacy_one_line_source_and_idempotent_prerequisite_check(tmp_path: Path) -> None:
    host = _host(tmp_path)
    (host["sources"] / "ubuntu.sources").unlink()
    (host["sources"] / "noble-official.list").write_text(
        "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] "
        "http://archive.ubuntu.com/ubuntu noble main universe\n"
    )
    first = _run(tmp_path, host, missing="caddy")
    assert "stable release acquisition" in first.stderr
    assert "install --no-install-recommends --yes caddy" in host["fake_log"].read_text()

    host["fake_log"].unlink()
    second = _run(tmp_path, host, missing="")
    assert "Ubuntu prerequisites are already satisfied" in second.stdout
    assert not host["fake_log"].exists()


@pytest.mark.parametrize(
    ("filename", "content", "architecture", "expected_uri"),
    [
        (
            "sources.list",
            "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] "
            "http://tr.archive.ubuntu.com/ubuntu noble main universe\n",
            "x86_64",
            "http://tr.archive.ubuntu.com/ubuntu",
        ),
        (
            "sources.list.d/security-mirror.sources",
            "Types: deb\nURIs: http://ports.ubuntu.com/ubuntu-ports\n"
            "Suites: noble noble-security\nComponents: main universe\n"
            "Architectures: arm64\n"
            f"Signed-By: {UBUNTU_KEYRING}\n",
            "aarch64",
            "http://ports.ubuntu.com/ubuntu-ports",
        ),
    ],
)
def test_official_source_discovery_supports_file_and_architecture_variants(
    tmp_path: Path,
    filename: str,
    content: str,
    architecture: str,
    expected_uri: str,
) -> None:
    host = _host(tmp_path)
    (host["sources"] / "ubuntu.sources").unlink()
    source = host["apt_root"] / filename
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(content)
    result = _run(
        tmp_path,
        host,
        dry_run=True,
        overrides={"LCC_BOOTSTRAP_TEST_ARCHITECTURE": architecture},
    )
    assert "DRY-RUN: would run apt-get update" in result.stdout
    assert f"URIs: {expected_uri}" in host["source_capture"].read_text()


def test_dry_run_discovers_sources_without_mutating_apt(tmp_path: Path) -> None:
    host = _host(tmp_path, {"cloudflare.list": CLOUDFLARE})
    result = _run(tmp_path, host, dry_run=True)
    assert "DRY-RUN: would run apt-get update and install" in result.stdout
    assert "stable release acquisition" in result.stderr
    assert not host["fake_log"].exists()
    assert "cloudflare.com" not in host["source_capture"].read_text()
