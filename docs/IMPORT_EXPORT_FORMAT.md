# Learning-Control-Center V1 Import / Export Format

## Purpose and authority

This is the single user- and AI-facing reference for the current V1 wire format. Exact JSON field names and validation rules come from the running application's Pydantic models, serializers, import validators, and persistence mappings. The application is always the final validator.

The import screen accepts JSON packages of type `roadmap_update`, `roadmap_replace`, `verification_update`, `state_update`, `portable_logical_backup`, or `restore`. Analysis Snapshots are JSON export-only artifacts. Human Reports are Markdown export-only artifacts. Operational SQLite backups are internal recovery files, not import packages.

All JSON objects described as strict reject unknown fields. Inspect is a non-mutating dry run; apply requires the returned confirmation token, creates a pre-import SQLite backup, runs in a transaction, validates integrity, and either commits completely or rolls back.

## Artifact matrix

| Artifact/type | Format | Importable | Exportable | Selective | Full state | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| `analysis_snapshot` | JSON envelope | no | yes | yes | no | Roadmap-aware data for analysis |
| Human Report | Markdown | no | yes | yes | no | Readable scoped summary |
| `portable_logical_backup` | JSON envelope | yes | yes | no | yes | Secret-free portable learning state |
| `restore` | JSON envelope | yes | no | no | yes | Accepted alias for restoring a portable payload |
| `roadmap_update` | JSON envelope | yes | no | n/a | complete incoming version | Add and activate a complete roadmap version |
| `roadmap_replace` | JSON envelope | yes | no | n/a | complete incoming version | V1 alias of `roadmap_update` |
| `verification_update` | JSON envelope | yes | no | n/a | no | Append verification records and implied statuses |
| `state_update` | JSON envelope | yes | no | n/a | no | Explicit competency status changes |
| Operational SQLite backup | SQLite file | internal only | internal only | no | complete database | Pre-mutation and operational recovery |

`roadmap_update` and `roadmap_replace` intentionally have identical V1 behavior: each payload describes one complete new roadmap version, not a patch. Existing stable identities and their history are preserved; definitions absent from the new active version are no longer active. A `(roadmap stable_key, version)` pair cannot be imported twice.

## JSON package envelope

```json
{
  "schemaVersion": 1,
  "packageType": "roadmap_update",
  "packageId": "example-package-001",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {}
}
```

## Verification and state updates

`verification_update` has payload `{"verifications": [verification, ...]}`. A verification requires `competency_identity_id`, `verification_source`, `method`, and `result`. It may contain `confidence` (null or 0..100), `reviewer_label`, `evidence_summary`, `notes`, and `evidence` (default `[]`). Evidence requires `kind` (1..64 characters) and non-empty `reference`; `description` defaults to `""`. The identity is the application's database UUID, not a stable key.

Verification sources are `self`, `automated`, and `external`; results are `passed`, `failed`, and `partial`. Apply records history and implies status `verified`, `needs_review`, or `practicing`, respectively.

`state_update` has payload `{"states": [state, ...]}`. A state requires `competency_identity_id` and `status`; `reason` defaults to `"Imported status update"`. A `verified` state also requires a `verification` object for the same identity with result `passed`. Other statuses must not include `verification`. Both package payloads are strict, validate references during inspect, and are preflighted against current state.

## Analysis Snapshot

Create this export with purpose `analysis_snapshot`, format `json`, and optional scope fields:

- `range`: `7d`, `30d`, `90d`, `all`, or `custom` (default `all`)
- `start_date`, `end_date`: ISO local dates (`YYYY-MM-DD`), both required for `custom`
- `current_phase_only`: boolean, default false
- `track_ids`: active-version database UUIDs
- `competency_identity_ids`: active-version competency UUIDs
- `categories`: any of `roadmap`, `analytics`, `sessions`, `verification`, `reports`, `settings`; empty means all

The envelope payload always contains `scope`. It records the request plus `resolved_start_date`, `resolved_end_date`, `timezone`, `resolved_categories`, and resolved selected track/competency labels and stable keys. Requested selectors are intersected. Unknown selectors are rejected.

Selected category keys are `roadmap` (filtered active roadmap/version/phase/track/competency context), `analytics` (canonical deterministic output), `sessions` (stored rows in range), `verification` (stored records in range), `reports` (overlapping immutable report snapshots), and `safeSettings` (discipline profile or null). Empty or undefined ratios remain null/`N/A` according to their source; omission is not zero. Authentication secrets are never included.

