# Learning Control Center Import / Export Format

## Purpose and authority

This is the canonical human- and AI-facing reference for the import/export contracts implemented by the current post-Phase-3 application. Exact wire behavior comes from the running Pydantic schemas, serializers, validators, services, persistence mappings, and integrity checks. The application is the final validator when prose and implementation differ.

The application contains five distinct contract families:

1. **Legacy V1 mutation packages** for legacy Roadmap versions, verification records, and learning-lifecycle status.
2. **Current export-only artifacts** for selective external analysis or human-readable reporting.
3. **Master Import V1 packages** for hand-authored V2 technical learning content.
4. **Portable logical backup/restore packages**, currently exported as schema V11, for application-generated state portability and recovery.
5. **Operational SQLite backups**, which are server recovery files outside the JSON import contract.

The modern learning model is V2-oriented. Master Import initializes semantic Competencies, a Target Profile, Curriculum, and the native Learning Graph through the existing Import / Export inspect, diff, confirmation, backup, and apply workflow. Master Import V1 does not author Projects or learner history.

## Import/export context in current LCC

Current canonical learning state includes versioned Target Profiles, semantic Competency definitions and criteria, capability scales and dimensions, Evidence, actual Activities and Sessions, SessionContributions, Curriculum, Projects, and the native Learning Graph. Analysis V3 diagnoses that state; Recommendation V2 produces deterministic audited decisions; Today V2 presents advisory suggestions and relates them to actual Activity; learning-control authority records the irreversible V1-to-V2 cutover and presentation history.

Roadmap is now a derived projection over V2 canonical state. Legacy `Roadmap`, `RoadmapVersion`, `Phase`, `Track`, and related definitions remain compatibility and historical data. The legacy `roadmap_update` and `roadmap_replace` packages create only those legacy structures and their stable identities; they do not create a Target Profile, native semantic definitions, capability targets, Curriculum, Projects, a native Learning Graph, Analysis V3 history, Recommendation V2 decisions, or Today V2 history.

## Contract and artifact matrix

| Contract family | Artifact/type | Format | Importable | Exportable | Schema behavior | Hand authoring | Purpose |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Legacy V1 mutation | `roadmap_update` | JSON envelope | yes | no | exactly V1 | supported with caution | Add and activate one complete legacy Roadmap version |
| Legacy V1 mutation | `roadmap_replace` | JSON envelope | yes | no | exactly V1 | supported with caution | Compatibility alias of `roadmap_update`; not a destructive replacement patch |
| Legacy V1 mutation | `verification_update` | JSON envelope | yes | no | exactly V1 | supported with existing internal IDs | Append legacy-compatible verification records and implied lifecycle statuses |
| Legacy V1 mutation | `state_update` | JSON envelope | yes | no | exactly V1 | supported with existing internal IDs | Apply explicit legacy learning-lifecycle status changes |
| Master Import | `master_import` | JSON envelope and uploaded UTF-8 text | yes | no | exactly V1; `mi-canon-v1` | yes | Complete owned technical-content snapshot with semantic diff and immutable revision ledger |
| Portable | `portable_logical_backup` | JSON envelope | yes | yes | reads V1–V11; exports V11 | no; application-generated only | Logical portable-state backup and full replacement restore |
| Portable alias | `restore` | JSON envelope | yes | no | reads V1–V11 | no; application-generated payload only | Import alias for the same portable restore contract |
| Export-only | `analysis_snapshot` | JSON envelope | no | yes | export envelope V1 | request is hand-authored; content is generated | Selective legacy analytics/legacy Roadmap-context export |
| Export-only | Human Report / `human_report` | Markdown | no | yes | no JSON package schema | request is hand-authored; content is generated | Human-readable selective summary |
| Operational | SQLite backup | SQLite file | offline/internal recovery only | internal only | database/Alembic format | no | Full deployed-database disaster recovery and automatic pre-mutation safety backup |

Portable schema V11 is an internal database-row and immutable-history representation with exact manifests and checkpoints. It is not a hand-authored Master Import format.

## JSON package envelope

```json
{
  "schemaVersion": 1,
  "packageType": "roadmap_update",
  "packageId": "example-package-001",
  "appVersion": "1.0.1",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {}
}
```

The envelope is strict: unknown envelope fields are rejected. `packageId` is non-empty and at most 255 characters. `appVersion` and `createdAt` are recorded producer metadata; the importer does not select behavior from the filename. Legacy mutation and Master Import packages require `schemaVersion: 1`. Portable backup/restore accepts schema versions 1 through 11. Exported `analysis_snapshot` envelopes use schema version 1, while exported portable backups use schema version 11.

## Legacy V1 mutation packages

These packages remain supported compatibility contracts. They are not a complete modern LCC initialization mechanism.

### Roadmap update and replace

`roadmap_update` and `roadmap_replace` intentionally have identical behavior: each payload describes one complete new legacy Roadmap version, not a partial patch. Existing matching stable identities and their history are preserved. Definitions absent from the new active version are no longer active in that version. A `(roadmap stable_key, version)` pair cannot be imported twice.

