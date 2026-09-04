# Learning-Control-Center Import / Export Format Guide

> **Audience:** Humans and AI systems  
> **Scope:** Learning-Control-Center V1 import/export packages

## Purpose

Learning-Control-Center uses versioned JSON packages for machine-readable import/export operations and Markdown for human-readable reports.

This guide is intended to help humans and external AI systems:

- understand Learning-Control-Center exports;
- generate roadmap-oriented import packages;
- prepare roadmap updates or replacements;
- interpret verification/state update packages;
- understand Analysis Snapshot and Portable Logical Backup exports.

The application remains the final validator for every import. Never infer package semantics from the filename alone.

## Common JSON envelope

```json
{
  "schemaVersion": 1,
  "packageType": "analysis_snapshot",
  "packageId": "2a778e95-8238-4c58-b858-b60a9eefdb8f",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {}
}
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `schemaVersion` | integer | yes | Import/export schema version. |
| `packageType` | string | yes | Defines package semantics. |
| `packageId` | string | yes | Unique package identity. |
| `appVersion` | string | yes | App version that created the package. |
| `createdAt` | RFC 3339 UTC timestamp | yes | Package creation instant. |
| `payload` | object | yes | Package-specific data. |

Canonical machine-readable package types include:

- `analysis_snapshot`
- `backup`
- `roadmap_update`
- `roadmap_replace`
- `verification_update`
- `state_update`
- `restore`

General rules:

- A mutating `packageId` must not be reused to produce duplicate effects.
- Unsupported future schema versions must be rejected safely.
- Known older versions may require tested migrations.
- Unknown critical data must never be silently dropped.
- Canonical elapsed durations use integer milliseconds.
- Instant timestamps use UTC semantics.

## Export types

### Analysis Snapshot

**Format:** JSON  
**Package type:** `analysis_snapshot`

Selective machine-readable context for humans or external AI systems.

Possible scope includes:

- current roadmap;
- current phase;
- selected tracks;
- selected competencies;
- verification;
- learning sessions;
- reports;
- analytics/signals;
- safe settings;
- date ranges.

It is not required to contain enough data to restore the complete application.

### Human Report

**Format:** Markdown

A selective human-readable report. It may be shared directly with an AI for analysis.

It should state:

- export date;
- included date range;
- included scope.

Markdown is never a canonical restore format.

### Portable Logical Backup

**Format:** JSON

Complete portable non-secret learning/application state.

It may include:

- roadmap identities and versions;
- phases and tracks;
- competency identities/definitions;
- prerequisites;
- exit criteria and state;
- competency status/history;
- verification records/evidence;
- learning sessions;
- reflections;
- generated reports;
- recommendation history;
- non-secret discipline/application settings;
- import/export history required for consistency.

It excludes:

- password hashes;
- live session tokens;
- CSRF secrets;
- bootstrap secrets;
- application secrets;
- authentication credentials.

Portable Logical Backup and operational SQLite backup are different concepts.

### Operational SQLite Backup

Server-side SQLite-safe backup.

It may contain authentication state and therefore requires stronger storage protection than portable logical backups.

## Roadmap package semantics

Roadmap-oriented mutating package types:

- `roadmap_update`
- `roadmap_replace`

### `roadmap_update`

Use for compatible changes while preserving stable identity/history.

Typical changes:

- add or update nodes;
- change titles/descriptions;
- change priorities/weights;
- move nodes between phase/track definitions;
- add/remove prerequisites;
- update exit criteria;
- update graph layout;
- archive nodes.

### `roadmap_replace`

Use when intentionally replacing the active roadmap definition.

Historical learning/verification data must still be protected according to stable identity rules.

## Canonical roadmap hierarchy

```text
Roadmap
└── Roadmap Version
    ├── Phase
    │   └── Track
    │       └── Competency Definition
    └── Relationships
        ├── Prerequisites
        ├── Exit Criteria
        └── Graph/Layout Metadata