Representative shape (IDs and metric details vary):

```json
{
  "schemaVersion": 1,
  "packageType": "analysis_snapshot",
  "packageId": "6db58de0-794d-49ec-86f6-2ea9bdaf84e1",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {
    "scope": {
      "range": "7d", "start_date": null, "end_date": null, "current_phase_only": false,
      "track_ids": [], "competency_identity_ids": [], "categories": ["roadmap"],
      "resolved_start_date": "2026-08-29", "resolved_end_date": "2026-09-04", "timezone": "UTC",
      "resolved_categories": ["roadmap"], "selected_tracks": [], "selected_competencies": []
    },
    "roadmap": {
      "id": "generated-uuid", "stableKey": "example.roadmap", "title": "Example",
      "activeVersion": "1.0.0", "currentPhaseId": "generated-uuid", "phases": []
    }
  }
}
```

Analysis Snapshots are not accepted by import.

## Human Report

Create with purpose `human_report`, format `markdown`, and the same selective scope fields as Analysis Snapshot. The Markdown begins with the export instant, requested range preset, resolved local-date period and timezone, included categories, current-phase flag, and resolved track/competency selection, followed by a readable summary. It is not a JSON package and cannot be imported or restored. It does not mutate immutable generated reports or user reflections.

```markdown
# Learning-Control-Center export

Exported: 2026-09-04T18:30:00.000Z
Range preset: custom
Resolved period: 2026-09-01 to 2026-09-04 (Europe/Istanbul)
Included categories: roadmap, analytics
Current phase only: yes
Tracks: Programming (track.programming)
Competencies: Functions (programming.functions)

## Summary

- Total learning duration: 3600000 ms
```

## Portable Logical Backup and restore

Portable export uses purpose `portable_logical_backup`, format `json`, ignores selective scope, and emits the normal envelope with `packageType: "portable_logical_backup"` and payload `{"tables": {...}}`. Every table below is required, even when its value is an empty array:

`roadmaps`, `roadmap_versions`, `phases`, `tracks`, `competency_identities`, `competency_definitions`, `competency_prerequisites`, `competency_understanding_items`, `competency_ability_items`, `exit_criterion_identities`, `exit_criterion_definitions`, `competency_states`, `verification_records`, `verification_evidence`, `competency_status_events`, `learning_sessions`, `daily_reflections`, `generated_reports`, `recommendation_snapshots`, `discipline_profiles`, `import_records`, `export_records`, `application_settings`.

Every row must contain exactly every database column for that table. Use an application-produced export as the template; portable rows use internal database IDs and are not intended for hand authoring. `users`, `auth_sessions`, and `operational_backups` are excluded, as are password hashes, session/CSRF tokens, and backup filesystem paths.

Restore accepts `portable_logical_backup` or `restore` with the same payload. It is never a merge: an empty learning state may restore directly; non-empty learning state requires explicit full-replacement confirmation. Authentication users and active sessions remain intact. Import inspection validates exact table/column coverage, values, foreign keys, one-current-roadmap pointers, verified-state evidence, and required-dependency cycles in a disposable database before mutation.

### Representative empty portable package

This valid package represents an empty portable learning state. Non-empty exports use the same keys with exact database rows.

```json
{
  "schemaVersion": 1,
  "packageType": "portable_logical_backup",
  "packageId": "example-empty-portable-v1",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {
    "tables": {
      "roadmaps": [],
      "roadmap_versions": [],
      "phases": [],
      "tracks": [],
      "competency_identities": [],
      "competency_definitions": [],
      "competency_prerequisites": [],
      "competency_understanding_items": [],
      "competency_ability_items": [],
      "exit_criterion_identities": [],
      "exit_criterion_definitions": [],
      "competency_states": [],
      "verification_records": [],
      "verification_evidence": [],
      "competency_status_events": [],
      "learning_sessions": [],
      "daily_reflections": [],
      "generated_reports": [],
      "recommendation_snapshots": [],
      "discipline_profiles": [],
      "import_records": [],
      "export_records": [],
      "application_settings": []
    }
  }
}
```

## Time, duration, and enum reference

