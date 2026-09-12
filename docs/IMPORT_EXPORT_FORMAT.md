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

The required table set includes all application-produced keys in the representative package below. It includes immutable profile, semantic competency, criterion, activation, built-in capability-scale, Activity, SessionContribution, contribution-retraction, Session-correction, Evidence, EvidenceLink, Evidence lifecycle, and redaction facts; `projection_invalidations` remains explicitly omitted because it is rebuildable. SessionContribution rows contain attribution only and never contain duration: the linked LearningSession remains the sole canonical owner of exact elapsed `duration_ms`.

Every row must contain exactly every database column for that table. Use an application-produced export as the template; portable rows use internal database IDs and are not intended for hand authoring. `users`, `auth_sessions`, and `operational_backups` are excluded, as are password hashes, session/CSRF tokens, and backup filesystem paths.

Restore accepts `portable_logical_backup` or `restore` with the same payload. It is never a merge: an empty learning state may restore directly; non-empty portable state requires explicit full-replacement confirmation. Authentication users and active sessions remain intact. A pristine automatically created discipline profile counts as empty; changed discipline settings and every other populated portable table count as existing portable state.

Import inspection validates exact table/column coverage, strict canonical scalar types, foreign keys, structured JSON, IANA timezones, complete competency state/history, current-roadmap pointers, phase/track/parent placement, exit-criterion ownership, verified-event evidence, and version-scoped hierarchy and required-dependency cycles in a disposable database before mutation. The same domain-integrity validation runs inside the restore transaction after insertion. Inspection and failed post-restore validation do not mutate the live portable state.

The restore inspection response includes `replacementDiff`, which compares current and incoming portable state by table and by portable domain. It reports existing and incoming row counts plus rows that will be added, modified, or removed, identifies every affected domain, and states that authentication is preserved and merge restore is unsupported.

Current application-produced portable backups use `schemaVersion: 2` and include immutable analysis, profile, semantic competency, activation, legacy-criterion lineage, Activity, Session attribution, unified Evidence, EvidenceLink, lifecycle, redaction, capability-evaluation, criterion-result, capability-event, and review-event history. Capability runs retain their canonical input payload and hashes. The payload also carries `capabilityProjectionCheckpoints`, which bind each exported current subject/scope projection to its immutable run, Evidence-set hash, and expected output hash. Their manifest explicitly omits current capability/review projections and `projection_invalidations`; restore clears the queue, validates retained run lineage against canonical Evidence at each cutoff, deterministically rebuilds the projections, and requires checkpoint output parity before commit. Frozen V1 portable backups remain accepted through a dedicated schema-version dispatch: built-in Technical/CEFR scale facts are added deterministically, every legacy criterion receives one unknown-preserving assertion, every legacy Session receives one deterministic Activity, a non-null legacy competency attribution receives one Primary SessionContribution, and qualifying legacy Session/Verification sources receive provenance-bearing Evidence without inferred strength, source confidence, criterion scope, or capability. Other absent V2 tables are initialized empty, and legacy recommendation rows retain a null analysis link. The inspection summary reports row counts and source/result hashes for those conversions and confirms that no semantic definition, target profile, or capability was inferred. Older valid V1 backups that also lack `roadmap_scope_events` are identified with `portableCompatibility: "legacy_scope_baseline"`; restore creates at most one deterministic current-scope baseline from the current roadmap pointers and that roadmap row's `updated_at`. It never invents earlier phase changes. A current-format restore preserves imported history and records a `portable_restore` event only when it changes the local current scope.

### Representative empty portable package

This valid package represents an empty portable learning state. Non-empty exports use the same keys with exact database rows.

