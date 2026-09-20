# Phase 3 Product QA

Macro Phase 3 prepares repeatable product-quality evidence. It does not perform or replace the separate Final Visual / Product QA screenshot campaign.

## Disposable realistic fixture

Build and run the populated 25-node product fixture for human inspection:

```bash
.venv/bin/python scripts/product_fixture_harness.py visual-qa-inspect \
  --timezone UTC \
  --clock 2026-09-19T10:00:00Z
```

The command prints the temporary frontend URL, fixture credentials, repository/build/fixture identity, manifest path, and cleanup behavior. It uses a new temporary SQLite database and never opens the configured user database. Press `Ctrl+C` to stop the processes and delete the fixture.

The machine-readable route/state/viewport inventory is `frontend/qa/visual-product-qa-manifest.json`. Validate every manifest entry without taking screenshots by running:

```bash
.venv/bin/python scripts/product_fixture_harness.py checkpoint6-playwright \
  --timezone UTC \
  --clock 2026-09-19T10:00:00Z
```

The command is an aggregate gate with a fresh disposable stack for each bounded stage: Profile/Learn/Projects, the daily control loop, Roadmap/shell, and the final release matrix. This prevents one mutation-heavy scenario from contaminating another while still running the complete critical flows in desktop Chromium and touch-enabled mobile Chromium. The release stage executes Analysis refresh → Today regeneration → actual-work creation → timer start/pause/reload/resume/complete → explicit replacement at all six target viewports, plus focused landscape-touch Chromium, Firefox, and Playwright WebKit coverage of navigation, forms, Roadmap, retry behavior, and the complete Today → Activity timer lifecycle.

The checked-in evidence for the validated candidate is deliberately small and reviewable:

- `frontend/qa/checkpoint6-release-evidence.json` binds the four-stage result to the exact source revision, candidate-runtime hash, production-build hash, fixture hash, browser versions, and performance artifact.
- `frontend/qa/wcag-2.2-aa-evidence.json` maps the declared WCAG 2.2 A/AA scope to concrete automated, component, code-review, and product-review evidence.
- `frontend/qa/roadmap-first-use-review.json` records the bounded first-use Roadmap comprehension review required by Phase 3. It is not the later screenshot campaign.
- `frontend/qa/platform-qa-ledger.json` separates executed engine automation from real platform/device/assistive-technology checks. Unavailable checks remain `Not executed`.

The automated small-height check proves that a focused Activity input can be scrolled into the 568×320 viewport. It is only a precheck; it does not claim a real mobile virtual keyboard or safe-area pass.

Install the browser runtimes in a normal CI image with:

```bash
npx --prefix frontend playwright install --with-deps firefox webkit
```

When a managed runner cannot install system packages but supplies a distro-matched extracted runtime library directory, set `LCC_WEBKIT_LIBRARY_PATH` to that directory. The harness passes it only to Playwright WebKit and records the actual browser/tool versions in the release evidence. This compatibility seam does not turn WebKit into Safari certification; Chromium emulation likewise does not certify Android.

## Real-platform smoke record

Use the same populated fixture and the steps below. Record the source revision and candidate-runtime hash, production-build and fixture hashes, OS/device version, browser version, assistive-technology version, viewport/orientation, result, evidence location, and issue link in `frontend/qa/platform-qa-ledger.json`.

Allowed results are `Passed`, `Failed`, `Blocked`, and `Not executed`. Never infer a pass from another browser engine, device emulation, axe, or a different screen reader.

Run this bounded checklist on each applicable platform/browser combination:

1. Sign in and confirm the skip link moves focus to the main landmark.
2. Traverse primary navigation by keyboard; on compact layouts open and dismiss the navigation drawer, confirm focus return, then navigate and confirm focus moves to the destination heading.
3. Open Today, review the no-debt copy, and verify status and role do not depend on color.
4. Follow one Today item to Activity, start a Session, pause, reload, resume, and complete it. Confirm the timer remains authoritative and announcements occur only on state changes.
5. Use “Do something else,” select or create actual work, cancel once, then explicitly confirm replacement. Confirm no relation is created before confirmation.
6. Open Profile, a competency detail, and the focused Roadmap destination. Confirm current capability and Profile target remain distinct.
7. In Roadmap Outline, search, expand/collapse a domain, reveal prerequisites, open/close detail, and use the non-drag X/Y position control.
8. Where Map is available, activate its explicit interaction mode, pan/zoom with the platform input, exit with Escape/back, and confirm ordinary page scrolling remains available.
9. Open Learn and Projects, inspect blocked/Unknown/available states, and follow one actual-work handoff without committing a mutation.
10. Open Analysis and Recommendation, inspect an explanation and immutable lineage, then open V1 Analytics and Generated Reports and confirm the read-only/compatibility labels.
11. At portrait and landscape sizes, verify no page-level horizontal overflow, clipped primary controls, inaccessible sheet actions, or content hidden by the virtual keyboard/safe area.
12. Enable reduced motion and platform high-contrast/forced-color settings where available; repeat navigation and Roadmap view switching.
13. With the named screen reader, traverse landmarks/headings, navigation, forms, Today status changes, Roadmap Outline/details, and chart exact-value tables. Confirm names, roles, states, focus, and restrained announcements.
14. Trigger one recoverable network error using the browser's offline/devtools facility, restore connectivity, and use the visible retry without losing unrelated content or form input.

Platform coverage required for final human/device validation:

- Windows: Chrome, Edge, and Firefox; NVDA with a representative Chromium browser and Firefox.
- Linux: Chrome/Chromium and Firefox; Orca where a reproducible environment is practical.
- macOS: Safari, Chrome, and Firefox; VoiceOver with Safari.
- Android: Chrome on a real touch-capable phone or tablet; TalkBack.
- iOS/iPadOS: Safari on real iPhone and iPad-class devices; VoiceOver.

Rows that cannot be exercised in the current environment remain `Not executed`. Phase 3 completion does not convert those rows to passes; they remain explicit work for the separate final human/device campaign.

## Final Visual / Product QA handoff

The later campaign must use a production build, the disposable fixture, the named manifest entries, and recorded fixture/build identity. It then captures screenshots, performs visual analysis, ranks fixes by severity, re-screenshots corrected states, and obtains an independent product review. None of those screenshot-adjudication steps are claimed by Macro Phase 3.