- Envelope `createdAt` is a string; application exports produce RFC 3339 UTC.
- Roadmap packages contain no timestamps or durations.
- Portable and Analysis Snapshot stored-row timestamps are integer UTC epoch milliseconds. Nullable instants are JSON null.
- Canonical elapsed duration is integer `duration_ms`; never convert it to floating-point minutes in machine-readable data.
- Local period dates are `YYYY-MM-DD`, resolved using the stored IANA timezone. Safe settings may contain that timezone.
- Competency status: `not_started`, `learning`, `practicing`, `ready_for_verification`, `verified`, `needs_review`.
- Priority: `core`, `important`, `supporting`, `optional`.
- Prerequisite kind in portable data: `required`, `recommended`.
- Exit-criterion state in portable data: `not_met`, `partial`, `met`.
- Verification source: `self`, `automated`, `external`; result: `passed`, `failed`, `partial`.
- Activity type: `learning`, `reading`, `practice`, `coding`, `debugging`, `project`, `review`, `verification`, `research`.
- Assistance mode: `none`, `docs_only`, `ai_hint`, `ai_assisted`, `agent_led`.
- Completed session outcome: `completed`, `partial`, `blocked`; portable cancelled timers can contain `cancelled`.

## Actual invalid cases

Each fragment below is invalid in its containing object:

```json
{"priority": "critical"}
```

```json
{"weight": 6}
```

```json
{"prerequisite_stable_keys": ["missing.key"]}
```

```json
{"unknown_critical_field": true}
```

A package where `a` requires `b` and `b` requires `a` creates a rejected required cycle. Repeated phase/track/competency stable keys, repeated phase `order_index`, a missing required field, an unknown field, an unsupported `schemaVersion`, an unsupported `packageType`, and a previously applied `packageId` are also rejected.

## Instructions for external AI systems

When generating data for Learning-Control-Center:

1. Emit valid JSON without comments, ellipses, or extra prose when JSON is requested.
2. Copy exact snake_case roadmap field names; do not translate them to camelCase.
3. Treat each roadmap import as one complete version, never a partial patch.
4. Preserve roadmap, competency, and exit-criterion stable identities across revisions.
5. Use only documented enum strings and integer millisecond durations.
6. Resolve every parent and prerequisite key to a supplied competency and avoid required/hierarchy cycles.
7. Do not add database IDs, statuses, timestamps, archived flags, or exit states to roadmap payloads.
8. Never include credentials, cookies, tokens, password hashes, or other secrets.
9. Distinguish absent/null/undefined data from numeric zero and respect the requested export scope.
10. Require application inspect/dry-run and human review before apply.

## Realistic disposable roadmap package

This synthetic package is for demonstrations and populated-state testing, not a personal learning roadmap.

