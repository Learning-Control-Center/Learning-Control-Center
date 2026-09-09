from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.contracts import AnalysisEnvelope, content_hash, immutable
from app.analytics import build_analytics, session_facts
from app.models import (
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyState,
    CompetencyStatusEvent,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    Phase,
    Roadmap,
    Track,
    VerificationRecord,
)
from app.settings_api import get_or_create_profile
from app.time_utils import local_date_for_ms, utc_now_ms

PURPOSE = "v1_recommendation_compat"
ALGORITHM_VERSION = "recommendation-v1"
APPLICATION_VERSION = "1.0.0"


def _required_exit_ready(
    db: Session, definitions: Sequence[CompetencyDefinition]
) -> dict[str, bool]:
    definition_ids = [item.id for item in definitions]
    items = db.scalars(
        select(ExitCriterionDefinition).where(
            ExitCriterionDefinition.competency_definition_id.in_(definition_ids),
            ExitCriterionDefinition.required.is_(True),
        )
    ).all()
    identities = {
        item.id: item
        for item in db.scalars(
            select(ExitCriterionIdentity).where(
                ExitCriterionIdentity.id.in_([item.exit_criterion_identity_id for item in items])
            )
        ).all()
    }
    grouped: dict[str, list[bool]] = {definition.id: [] for definition in definitions}
    for item in items:
        grouped[item.competency_definition_id].append(
            identities[item.exit_criterion_identity_id].current_state == "met"
        )
    return {
        definition_id: bool(values) and all(values) for definition_id, values in grouped.items()
    }