Both package types require `schemaVersion: 1`. Their exact payload schema, validation rules, and tested examples appear later under [Legacy V1 Roadmap examples and schema](#legacy-v1-roadmap-examples-and-schema).

#### Authority boundary for legacy Roadmap imports

Legacy Roadmap package application uses the same learning-control authority guard as normal V1 Roadmap mutation. Before canonical V2 activation, valid `roadmap_update` and `roadmap_replace` packages can be inspected and applied. After activation, inspection of either type fails with HTTP 409 and `LEGACY_AUTHORITY_READ_ONLY` before a confirmation token is issued.

Apply independently reruns inspection and enforces the same domain-level guard inside the mutation path. A token obtained before V2 activation therefore cannot be used to apply a legacy Roadmap package after activation. Compatibility/history reads remain available; only legacy mutation is contracted.

### Verification and state updates

`verification_update` has payload `{"verifications": [verification, ...]}`. A verification requires `competency_identity_id`, `verification_source`, `method`, and `result`. It may contain `confidence` (null or 0..100), `reviewer_label`, `evidence_summary`, `notes`, and `evidence` (default `[]`). Evidence requires `kind` (1..64 characters) and non-empty `reference`; `description` defaults to `""`. The identity is the application's database UUID, not a stable key.

Verification sources are `self`, `automated`, and `external`; results are `passed`, `failed`, and `partial`. Apply records history and implies status `verified`, `needs_review`, or `practicing`, respectively.

`state_update` has payload `{"states": [state, ...]}`. A state requires `competency_identity_id` and `status`; `reason` defaults to `"Imported status update"`. A `verified` state also requires a `verification` object for the same identity with result `passed`. Other statuses must not include `verification`. Both package payloads are strict, validate references during inspect, and are preflighted against current state. These imports update legacy-compatible verification/lifecycle records; they do not directly assign V2 capability levels or replace evidence-backed capability evaluation.

## Export-only artifacts

Every successful export creates and commits an `ExportRecord` containing the purpose, format, request scope summary, and creation time. Exporting does not change canonical learning facts, immutable generated reports, or reflections, but it is not database-read-only because it appends this audit metadata.

### Import/export `analysis_snapshot`

The wire type `analysis_snapshot` is the selective export produced by the import/export subsystem. It is **not** the same contract as the canonical persisted Analysis V3 `AnalysisRun`/`AnalysisSnapshot` history. It uses the legacy deterministic analytics builder and optional filtered legacy active-Roadmap context. It neither exports an Analysis V3 snapshot by ID nor represents the Analysis V3 current pointer.

Create this export with purpose `analysis_snapshot`, format `json`, and optional scope fields:

- `range`: `7d`, `30d`, `90d`, `all`, or `custom` (default `all`)
- `start_date`, `end_date`: ISO local dates (`YYYY-MM-DD`), both required for `custom`
- `current_phase_only`: boolean, default false
- `track_ids`: active-version database UUIDs
- `competency_identity_ids`: active-version competency UUIDs
- `categories`: any of `roadmap`, `analytics`, `sessions`, `verification`, `reports`, `settings`; empty means all

The envelope payload always contains `scope`. It records the request plus `resolved_start_date`, `resolved_end_date`, `timezone`, `resolved_categories`, and resolved selected track/competency labels and stable keys. Requested selectors are intersected. Unknown selectors are rejected.

Selected category keys are `roadmap` (filtered legacy active-roadmap/version/phase/track/competency context), `analytics` (legacy-compatible deterministic analytics output), `sessions` (stored rows in range), `verification` (stored records in range), `reports` (overlapping immutable report snapshots), and `safeSettings` (discipline profile or null). Empty or undefined ratios remain null/`N/A` according to their source; omission is not zero. Authentication secrets are never included.

Representative shape (IDs and metric details vary):

```json
{
  "schemaVersion": 1,
  "packageType": "analysis_snapshot",
  "packageId": "6db58de0-794d-49ec-86f6-2ea9bdaf84e1",
  "appVersion": "1.0.1",
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

`analysis_snapshot` artifacts are not accepted by import.

### Human Report

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

## Master Import V1

`master_import` is a hand-authored technical-content package inside the existing Import / Export UI and API. The envelope has `schemaVersion: 1`, `packageType: "master_import"`, `packageId`, `appVersion`, UTC RFC 3339 `createdAt`, and `payload`. The upload transports the decoded UTF-8 JSON text as `rawText` alongside the parsed envelope, so inspection can reject duplicate JSON keys and bind confirmation to exactly the text that was inspected. No original file-byte digest is claimed after browser decoding. The server limits this text to 32 MiB, rejects a BOM, non-NFC strings, unpaired surrogates, floating-point/exponent forms, negative zero, integers outside ±9,007,199,254,740,991, and nesting deeper than 80 levels. It also caps parsed JSON nodes at 500,000, container items at 100,000, each authored string at 16,384 Unicode characters, authored entities at 40,000, and authored references at 200,000. Pydantic models recursively reject unknown fields. The existing inspect/apply HTTP endpoints enforce a 128 MiB request-body bound, including streamed bodies, before FastAPI parses JSON. V1 still materializes the bounded request envelope and uploaded text before Master-specific validation, so peak process memory can exceed the wire size; it is not a streaming JSON importer.

The payload requires `lineageKey`, `ownerKey`, `contentRevision`, `previousContentDigest`, `canonicalizationVersion: "mi-canon-v1"`, `provenance` (`sourceName`, `authoredBy`, `sourceRevision`), UTC `effectiveAt`, explicit `activationIntent` booleans, `competencies`, `targetProfile`, `curriculum`, `learningGraph`, `initialSpine`, and `removedFromActiveVersion`. V1 requires all four activation booleans true. Competencies contain semantic criteria. The Profile contains technical domains, targets, milestones, and readiness gates. Curriculum contains objectives, Learning Units with action/duration/targets/requirements/evidence opportunities, and assessment rubrics. The Graph contains native edges. Cross-section references use stable keys; criterion references use `competencyKey::criterionKey`. V1 fixes scale references to the seeded `technical`/`v1` scale and validates every authored `levelKey` against it; it does not accept arbitrary scale versions. The exact nested field and enum contract is enforced by `backend/app/master_import/contracts.py`.

V1 is a **complete snapshot of the lineage's owned technical roots**. Revision 1 starts a lineage; a successor increments `contentRevision` by one and supplies the exact prior `mi-content-v1` digest in `previousContentDigest`. It includes every previously owned root and may add roots. Roots cannot be deleted or retired in V1. A child absent from a successor's active aggregate must appear exactly once in `removedFromActiveVersion` with entity kind, stable key, and reason; its historical identity and versions remain. Unchanged content cannot be published as a new revision. An exact package replay is a no-op. Reusing a `packageId` for different package content is rejected. Stable keys cannot be appropriated from another lineage or preexisting unowned identity, and existing semantic meaning cannot drift under the same key. A Criterion stable key fixes its Competency, level, demonstration rule, and `requirementType`; textual definition changes remain versionable.

`mi-canon-v1` is a durable digest contract implemented without Pydantic serialization or generic JSON output. It encodes NFC Unicode scalar strings as UTF-8; object keys sort by UTF-8 byte order; arrays retain order; there is no insignificant whitespace; strings use JSON escaping for quote, backslash, and controls (short escapes for backspace, tab, newline, form feed, and carriage return; lowercase `\\u00xx` for other controls); integers use shortest decimal form; booleans and null use lowercase JSON literals. Null and an omitted field differ. The `mi-content-v1:sha256:` digest hashes `LCC-MASTER-CONTENT-v1` plus a NUL byte plus canonical semantic content: package type/schema and the payload's canonicalization version, lineage/owner keys, effective time, activation intent, four authored content sections, initial spine, and explicit removals. Envelope metadata, provenance, content revision, and predecessor digest are excluded from the content digest. The `mi-package-v1:sha256:` digest hashes `LCC-MASTER-PACKAGE-v1` plus a NUL byte plus the complete canonical envelope, including all metadata. `mi-text-v1:sha256:` hashes the UTF-8 encoding of the decoded uploaded text to bind inspect and apply. Future canonicalization changes require a new named version; historical `mi-canon-v1` behavior cannot change.

Inspect parses and validates the entire proposed package before live canonical writes, resolves all stable references against proposed and existing state, checks semantic and graph invariants, simulates apply in a migrated temporary database, and returns a semantic diff and confirmation token bound to the authenticated session. The diff reports complete counts and up to 32 stable-key details per category, with explicit omitted counts; it does not echo complete Profile, Curriculum, or Graph payloads, and the same bounded result is recorded in `ImportRecord.dry_run_summary_json`. The all-Unknown cold-start check requires at least one immediately reachable legitimate first action and an initial spine without accidental prerequisite deadlock; it does not require an entry in every later-stage domain. For hard capability and criterion prerequisites, validation requires a conservative structural route through earlier spine Units, compatible targets, evidence opportunities or assessment rubrics, and the required Technical scale criteria. This only establishes a possible learning path; it does not claim the learner has earned Evidence or capability. Apply checks exact text/package and relevant base state, obtains SQLite `BEGIN IMMEDIATE`, rechecks the base under that writer reservation, then creates the existing operational pre-import backup before canonical mutation. All canonical content, activations, ImportRecord audit summary, and authoritative ledgers commit in that one outer transaction after domain-integrity validation. A canonical failure rolls back the database transaction; the already written pre-import backup file remains in protected backup storage for operator recovery even when its transactional audit row rolls back. Post-commit derived processing can be complete, pending retry, or failed without rolling back that canonical commit.

Master Import never authors learner Activity, Evidence, Verification, capability state, Analysis, Recommendation, Today history, Projects, externally managed ProfileTargets, or V2 authority cutover. A fresh technical capability remains Unknown until genuine learning evidence exists. The dedicated append-only Master Import ledgers are portable in V10; `ImportRecord.dry_run_summary_json` is only presentation/audit data.

## Portable logical backup and restore

### Purpose and package types

Portable export uses purpose and package type `portable_logical_backup`, format `json`, and currently emits `schemaVersion: 11`. Import accepts either `portable_logical_backup` or the compatibility alias `restore` with the same payload. Both package types accept supported historical portable schemas V1 through V11.

Portable data is an application-generated recovery representation. Every included table row must contain exactly the database columns expected for that table, with strict canonical scalar types. The payload also carries a version-exact manifest and integrity/rebuild checkpoints. Internal IDs, immutable lineages, policy versions, hashes, and cross-table references make this unsuitable for ordinary hand authoring.

### Project-selective production and full-replacement restore

An optional `project_ids` export selector chooses Projects; an empty list means all Projects. The exporter adds every Project required by retained shared facts, including Project-generated Evidence, Profile readiness predicates, Analysis/Recommendation frozen inputs, and the transitive replacement side of cross-Project Activity-link or Session-contribution corrections. `portableScope` records requested, included, and closure-added Project IDs. Shared retained history is never discarded merely to satisfy a narrower Project request.

Project selection applies only while **producing** the artifact. Restoring any portable artifact is still a full portable-state replacement. Projects omitted from a project-selective artifact are not merged or preserved from the destination.

An empty learning state may restore directly. Any existing portable state requires `replace_existing: true`; merge restore is unsupported. A pristine automatically created discipline profile counts as empty, while modified discipline settings and every other populated portable table count as existing state. Existing authentication users and active authentication sessions are not part of logical replacement and remain intact.

### Validation and restore integrity

Inspection requires supported table coverage, exact row columns, strict canonical row types, valid structured JSON and IANA timezones, valid foreign keys, complete history/state relationships, versioned ownership, hierarchy and required-dependency acyclicity, immutable-history hashes, registered policy versions, and checkpoint coherence. Portable rows are inserted into a freshly migrated temporary SQLite database, then the same domain-integrity rules and available catalog/projection parity checks run there before a confirmation token is issued.

Apply repeats inspection against current state. Inside the restore transaction it deletes portable and rebuildable state, inserts normalized portable rows, reconstructs declared projections/current state, validates retained immutable lineage, checks exported checkpoints, and runs domain-integrity validation again. Any failure rolls back the live database transaction.

### Authentication, secrets, and host-local metadata

Portable tables exclude `users`, `auth_sessions`, authentication rate-limit/security-audit tables, and `operational_backups`. Consequently password hashes, session lookup hashes, CSRF state, and operational-backup table rows are not portable. Runtime environment secrets such as bootstrap and application security secrets are not database portable state. External Evidence and verification references are filtered or redacted when they contain recognized credential-bearing URL forms.

The current exporter does **not** provide a universal secret scanner for arbitrary free-text or non-secret application-setting values. `import_records` remains portable for audit continuity, and its exact row shape still includes nullable `pre_import_backup_reference`; however, the portable boundary always serializes that column as JSON null. On read, supported historical packages that contain a valid string value are accepted and normalized to null before restore; non-string/non-null values still fail strict scalar-type validation. The destination therefore retains the audit event but does not import another host's backup path or pretend that the referenced backup exists locally.

This host-local backup-path normalization originated in portable schema V9 and continues in V11. Historical V1–V10 packages remain readable. A live successful import still records its newly created local backup path in the destination database for local recovery, but later portable exports null that host-local value.

### Portable schema history and linear adapters

Portable readers use a linear V1→V2→V3→V4→V5→V6→V7→V8→V9→V10→V11 adapter chain. “Lossless” means available source facts are preserved or deterministically represented with provenance. It does **not** mean ambiguous legacy data is upgraded into invented modern meaning.

| Portable schema | Contract introduced | Historical adapter behavior |
| --- | --- | --- |
| V1 | Legacy Roadmap, lifecycle status/history, verification, sessions, reports, recommendation snapshots, settings, and operation history | Deterministically backfills V2-compatible Activities, primary SessionContributions, unified Evidence/links, legacy criterion assertions, built-in scales, and audit lineage. Missing creation/evidence semantics remain unknown or legacy-unspecified. |
| V2 | Target Profile and semantic-competency foundations; capability scales; canonical Activity/contribution and Evidence domains; immutable capability/review history; the immutable Analysis run/snapshot boundary; projection checkpoints | Does not infer Target Profiles or native semantic definitions from legacy Roadmap meaning. |
| V3 | Curriculum identities, immutable versions, activity links/history, and Curriculum catalog checkpoint | Initializes absent Curriculum tables as empty; does not infer Curriculum. |
| V4 | Projects, project versions/history, Activity/Session attribution, criterion evaluation, project-selective scope, and Project catalog checkpoint | Initializes absent Project tables as empty; does not infer Projects. |
| V5 | Native Learning Graph, activation history, Roadmap Projection manual inputs/preferences, legacy Roadmap compatibility state, and projection checkpoint | Initializes native graph/projection tables; derives only exact legacy Roadmap active-pointer compatibility state and does not infer native graph meaning. |
| V6 | Immutable discipline-configuration event history and Analysis V3 run/snapshot/fact/gap/signal/Unknown history plus current-pointer checkpoint | Creates an exact discipline-configuration compatibility baseline when possible; initializes Analysis V3 history empty and does not fabricate diagnostics. |
| V7 | Complete immutable Recommendation V2 runs, candidates, eligibility decisions, rule results, expected values, score components, selections, recommendations, and reasons | Initializes Recommendation V2 history empty; does not reinterpret legacy recommendation snapshots as V2 decisions. |
| V8 | Today V2 generations, suggestions, interactions/corrections, actual-Activity relations/corrections, and current-state checkpoint | Initializes Today V2 history empty; does not infer interactions from legacy decisions or sessions. |
| V9 | Monotonic learning-control authority state/history and `authorityCheckpoint`; physical contraction of legacy Roadmap pointer columns | Validates exact V8 Roadmap-pointer parity, preserves it in compatibility state, removes contracted pointer columns, and creates only the legacy-authority bootstrap baseline. It never infers V2 activation. |
| V10 | Authoritative Master Import revision and stable-key ownership ledgers | Initializes both ledgers empty for V9 and earlier packages. It never infers Master Import ownership from old import audit rows. |
| V11 | Authored assessment execution binding, server-stored task artifacts, and append-only first-party review history | Initializes all three tables empty for V10 and earlier packages. It never infers assessment attempts or criterion results from Session completion. |

Adapters do not fabricate Target Profiles, unavailable native semantic definitions, Curriculum, Projects, native Learning Graph semantics, Analysis V3 history, Recommendation V2 decisions, Today V2 interactions, or V2 authority activation. The deterministic compatibility baselines listed above are the only deliberate additions.

### Canonical and immutable portable state

The V11 manifest includes canonical facts and the immutable history needed to preserve meaning and auditability, including:

- stable and versioned legacy/V2 identities, definitions, targets, criteria, graphs, Curricula, and Projects;
- activation and scope history;
- actual Activities, Sessions, SessionContributions, corrections, and retractions;
- Evidence, EvidenceLinks, provenance, invalidation/retraction/redaction history, verification, and criterion evaluations;
- capability evaluation runs, criterion results, capability/review events, and their immutable lineage;
- discipline configuration history;
- immutable Analysis V3, Recommendation V2, and Today V2 histories;
- learning-control authority state and append-only authority events;
- Master Import revision lineage, selected versions, activation provenance, and owned-key ledger;
- exact authored assessment execution lineage, server-stored and hashed criterion artifacts, and append-only reviewed criterion observations; the resulting canonical Evidence remains in the existing Evidence tables;
- non-authentication application settings and import/export audit history.

Some included Analysis, Recommendation, and Today records are immutable histories of derived computation, not source learning facts. Their inclusion preserves exact decisions and audit lineage; it does not make them inputs for rebuilding Evidence or capability truth.

`SessionContribution` rows contain attribution only and never duration. The linked `LearningSession` remains the sole canonical owner of exact elapsed `duration_ms`.

### Omitted rebuildable state

The exact V11 manifest labels the following projections/current-state material as rebuildable and omits it from portable tables:

- competency capability current state;
- competency review current state;
- durable projection-invalidation work rows;
- Curriculum availability;
- Project current lifecycle and task availability;
- Learning Graph edge satisfaction;
- Roadmap Projection cache and persisted checkpoint rows;
- Analysis V3 current-pointer rows;
- Today suggestion current-state rows.

Restore rebuilds or reconstructs these from retained facts/history and verifies parity where the package provides a checkpoint. It does not rerun Analysis V3 merely because a snapshot was restored. The Analysis current pointer is reconstructed from `analysisV3CurrentCheckpoint`: it remains current only when source generation, completed-through date, policy bundle, and deterministic inputs still match the live restored state; otherwise it is marked stale and an Analysis invalidation is queued. Recommendation V2 history is verified, not regenerated. Today current state, capability/review state, catalogs, and Roadmap Projection are rebuilt and checked according to the manifest actions.

### Learning-control authority in V9

Authority history is contiguous, hash-verified, and monotonic. A valid history begins with the system/migration legacy bootstrap. Canonical authority may transition once from `legacy_v1` to `v2`; later surface changes may select `v2` or labeled `v1_read_only` presentation while canonical authority remains V2.

Every pre-V9 portable package is adapted to the legacy-authority baseline. No adapter infers that V2 was activated. When the destination is already V2-authoritative, restore rejects an incoming legacy authority state, a shorter history, or a history that does not contain the destination's existing authority events as an exact prefix. Restore therefore cannot demote V2 authority or silently remove/rewrite protected authority history.

### Historical V9 representative empty portable package

The following is a **historical V9 application-generated representative structure**, retained as a compatibility example. It is not a hand-authoring template or a V11 export template. Current non-empty V11 exports also include the Master Import ledger and assessment history tables and use the exact V11 manifest/checkpoints.

```json
{
  "schemaVersion": 9,
  "packageType": "portable_logical_backup",
  "packageId": "example-empty-portable-v9",
  "appVersion": "1.0.1",
  "createdAt": "2026-09-04T18:30:00.000Z",
  "payload": {
    "manifest": {
      "includedCanonicalDomains": ["learning_state", "target_profiles", "semantic_competency_definitions", "capability_scales", "legacy_criterion_assertions", "activities", "session_contributions", "evidence", "evidence_links", "curriculum", "curriculum_activity_links", "projects", "project_activity_and_session_attribution", "project_evidence_evaluations", "learning_graph", "roadmap_projection_manual_input", "legacy_roadmap_active_state_compatibility", "discipline_configuration_history", "analysis_v3_diagnostics", "recommendation_v2_decision_history", "today_v2_advisory_history", "suggestion_actual_activity_relations", "learning_control_authority_state"],
      "includedImmutableHistory": ["analysis_runs", "analysis_snapshots", "target_profile_activation_events", "competency_definition_activation_events", "migration_backfill_runs", "contribution_retractions", "session_corrections", "evidence_retractions", "evidence_invalidations", "evidence_link_retractions", "evidence_redactions", "capability_evaluation_runs", "criterion_evaluation_results", "capability_state_events", "review_events", "curriculum_versions", "curriculum_activation_events", "activity_curriculum_link_corrections", "project_versions", "project_version_activation_events", "project_events", "activity_project_task_link_corrections", "session_project_contribution_retractions", "project_criterion_evaluations", "learning_graph_versions", "learning_graph_activation_events", "discipline_configuration_events", "analysis_v3_runs_snapshots_facts_gaps_signals_unknowns", "recommendation_v2_runs_candidates_eligibility_scores_decisions_reasons", "today_v2_generations_suggestions_interactions_relations_corrections", "learning_control_authority_events"],
      "omittedRebuildableState": ["competency_capability_states", "competency_review_states", "projection_invalidations", "curriculum_availability", "project_current_lifecycle", "project_task_availability", "learning_graph_edge_satisfaction", "roadmap_projection_cache", "roadmap_projection_checkpoint", "analysis_v3_current_state", "today_suggestion_current_states"],
      "restoreActions": ["clear_projection_invalidations", "rebuild_capability_states", "rebuild_review_states", "verify_projection_hash_parity", "rebuild_curriculum_availability", "verify_curriculum_catalog_hash_parity", "rebuild_project_current_state", "rebuild_project_task_availability", "verify_project_catalog_hash_parity", "rebuild_learning_graph_satisfaction", "rebuild_roadmap_projection", "verify_roadmap_projection_hash_parity", "verify_legacy_roadmap_active_state_parity", "rebuild_analysis_v3_current_pointer", "verify_analysis_v3_history_hash_parity", "verify_recommendation_v2_history_hash_parity", "rebuild_today_suggestion_current_states", "verify_today_v2_history_and_current_state_parity", "verify_monotonic_learning_control_authority_history"]
    },
    "capabilityProjectionCheckpoints": [],
    "curriculumCatalogCheckpoint": {"cutoffAt":1788912000001,"cutoffSemantics":"exclusive","policyVersion":"curriculum-availability-policy/v1","sourceHash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945","catalogHash":"61b6254101fd15086e67911ab858b340167acff86d66edc82431399e61c1b615","activeVersionReferences":[],"availabilityHashes":[],"inputHash":"cf23256f777851e3cef3ef46370ae4d274c11c2aa9f998e334a91668aee84593"},
    "projectCatalogCheckpoint": {"cutoffAt":1788912000001,"cutoffSemantics":"exclusive","policyVersion":"project-availability-policy/v1","sourceHash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945","catalogHash":"4e825ede2c83a813dcaf6d951b3a846aaed89aae01f69c7d07cd0e76a9d2eacf","activeVersionReferences":[],"candidateHashes":[],"inputHash":"23f40942cce46d0a4b88ce3ca47e878fcdc4a5af2daef5351edfc5d9010ac054"},
    "roadmapProjectionCheckpoint": {"configured":false,"cutoffAt":1788912000001,"cutoffSemantics":"exclusive","projectionPolicyVersion":null,"layoutPolicyVersion":null,"scopeKey":null,"sourceHash":"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a","outputHash":"d3e9b13a416cdad40977887be139b874d2efd768e32deaea89d243ae4872835d","inputHash":"14afaad94192f5caef8855d96162f0fd660094f2d85462d93f55322d0724e760"},
    "analysisV3CurrentCheckpoint": {"states":[],"checkpointHash":"d5f1150a51fb16f8f46e3f0da2267fd999ecdf5d66d84e3558b7066de8e452aa"},
    "recommendationV2HistoryCheckpoint": {"runHashes":[],"historyHash":"7ffb1b5f19bd1b58c242532ec29af62e2b12bb77bd8a309ffa4816d1877904fc","checkpointHash":"628cbe4e408d1197c2799a0840b4275dd21c2a75a6ce8fb7d781f58f57df141c"},
    "todayV2CurrentCheckpoint": {"states":[],"checkpointHash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},
    "authorityCheckpoint": {"stateHash":"3398d80de37ae4536877efa09ecb5296710cdc1a7410e204b521ecb01da48c3f","historyHash":"9781a57dd4b3177a51a4c49184ac5130ef6bcd17aecb7303b4bc6905a4a76e4e","eventSequence":1,"checkpointHash":"fc3acb632e72f48111704473dbc164e1a13fe4d3877fb8d60792e7163d77d961"},
    "portableScope": {"requestedProjectIds":[],"includedProjectIds":[],"closureAddedProjectIds":[]},
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
      "curricula": [],
      "curriculum_versions": [],
      "curriculum_objective_identities": [],
      "curriculum_objective_definitions": [],
      "learning_unit_identities": [],
      "learning_unit_definitions": [],
      "learning_unit_targets": [],
      "learning_unit_requirements": [],
      "curriculum_evidence_opportunities": [],
      "assessment_rubric_identities": [],
      "assessment_rubric_definitions": [],
      "active_curriculum_version_states": [],
      "curriculum_activation_events": [],
      "activity_curriculum_unit_links": [],
      "activity_curriculum_link_corrections": [],
      "projects": [],
      "project_versions": [],
      "project_goal_identities": [],
      "project_goal_definitions": [],
      "project_milestone_identities": [],
      "project_milestone_definitions": [],
      "project_task_identities": [],
      "project_task_definitions": [],
      "project_criterion_identities": [],
      "project_criterion_definitions": [],
      "project_targets": [],
      "project_requirements": [],
      "project_task_dependencies": [],
      "project_evidence_opportunities": [],
      "active_project_version_states": [],
      "project_version_activation_events": [],
      "project_events": [],
      "activity_project_task_links": [],
      "activity_project_task_link_corrections": [],
      "session_project_contributions": [],
      "session_project_contribution_retractions": [],
      "project_criterion_evaluations": [],
      "project_criterion_evaluation_evidence": [],
      "learning_graphs": [],
      "learning_graph_versions": [],
      "competency_edge_identities": [],
      "competency_edge_definitions": [],
      "active_learning_graph_states": [],
      "learning_graph_activation_events": [],
      "legacy_roadmap_active_states": [],
      "roadmap_node_position_overrides": [],
      "roadmap_projection_preferences": [],
      "discipline_configuration_events": [],
      "analysis_v3_run_lineages": [],
      "analysis_v3_snapshot_details": [],
      "analysis_v3_normalized_facts": [],
      "analysis_v3_competency_gaps": [],
      "analysis_v3_signals": [],
      "analysis_v3_unknown_markers": [],
      "recommendation_v2_runs": [],
      "recommendation_v2_candidates": [],
      "recommendation_v2_eligibility_decisions": [],
      "recommendation_v2_eligibility_rule_results": [],
      "recommendation_v2_expected_values": [],
      "recommendation_v2_score_components": [],
      "recommendation_v2_selection_decisions": [],
      "recommendation_v2_recommendations": [],
      "recommendation_v2_reasons": [],
      "today_generations": [],
      "today_suggestions": [],
      "today_interactions": [],
      "today_interaction_corrections": [],
      "suggestion_activity_relations": [],
      "suggestion_activity_relation_corrections": [],
      "learning_control_authority_events": [{"id":"authority-bootstrap-legacy-v1","event_sequence":1,"idempotency_key":"authority-bootstrap-legacy-v1","command_type":"bootstrap","prior_state_json":null,"resulting_state_json":"{\"canonicalLearningAuthority\":\"legacy_v1\",\"recommendationPresentation\":\"legacy_v1\",\"roadmapPresentation\":\"legacy_v1\",\"todayPresentation\":\"legacy_v1\"}","reason":"Initial legacy authority baseline","actor":"system","source":"migration","payload_hash":"7a42047848503f33b99f5b03e3489ed106170ed2e6b90d6e8b801983126befe0","occurred_at":0}],
      "learning_control_authority_state": [{"id":1,"canonical_learning_authority":"legacy_v1","roadmap_presentation":"legacy_v1","recommendation_presentation":"legacy_v1","today_presentation":"legacy_v1","event_sequence":1,"last_event_id":"authority-bootstrap-legacy-v1","state_hash":"3398d80de37ae4536877efa09ecb5296710cdc1a7410e204b521ecb01da48c3f","updated_at":0}],
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

## Inspect and apply protocol

All mutating JSON imports use the two-step `/import/inspect` then `/import/apply` protocol.

### Inspect

Inspection is a non-mutating dry run against live state plus isolated temporary databases where required. It performs, as applicable:

1. strict envelope and package-specific payload parsing;
2. configured maximum-package-size enforcement;
3. package-type/schema-version compatibility checks;
4. duplicate applied `packageId` lookup;
5. reference, scalar-type, structured-JSON, timezone, ownership, history, integrity, hierarchy, and cycle validation;
6. a migrated temporary-SQLite portable restore validation or a preflight mutation against a temporary copy of current portable state;
7. a package-specific summary and diff;
8. generation of a confirmation token.

Inspection does not insert an `ImportRecord` and does not mutate live portable learning state. The confirmation token is held only in process memory, keyed by `packageId`, and expires after 900,000 ms (15 minutes). It is bound to the SHA-256 digest of the exact normalized package object, not merely to the filename or package ID. Restarting the backend loses outstanding previews. A later inspection of the same package ID replaces its cached preview.

### Apply

Apply first requires a present, unexpired token whose cached digest matches the submitted package exactly. It rechecks whether the package ID has already been applied and reruns the complete inspection against current state, preventing a stale preview from bypassing changed-state validation.

The mutation then runs inside a database transaction:

1. create and integrity-check a SQLite-safe `pre-import` operational backup;
2. apply the package-specific mutation or full portable replacement;
3. insert an `ImportRecord` containing package metadata, the inspected summary, applied time, and the local pre-import backup path reference (the path is nulled at the portable boundary);
4. run post-apply domain-integrity validation;
5. commit only if all steps succeed.

An application or integrity failure rolls back the live database transaction. Projection work triggered by verification/state imports is drained after the source transaction commits. A successful apply removes the cached preview token. Because apply-time reinspection refreshes the process-local preview and failures may invalidate practical token reuse, clients should perform a new inspection after any failed apply rather than assuming the old token remains usable.

Successful exports append an `ExportRecord` after generating the artifact. Successful imports append an `ImportRecord` in the mutation transaction. Applied package IDs are therefore protected against duplicate effects on later inspect and apply requests.

## Portable replacement diff

Portable inspection returns `replacementDiff` with `operation: "fullReplacement"`, `mergeSupported: false`, and `authenticationPreserved: true`. It provides complete per-table counts for every current portable table: existing rows, incoming rows, and rows added, modified, or removed. Primary-key equality determines row identity; any differing stored row is modified.

The category taxonomy partitions every portable table exactly once:

- modern canonical and history domains: `targetProfiles`, `semanticCompetencies`, `capabilityReview`, `sessions`, `evidence`, `curriculum`, `projects`, `learningGraph`, `roadmapProjection`, `discipline`, `analysisV3`, `recommendationV2`, `todayV2`, and `learningControlAuthority`;
- legacy compatibility/history domains: `roadmap`, `competencyProgress`, `verification`, `recommendations`, and `legacyCompatibility`;
- supporting and operational domains: `reflections`, `reports`, `settings`, and `operationHistory`.

`sessions` owns Activities, Learning Sessions, SessionContributions, and their correction/retraction history. Curriculum-owned Activity links roll up under `curriculum`; Project-owned Activity links, Session attribution, and Project criterion evaluation roll up under `projects`. The shared `competency_identities` anchor rolls up under `semanticCompetencies`. Discipline configuration is separate from general `settings`. These ownership rules keep cross-domain tables deterministic and prevent category totals from double counting the same table row.

For each table and category, `existing` and `incoming` are row counts on the two sides of replacement. `added` and `removed` use table primary keys; `modified` means the primary key exists on both sides but at least one stored column differs. A category's numbers are sums across its disjoint tables, so they are row-change counts rather than counts of conceptual learning entities. `categoriesTouched` is the deterministic taxonomy-ordered list of categories whose added, modified, or removed total is nonzero. Aggregate flags `willAddRows`, `willModifyRows`, and `willDeleteRows` are computed from the complete per-table diff.

## Operational SQLite backups

Operational backups are separate from portable logical backups. They are SQLite files created with SQLite's backup API and checked with `PRAGMA integrity_check` and `PRAGMA foreign_key_check`. Manual operational backup and automatic pre-import/pre-authority backup records contain a checksum, size, purpose, path, and creation time. Operational files may contain users, password hashes, authentication sessions, security history, and all other deployed database state. They require protected server storage and the separate offline recovery workflow; they are never accepted as a JSON import package.

## Time, duration, and enum reference

- Envelope `createdAt` is a string; application exports produce RFC 3339 UTC.
- Roadmap packages contain no timestamps or durations.
- Portable and import/export `analysis_snapshot` stored-row timestamps are integer UTC epoch milliseconds. Nullable instants are JSON null.
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

## Legacy V1 Roadmap validation examples

The remaining schemas and examples in this section describe only the legacy V1 Roadmap mutation contract. They are retained because they are valid, tested compatibility inputs. They do not describe modern V2 initialization.

### Actual invalid cases

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

1. Do not use `roadmap_update` or `roadmap_replace` as a substitute for complete modern LCC initialization. They create legacy Roadmap compatibility state only.
2. Do not hand-author portable V11 as though it were a semantic import schema. It contains internal database rows, IDs, versioned immutable history, exact manifests, policy references, hashes, and rebuild checkpoints.
3. Use the documented `master_import` V1 contract for approved hand-authored modern technical content; do not fabricate learner state or use it for external non-technical outcomes.
4. Emit only a documented package type and supported schema version. Emit valid JSON without comments, ellipses, or surrounding prose when JSON is requested.
5. For a requested legacy Roadmap package, copy exact snake_case field names, treat the Roadmap as one complete version rather than a patch, and preserve roadmap, competency, and exit-criterion stable identities across genuine revisions.
6. Resolve every parent and prerequisite key to a supplied legacy Roadmap competency and avoid hierarchy and required-dependency cycles.
7. Do not add internal database IDs, lifecycle statuses, timestamps, archived flags, or exit states to a legacy Roadmap payload. Conversely, do not guess the internal competency UUIDs required by verification/state packages.
8. Never fabricate portable rows, immutable events, activation history, internal IDs, hashes, manifests, checkpoints, policy versions, or compatibility baselines.
9. Preserve Unknown, absent, and null semantics. Never translate them to zero, false, failure, `not_started`, `unexposed`, or another asserted value without an authoritative contract.
10. Use only documented enum strings and integer millisecond durations. Never convert canonical duration to floating-point minutes.
11. Never invent or embed credentials, cookies, session material, password hashes, API keys, tokens, bootstrap secrets, application secrets, or credential-bearing URLs.
12. Treat import/export `analysis_snapshot` as the selective legacy analytics export contract, not as Analysis V3 history.
13. Always require application inspection/dry-run, review the exact diff—especially per-table portable replacement changes—and obtain human approval before apply.

## Legacy V1 Roadmap examples and schema

The examples in this section are executable compatibility examples. Their continued validity does not make legacy Roadmap the canonical V2 learning model.

### Realistic disposable roadmap package

This synthetic **legacy V1 Roadmap** package is for demonstrations and populated-state testing. It is not a personal learning roadmap and does not initialize the modern V2 learning system.

```json
{
  "schemaVersion": 1,
  "packageType": "roadmap_replace",
  "packageId": "example-realistic-roadmap-v1",
  "appVersion": "1.0.1",
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
| `schemaVersion` | integer | yes | `1` for legacy mutation, Master Import, and `analysis_snapshot` packages; portable backup/restore accepts `1` through `11` and exports `11` | `11` |
| `packageType` | string | yes | One accepted import type listed above; exports use `analysis_snapshot` or `portable_logical_backup` | `roadmap_update` |
| `packageId` | string | yes | Non-empty, at most 255 characters; exact Master Import replay is idempotent, while changed reuse is rejected | `example-package-001` |
| `appVersion` | string | yes | Producer application version; recorded, not semantically compared | `1.0.1` |
| `createdAt` | string | yes | Producer timestamp; exports emit RFC 3339 UTC | `2026-09-04T18:30:00.000Z` |
| `payload` | object | yes | Strict package-specific object | `{"roadmap": {...}}` |

Unknown envelope fields are rejected. A successfully applied `packageId` is recorded and later inspection of that ID returns `IMPORT_PACKAGE_DUPLICATE`. The server rejects an unsupported schema version before mutation.

Roadmap inspection compares the active definition with the incoming definition by stable key. Its diff includes roadmap metadata, version transition, current phase, phase and track additions/removals/changes, competency additions/archival, phase and track movement, parent hierarchy, required and recommended prerequisites, learning objectives, layout positions, and exit-criterion additions/removals/changes. Existing competency state and learning logs remain outside a roadmap-definition mutation and are reported as preserved.

### Roadmap package schema

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

### Minimal valid roadmap package

This disposable example is a complete legacy V1 Roadmap version and can be copied as valid JSON when that compatibility format is specifically required. It does not initialize V2 Target Profile, Curriculum, Projects, native Learning Graph, or other modern domains.

```json
{
  "schemaVersion": 1,
  "packageType": "roadmap_update",
  "packageId": "example-minimal-roadmap-v1",
  "appVersion": "1.0.1",
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
