# Changelog

All notable public changes to Learning Control Center are recorded here. The project follows
[Semantic Versioning](https://semver.org/) for published release identities.

## [1.0.1] - Unreleased

### Added

- Human-first stable installation with secure generated production configuration and guarded
  first-user bootstrap-token handling.
- An explicit DEVELOPMENT / UNSTABLE `main` channel that resolves once to an exact Git commit and
  never follows the branch automatically.
- Installed channel/source metadata and channel-aware update and rollback records.

### Changed

- Stable releases now include a deterministic release-bound `install.sh` whose embedded archive
  digest must agree with the published checksum.

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
