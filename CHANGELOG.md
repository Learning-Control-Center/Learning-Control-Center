# Changelog

All notable public changes to Learning Control Center are recorded here. The project follows
[Semantic Versioning](https://semver.org/) for published release identities.

## [1.0.1] - Unreleased

### Fixed

- Production administration ignores caller-local dotenv files and runs offline commands from the
  active release, including password recovery from an inaccessible working directory.
- The first account consumes bootstrap authority without making a pre-finalization restart fatal;
  password recovery now manages Core and backup-timer lifecycle from one administrator command.

### Added

- Human-first main installation with secure generated production configuration and guarded
  first-user bootstrap-token handling.
- A canonical current-`main` channel that resolves validated public code once to an exact Git
  commit on every explicit install/update and never follows the moving branch automatically.
- Installed channel/source metadata and channel-aware update and rollback records.
- Ubuntu 24.04 prerequisite provisioning through the host's configured authenticated APT policy,
  limited to named missing LCC dependencies.
- A persistent installed updater, also available through `lcc-admin update`, that resolves the
  recorded repository's current `main` revision and migrates pinned-release installations only
  after confirmation.
- Configurable loopback-only application ports with install-time conflict handling, explicit
  non-interactive selection, update persistence, and transactional `lcc-admin app-port` changes.
- Generic `bootstrap.sh`, `install.sh`, `update.sh`, and `uninstall.sh` entry points, with the
  historical Ubuntu names retained as thin compatibility forwarders.
- A gateway-independent Core with managed Caddy or an explicit same-host external proxy mode,
  and a shared V1 migration, update, rollback, and uninstall transition.
- Single-port serving of the verified frontend, SPA routes, and API by the application, with
  managed Caddy and external gateways forwarding the complete site.
- Deferred public-origin configuration for Core and external installs, managed through a guarded
  `lcc-admin public-origin` transaction.

### Changed

- Releases remain optional immutable snapshots with a deterministic release-bound `install.sh`
  whose embedded archive digest must agree with the published checksum.
- Production hosts now consume a verified frontend artifact built during release packaging, so
  Node.js/npm are no longer server dependencies.
- Re-running the main bootstrap now installs, safely updates through the canonical engine, reports
  an exact-SHA no-op, or explicitly confirms release-to-main migration as appropriate.
- Ubuntu prerequisite provisioning no longer reconstructs APT repository provenance, isolates
  package lists, or compares `SIGNED_BY` metadata. APT authenticates and selects packages under
  the host administrator's policy; unrelated third-party repositories are left untouched.
- Normal install/update acquisition uses the public GitHub SHA-addressed bootstrap and source
  archive without requiring Git on the production host.

## [1.0.0] - 2026-09-21

Initial public release.

### Added

- Single-user learning control workflows spanning competency targets, Curriculum, Projects,
  Activities, Sessions, Evidence, verification, Roadmap, Analysis, Recommendations, and Today.
- Deterministic analytics, reports, explanations, capability projection, and recovery tooling.
- Safe import inspection, dry-run, diff, confirmation, pre-import backup, transactional apply, and
  portable logical backup/restore behavior.
- Ubuntu Server 24.04 LTS installer, hardened systemd services, Caddy HTTPS integration, scheduled
  operational backups, administrator commands, controlled updates, rollback, and preserve-by-default
  uninstall.
- Version-pinned GitHub release bootstrap and deterministic release archive/checksum tooling.

### Validation boundaries

- Automated browser coverage includes Chromium, Firefox, and Playwright WebKit.
- Final screenshot-based visual/product review is complete and the final independent critic reported
  no P0/P1 findings.
- Unavailable real Windows, macOS, mobile-device, and screen-reader checks remain `Not executed` as
  recorded in `frontend/qa/platform-qa-ledger.json`.

[1.0.0]: https://github.com/Learning-Control-Center/Learning-Control-Center/releases/tag/v1.0.0
[1.0.1]: https://github.com/Learning-Control-Center/Learning-Control-Center/compare/v1.0.0...main