```json
{
  "schemaVersion": 2,
  "packageType": "portable_logical_backup",
  "packageId": "example-empty-portable-v2",
  "appVersion": "1.0.0",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {
    "manifest": {
      "includedCanonicalDomains": ["learning_state", "target_profiles", "semantic_competency_definitions", "capability_scales", "legacy_criterion_assertions", "activities", "session_contributions", "evidence", "evidence_links"],
      "includedImmutableHistory": ["analysis_runs", "analysis_snapshots", "target_profile_activation_events", "competency_definition_activation_events", "migration_backfill_runs", "contribution_retractions", "session_corrections", "evidence_retractions", "evidence_invalidations", "evidence_link_retractions", "evidence_redactions", "capability_evaluation_runs", "criterion_evaluation_results", "capability_state_events", "review_events"],
      "omittedRebuildableState": ["competency_capability_states", "competency_review_states", "projection_invalidations"],
      "restoreActions": ["clear_projection_invalidations", "rebuild_capability_states", "rebuild_review_states", "verify_projection_hash_parity"]
    },
    "capabilityProjectionCheckpoints": [],
    "tables": {
      "roadmaps": [],
      "roadmap_versions": [],
      "phases": [],
      "tracks": [],
      "roadmap_scope_events": [],
      "competency_identities": [],
      "capability_scale_versions": [{"id":"9824edcf-8777-5db1-849a-53602ff566d6","scale_stable_key":"technical","scale_version":"v1","display_name":"Technical","description":"Ordinal technical capability.","created_at":1788912000000},{"id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","scale_stable_key":"cefr","scale_version":"v1","display_name":"CEFR","description":"CEFR-aligned language capability.","created_at":1788912000000}],
      "capability_scale_dimensions": [{"id":"8bc38ccb-2fa6-5ef2-8f10-1aa21e2571b0","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"speaking","display_label":"Speaking","description":"CEFR speaking capability evaluated independently.","order_index":0},{"id":"a6f1922d-8343-5009-83a2-447f0fae3b7d","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"listening","display_label":"Listening","description":"CEFR listening capability evaluated independently.","order_index":1},{"id":"6b81ec2b-8cb5-512d-84f0-05f13c05633a","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"reading","display_label":"Reading","description":"CEFR reading capability evaluated independently.","order_index":2},{"id":"40d6782f-8234-5474-ba2f-052853dcf062","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"writing","display_label":"Writing","description":"CEFR writing capability evaluated independently.","order_index":3},{"id":"8d11dfd0-dd2a-51ab-a589-ba1456ec8566","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"grammar","display_label":"Grammar","description":"CEFR grammar capability evaluated independently.","order_index":4},{"id":"76e1782b-4d2f-5711-a1dc-acd05ba0f91b","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"vocabulary","display_label":"Vocabulary","description":"CEFR vocabulary capability evaluated independently.","order_index":5}],
      "capability_scale_levels": [{"id":"d6ca66a1-5748-5866-bfc0-758d4d150931","scale_version_id":"9824edcf-8777-5db1-849a-53602ff566d6","stable_key":"unexposed","ordinal_rank":0,"display_label":"Unexposed","description":"Explicit current information shows no meaningful encounter or performance; absence of evidence is insufficient.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"6232b598-daed-5db0-afd6-e5e0147f9c67","scale_version_id":"9824edcf-8777-5db1-849a-53602ff566d6","stable_key":"familiar","ordinal_rank":1,"display_label":"Familiar","description":"Can recognize the competency, explain its purpose and vocabulary, and follow representative examples without claiming task performance.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"01b6cbb7-f0a8-5f64-8ecc-83d2b0e53b9a","scale_version_id":"9824edcf-8777-5db1-849a-53602ff566d6","stable_key":"guided","ordinal_rank":2,"display_label":"Guided","description":"Can complete representative bounded tasks when a guide supplies material steps or decisions.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"0e7d329f-9ce0-56d9-8ae1-bb3d51ed1cb2","scale_version_id":"9824edcf-8777-5db1-849a-53602ff566d6","stable_key":"independent","ordinal_rank":3,"display_label":"Independent","description":"Can select an approach, complete representative tasks with ordinary reference documentation, and handle defined common failures.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"26721537-180b-5b96-a5c4-c31aaa321d74","scale_version_id":"9824edcf-8777-5db1-849a-53602ff566d6","stable_key":"strong","ordinal_rank":4,"display_label":"Strong","description":"Repeatedly performs independently across varied non-trivial contexts, explains trade-offs, handles important failures, and produces maintainable outcomes.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"4866ac9f-db1f-51b1-a0cd-10e4d6854cd1","scale_version_id":"9824edcf-8777-5db1-849a-53602ff566d6","stable_key":"advanced","ordinal_rank":5,"display_label":"Advanced","description":"Independently handles complex or novel contexts, adapts alternatives, diagnoses systemic failures, and can design, review, or teach within scope.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"22f1fb19-80e5-59b9-ba89-dd8574c13a80","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"a1","ordinal_rank":1,"display_label":"A1","description":"Can understand and use very basic familiar language and interact simply with substantial contextual support.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"cd466946-41b8-5220-bde7-18d9be4e7f65","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"a2","ordinal_rank":2,"display_label":"A2","description":"Can handle simple routine communication and describe immediate needs or familiar matters with limited complexity.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"41efa7df-8581-5375-9b10-3ed49a8551ab","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"b1","ordinal_rank":3,"display_label":"B1","description":"Can understand clear standard input, manage common independent situations, and produce connected communication on familiar topics.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"f92efda3-02b4-5d98-a67b-e9a09e73c7d8","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"b2","ordinal_rank":4,"display_label":"B2","description":"Can understand complex material, interact with practical fluency, and communicate detailed positions across a broad range of topics.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"e7acb552-19dd-5fac-9725-325b4423fc22","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"c1","ordinal_rank":5,"display_label":"C1","description":"Can understand demanding material, communicate fluently and flexibly, and produce well-structured language for complex purposes.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"},{"id":"61e44204-c18d-508b-962c-c60dce8610a4","scale_version_id":"5edac3a6-1a8a-5b1c-bc3f-88b4efb0cea1","stable_key":"c2","ordinal_rank":6,"display_label":"C2","description":"Can integrate difficult information from varied sources and communicate precisely with fine distinctions in highly complex contexts.","criterion_policy_reference":"criterion-evaluation-policy/v1","evidence_policy_reference":"evidence-qualification-policy/v1"}],
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
      "activity_category_versions": [{"id":"002bb5dc-1bc8-5545-83cc-9e41df8abd83","stable_key":"learning","vocabulary_version":"v1","display_label":"Learning","created_at":1788912000000},{"id":"a006c740-0dd7-51a0-84c4-af9522272a97","stable_key":"reading","vocabulary_version":"v1","display_label":"Reading","created_at":1788912000000},{"id":"4fe94d35-2a7a-51dc-a60d-df1864777f00","stable_key":"practice","vocabulary_version":"v1","display_label":"Practice","created_at":1788912000000},{"id":"928503ef-c646-5d85-9b5c-6317995d6352","stable_key":"coding","vocabulary_version":"v1","display_label":"Coding","created_at":1788912000000},{"id":"4dee4329-3688-5762-a5b9-a331e7abffbb","stable_key":"debugging","vocabulary_version":"v1","display_label":"Debugging","created_at":1788912000000},{"id":"c732cda7-f573-5ba6-88f2-23a0525b7fff","stable_key":"project","vocabulary_version":"v1","display_label":"Project","created_at":1788912000000},{"id":"1d658e5a-b675-5fb7-b01c-acdb25aebd2e","stable_key":"review","vocabulary_version":"v1","display_label":"Review","created_at":1788912000000},{"id":"79b47dbe-8ad9-5ba5-aa02-a4d2f7c26a1b","stable_key":"verification","vocabulary_version":"v1","display_label":"Verification","created_at":1788912000000},{"id":"a8db0ac2-54aa-5c73-bdef-be751d4e25f3","stable_key":"research","vocabulary_version":"v1","display_label":"Research","created_at":1788912000000}],
      "activities": [],
      "session_contributions": [],
      "contribution_retractions": [],
      "session_corrections": [],
      "evidence": [],
      "evidence_links": [],
      "evidence_retractions": [],
      "evidence_invalidations": [],
      "evidence_link_retractions": [],
      "evidence_redactions": [],
      "capability_evaluation_runs": [],
      "criterion_evaluation_results": [],
      "capability_state_events": [],
      "review_events": [],
      "target_profiles": [],
      "target_profile_versions": [],
      "profile_domains": [],
      "profile_target_identities": [],
      "profile_targets": [],
      "milestone_identities": [],
      "profile_milestones": [],
      "profile_milestone_targets": [],
      "readiness_gate_identities": [],
      "readiness_gates": [],
      "readiness_gate_predicates": [],
      "readiness_gate_targets": [],
      "active_target_profile_state": [],
      "target_profile_activation_events": [],
      "semantic_competency_definitions": [],
      "semantic_definition_dimensions": [],
      "criterion_identities": [],
      "criterion_definitions": [],
      "active_competency_definition_states": [],
      "competency_definition_activation_events": [],
      "legacy_criterion_assertions": [],
      "migration_backfill_runs": [{"id":"dd3eef0d-c7ee-5f65-9911-f27d07743d2d","policy_key":"legacy-backfill-policy/v1","source_kind":"v1_exit_criteria","source_row_count":0,"result_row_count":0,"source_hash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945","result_hash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945","recorded_at":1788912000000},{"id":"62bbb6ce-c7bf-517d-83c5-27b02b370af4","policy_key":"activity-legacy-backfill-policy/v1","source_kind":"v1_learning_sessions","source_row_count":0,"result_row_count":0,"source_hash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945","result_hash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945","recorded_at":1788912000000},{"id":"9e14f409-7920-5a86-b5c8-dc180085b933","policy_key":"unified-evidence-legacy-backfill/v1","source_kind":"v1_evidence_sources","source_row_count":0,"result_row_count":0,"source_hash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945","result_hash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945","recorded_at":1788912000000}],
      "daily_reflections": [],
      "generated_reports": [],
      "analysis_runs": [],
      "analysis_snapshots": [],
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
| `schemaVersion` | integer | yes | `1` for V1 packages; portable backup/restore also accepts and exports `2` | `2` |
| `packageType` | string | yes | One accepted import type listed above; exports use `analysis_snapshot` or `portable_logical_backup` | `roadmap_update` |
| `packageId` | string | yes | Non-empty, at most 255 characters; must not have been applied before | `example-package-001` |
| `appVersion` | string | yes | Producer application version; recorded, not semantically compared | `1.0.0` |
| `createdAt` | string | yes | Producer timestamp; exports emit RFC 3339 UTC | `2026-09-04T18:30:00.000Z` |
| `payload` | object | yes | Strict package-specific object | `{"roadmap": {...}}` |

Unknown envelope fields are rejected. A successfully applied `packageId` is recorded and later inspection of that ID returns `IMPORT_PACKAGE_DUPLICATE`. The server rejects an unsupported schema version before mutation.

Roadmap inspection compares the active definition with the incoming definition by stable key. Its diff includes roadmap metadata, version transition, current phase, phase and track additions/removals/changes, competency additions/archival, phase and track movement, parent hierarchy, required and recommended prerequisites, learning objectives, layout positions, and exit-criterion additions/removals/changes. Existing competency state and learning logs remain outside a roadmap-definition mutation and are reported as preserved.

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