```json
{
  "schemaVersion": 1,
  "packageType": "roadmap_replace",
  "packageId": "example-realistic-roadmap-v1",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {
    "roadmap": {
      "stable_key": "example.systems-learning",
      "title": "Disposable Systems Learning Roadmap",
      "description": "Synthetic data for testing populated application states.",
      "version": "1.0.0",
      "changelog": "Initial disposable example.",
      "source": "format-guide",
      "current_phase_stable_key": "phase.foundations",
      "phases": [
        {
          "stable_key": "phase.foundations", "title": "Foundations", "description": "Core tools and programming concepts.", "order_index": 0,
          "tracks": [
            {"stable_key": "track.computing", "title": "Computing", "description": "System fundamentals.", "order_index": 0, "competencies": [
              {"stable_key": "systems.basics", "title": "System Basics", "description": "Understand processes and files.", "goal": "Navigate a local system confidently.", "priority": "core", "weight": 4, "order_index": 0, "parent_stable_key": null, "prerequisite_stable_keys": [], "recommended_prerequisite_stable_keys": [], "must_understand": ["Processes", "Files and directories"], "must_be_able_to": ["Inspect a running process"], "exit_criteria": [{"stable_key": "inspect-process", "text": "Inspect and explain a running process.", "required": true, "weight": 4}], "position_x": 80, "position_y": 80},
              {"stable_key": "systems.command-line", "title": "Command Line", "description": "Use a shell safely.", "goal": "Complete common shell workflows.", "priority": "important", "weight": 3, "order_index": 1, "parent_stable_key": "systems.basics", "prerequisite_stable_keys": ["systems.basics"], "recommended_prerequisite_stable_keys": [], "must_understand": ["Commands and arguments"], "must_be_able_to": ["Navigate and inspect files"], "exit_criteria": [{"stable_key": "shell-workflow", "text": "Complete a file inspection workflow safely.", "required": true, "weight": 3}], "position_x": 340, "position_y": 80}
            ]},
            {"stable_key": "track.programming", "title": "Programming", "description": "Programming foundations.", "order_index": 1, "competencies": [
              {"stable_key": "programming.syntax", "title": "Syntax and Values", "description": "Work with values and control flow.", "goal": "Write small correct programs.", "priority": "core", "weight": 4, "order_index": 0, "parent_stable_key": null, "prerequisite_stable_keys": [], "recommended_prerequisite_stable_keys": [], "must_understand": ["Values", "Control flow"], "must_be_able_to": ["Write a small program"], "exit_criteria": [{"stable_key": "small-program", "text": "Write and explain a small program.", "required": true, "weight": 4}], "position_x": 80, "position_y": 260},
              {"stable_key": "programming.functions", "title": "Functions", "description": "Design reusable functions.", "goal": "Decompose behavior into testable units.", "priority": "core", "weight": 5, "order_index": 1, "parent_stable_key": "programming.syntax", "prerequisite_stable_keys": ["programming.syntax"], "recommended_prerequisite_stable_keys": [], "must_understand": ["Parameters and return values"], "must_be_able_to": ["Extract a reusable function"], "exit_criteria": [{"stable_key": "function-design", "text": "Design and test a focused function.", "required": true, "weight": 5}], "position_x": 340, "position_y": 260},
              {"stable_key": "programming.testing", "title": "Automated Testing", "description": "Write deterministic tests.", "goal": "Protect behavior with focused tests.", "priority": "important", "weight": 4, "order_index": 2, "parent_stable_key": "programming.syntax", "prerequisite_stable_keys": ["programming.functions"], "recommended_prerequisite_stable_keys": ["systems.command-line"], "must_understand": ["Assertions and isolation"], "must_be_able_to": ["Test success and failure cases"], "exit_criteria": [{"stable_key": "focused-tests", "text": "Write deterministic tests for a small module.", "required": true, "weight": 4}], "position_x": 600, "position_y": 260}
            ]}
          ]
        },
        {
          "stable_key": "phase.building", "title": "Building", "description": "Web and data capabilities.", "order_index": 1,
          "tracks": [
            {"stable_key": "track.web", "title": "Web", "description": "HTTP applications.", "order_index": 0, "competencies": [
              {"stable_key": "web.http", "title": "HTTP Fundamentals", "description": "Understand web request semantics.", "goal": "Reason about HTTP exchanges.", "priority": "core", "weight": 4, "order_index": 0, "parent_stable_key": null, "prerequisite_stable_keys": ["systems.basics"], "recommended_prerequisite_stable_keys": [], "must_understand": ["Methods and status codes"], "must_be_able_to": ["Inspect an HTTP exchange"], "exit_criteria": [{"stable_key": "explain-exchange", "text": "Explain a complete HTTP request and response.", "required": true, "weight": 4}], "position_x": 80, "position_y": 80},
              {"stable_key": "web.api-design", "title": "API Design", "description": "Design explicit service contracts.", "goal": "Build a coherent small API.", "priority": "core", "weight": 5, "order_index": 1, "parent_stable_key": "web.http", "prerequisite_stable_keys": ["web.http", "programming.functions"], "recommended_prerequisite_stable_keys": ["programming.testing"], "must_understand": ["Resources and validation"], "must_be_able_to": ["Specify an API contract"], "exit_criteria": [{"stable_key": "api-contract", "text": "Implement and test a small validated API.", "required": true, "weight": 5}], "position_x": 340, "position_y": 80}
            ]},
            {"stable_key": "track.data", "title": "Data", "description": "Persistent data design.", "order_index": 1, "competencies": [
              {"stable_key": "data.sql", "title": "SQL Fundamentals", "description": "Query relational data.", "goal": "Write safe relational queries.", "priority": "important", "weight": 4, "order_index": 0, "parent_stable_key": null, "prerequisite_stable_keys": ["systems.basics"], "recommended_prerequisite_stable_keys": [], "must_understand": ["Tables and joins"], "must_be_able_to": ["Write a parameterized query"], "exit_criteria": [{"stable_key": "relational-query", "text": "Write and explain a multi-table query.", "required": true, "weight": 4}], "position_x": 80, "position_y": 280},
              {"stable_key": "data.modeling", "title": "Data Modeling", "description": "Model constraints explicitly.", "goal": "Design a consistent relational model.", "priority": "supporting", "weight": 3, "order_index": 1, "parent_stable_key": "data.sql", "prerequisite_stable_keys": ["data.sql"], "recommended_prerequisite_stable_keys": ["web.api-design"], "must_understand": ["Keys and constraints"], "must_be_able_to": ["Model a small domain"], "exit_criteria": [{"stable_key": "constrained-model", "text": "Design a model with enforced invariants.", "required": true, "weight": 3}], "position_x": 340, "position_y": 280}
            ]}
          ]
        },
        {
          "stable_key": "phase.delivery", "title": "Delivery", "description": "Operate and communicate a system.", "order_index": 2,
          "tracks": [{"stable_key": "track.delivery", "title": "Delivery", "description": "Source control and operations.", "order_index": 0, "competencies": [
            {"stable_key": "delivery.version-control", "title": "Version Control", "description": "Manage coherent changes.", "goal": "Create reviewable history.", "priority": "important", "weight": 3, "order_index": 0, "parent_stable_key": null, "prerequisite_stable_keys": [], "recommended_prerequisite_stable_keys": ["systems.command-line"], "must_understand": ["Commits and branches"], "must_be_able_to": ["Prepare a focused commit"], "exit_criteria": [{"stable_key": "focused-commit", "text": "Create and explain a focused commit.", "required": true, "weight": 3}], "position_x": 80, "position_y": 100},
            {"stable_key": "delivery.deployment", "title": "Deployment", "description": "Deliver a service safely.", "goal": "Deploy and verify a small service.", "priority": "core", "weight": 5, "order_index": 1, "parent_stable_key": null, "prerequisite_stable_keys": ["systems.command-line", "web.api-design"], "recommended_prerequisite_stable_keys": ["delivery.version-control"], "must_understand": ["Configuration and rollback"], "must_be_able_to": ["Deploy and smoke-test a service"], "exit_criteria": [{"stable_key": "safe-deploy", "text": "Deploy, verify, and describe rollback.", "required": true, "weight": 5}], "position_x": 340, "position_y": 100},
            {"stable_key": "delivery.documentation", "title": "Technical Documentation", "description": "Communicate system behavior.", "goal": "Write concise operational documentation.", "priority": "optional", "weight": 2, "order_index": 2, "parent_stable_key": null, "prerequisite_stable_keys": [], "recommended_prerequisite_stable_keys": ["delivery.deployment"], "must_understand": ["Audience and scope"], "must_be_able_to": ["Write a focused runbook"], "exit_criteria": [{"stable_key": "usable-runbook", "text": "Write a runbook another person can follow.", "required": false, "weight": 2}], "position_x": 600, "position_y": 100}
          ]}]
        }
      ]
    }
  }
}
```