def build_v1_recommendation_envelope(db: Session, *, now_ms: int | None = None) -> AnalysisEnvelope:
    now = now_ms if now_ms is not None else utc_now_ms()
    cutoff = now + 1
    profile = get_or_create_profile(db)
    today = local_date_for_ms(now, profile.timezone)
    roadmap = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
    definitions: list[CompetencyDefinition] = []
    normalized: dict[str, Any]
    lineage: list[dict[str, Any]] = []
    if roadmap is None or roadmap.active_version_id is None or roadmap.current_phase_id is None:
        normalized = {
            "legacy_v1_recommendation_input": {
                "setupRequired": True,
                "guidance": "Import a roadmap package to receive recommendations.",
                "localDate": today.isoformat(),
                "currentPhaseId": None,
                "currentPhaseOrderIndex": None,
                "todayTargetDurationMs": profile.target_duration_ms_per_active_day,
                "todayCompletedDurationMs": 0,
                "legacyLifecycleStates": {},
                "candidateDefinitions": [],
                "sessionFacts": [],
                "disciplineConstraints": {
                    "weeklyTargetActiveDays": profile.weekly_target_active_days,
                    "targetDurationMsPerActiveDay": profile.target_duration_ms_per_active_day,
                    "timezone": profile.timezone,
                },
            }
        }
    else:
        phases = db.scalars(
            select(Phase)
            .where(Phase.roadmap_version_id == roadmap.active_version_id)
            .order_by(Phase.order_index)
        ).all()
        phase_by_id = {item.id: item for item in phases}
        tracks = db.scalars(
            select(Track).where(Track.roadmap_version_id == roadmap.active_version_id)
        ).all()
        track_by_id = {item.id: item for item in tracks}
        definitions = list(
            db.scalars(
                select(CompetencyDefinition).where(
                    CompetencyDefinition.roadmap_version_id == roadmap.active_version_id,
                    CompetencyDefinition.archived.is_(False),
                )
            ).all()
        )
        identities = {
            item.id: item
            for item in db.scalars(
                select(CompetencyIdentity).where(
                    CompetencyIdentity.id.in_([item.competency_identity_id for item in definitions])
                )
            ).all()
        }
        states = {
            item.competency_identity_id: item
            for item in db.scalars(
                select(CompetencyState).where(
                    CompetencyState.competency_identity_id.in_(
                        [item.competency_identity_id for item in definitions]
                    )
                )
            ).all()
        }
        analytics = build_analytics(
            db,
            now_ms=now,
            range_name="30d",
            exclusive_cutoff_ms=cutoff,
        )
        prerequisite_items = db.scalars(
            select(CompetencyPrerequisite).where(
                CompetencyPrerequisite.competency_definition_id.in_(
                    [item.id for item in definitions]
                )
            )
        ).all()
        prerequisites: dict[str, list[CompetencyPrerequisite]] = {}
        direct_dependents: dict[str, int] = {}
        for item in prerequisite_items:
            prerequisites.setdefault(item.competency_definition_id, []).append(item)
            if item.kind == "required":
                direct_dependents[item.prerequisite_competency_identity_id] = (
                    direct_dependents.get(item.prerequisite_competency_identity_id, 0) + 1
                )
        exit_ready = _required_exit_ready(db, definitions)
        sessions = session_facts(db, profile.timezone, exclusive_cutoff_ms=cutoff)
        today_completed = sum(fact.duration_ms for fact in sessions if fact.local_date == today)
        previous_30_start = today - timedelta(days=30)
        previous_7_start = today - timedelta(days=7)
        previous_day = today - timedelta(days=1)
        previous_30_practical: dict[str, dict[str, int]] = {}
        previous_7_track_duration: dict[str, int] = {}
        previous_day_competency_duration: dict[str, int] = {}
        previous_day_total = 0
        for fact in sessions:
            if previous_7_start <= fact.local_date < today and fact.track_id is not None:
                previous_7_track_duration[fact.track_id] = (
                    previous_7_track_duration.get(fact.track_id, 0) + fact.duration_ms
                )
            if fact.local_date == previous_day:
                previous_day_total += fact.duration_ms
                if fact.competency_identity_id is not None:
                    previous_day_competency_duration[fact.competency_identity_id] = (
                        previous_day_competency_duration.get(fact.competency_identity_id, 0)
                        + fact.duration_ms
                    )
            if (
                fact.competency_identity_id is not None
                and previous_30_start <= fact.local_date < today
                and fact.practical
            ):
                window = previous_30_practical.setdefault(
                    fact.competency_identity_id,
                    {"durationMs": 0, "independentDurationMs": 0},
                )
                window["durationMs"] += fact.duration_ms
                if fact.independent_practical:
                    window["independentDurationMs"] += fact.duration_ms
        normalized = {
            "legacy_v1_recommendation_input": {
                "setupRequired": False,
                "guidance": None,
                "localDate": today.isoformat(),
                "currentPhaseId": roadmap.current_phase_id,
                "currentPhaseOrderIndex": phase_by_id[roadmap.current_phase_id].order_index,
                "todayTargetDurationMs": profile.target_duration_ms_per_active_day,
                "todayCompletedDurationMs": today_completed,
                "legacyLifecycleStates": {
                    identity_id: state.current_status
                    for identity_id, state in sorted(states.items())
                },
                "candidateDefinitions": [
                    {
                        "definitionId": definition.id,
                        "competencyIdentityId": definition.competency_identity_id,
                        "stableKey": identities[definition.competency_identity_id].stable_key,
                        "title": definition.title,
                        "priority": definition.priority,
                        "weight": definition.weight,
                        "phaseId": definition.phase_id,
                        "phaseOrderIndex": phase_by_id[definition.phase_id].order_index,
                        "trackId": definition.track_id,
                        "trackStableKey": track_by_id[definition.track_id].stable_key,
                        "legacyStatus": states[definition.competency_identity_id].current_status,
                        "analyticsFacts": analytics["competencies"][
                            definition.competency_identity_id
                        ],
                        "exitCriteriaReady": exit_ready[definition.id],
                        "directRequiredDependentCount": direct_dependents.get(
                            definition.competency_identity_id, 0
                        ),
                        "requiredPrerequisiteIdentityIds": sorted(
                            item.prerequisite_competency_identity_id
                            for item in prerequisites.get(definition.id, [])
                            if item.kind == "required"
                        ),
                        "requiredPrerequisites": [
                            {
                                "competencyIdentityId": item.prerequisite_competency_identity_id,
                                "legacyVerified": (
                                    states[item.prerequisite_competency_identity_id].current_status
                                    == "verified"
                                ),
                            }
                            for item in sorted(
                                prerequisites.get(definition.id, []),
                                key=lambda item: item.prerequisite_competency_identity_id,
                            )
                            if item.kind == "required"
                        ],
                        "allPrerequisites": [
                            {
                                "competencyIdentityId": item.prerequisite_competency_identity_id,
                                "kind": item.kind,
                                "legacyVerified": (
                                    states[item.prerequisite_competency_identity_id].current_status
                                    == "verified"
                                ),
                            }
                            for item in sorted(
                                prerequisites.get(definition.id, []),
                                key=lambda item: (
                                    item.kind,
                                    item.prerequisite_competency_identity_id,
                                ),
                            )
                        ],
                    }
                    for definition in sorted(definitions, key=lambda item: item.id)
                ],
                "sessionFacts": [
                    {
                        "sessionId": fact.id,
                        "competencyIdentityId": fact.competency_identity_id,
                        "trackId": fact.track_id,
                        "localDate": fact.local_date.isoformat(),
                        "durationMs": fact.duration_ms,
                        "practical": fact.practical,
                        "independentPractical": fact.independent_practical,
                    }
                    for fact in sorted(sessions, key=lambda item: item.id)
                ],
                "sessionWindowFacts": {
                    "previous30PracticalByCompetency": previous_30_practical,
                    "previous7DurationByTrack": previous_7_track_duration,
                    "previousDayTotalDurationMs": previous_day_total,
                    "previousDayDurationByCompetency": previous_day_competency_duration,
                    "todayCompletedDurationMs": today_completed,
                    "telemetryPresent": bool(sessions),
                },
                "disciplineConstraints": {
                    "weeklyTargetActiveDays": profile.weekly_target_active_days,
                    "targetDurationMsPerActiveDay": profile.target_duration_ms_per_active_day,
                    "timezone": profile.timezone,
                },
            }
        }
        status_events = db.scalars(
            select(CompetencyStatusEvent).where(
                CompetencyStatusEvent.competency_identity_id.in_(states),
                CompetencyStatusEvent.created_at < cutoff,
            )
        ).all()
        verification_records = db.scalars(
            select(VerificationRecord).where(
                VerificationRecord.competency_identity_id.in_(states),
                VerificationRecord.created_at < cutoff,
            )
        ).all()
        exit_identities = db.scalars(
            select(ExitCriterionIdentity).where(
                ExitCriterionIdentity.competency_identity_id.in_(states)
            )
        ).all()
        exit_definitions = db.scalars(
            select(ExitCriterionDefinition).where(
                ExitCriterionDefinition.competency_definition_id.in_(
                    [item.id for item in definitions]
                )
            )
        ).all()
        lineage = [
            {"sourceType": "discipline_profile", "sourceId": "1"},
            {"sourceType": "roadmap_version", "sourceId": roadmap.active_version_id},
            {"sourceType": "roadmap_scope", "sourceId": f"{roadmap.id}:{roadmap.current_phase_id}"},
            *(
                {"sourceType": "phase", "sourceId": item.id}
                for item in sorted(phases, key=lambda item: item.id)
            ),
            *(
                {"sourceType": "track", "sourceId": item.id}
                for item in sorted(tracks, key=lambda item: item.id)
            ),
            *(
                {"sourceType": "competency_identity", "sourceId": item.id}
                for item in sorted(identities.values(), key=lambda item: item.id)
            ),
            *(
                {"sourceType": "competency_definition", "sourceId": item.id}
                for item in sorted(definitions, key=lambda item: item.id)
            ),
            *(
                {"sourceType": "competency_state", "sourceId": identity_id}
                for identity_id in sorted(states)
            ),
            *(
                {"sourceType": "competency_prerequisite", "sourceId": item.id}
                for item in sorted(prerequisite_items, key=lambda item: item.id)
            ),
            *(
                {"sourceType": "exit_criterion_definition", "sourceId": item.id}
                for item in sorted(exit_definitions, key=lambda item: item.id)
            ),
            *(
                {"sourceType": "exit_criterion_identity", "sourceId": item.id}
                for item in sorted(exit_identities, key=lambda item: item.id)
            ),
            *(
                {"sourceType": "competency_status_event", "sourceId": item.id}
                for item in sorted(status_events, key=lambda item: item.id)
            ),
            *(
                {"sourceType": "verification_record", "sourceId": item.id}
                for item in sorted(verification_records, key=lambda item: item.id)
            ),
            *(
                {"sourceType": "learning_session", "sourceId": item.id}
                for item in sorted(sessions, key=lambda item: item.id)
            ),
        ]
    configuration = normalized["legacy_v1_recommendation_input"]["disciplineConstraints"]
    unknown_markers = tuple(
        {
            "code": code,
            "path": path,
            "reason": "Not yet available during the V1 compatibility phase.",
        }
        for code, path in (
            ("TARGET_PROFILE_MISSING", "targetProfile"),
            ("LEARNING_GRAPH_MISSING", "learningGraph"),
            ("CURRICULUM_MISSING", "curriculum"),
            ("SEMANTIC_DEFINITIONS_MISSING", "semanticDefinitions"),
            ("CAPABILITY_SCALE_MISSING", "capabilityScaleVersions"),
            ("CAPABILITY_POLICY_MISSING", "capabilityPolicy"),
            ("CAPABILITY_EVALUATION_MISSING", "capabilityEvaluation"),
        )
    )
    input_hash = content_hash({"lineage": lineage, "normalizedFacts": normalized})
    output_material = {
        "normalizedFacts": normalized,
        "signals": [],
        "completeness": "partial",
        "unknownMarkers": unknown_markers,
    }
    policy_versions = {
        "analysis": "analysis-envelope-v1",
        "recommendation": ALGORITHM_VERSION,
        "analyticsInput": "analytics-v2-frozen-adapter",
        "criterionEvaluation": None,
        "evidenceQualification": None,
        "freshness": None,
        "gap": None,
        "readiness": None,
        "capabilityEvaluation": None,
    }
    return AnalysisEnvelope(
        purpose=PURPOSE,
        schema_version=1,
        generated_at=now,
        cutoff_at=cutoff,
        cutoff_semantics="exclusive",
        timezone=profile.timezone,
        completed_through_date=today.isoformat(),
        target_profile_id=None,
        target_profile_version_id=None,
        capability_scale_version_references=(),
        learning_graph_reference=None,
        curriculum_reference=None,
        semantic_definition_references=(),
        policy_versions=immutable(policy_versions),
        discipline_configuration_reference="discipline_profile:1",
        configuration_hash=content_hash(configuration),
        application_version=APPLICATION_VERSION,
        input_lineage=immutable(lineage),
        input_hash=input_hash,
        normalized_facts=immutable(normalized),
        signals=(),
        completeness="partial",
        unknown_markers=immutable(unknown_markers),
        output_hash=content_hash(output_material),
    )