```

## Stable identity

### Competency stable keys

Examples:

```text
python.functions
git.branching
linux.permissions
http.caching
backend.authentication
```

Rules:

- Stable keys represent semantic identity.
- Titles are presentation, not identity.
- Harmless title changes must not require new stable keys.
- Do not reuse one stable key for a different concept.
- Fundamental semantic changes require a new stable key.

### Exit-criterion stable keys

Exit criteria also use stable keys scoped to the competency identity.

Example:

```text
python.functions.write-basic-function
```

If the same competency and criterion stable key appear in a compatible later roadmap version, criterion state may be preserved.

If the meaning changes materially, use a new stable key.

## Canonical enums

### Competency priority

```text
core
important
supporting
optional
```

### Competency weight

Integer:

```text
1..5
```

### Competency status

```text
not_started
learning
practicing
ready_for_verification
verified
needs_review
```

### Prerequisite kind

```text
required
recommended
```

Required dependency cycles are invalid.

### Exit-criterion state

```text
not_met
partial
met
```

Exit criteria also carry a `required` boolean.

### Verification source

```text
self
automated
external
```

### Verification result

```text
passed
failed
partial
```

### Assistance mode

```text
none
docs_only
ai_hint
ai_assisted
agent_led
```

### Activity type

```text
learning
reading
coding
debugging
project
review
verification
research
```

## Canonical roadmap concepts

### Roadmap

Contains semantic identity, title, description, and current-version relationship.

### Roadmap Version

Contains roadmap relationship, version number, schema version, changelog/source metadata, and creation time.

Once configured, exactly one roadmap version is current.

### Phase

Contains stable key, title, description, order, and active/current state.

Once configured, exactly one phase is current.

### Track

Contains stable key, phase relationship, title, description, and ordering.

### Competency

Canonical properties include:

- globally stable competency key;
- phase/track placement;
- optional parent competency;
- title;
- description;
- goal;
- priority;
- weight;
- current status;
- ordering;
- active/archived state;
- optional graph position;
- must-understand items;
- must-be-able-to items;
- prerequisites;
- exit criteria.

### Prerequisite

Links a competency to a prerequisite competency with:

```text
required | recommended
```

### Exit criterion

Includes stable key, owner competency, text, required flag, current state, and ordering/weight where supported.

## Semantic roadmap package example

> This example documents the canonical semantics. The running application remains the final authority for exact serialized property names and import validation.

```json
{
  "schemaVersion": 1,
  "packageType": "roadmap_replace",
  "packageId": "85e5c3a7-96a6-4d1c-a437-e144b600179b",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {
    "roadmap": {
      "stableKey": "technical-foundations",
      "title": "Technical Foundations",
      "description": "Disposable example roadmap."
    },
    "version": {
      "version": 1,
      "schemaVersion": 1,
      "changelog": "Initial example",
      "source": "external"
    },
    "phases": [
      {
        "stableKey": "phase.foundations",
        "title": "Foundations",
        "description": "Core foundations.",
        "orderIndex": 0,
        "active": true
      }
    ],
    "tracks": [
      {
        "stableKey": "track.programming",
        "phaseStableKey": "phase.foundations",
        "title": "Programming",
        "description": "Programming fundamentals.",
        "orderIndex": 0
      }
    ],
    "competencies": [
      {
        "stableKey": "python.functions",
        "phaseStableKey": "phase.foundations",
        "trackStableKey": "track.programming",
        "title": "Python Functions",
        "description": "Understand and write reusable Python functions.",
        "goal": "Write and reason about small functions independently.",
        "priority": "core",
        "weight": 5,
        "status": "not_started",
        "orderIndex": 0,
        "active": true,
        "mustUnderstand": [
          "parameters",
          "arguments",
          "return values",
          "local scope"
        ],
        "mustBeAbleTo": [
          "write a function from a requirement",
          "call a function correctly",
          "explain its inputs and output"
        ],
        "exitCriteria": [
          {
            "stableKey": "python.functions.write-basic-function",
            "text": "Write a basic function independently.",
            "required": true,
            "state": "not_met"
          }
        ]
      }
    ],
    "prerequisites": []
  }
}
```

Before importing a hand-authored or AI-generated package, always use the application's validation/dry-run flow.

## Dependency example

```json
{
  "competencyStableKey": "http.caching",
  "prerequisiteStableKey": "http.basics",
  "kind": "required"
}
```

Every reference must resolve. Required dependencies must not form cycles.

## Verification/state updates

External update packages may update supported domains such as:

- roadmap nodes;
- priorities/weights;
- prerequisites;
- exit criteria;
- verification records;
- selected competency state.

They must never modify authentication or secrets.

Verification behavior:

- `passed` -> `verified`
- `partial` -> `practicing`
- `failed` -> `needs_review`

Every transition to `verified` requires a verification record.

Verification history is preserved.

## Import safety pipeline

Every mutating import conceptually follows:

1. type/size check;
2. parse;
3. schema validation;
4. compatibility validation;
5. semantic validation;
6. stable-key/reference validation;
7. dependency-cycle validation;
8. duplicate package-ID check;
9. dry run;
10. human-readable diff;
11. automatic pre-import backup;
12. explicit confirmation;
13. transaction;
14. apply;
15. post-apply integrity validation;
16. commit;
17. import-history recording.

Failure must roll back canonical state.

Selecting a file must never mutate data by itself.

## Validation rules

Generated packages should obey:

- stable-key uniqueness requirements;
- competency identity must not be derived from title;
- weight is integer `1..5`;
- every reference resolves;
- required prerequisite graph contains no cycle;
- enum values use exact serialized values;
- mutating package IDs are unique;
- unsupported schema versions are rejected;
- historical learning/verification state is not casually destroyed;
- portable packages exclude secrets;
- durations are integer milliseconds;
- instant timestamps use UTC semantics.

## Invalid examples

### Invalid weight

```json
{
  "stableKey": "python.functions",
  "weight": 9
}
```

Reason: weight must be `1..5`.

### Broken prerequisite

```json
{
  "competencyStableKey": "http.caching",
  "prerequisiteStableKey": "does.not.exist",
  "kind": "required"
}
```

Reason: prerequisite reference does not resolve.

### Invalid prerequisite kind

```json
{
  "competencyStableKey": "http.caching",
  "prerequisiteStableKey": "http.basics",
  "kind": "mandatory"
}
```

Reason: only `required` and `recommended` are canonical.

### Invalid package type

```json
{
  "schemaVersion": 1,
  "packageType": "random_data",
  "packageId": "3b9966bd-a119-4612-92be-4af0f361e6d4",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:40:00.000Z",
  "payload": {}
}
```

Reason: unsupported package type.

## Instructions for external AI systems

When this file is provided to an AI system:

1. Treat Learning-Control-Center data as structured application state.
2. Follow the package envelope and current schema.
3. Never invent enum values.
4. Never use display titles as canonical identity.
5. Generate durable semantic stable keys.
6. Preserve existing stable keys when updating an existing roadmap.
7. Ensure every reference resolves.
8. Never create a required dependency cycle.
9. Keep competency weights within `1..5`.
10. Preserve verification history instead of reducing it to a Boolean.
11. Never include passwords, tokens, CSRF secrets, bootstrap secrets, or other credentials.
12. Do not include comments inside JSON.
13. When asked for an import package, output valid JSON only unless explanation is requested.
14. When analyzing an export, distinguish measured data from inference.
15. Do not infer package semantics from filename.
16. Treat `schemaVersion`, `packageType`, and stable identity as authoritative metadata.
17. If the running application schema and this guide disagree on an exact serialized field, report the mismatch instead of silently guessing.

## Guidance for analyzing exports

When an AI receives an Analysis Snapshot or Human Report:

- respect the included date range and scope;
- do not assume omitted data is zero;
- treat `N/A` as undefined/not applicable, not zero;
- distinguish self, automated, and external verification;
- distinguish assistance modes;
- do not treat AI-assisted work as invalid;
- preserve activity-type distinctions;
- use exact `duration_ms` values when available;
- never invent missing sessions or competencies.

## Guidance for roadmap generation

A good generated roadmap should:

- represent competencies rather than vague tasks;
- use meaningful tracks/phases;
- use stable semantic keys;
- use priorities intentionally;
- use weights `1..5`;
- use `required` prerequisites only when truly blocking;
- use `recommended` prerequisites for helpful sequencing;
- define goals;
- define what must be understood;
- define what the learner must be able to do;
- define verifiable exit criteria;
- avoid huge flat lists;
- avoid circular required dependencies;
- avoid using roadmap nodes as daily task-manager entries.

## Compatibility note

This guide documents the canonical V1 semantics available from the current Learning-Control-Center specification context.

The running server remains the final authority for exact wire-schema validation.

Before applying an AI-generated or hand-authored package:

1. run application validation/dry-run;
2. inspect the diff;
3. confirm stable identities/references;
4. do not apply the package if the app reports a schema mismatch.

The import system is intentionally designed to fail safely rather than accept guessed or ambiguous state.