### Envelope field reference

| Name | JSON type | Required | Allowed value/semantics | Example |
| --- | --- | --- | --- | --- |
| `schemaVersion` | integer | yes | Exactly `1` | `1` |
| `packageType` | string | yes | One accepted import type listed above; exports use `analysis_snapshot` or `portable_logical_backup` | `roadmap_update` |
| `packageId` | string | yes | Non-empty, at most 255 characters; must not have been applied before | `example-package-001` |
| `appVersion` | string | yes | Producer application version; recorded, not semantically compared | `1.0.0` |
| `createdAt` | string | yes | Producer timestamp; exports emit RFC 3339 UTC | `2026-09-04T18:30:00.000Z` |
| `payload` | object | yes | Strict package-specific object | `{"roadmap": {...}}` |

Unknown envelope fields are rejected. A successfully applied `packageId` is recorded and later inspection of that ID returns `IMPORT_PACKAGE_DUPLICATE`. The server rejects an unsupported schema version before mutation.

## Roadmap package schema

A roadmap package has exactly one payload field, `roadmap`.

| Object | Field | Type | Required | Rules/default |
| --- | --- | --- | --- | --- |
| roadmap | `stable_key` | string | yes | 1..255; stable roadmap identity |
| roadmap | `title` | string | yes | 1..255 |
| roadmap | `description` | string | no | default `""` |
| roadmap | `version` | string | yes | 1..64; unique for this roadmap identity |
| roadmap | `changelog` | string | no | default `""` |
| roadmap | `source` | string | no | default `"manual"` |
| roadmap | `current_phase_stable_key` | string | yes | must name a supplied phase |
| roadmap | `phases` | array | yes | at least one phase |
| phase | `stable_key` | string | yes | unique within package |
| phase | `title` | string | yes | 1..255 |
| phase | `description` | string | no | default `""` |
| phase | `order_index` | integer | yes | non-negative; unique per version in storage |
| phase | `tracks` | array | no | default `[]` |
| track | `stable_key` | string | yes | unique across the version |
| track | `title` | string | yes | 1..255 |
| track | `description` | string | no | default `""` |
| track | `order_index` | integer | yes | non-negative |
| track | `competencies` | array | no | default `[]` |
| competency | `stable_key` | string | yes | stable competency identity; unique across version |
| competency | `title` | string | yes | 1..255 |
| competency | `description` | string | no | default `""` |
| competency | `goal` | string | no | default `""` |
| competency | `priority` | string | yes | priority enum below |
| competency | `weight` | integer | yes | 1..5 |
| competency | `order_index` | integer | yes | non-negative |
| competency | `parent_stable_key` | string/null | no | supplied competency key; default null |
| competency | `prerequisite_stable_keys` | string array | no | required dependencies; default `[]` |
| competency | `recommended_prerequisite_stable_keys` | string array | no | advisory dependencies; default `[]` |
| competency | `must_understand` | string array | no | ordered definition items; default `[]` |
| competency | `must_be_able_to` | string array | no | ordered definition items; default `[]` |
| competency | `exit_criteria` | array | no | ordered criteria; default `[]` |
| competency | `position_x`, `position_y` | integer/null | no | persisted graph position; default null |
| exit criterion | `stable_key` | string | yes | 1..255; stable within competency identity |
| exit criterion | `text` | string | yes | non-empty |
| exit criterion | `required` | boolean | no | default `true` |
| exit criterion | `weight` | integer/null | no | null or 1..5 |

