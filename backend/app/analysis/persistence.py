from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.analysis.contracts import AnalysisEnvelope, canonical_json
from app.models import AnalysisRun, AnalysisSnapshot


def persist_envelope(db: Session, envelope: AnalysisEnvelope) -> AnalysisSnapshot:
    run = AnalysisRun(
        idempotency_key=str(uuid.uuid4()),
        purpose=envelope.purpose,
        scope_json=canonical_json(
            {"timezone": envelope.timezone, "completedThroughDate": envelope.completed_through_date}
        ),
        generated_at=envelope.generated_at,
        cutoff_at=envelope.cutoff_at,
        status="partial" if envelope.completeness == "partial" else "completed",
        algorithm_version=envelope.policy_versions["recommendation"] or "unavailable",
        configuration_reference=envelope.discipline_configuration_reference,
        configuration_hash=envelope.configuration_hash,
        input_lineage_json=canonical_json(envelope.input_lineage),
        input_hash=envelope.input_hash,
        application_version=envelope.application_version,
        failure_metadata_json=None,
        completeness_metadata_json=canonical_json(
            {"status": envelope.completeness, "unknownMarkers": envelope.unknown_markers}
        ),
    )
    db.add(run)
    db.flush()
    snapshot = AnalysisSnapshot(
        run_id=run.id,
        purpose=envelope.purpose,
        schema_version=envelope.schema_version,
        generated_at=envelope.generated_at,
        cutoff_at=envelope.cutoff_at,
        cutoff_semantics=envelope.cutoff_semantics,
        timezone=envelope.timezone,
        completed_through_date=envelope.completed_through_date,
        target_profile_id=envelope.target_profile_id,
        target_profile_version_id=envelope.target_profile_version_id,
        capability_scale_version_references_json=canonical_json(
            envelope.capability_scale_version_references
        ),
        learning_graph_reference=envelope.learning_graph_reference,
        curriculum_reference=envelope.curriculum_reference,
        semantic_definition_references_json=canonical_json(envelope.semantic_definition_references),
        policy_versions_json=canonical_json(envelope.policy_versions),
        discipline_configuration_reference=envelope.discipline_configuration_reference,
        configuration_hash=envelope.configuration_hash,
        application_version=envelope.application_version,
        input_lineage_json=canonical_json(envelope.input_lineage),
        input_hash=envelope.input_hash,
        normalized_facts_json=canonical_json(envelope.normalized_facts),
        signals_json=canonical_json(envelope.signals),
        completeness=envelope.completeness,
        unknown_markers_json=canonical_json(envelope.unknown_markers),
        output_hash=envelope.output_hash,
    )
    db.add(snapshot)
    db.flush()
    return snapshot
