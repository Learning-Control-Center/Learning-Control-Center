# Public promotion and optional release snapshots

Normal installation and update use public GitHub `main` selected to one full commit SHA. Tags and
Releases are optional immutable snapshots; the historical `v1.0.0` release assets are never
rewritten. Private Forgejo holds development `dev` and may mirror sanitized `main`, but it is
not an installer source.

## Promote validated source

Development changes live on private `dev`. After validation, prepare a sanitized candidate from
existing public `main` without merging private ancestry:

```bash
scripts/prepare-public-promotion.sh \
  --source-ref dev --public-base main --output-dir /tmp/lcc-public-candidate
```

Review the isolated candidate, its complete diff, privacy/history checks, tests, frontend artifact,
and public documentation. An actual local-main ref change and any remote publication are separate,
explicitly approved operations. Never merge, fast-forward, rebase, or push private `dev` to GitHub.
Neither `AGENTS.md` nor `memory-bank/` may be at the public tip or in its reachable history. The
public candidate must include the generic `bootstrap.sh`, `install.sh`, `update.sh`, `uninstall.sh`,
Ubuntu adapter, gateway assets, and compatibility wrappers. Candidate validation does not prove
that GitHub has already served the new bootstrap; test that separately after publication.

## Optional versioned release

Only after a sanitized public commit has been deliberately published and a release tag has been
created by the release operator, package that exact tagged commit. The repository must be clean:

```bash
scripts/package-release.sh \
  --repository /path/to/sanitized/public/checkout \
  --source-ref vNEXT --release-id vNEXT --output-dir /tmp/lcc-release-assets
```

`vNEXT` above is a placeholder, not an existing release. The packager verifies the tracked
frontend artifact, rebuilds from `package-lock.json`, rejects a mismatch, and creates a
byte-deterministic archive and checksum. It removes private/runtime paths and checks required
source, scripts, deployment assets, metadata, symlinks, and executable modes. It renders a small
version-bound `install.sh` with the exact tag, source commit, and archive SHA-256 embedded. This
installer downloads only that GitHub release archive over HTTPS, verifies the embedded digest,
validates member safety and layout, and hands off to the generic installer. A V2 pinned release
installer is intended for a **fresh** host; an existing installation uses the public-main update
path. Its version and source cannot be overridden on the command line. Release publication is
not part of normal main-first installation or update discovery.

Review assets and private-path exclusions, then upload only the intended files to the explicit
GitHub release. Do not claim a tag or release exists until it has actually been created. An
optional private Forgejo mirror does not change the canonical installer source. Read-only smoke
checks of the published asset URLs, digest, archive layout, pinned fresh install, and normal
public-main install must follow publication. Public ACME and real-server lifecycle acceptance are
separate gates from local/disposable packaging and test TLS.