Relationships use stable keys, never database IDs or package-local IDs. Nesting assigns a competency to its containing phase and track. Imports do not accept competency status, exit-criterion state, archived flags, internal IDs, or timestamps in roadmap payloads.

### Stable identity rules

`roadmap.stable_key` identifies a roadmap across versions. Each competency `stable_key` globally identifies the same competency across roadmap versions, preserving status, sessions, and verification history when its title, definition, track, phase, or graph position changes. Change a key only when the concept is genuinely a different competency.

An exit criterion is identified by `(competency stable_key, exit criterion stable_key)`. Reuse that key when wording or weight changes but the criterion remains the same; use a new key for a different criterion. Titles and display text are not identities.

### Roadmap validation

The application rejects duplicate phase, track, or competency keys; duplicate persisted phase ordering; duplicate roadmap versions; missing current phase; missing parent or prerequisite references; required-prerequisite cycles; hierarchy cycles; invalid enum values; out-of-range weights/order indexes; and unknown fields. Inspect runs the actual apply logic against an isolated copy of current state, so predictable persistence conflicts are reported before confirmation. Transactional apply and post-apply integrity checks remain defense in depth.

## Minimal valid roadmap package

This disposable example is a complete version and can be copied as valid JSON.

```json
{
  "schemaVersion": 1,
  "packageType": "roadmap_update",
  "packageId": "example-minimal-roadmap-v1",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {
    "roadmap": {
      "stable_key": "example.minimal-roadmap",
      "title": "Disposable Minimal Roadmap",
      "description": "Small format-validation example.",
      "version": "1.0.0",
      "changelog": "Initial disposable example.",
      "source": "format-guide",
      "current_phase_stable_key": "phase.foundations",
      "phases": [{
        "stable_key": "phase.foundations",
        "title": "Foundations",
        "description": "One example phase.",
        "order_index": 0,
        "tracks": [{
          "stable_key": "track.fundamentals",
          "title": "Fundamentals",
          "description": "One example track.",
          "order_index": 0,
          "competencies": [{
            "stable_key": "example.core-concept",
            "title": "Explain the Core Concept",
            "description": "A disposable competency used to validate the package format.",
            "goal": "Explain and demonstrate the concept independently.",
            "priority": "core",
            "weight": 3,
            "order_index": 0,
            "parent_stable_key": null,
            "prerequisite_stable_keys": [],
            "recommended_prerequisite_stable_keys": [],
            "must_understand": ["The concept's purpose"],
            "must_be_able_to": ["Demonstrate the concept"],
            "exit_criteria": [{"stable_key": "explain-and-demonstrate", "text": "Explain and demonstrate the concept without step-by-step assistance.", "required": true, "weight": 3}],
            "position_x": 120,
            "position_y": 100
          }]
        }]
      }]
    }
  }
}
```
