# Changelog

All notable public changes to Learning Control Center are recorded here. The project follows
[Semantic Versioning](https://semver.org/) for published release identities.

## [1.0.1] - Unreleased

### Added

- Human-first stable installation with secure generated production configuration and guarded
  first-user bootstrap-token handling.
- An explicit current-`main` channel that resolves validated public code once to an exact Git
  commit and never follows the moving branch automatically.
- Installed channel/source metadata and channel-aware update and rollback records.
- Automatic Ubuntu 24.04 prerequisite provisioning from signed Ubuntu repositories.
- A persistent installed updater, also available through `lcc-admin update`, that discovers the
  newest final stable release or the current exact `main` revision without changing channel.

### Changed

- Stable releases now include a deterministic release-bound `install.sh` whose embedded archive
  digest must agree with the published checksum.
- Production hosts now consume a verified frontend artifact built during release packaging, so
  Node.js/npm are no longer server dependencies.
- Re-running a stable release launcher now installs, safely updates through the canonical engine,
  reports an exact-version no-op, or refuses an implicit downgrade as appropriate.

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
