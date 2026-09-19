from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import Engine, Table, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.analysis.v3.models import (
    AnalysisV3CompetencyGap,
    AnalysisV3CurrentState,
    AnalysisV3NormalizedFact,
    AnalysisV3RunLineage,
    AnalysisV3Signal,
    AnalysisV3SnapshotDetail,
    AnalysisV3UnknownMarker,
)
from app.analysis.v3.policy import ANALYSIS_POLICY_VERSION, analysis_policy_bundle
from app.analysis.v3.service import build_analysis_inputs
from app.analysis_sources import analysis_source_generation
from app.analytics import build_analytics
from app.api_serialization import serialize_api_instants
from app.auth import AuthContext, get_auth_context, require_csrf
from app.capability import (
    capability_evidence_set_hash,
    drain_projection_invalidations,
    enqueue_full_capability_rebuild,
    seed_capability_projections_from_history,
)
from app.compatibility.v1.portable import (
    read_v1_portable_package,
    upgrade_v1_activity_session_tables,
    upgrade_v1_evidence_tables,
    upgrade_v1_profile_competency_tables,
)
from app.compatibility.v1.roadmap_active_state import current_legacy_roadmap
from app.config import Settings, get_settings_dependency
from app.curriculum.models import (
    ActiveCurriculumVersionState,
    ActivityCurriculumLinkCorrection,
    ActivityCurriculumUnitLink,
    AssessmentRubricDefinition,
    AssessmentRubricIdentity,
    Curriculum,
    CurriculumActivationEvent,
    CurriculumObjectiveDefinition,
    CurriculumObjectiveIdentity,
    CurriculumVersion,
    EvidenceOpportunityDefinition,
    LearningUnitDefinition,
    LearningUnitIdentity,
    LearningUnitRequirement,
    LearningUnitTarget,
)
from app.curriculum.service import (
    CURRICULUM_AVAILABILITY_POLICY,
    build_catalog,
    build_unit_availability,
    serialize_catalog,
)
from app.database import create_database_engine, get_db, run_migrations
from app.determinism import canonical_json, content_hash
from app.domain import transition_status
from app.domain_integrity import (
    portable_state_presence,
    validate_domain_integrity,
    validate_portable_row_types,
)
from app.errors import AppError
from app.evidence import create_verification_with_evidence
from app.import_diff import build_portable_replacement_diff, build_roadmap_diff
from app.learning_graph.models import (
    ActiveLearningGraphState,
    CompetencyEdgeDefinition,
    CompetencyEdgeIdentity,
    LearningGraph,
    LearningGraphActivationEvent,
    LearningGraphVersion,
)
from app.models import (
    ActiveCompetencyDefinitionState,
    ActiveTargetProfileState,
    Activity,
    ActivityCategoryVersion,
    AnalysisRun,
    AnalysisSnapshot,
    ApplicationSetting,
    CapabilityEvaluationRun,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CapabilityStateEvent,
    CompetencyAbilityItem,
    CompetencyCapabilityState,
    CompetencyDefinition,
    CompetencyDefinitionActivationEvent,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyReviewState,
    CompetencyState,
    CompetencyStatusEvent,
    CompetencyUnderstandingItem,
    ContributionRetraction,
    CriterionDefinition,
    CriterionEvaluationResult,
    CriterionIdentity,
    DailyReflection,
    DisciplineConfigurationEvent,
    DisciplineProfile,
    Evidence,
    EvidenceInvalidation,
    EvidenceLink,
    EvidenceLinkRetraction,
    EvidenceRedaction,
    EvidenceRetraction,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    ExportRecord,
    GeneratedReport,
    ImportRecord,
    LearningSession,
    LegacyCriterionAssertion,
    MigrationBackfillRun,
    MilestoneIdentity,
    OperationalBackup,
    Phase,
    ProfileDomain,
    ProfileMilestone,
    ProfileMilestoneTarget,
    ProfileTarget,
    ProfileTargetIdentity,
    ProjectionInvalidation,
    ReadinessGate,
    ReadinessGateIdentity,
    ReadinessGatePredicate,
    ReadinessGateTarget,
    RecommendationSnapshot,
    ReviewEvent,
    Roadmap,
    RoadmapScopeEvent,
    RoadmapVersion,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
    SessionContribution,
    SessionCorrection,
    TargetProfile,
    TargetProfileActivationEvent,
    TargetProfileVersion,
    Track,
    VerificationEvidence,
    VerificationRecord,
)
from app.portability.registry import (
    PORTABLE_SCHEMA_CURRENT,
    PORTABLE_V1_FORBIDDEN_TABLES,
    PORTABLE_V2_FORBIDDEN_TABLES,
    PORTABLE_V2_FOUNDATION_TABLES,
    PORTABLE_V2_MANIFEST,
    PORTABLE_V3_CURRICULUM_TABLES,
    PORTABLE_V3_FORBIDDEN_TABLES,
    PORTABLE_V3_MANIFEST,
    PORTABLE_V4_FORBIDDEN_TABLES,
    PORTABLE_V4_MANIFEST,
    PORTABLE_V4_PROJECT_TABLES,
    PORTABLE_V5_FORBIDDEN_TABLES,
    PORTABLE_V5_GRAPH_PROJECTION_TABLES,
    PORTABLE_V5_MANIFEST,
    PORTABLE_V6_ANALYSIS_TABLES,
    PORTABLE_V6_FORBIDDEN_TABLES,
    PORTABLE_V6_MANIFEST,
    PORTABLE_V7_FORBIDDEN_TABLES,
    PORTABLE_V7_MANIFEST,
    PORTABLE_V7_RECOMMENDATION_TABLES,
    PORTABLE_V8_MANIFEST,
    PORTABLE_V8_TODAY_TABLES,
    supports_portable_schema,
    upgrade_v2_to_v3_tables,
    upgrade_v3_to_v4_tables,
    upgrade_v4_to_v5_tables,
    upgrade_v5_to_v6_tables,
    upgrade_v6_to_v7_tables,
    upgrade_v7_to_v8_tables,
)
from app.projects.contracts import ProjectCatalogPublicDTO
from app.projects.models import (
    ActiveProjectVersionState,
    ActivityProjectTaskLink,
    ActivityProjectTaskLinkCorrection,
    Project,
    ProjectCriterionDefinition,
    ProjectCriterionEvaluation,
    ProjectCriterionEvaluationEvidence,
    ProjectCriterionIdentity,
    ProjectEvent,
    ProjectEvidenceOpportunity,
    ProjectGoalDefinition,
    ProjectGoalIdentity,
    ProjectMilestoneDefinition,
    ProjectMilestoneIdentity,
    ProjectRequirement,
    ProjectTarget,
    ProjectTaskDefinition,
    ProjectTaskDependency,
    ProjectTaskIdentity,
    ProjectVersion,
    ProjectVersionActivationEvent,
    SessionProjectContribution,
    SessionProjectContributionRetraction,
)
from app.projects.service import PROJECT_AVAILABILITY_POLICY
from app.projects.service import build_catalog as build_project_catalog
from app.recommendation.v2.models import (
    RecommendationV2Candidate,
    RecommendationV2EligibilityDecision,
    RecommendationV2EligibilityRuleResult,
    RecommendationV2ExpectedValue,
    RecommendationV2Reason,
    RecommendationV2Recommendation,
    RecommendationV2Run,
    RecommendationV2ScoreComponent,
    RecommendationV2SelectionDecision,
)
from app.roadmap_projection.models import (
    LegacyRoadmapActiveState,
    RoadmapNodePositionOverride,
    RoadmapProjectionCache,
    RoadmapProjectionCheckpoint,
    RoadmapProjectionPreference,
)
from app.roadmap_projection.service import (
    build_projection as build_roadmap_projection,
)
from app.roadmap_projection.service import (
    rebuild_projection as rebuild_roadmap_projection,
)
from app.schemas import (
    ExportRequest,
    ImportApplyRequest,
    ImportInspectRequest,
    PortablePackagePayload,
    RoadmapCreate,
    RoadmapPackagePayload,
    StateUpdatePayload,
    VerificationUpdatePayload,
    validate_external_reference,
)
from app.security import RateLimitRule, new_secret, rate_limiter
from app.settings_api import get_or_create_profile
from app.time_utils import (
    epoch_ms_to_rfc3339,
    local_date_for_ms,
    local_day_bounds_ms,
    utc_now_ms,
)
from app.today.models import (
    SuggestionActivityRelation,
    SuggestionActivityRelationCorrection,
    TodayGeneration,
    TodayInteraction,
    TodayInteractionCorrection,
    TodaySuggestion,
    TodaySuggestionCurrentState,
)
from app.today.service import rebuild_today_current_states, today_current_checkpoint

router = APIRouter(prefix="/import-export", tags=["import/export"])
logger = logging.getLogger(__name__)

PORTABLE_MODELS = [
    Roadmap,
    RoadmapVersion,
    Phase,
    Track,
    RoadmapScopeEvent,
    CompetencyIdentity,
    CapabilityScaleVersion,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CompetencyDefinition,
    CompetencyPrerequisite,
    CompetencyUnderstandingItem,
    CompetencyAbilityItem,
    ExitCriterionIdentity,
    ExitCriterionDefinition,
    TargetProfile,
    TargetProfileVersion,
    ProfileDomain,
    ProfileTargetIdentity,
    ProfileTarget,
    MilestoneIdentity,
    ProfileMilestone,
    ProfileMilestoneTarget,
    ReadinessGateIdentity,
    ReadinessGate,
    ReadinessGatePredicate,
    ReadinessGateTarget,
    ActiveTargetProfileState,
    TargetProfileActivationEvent,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
    CriterionIdentity,
    CriterionDefinition,
    ActiveCompetencyDefinitionState,
    CompetencyDefinitionActivationEvent,
    LegacyCriterionAssertion,
    LearningGraph,
    LearningGraphVersion,
    CompetencyEdgeIdentity,
    CompetencyEdgeDefinition,
    ActiveLearningGraphState,
    LearningGraphActivationEvent,
    Curriculum,
    CurriculumVersion,
    CurriculumObjectiveIdentity,
    CurriculumObjectiveDefinition,
    LearningUnitIdentity,
    LearningUnitDefinition,
    LearningUnitTarget,
    LearningUnitRequirement,
    EvidenceOpportunityDefinition,
    AssessmentRubricIdentity,
    AssessmentRubricDefinition,
    ActiveCurriculumVersionState,
    CurriculumActivationEvent,
    Project,
    ProjectVersion,
    ProjectMilestoneIdentity,
    ProjectMilestoneDefinition,
    ProjectGoalIdentity,
    ProjectGoalDefinition,
    ProjectTaskIdentity,
    ProjectTaskDefinition,
    ProjectCriterionIdentity,
    ProjectCriterionDefinition,
    ProjectTarget,
    ProjectRequirement,
    ProjectTaskDependency,
    ProjectEvidenceOpportunity,
    ActiveProjectVersionState,
    ProjectVersionActivationEvent,
    ProjectEvent,
    LegacyRoadmapActiveState,
    RoadmapNodePositionOverride,
    RoadmapProjectionPreference,
    ActivityCategoryVersion,
    Activity,
    ActivityCurriculumUnitLink,
    ActivityCurriculumLinkCorrection,
    ActivityProjectTaskLink,
    ActivityProjectTaskLinkCorrection,
    LearningSession,
    SessionContribution,
    ContributionRetraction,
    SessionProjectContribution,
    SessionProjectContributionRetraction,
    SessionCorrection,
    CompetencyState,
    VerificationRecord,
    VerificationEvidence,
    Evidence,
    EvidenceLink,
    EvidenceRetraction,
    EvidenceInvalidation,
    EvidenceLinkRetraction,
    EvidenceRedaction,
    ProjectCriterionEvaluation,
    ProjectCriterionEvaluationEvidence,
    CapabilityEvaluationRun,
    CriterionEvaluationResult,
    CapabilityStateEvent,
    ReviewEvent,
    CompetencyStatusEvent,
    MigrationBackfillRun,
    DailyReflection,
    GeneratedReport,
    AnalysisRun,
    AnalysisSnapshot,
    AnalysisV3RunLineage,
    AnalysisV3SnapshotDetail,
    AnalysisV3NormalizedFact,
    AnalysisV3CompetencyGap,
    AnalysisV3Signal,
    AnalysisV3UnknownMarker,
    RecommendationV2Run,
    RecommendationV2Candidate,
    RecommendationV2EligibilityDecision,
    RecommendationV2EligibilityRuleResult,
    RecommendationV2ExpectedValue,
    RecommendationV2ScoreComponent,
    RecommendationV2SelectionDecision,
    RecommendationV2Recommendation,
    RecommendationV2Reason,
    TodayGeneration,
    TodaySuggestion,
    TodayInteraction,
    TodayInteractionCorrection,
    SuggestionActivityRelation,
    SuggestionActivityRelationCorrection,
    RecommendationSnapshot,
    DisciplineProfile,
    DisciplineConfigurationEvent,
    ImportRecord,
    ExportRecord,
    ApplicationSetting,
]


def _table(model: Any) -> Table:
    return cast(Table, model.__table__)


PORTABLE_BY_TABLE = {_table(model).name: model for model in PORTABLE_MODELS}


@dataclass
class Preview:
    digest: str
    token: str
    expires_at: int
    summary: dict[str, Any]


@dataclass(frozen=True)
class ResolvedExportScope:
    start_date: date
    end_date: date
    timezone: str
    selected_identity_ids: set[str] | None
    scope_payload: dict[str, Any]


_previews: dict[str, Preview] = {}


def _package_digest(package: dict[str, Any]) -> str:
    raw = json.dumps(package, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _row_dict(item: Any) -> dict[str, Any]:
    return {column.name: getattr(item, column.name) for column in _table(type(item)).columns}


def _capability_projection_checkpoints(db: Session) -> list[dict[str, str]]:
    checkpoints: list[dict[str, str]] = []
    for state in db.scalars(select(CompetencyCapabilityState)).all():
        run = db.get(CapabilityEvaluationRun, state.evaluation_run_id)
        if run is None:
            raise AppError(
                500,
                "CAPABILITY_PROJECTION_INVALID",
                "Capability projection references missing immutable history.",
            )
        checkpoints.append(
            {
                "competencyIdentityId": state.competency_identity_id,
                "scopeKey": state.scope_key,
                "evaluationRunId": state.evaluation_run_id,
                "evidenceSetHash": state.evidence_set_hash,
                "outputHash": run.output_hash,
            }
        )
    return checkpoints


def _portable_payload(db: Session, project_ids: set[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "manifest": PORTABLE_V8_MANIFEST,
        "tables": {
            _table(model).name: [_row_dict(item) for item in db.scalars(select(model)).all()]
            for model in PORTABLE_MODELS
        },
    }
    tables = payload["tables"]
    all_project_ids = {row["id"] for row in tables["projects"]}
    if project_ids is not None and project_ids - all_project_ids:
        raise AppError(
            422,
            "EXPORT_SCOPE_INVALID",
            "A selected Project does not exist.",
            {"unknownProjectIds": sorted(project_ids - all_project_ids)},
        )
    requested_project_ids = set(project_ids) if project_ids is not None else set(all_project_ids)
    versions_by_id = {row["id"]: row for row in tables["project_versions"]}
    task_defs_by_id = {row["id"]: row for row in tables["project_task_definitions"]}
    project_links_by_id = {row["id"]: row for row in tables["activity_project_task_links"]}
    project_criteria_by_identity = {
        row["id"]: row for row in tables["project_criterion_identities"]
    }
    closure_project_ids: set[str] = set()
    analysis_runs_by_id = {row["id"]: row for row in tables["analysis_runs"]}
    for lineage in tables["analysis_v3_run_lineages"]:
        run = analysis_runs_by_id.get(lineage["run_id"])
        if run is None:
            continue
        frozen_inputs = json.loads(run["input_lineage_json"])
        project_catalog = frozen_inputs.get("projectCatalog", {})
        closure_project_ids.update(
            str(item["project_id"]) for item in project_catalog.get("active_version_references", [])
        )
        closure_project_ids.update(
            str(item["project_id"]) for item in project_catalog.get("candidates", [])
        )
    for run in tables["recommendation_v2_runs"]:
        frozen_inputs = json.loads(run["frozen_input_json"])
        project_catalog = frozen_inputs.get(
            "projects", frozen_inputs.get("projectAndUserConstraints", {})
        )
        closure_project_ids.update(
            str(item["project_id"])
            for item in project_catalog.get("candidates", [])
            if item.get("project_id")
        )
    closure_project_ids.update(
        str(row["project_id"])
        for row in tables["recommendation_v2_candidates"]
        if row.get("project_id")
    )
    for row in tables["evidence"]:
        if row["source_type"] != "activity_project_task_link":
            continue
        source_link = project_links_by_id.get(row["source_id"])
        task = task_defs_by_id.get(source_link["task_definition_id"]) if source_link else None
        version = versions_by_id.get(task["project_version_id"]) if task else None
        if version is not None:
            closure_project_ids.add(version["project_id"])
    for row in tables["readiness_gate_predicates"]:
        if row["predicate_type"] != "project_criterion_demonstrated":
            continue
        subject = json.loads(row["subject_json"])
        identity = project_criteria_by_identity.get(subject.get("projectCriterionIdentityId"))
        if identity is not None:
            closure_project_ids.add(identity["project_id"])
    included_project_ids = requested_project_ids | closure_project_ids
    link_project_ids: dict[str, str] = {}
    for link in tables["activity_project_task_links"]:
        task = task_defs_by_id.get(link["task_definition_id"])
        version = versions_by_id.get(task["project_version_id"]) if task else None
        if version is not None:
            link_project_ids[link["id"]] = version["project_id"]
    contribution_project_ids = {
        row["id"]: row["project_id"] for row in tables["session_project_contributions"]
    }
    changed = True
    while changed:
        changed = False
        for correction in tables["activity_project_task_link_corrections"]:
            original_project_id = link_project_ids.get(correction["activity_project_task_link_id"])
            replacement_project_id = link_project_ids.get(correction["replacement_link_id"])
            if (
                original_project_id in included_project_ids
                and replacement_project_id is not None
                and replacement_project_id not in included_project_ids
            ):
                included_project_ids.add(replacement_project_id)
                changed = True
        for retraction in tables["session_project_contribution_retractions"]:
            original_project_id = contribution_project_ids.get(retraction["contribution_id"])
            replacement_project_id = contribution_project_ids.get(
                retraction["replacement_contribution_id"]
            )
            if (
                original_project_id in included_project_ids
                and replacement_project_id is not None
                and replacement_project_id not in included_project_ids
            ):
                included_project_ids.add(replacement_project_id)
                changed = True
    closure_project_ids = included_project_ids - requested_project_ids

    if included_project_ids != all_project_ids:
        included_version_ids = {
            row["id"]
            for row in tables["project_versions"]
            if row["project_id"] in included_project_ids
        }
        identity_tables = (
            "project_goal_identities",
            "project_task_identities",
            "project_criterion_identities",
            "project_milestone_identities",
        )
        for table_name in identity_tables:
            tables[table_name] = [
                row for row in tables[table_name] if row["project_id"] in included_project_ids
            ]
        definition_tables = (
            "project_goal_definitions",
            "project_task_definitions",
            "project_criterion_definitions",
            "project_milestone_definitions",
            "project_targets",
            "project_requirements",
            "project_task_dependencies",
            "project_evidence_opportunities",
        )
        for table_name in definition_tables:
            tables[table_name] = [
                row
                for row in tables[table_name]
                if row["project_version_id"] in included_version_ids
            ]
        included_task_definition_ids = {row["id"] for row in tables["project_task_definitions"]}
        included_criterion_definition_ids = {
            row["id"] for row in tables["project_criterion_definitions"]
        }
        tables["projects"] = [
            row for row in tables["projects"] if row["id"] in included_project_ids
        ]
        tables["project_versions"] = [
            row for row in tables["project_versions"] if row["id"] in included_version_ids
        ]
        tables["active_project_version_states"] = [
            row
            for row in tables["active_project_version_states"]
            if row["project_id"] in included_project_ids
        ]
        for table_name in ("project_version_activation_events", "project_events"):
            tables[table_name] = [
                row for row in tables[table_name] if row["project_id"] in included_project_ids
            ]
        tables["activity_project_task_links"] = [
            row
            for row in tables["activity_project_task_links"]
            if row["task_definition_id"] in included_task_definition_ids
        ]
        included_link_ids = {row["id"] for row in tables["activity_project_task_links"]}
        tables["activity_project_task_link_corrections"] = [
            row
            for row in tables["activity_project_task_link_corrections"]
            if row["activity_project_task_link_id"] in included_link_ids
        ]
        tables["session_project_contributions"] = [
            row
            for row in tables["session_project_contributions"]
            if row["project_id"] in included_project_ids
        ]
        included_contribution_ids = {row["id"] for row in tables["session_project_contributions"]}
        tables["session_project_contribution_retractions"] = [
            row
            for row in tables["session_project_contribution_retractions"]
            if row["contribution_id"] in included_contribution_ids
        ]
        tables["project_criterion_evaluations"] = [
            row
            for row in tables["project_criterion_evaluations"]
            if row["project_criterion_definition_id"] in included_criterion_definition_ids
        ]
        included_evaluation_ids = {row["id"] for row in tables["project_criterion_evaluations"]}
        tables["project_criterion_evaluation_evidence"] = [
            row
            for row in tables["project_criterion_evaluation_evidence"]
            if row["project_criterion_evaluation_id"] in included_evaluation_ids
        ]
    payload["portableScope"] = {
        "requestedProjectIds": sorted(requested_project_ids),
        "includedProjectIds": sorted(included_project_ids),
        "closureAddedProjectIds": sorted(included_project_ids - requested_project_ids),
    }
    evidence_by_id = {row["id"]: row for row in tables["evidence"]}
    verification_records = {row["id"]: row for row in tables["verification_records"]}
    verification_context = {row["id"]: row for row in tables["verification_evidence"]}
    for evidence in evidence_by_id.values():
        try:
            validate_external_reference(evidence["external_reference"])
        except ValueError:
            evidence["external_reference"] = None
    for source in verification_context.values():
        try:
            validate_external_reference(source["reference"])
        except ValueError:
            source["reference"] = "[redacted]"
    for redaction in tables["evidence_redactions"]:
        evidence = evidence_by_id.get(redaction["evidence_id"])
        if evidence is None:
            continue
        fields = set(json.loads(redaction["redacted_fields_json"]))
        if "description" in fields:
            evidence["description"] = None
        if "external_reference" in fields:
            evidence["external_reference"] = None
        if evidence["source_type"] == "verification_record" and "description" in fields:
            source = verification_records.get(evidence["source_id"])
            if source is not None:
                source["evidence_summary"] = None
        if evidence["source_type"] == "verification_evidence":
            source = verification_context.get(evidence["source_id"])
            if source is not None:
                if "description" in fields:
                    source["description"] = ""
                if "external_reference" in fields:
                    source["reference"] = "[redacted]"
    payload["capabilityProjectionCheckpoints"] = _capability_projection_checkpoints(db)
    curriculum_cutoff = utc_now_ms() + 1
    curriculum_catalog = build_catalog(db, curriculum_cutoff)
    references = serialize_catalog(curriculum_catalog)["activeVersionReferences"]
    availability_hashes: list[dict[str, str]] = []
    for item in curriculum_catalog.units:
        unit = db.get(LearningUnitDefinition, item.unit_definition_id)
        if unit is None:
            raise AppError(
                500,
                "CURRICULUM_HISTORY_INVALID",
                "The active Curriculum catalog references a missing unit.",
            )
        availability_hashes.append(
            {
                "unitDefinitionId": unit.id,
                "inputHash": build_unit_availability(db, unit, curriculum_cutoff).input_hash,
            }
        )
    checkpoint = {
        "cutoffAt": curriculum_cutoff,
        "cutoffSemantics": "exclusive",
        "policyVersion": CURRICULUM_AVAILABILITY_POLICY,
        "sourceHash": content_hash(references),
        "catalogHash": curriculum_catalog.input_hash,
        "activeVersionReferences": references,
        "availabilityHashes": availability_hashes,
    }
    checkpoint["inputHash"] = content_hash(checkpoint)
    payload["curriculumCatalogCheckpoint"] = checkpoint
    project_cutoff = utc_now_ms() + 1
    project_catalog = build_project_catalog(db, project_cutoff)
    if included_project_ids != all_project_ids:
        project_catalog = ProjectCatalogPublicDTO.build(
            cutoff_at=project_catalog.cutoff_at,
            active_version_references=tuple(
                item
                for item in project_catalog.active_version_references
                if item.project_id in included_project_ids
            ),
            candidates=tuple(
                item
                for item in project_catalog.candidates
                if item.project_id in included_project_ids
            ),
        )
    project_references = [asdict(item) for item in project_catalog.active_version_references]
    candidate_hashes = [
        {"taskDefinitionId": item.task_definition_id, "inputHash": item.input_hash}
        for item in project_catalog.candidates
    ]
    project_checkpoint = {
        "cutoffAt": project_cutoff,
        "cutoffSemantics": "exclusive",
        "policyVersion": PROJECT_AVAILABILITY_POLICY,
        "sourceHash": content_hash(project_references),
        "catalogHash": project_catalog.input_hash,
        "activeVersionReferences": project_references,
        "candidateHashes": candidate_hashes,
    }
    project_checkpoint["inputHash"] = content_hash(project_checkpoint)
    payload["projectCatalogCheckpoint"] = project_checkpoint
    projection = build_roadmap_projection(
        db,
        cutoff_at=project_cutoff,
        project_catalog=project_catalog,
        include_current_presentation=True,
    )
    projection_checkpoint = {
        "configured": bool(projection.get("configured")),
        "cutoffAt": project_cutoff,
        "cutoffSemantics": "exclusive",
        "projectionPolicyVersion": projection.get("projectionPolicyVersion"),
        "layoutPolicyVersion": projection.get("layoutPolicyVersion"),
        "scopeKey": projection.get("scopeKey"),
        "sourceHash": content_hash(projection.get("sourceLineage", {})),
        "outputHash": content_hash(projection),
    }
    projection_checkpoint["inputHash"] = content_hash(projection_checkpoint)
    payload["roadmapProjectionCheckpoint"] = projection_checkpoint
    analysis_checkpoint: dict[str, Any] = {
        "states": [
            {
                "scopeKey": item.scope_key,
                "purpose": item.purpose,
                "runId": item.run_id,
                "snapshotId": item.snapshot_id,
                "status": item.status,
                "exclusiveCutoffAt": item.exclusive_cutoff_at,
                "sourceGeneration": item.source_generation,
                "inputHash": item.input_hash,
                "policyBundleHash": item.policy_bundle_hash,
                "updatedAt": item.updated_at,
            }
            for item in db.scalars(
                select(AnalysisV3CurrentState).order_by(
                    AnalysisV3CurrentState.scope_key, AnalysisV3CurrentState.purpose
                )
            ).all()
        ]
    }
    analysis_checkpoint["checkpointHash"] = content_hash(analysis_checkpoint)
    payload["analysisV3CurrentCheckpoint"] = analysis_checkpoint
    recommendation_history = {
        table_name: sorted(tables[table_name], key=canonical_json)
        for table_name in sorted(PORTABLE_V7_RECOMMENDATION_TABLES)
    }
    recommendation_checkpoint = {
        "runHashes": [
            {
                "runId": row["id"],
                "analysisSnapshotId": row["analysis_snapshot_id"],
                "inputHash": row["input_hash"],
                "outputHash": row["output_hash"],
                "policyBundleHash": row["policy_bundle_hash"],
            }
            for row in sorted(tables["recommendation_v2_runs"], key=lambda item: item["id"])
        ],
        "historyHash": content_hash(recommendation_history),
    }
    recommendation_checkpoint["checkpointHash"] = content_hash(recommendation_checkpoint)
    payload["recommendationV2HistoryCheckpoint"] = recommendation_checkpoint
    payload["todayV2CurrentCheckpoint"] = today_current_checkpoint(db)
    return payload


def _validate_capability_checkpoints(
    payload: dict[str, Any], tables: dict[str, list[dict[str, Any]]]
) -> None:
    checkpoints = payload.get("capabilityProjectionCheckpoints", [])
    if not isinstance(checkpoints, list):
        raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Capability checkpoints are invalid.")
    runs = {row["id"]: row for row in tables.get("capability_evaluation_runs", [])}
    seen: set[tuple[str, str]] = set()
    expected_keys = {
        "competencyIdentityId",
        "scopeKey",
        "evaluationRunId",
        "evidenceSetHash",
        "outputHash",
    }
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict) or set(checkpoint) != expected_keys:
            raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Capability checkpoints are invalid.")
        key = (checkpoint["competencyIdentityId"], checkpoint["scopeKey"])
        run = runs.get(checkpoint["evaluationRunId"])
        if (
            key in seen
            or run is None
            or run["competency_identity_id"] != key[0]
            or run["scope_key"] != key[1]
            or run["evidence_set_hash"] != checkpoint["evidenceSetHash"]
            or run["output_hash"] != checkpoint["outputHash"]
        ):
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_CHECKPOINT_INVALID",
                "Capability projection checkpoint is disconnected from immutable history.",
            )
        seen.add(key)


def _validate_roadmap_projection_checkpoint(payload: dict[str, Any], schema_version: int) -> None:
    checkpoint = payload.get("roadmapProjectionCheckpoint")
    if schema_version < 5:
        if checkpoint is not None:
            raise AppError(
                422,
                "PORTABLE_SCHEMA_INVALID",
                "Portable schema versions before V5 cannot contain a "
                "Roadmap Projection checkpoint.",
            )
        return
    expected = {
        "configured",
        "cutoffAt",
        "cutoffSemantics",
        "projectionPolicyVersion",
        "layoutPolicyVersion",
        "scopeKey",
        "sourceHash",
        "outputHash",
        "inputHash",
    }
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != expected
        or type(checkpoint["configured"]) is not bool
        or type(checkpoint["cutoffAt"]) is not int
        or checkpoint["cutoffSemantics"] != "exclusive"
        or checkpoint["inputHash"]
        != content_hash({key: value for key, value in checkpoint.items() if key != "inputHash"})
    ):
        raise AppError(
            422,
            "PORTABLE_ROADMAP_PROJECTION_CHECKPOINT_INVALID",
            "The Roadmap Projection checkpoint is invalid.",
        )


def _assert_roadmap_projection_checkpoint_parity(
    db: Session, checkpoint: dict[str, Any] | None
) -> None:
    if checkpoint is None:
        return
    projection = build_roadmap_projection(
        db,
        cutoff_at=checkpoint["cutoffAt"],
        include_current_presentation=True,
    )
    if (
        bool(projection.get("configured")) != checkpoint["configured"]
        or projection.get("scopeKey") != checkpoint["scopeKey"]
        or projection.get("projectionPolicyVersion") != checkpoint["projectionPolicyVersion"]
        or projection.get("layoutPolicyVersion") != checkpoint["layoutPolicyVersion"]
        or content_hash(projection.get("sourceLineage", {})) != checkpoint["sourceHash"]
        or content_hash(projection) != checkpoint["outputHash"]
    ):
        raise AppError(
            422,
            "PORTABLE_ROADMAP_PROJECTION_PARITY_FAILED",
            "Restored canonical facts do not reproduce the Roadmap Projection checkpoint.",
        )


def _validate_analysis_v3_checkpoint(
    payload: dict[str, Any], tables: dict[str, list[dict[str, Any]]], schema_version: int
) -> None:
    checkpoint = payload.get("analysisV3CurrentCheckpoint")
    if schema_version < 6:
        if checkpoint is not None:
            raise AppError(
                422,
                "PORTABLE_SCHEMA_INVALID",
                "Portable schema versions before V6 cannot contain an Analysis V3 checkpoint.",
            )
        return
    if not isinstance(checkpoint, dict) or "checkpointHash" not in checkpoint:
        raise AppError(
            422, "PORTABLE_ANALYSIS_CHECKPOINT_INVALID", "The Analysis V3 checkpoint is invalid."
        )
    if checkpoint["checkpointHash"] != content_hash(
        {key: value for key, value in checkpoint.items() if key != "checkpointHash"}
    ):
        raise AppError(
            422,
            "PORTABLE_ANALYSIS_CHECKPOINT_INVALID",
            "The Analysis V3 checkpoint hash is invalid.",
        )
    if set(checkpoint) != {"states", "checkpointHash"} or not isinstance(
        checkpoint.get("states"), list
    ):
        raise AppError(
            422, "PORTABLE_ANALYSIS_CHECKPOINT_INVALID", "The Analysis V3 checkpoint is invalid."
        )
    expected = {
        "scopeKey",
        "purpose",
        "runId",
        "snapshotId",
        "status",
        "exclusiveCutoffAt",
        "sourceGeneration",
        "inputHash",
        "policyBundleHash",
        "updatedAt",
    }
    runs = {row["id"]: row for row in tables.get("analysis_runs", [])}
    snapshots = {row["id"]: row for row in tables.get("analysis_snapshots", [])}
    lineages = {row["run_id"]: row for row in tables.get("analysis_v3_run_lineages", [])}
    seen: set[tuple[str, str]] = set()
    for state in checkpoint["states"]:
        if not isinstance(state, dict):
            raise AppError(
                422,
                "PORTABLE_ANALYSIS_CHECKPOINT_INVALID",
                "The Analysis V3 checkpoint is invalid.",
            )
        snapshot = snapshots.get(state.get("snapshotId"))
        lineage = lineages.get(state.get("runId"))
        run = runs.get(state.get("runId"))
        key = (str(state.get("scopeKey")), str(state.get("purpose")))
        if (
            set(state) != expected
            or key in seen
            or state.get("status") not in {"current", "stale", "pending", "failed"}
            or state.get("scopeKey") != "learning-control"
            or state.get("purpose") not in {"learning_control", "candidate_readiness"}
            or type(state.get("updatedAt")) is not int
            or run is None
            or state.get("updatedAt", -1) < run["generated_at"]
            or run["purpose"] != state.get("purpose")
            or snapshot is None
            or snapshot["run_id"] != state.get("runId")
            or snapshot["purpose"] != state.get("purpose")
            or snapshot["cutoff_at"] != state.get("exclusiveCutoffAt")
            or snapshot["input_hash"] != state.get("inputHash")
            or lineage is None
            or lineage["policy_bundle_hash"] != state.get("policyBundleHash")
            or lineage["source_generation"] != state.get("sourceGeneration")
        ):
            raise AppError(
                422,
                "PORTABLE_ANALYSIS_CHECKPOINT_INVALID",
                "The Analysis V3 checkpoint is disconnected from immutable history.",
            )
        seen.add(key)


def _recommendation_output_hash(tables: dict[str, list[dict[str, Any]]], run_id: str) -> str:
    from app.recommendation.v2.contracts import candidate_from_payload
    from app.recommendation.v2.policy import evaluate_registered

    run = next(row for row in tables["recommendation_v2_runs"] if row["id"] == run_id)
    candidates = sorted(
        (row for row in tables["recommendation_v2_candidates"] if row["run_id"] == run_id),
        key=lambda row: row["ordinal"],
    )
    return evaluate_registered(
        run["policy_registry_version"],
        tuple(candidate_from_payload(json.loads(row["candidate_json"])) for row in candidates),
        run["available_time_ms"],
    ).output_hash


def _recommendation_audit_projection(
    tables: dict[str, list[dict[str, Any]]], run_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.recommendation.v2.contracts import candidate_from_payload
    from app.recommendation.v2.policy import evaluate_registered

    run = next(row for row in tables["recommendation_v2_runs"] if row["id"] == run_id)
    candidate_rows = sorted(
        (row for row in tables["recommendation_v2_candidates"] if row["run_id"] == run_id),
        key=lambda row: row["ordinal"],
    )
    candidate_ids = {row["id"] for row in candidate_rows}
    eligibility = {
        row["candidate_id"]: row
        for row in tables["recommendation_v2_eligibility_decisions"]
        if row["candidate_id"] in candidate_ids
    }
    expected_values = {
        row["candidate_id"]: row
        for row in tables["recommendation_v2_expected_values"]
        if row["candidate_id"] in candidate_ids
    }
    selections = {
        row["candidate_id"]: row
        for row in tables["recommendation_v2_selection_decisions"]
        if row["candidate_id"] in candidate_ids
    }
    rules: dict[str, list[dict[str, Any]]] = defaultdict(list)
    components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reasons: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tables["recommendation_v2_eligibility_rule_results"]:
        if row["candidate_id"] in candidate_ids:
            rules[row["candidate_id"]].append(row)
    for row in tables["recommendation_v2_score_components"]:
        if row["candidate_id"] in candidate_ids:
            components[row["candidate_id"]].append(row)
    for row in tables["recommendation_v2_reasons"]:
        if row["candidate_id"] in candidate_ids:
            reasons[row["candidate_id"]].append(row)
    actual_candidates: list[dict[str, Any]] = []
    actual_decisions: list[dict[str, Any]] = []
    actual_reasons: list[dict[str, Any]] = []
    component_order = {
        code: ordinal
        for ordinal, code in enumerate(
            (
                "TARGET_PRIORITY",
                "PRIMARY_NEED",
                "DEADLINE_PRESSURE",
                "ALLOCATION_BALANCE",
                "NEGLECT_OR_STALL",
                "EXPECTED_LEARNING_VALUE",
                "CONTEXT_COST",
            )
        )
    }
    for candidate_row in candidate_rows:
        candidate_id = candidate_row["id"]
        candidate = json.loads(candidate_row["candidate_json"])
        eligibility_row = eligibility[candidate_id]
        expected_value = expected_values[candidate_id]
        selection = selections[candidate_id]
        actual_candidates.append(
            {
                "candidate": candidate,
                "eligible": bool(eligibility_row["eligible"]),
                "eligibility_reason": eligibility_row["reason_code"],
                "eligibility_rules": [
                    {
                        "code": row["rule_code"],
                        "outcome": row["outcome"],
                        "decisive": bool(row["decisive"]),
                        "facts": json.loads(row["facts_json"]),
                        "subject_ids": json.loads(row["subject_ids_json"]),
                    }
                    for row in sorted(rules[candidate_id], key=lambda item: item["ordinal"])
                ],
                "expected_learning_value": expected_value["value"],
                "expected_learning_value_reasons": json.loads(expected_value["reason_codes_json"]),
                "expected_learning_value_facts": json.loads(expected_value["matched_facts_json"]),
                "expected_learning_value_source_ids": sorted(
                    {
                        source_id
                        for row in reasons[candidate_id]
                        if row["reason_code"] in json.loads(expected_value["reason_codes_json"])
                        for source_id in json.loads(row["source_ids_json"])
                    }
                ),
                "score_components": [
                    {
                        "code": row["component_code"],
                        "value": row["value"],
                        "allowed_minimum": row["allowed_minimum"],
                        "allowed_maximum": row["allowed_maximum"],
                        "decisive_facts": json.loads(row["decisive_facts_json"]),
                        "source_ids": json.loads(row["source_ids_json"]),
                    }
                    for row in sorted(
                        components[candidate_id],
                        key=lambda item: component_order[item["component_code"]],
                    )
                ],
                "score_total": selection["score_total"],
                "rank_ordinal": selection["rank_ordinal"],
            }
        )
        actual_decisions.append(
            {
                "candidate_stable_id": candidate_row["stable_id"],
                "decision": selection["decision"],
                "portfolio_role": selection["portfolio_role"],
                "reason_code": selection["reason_code"],
                "advisory_duration_ms": selection["advisory_duration_ms"],
                "duration_reason_code": selection["duration_reason_code"],
                "admission_ordinal": selection["admission_ordinal"],
                "displaced_by_candidate_stable_id": selection["displaced_by_candidate_stable_id"],
                "decisive_facts": json.loads(selection["decisive_facts_json"]),
            }
        )
        actual_reasons.extend(
            {
                "candidate_stable_id": candidate_row["stable_id"],
                "ordinal": row["ordinal"],
                "reason_code": row["reason_code"],
                "title": row["title"],
                "facts": json.loads(row["explanation_facts_json"]),
                "score_contribution": row["score_contribution"],
                "template_key": row["template_key"],
                "template_version": row["template_version"],
                "rendered_text": row["rendered_text"],
                "source_ids": json.loads(row["source_ids_json"]),
                "policy_version": row["policy_version"],
            }
            for row in sorted(reasons[candidate_id], key=lambda item: item["ordinal"])
        )
    stable_by_candidate_id = {row["id"]: row["stable_id"] for row in candidate_rows}
    actual_recommendations = [
        {
            "candidate_stable_id": stable_by_candidate_id[row["candidate_id"]],
            "portfolio_role": row["portfolio_role"],
            "advisory_duration_ms": row["advisory_duration_ms"],
            "duration_range_ms": (
                [
                    row["duration_minimum_ms"],
                    row["duration_preferred_ms"],
                    row["duration_maximum_ms"],
                ]
                if row["duration_minimum_ms"] is not None
                else None
            ),
            "rank_ordinal": row["rank_ordinal"],
            "score_total": row["score_total"],
            "score_breakdown_hash": row["score_breakdown_hash"],
            "selection_reason_code": row["selection_reason_code"],
        }
        for row in sorted(
            (row for row in tables["recommendation_v2_recommendations"] if row["run_id"] == run_id),
            key=lambda item: item["portfolio_role"],
        )
    ]
    actual_recommendations.sort(key=lambda item: item["candidate_stable_id"])
    candidates = tuple(
        candidate_from_payload(json.loads(row["candidate_json"])) for row in candidate_rows
    )
    expected = evaluate_registered(
        run["policy_registry_version"], candidates, run["available_time_ms"]
    )
    actual_projection = {
        "candidates": actual_candidates,
        "decisions": actual_decisions,
        "reasons": actual_reasons,
        "recommendations": actual_recommendations,
    }
    expected_projection = {
        "candidates": [asdict(item) for item in expected.candidates],
        "decisions": [asdict(item) for item in expected.decisions],
        "reasons": [asdict(item) for item in expected.reasons],
        "recommendations": sorted(
            (asdict(item) for item in expected.recommendations),
            key=lambda item: item["candidate_stable_id"],
        ),
    }
    return (
        json.loads(canonical_json(actual_projection)),
        json.loads(canonical_json(expected_projection)),
    )


def _validate_recommendation_v2_checkpoint(
    payload: dict[str, Any], tables: dict[str, list[dict[str, Any]]], schema_version: int
) -> None:
    checkpoint = payload.get("recommendationV2HistoryCheckpoint")
    if schema_version < 7:
        if checkpoint is not None:
            raise AppError(
                422,
                "PORTABLE_SCHEMA_INVALID",
                "Portable schema versions before V7 cannot contain Recommendation V2 history.",
            )
        return
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != {"runHashes", "historyHash", "checkpointHash"}
        or not isinstance(checkpoint.get("runHashes"), list)
        or checkpoint["checkpointHash"]
        != content_hash(
            {key: value for key, value in checkpoint.items() if key != "checkpointHash"}
        )
    ):
        raise AppError(
            422,
            "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
            "The Recommendation V2 history checkpoint is invalid.",
        )
    history = {
        table_name: sorted(tables[table_name], key=canonical_json)
        for table_name in sorted(PORTABLE_V7_RECOMMENDATION_TABLES)
    }
    if content_hash(history) != checkpoint["historyHash"]:
        raise AppError(
            422,
            "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
            "Recommendation V2 immutable history failed hash validation.",
        )
    runs = {row["id"]: row for row in tables["recommendation_v2_runs"]}
    run_hashes = {row["runId"]: row for row in checkpoint["runHashes"]}
    if set(runs) != set(run_hashes):
        raise AppError(
            422,
            "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
            "Recommendation V2 run coverage is incomplete.",
        )
    analysis_snapshots = {row["id"]: row for row in tables["analysis_snapshots"]}
    for run in runs.values():
        replay_of = run.get("replay_of_run_id")
        if replay_of is None:
            continue
        original = runs.get(replay_of)
        if (
            original is None
            or replay_of == run["id"]
            or original["status"] != "completed"
            or run["analysis_snapshot_id"] != original["analysis_snapshot_id"]
            or run["available_time_ms"] != original["available_time_ms"]
            or run["policy_registry_version"] != original["policy_registry_version"]
            or run["policy_bundle_hash"] != original["policy_bundle_hash"]
            or (
                run["status"] == "completed"
                and (
                    run["input_hash"] != original["input_hash"]
                    or run["output_hash"] != original["output_hash"]
                )
            )
        ):
            raise AppError(
                422,
                "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                "Recommendation V2 replay lineage is invalid.",
            )
        seen = {run["id"]}
        cursor = original
        while cursor.get("replay_of_run_id") is not None:
            cursor_id = cursor["replay_of_run_id"]
            if cursor_id in seen or cursor_id not in runs:
                raise AppError(
                    422,
                    "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                    "Recommendation V2 replay lineage contains a cycle.",
                )
            seen.add(cursor_id)
            cursor = runs[cursor_id]
    for run_id, run in runs.items():
        from app.recommendation.v2.policy import registered_policy_bundle

        frozen = json.loads(run["frozen_input_json"])
        reference = run_hashes[run_id]
        try:
            registered_bundle = registered_policy_bundle(run["policy_registry_version"])
        except KeyError as exc:
            raise AppError(
                422,
                "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                "Recommendation V2 policy registry is unavailable.",
            ) from exc
        if (
            set(reference)
            != {"runId", "analysisSnapshotId", "inputHash", "outputHash", "policyBundleHash"}
            or run["analysis_snapshot_id"] not in analysis_snapshots
            or run["analysis_snapshot_id"] != reference["analysisSnapshotId"]
            or run["input_hash"] != content_hash(frozen)
            or run["input_hash"] != reference["inputHash"]
            or run["policy_bundle_hash"] != content_hash(json.loads(run["policy_bundle_json"]))
            or json.loads(run["policy_bundle_json"]) != registered_bundle
            or run["algorithm_version"] != registered_bundle["algorithm"]
            or run["application_version"] != registered_bundle["application"]
            or run["policy_bundle_hash"] != reference["policyBundleHash"]
            or run["output_hash"] != reference["outputHash"]
            or run["status"] not in {"completed", "failed"}
            or (
                run["status"] == "failed"
                and (
                    run["output_hash"] is not None
                    or run["failure_metadata_json"] is None
                    or any(
                        candidate["run_id"] == run_id
                        for candidate in tables["recommendation_v2_candidates"]
                    )
                    or any(
                        recommendation["run_id"] == run_id
                        for recommendation in tables["recommendation_v2_recommendations"]
                    )
                )
            )
            or (
                run["status"] == "completed"
                and (
                    run["output_hash"] != _recommendation_output_hash(tables, run_id)
                    or run["failure_metadata_json"] is not None
                )
            )
        ):
            raise AppError(
                422,
                "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                "Recommendation V2 history is disconnected or failed deterministic hash parity.",
            )
        snapshot = analysis_snapshots[run["analysis_snapshot_id"]]
        analysis_reference = frozen.get("analysisSnapshot", {})
        analysis_lineage = json.loads(snapshot["input_lineage_json"])
        frozen_projects = frozen.get("projects", {})
        complete_inputs = all(
            key in frozen
            for key in (
                "targetProfileVersion",
                "learningGraph",
                "curriculum",
                "projects",
            )
        )
        original = runs.get(run.get("replay_of_run_id"))
        if (
            analysis_reference.get("id") != snapshot["id"]
            or analysis_reference.get("inputHash") != snapshot["input_hash"]
            or analysis_reference.get("outputHash") != snapshot["output_hash"]
            or analysis_reference.get("cutoffAt") != snapshot["cutoff_at"]
            or run["cutoff_at"] != snapshot["cutoff_at"]
            or run["target_profile_version_id"] != snapshot["target_profile_version_id"]
            or run["learning_graph_version_id"] != snapshot["learning_graph_reference"]
            or run["curriculum_reference"] != snapshot["curriculum_reference"]
            or json.loads(run["semantic_definition_references_json"])
            != json.loads(snapshot["semantic_definition_references_json"])
            or json.loads(run["capability_scale_version_references_json"])
            != json.loads(snapshot["capability_scale_version_references_json"])
            or run["available_time_ms"] != frozen.get("availableTimeMs")
            or run["user_constraints_hash"] != content_hash(frozen.get("userConstraints", {}))
            or run["project_reference"]
            != (frozen_projects.get("input_hash") if isinstance(frozen_projects, dict) else None)
            or (
                original is not None
                and run["status"] == "failed"
                and (
                    (
                        not complete_inputs
                        and frozen.get("replayOfRunId") != run.get("replay_of_run_id")
                    )
                    or (complete_inputs and run["input_hash"] != original["input_hash"])
                )
            )
            or (
                run["status"] == "completed"
                and (
                    frozen.get("targetProfileVersion") != analysis_lineage.get("profile")
                    or frozen.get("learningGraph") != analysis_lineage.get("graph")
                    or frozen.get("curriculum") != analysis_lineage.get("curriculumCatalog")
                    or frozen.get("projects") != analysis_lineage.get("projectCatalog")
                )
            )
            or (
                run["status"] == "failed"
                and complete_inputs
                and (
                    frozen["targetProfileVersion"] != analysis_lineage.get("profile")
                    or frozen["learningGraph"] != analysis_lineage.get("graph")
                    or frozen["curriculum"] != analysis_lineage.get("curriculumCatalog")
                    or frozen["projects"] != analysis_lineage.get("projectCatalog")
                )
            )
        ):
            raise AppError(
                422,
                "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                "Recommendation V2 frozen lineage is inconsistent.",
            )
        if run["status"] == "completed":
            from app.recommendation.v2.input_replay import (
                regenerate_candidates_from_frozen_input,
            )

            generated_candidates = regenerate_candidates_from_frozen_input(
                frozen, run["policy_registry_version"]
            )
            persisted_candidate_payloads = [
                json.loads(row["candidate_json"])
                for row in sorted(
                    (
                        row
                        for row in tables["recommendation_v2_candidates"]
                        if row["run_id"] == run_id
                    ),
                    key=lambda row: row["ordinal"],
                )
            ]
            if frozen["candidates"] != persisted_candidate_payloads or frozen[
                "candidates"
            ] != json.loads(canonical_json([asdict(item) for item in generated_candidates])):
                raise AppError(
                    422,
                    "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                    "Recommendation V2 candidate generation does not replay exactly.",
                )
            try:
                actual_audit, expected_audit = _recommendation_audit_projection(tables, run_id)
            except (KeyError, TypeError, ValueError) as exc:
                raise AppError(
                    422,
                    "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                    "Recommendation V2 audit cannot be replayed.",
                ) from exc
            if actual_audit != expected_audit:
                raise AppError(
                    422,
                    "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                    "Recommendation V2 persisted audit differs from deterministic replay.",
                )
            run_candidates = {
                row["id"]: row
                for row in tables["recommendation_v2_candidates"]
                if row["run_id"] == run_id
            }
            expected_candidates = {
                item["candidate"]["stable_id"]: item for item in expected_audit["candidates"]
            }
            expected_values = {
                row["candidate_id"]: row
                for row in tables["recommendation_v2_expected_values"]
                if row["candidate_id"] in run_candidates
            }
            policy_mismatch = any(
                row["policy_version"] != registered_bundle["eligibility"]
                for table_name in (
                    "recommendation_v2_eligibility_decisions",
                    "recommendation_v2_eligibility_rule_results",
                )
                for row in tables[table_name]
                if row["candidate_id"] in run_candidates
            ) or any(
                row["policy_version"] != registered_bundle[policy_key]
                for table_name, policy_key in (
                    ("recommendation_v2_expected_values", "expectedLearningValue"),
                    ("recommendation_v2_score_components", "score"),
                    ("recommendation_v2_selection_decisions", "portfolio"),
                    ("recommendation_v2_reasons", "reason"),
                )
                for row in tables[table_name]
                if row["candidate_id"] in run_candidates
            )
            value_mismatch = any(
                candidate_id not in expected_values
                or json.loads(expected_values[candidate_id]["matched_facts_json"])
                != expected_candidates[candidate["stable_id"]]["expected_learning_value_facts"]
                or expected_values[candidate_id]["deciding_rule_code"]
                != (
                    expected_candidates[candidate["stable_id"]]["expected_learning_value_reasons"][
                        0
                    ]
                    if expected_candidates[candidate["stable_id"]][
                        "expected_learning_value_reasons"
                    ]
                    else "DECISIVE_LEARNING_VALUE_FACTS_MISSING"
                )
                for candidate_id, candidate in run_candidates.items()
            )
            selected_mismatch = any(
                row["algorithm_version"] != registered_bundle["algorithm"]
                or row["analysis_snapshot_id"] != run["analysis_snapshot_id"]
                or row["presentation_version"] != "recommendation-presentation/v1"
                for row in tables["recommendation_v2_recommendations"]
                if row["run_id"] == run_id
            )
            if policy_mismatch or value_mismatch or selected_mismatch:
                raise AppError(
                    422,
                    "PORTABLE_RECOMMENDATION_CHECKPOINT_INVALID",
                    "Recommendation V2 persisted policy or explanation lineage is inconsistent.",
                )


def _validate_today_v2_checkpoint(
    payload: dict[str, Any], tables: dict[str, list[dict[str, Any]]], schema_version: int
) -> None:
    checkpoint = payload.get("todayV2CurrentCheckpoint")
    if schema_version < 8:
        if checkpoint is not None:
            raise AppError(
                422,
                "PORTABLE_SCHEMA_INVALID",
                "Portable schema versions before V8 cannot contain Today V2 state.",
            )
        return
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != {"states", "checkpointHash"}
        or not isinstance(checkpoint.get("states"), list)
        or checkpoint["checkpointHash"] != content_hash(checkpoint["states"])
    ):
        raise AppError(
            422,
            "PORTABLE_TODAY_CHECKPOINT_INVALID",
            "The Today V2 current-state checkpoint is invalid.",
        )
    interactions_by_suggestion: dict[str, list[dict[str, Any]]] = {}
    for interaction in tables["today_interactions"]:
        interactions_by_suggestion.setdefault(interaction["suggestion_id"], []).append(
            interaction
        )
    corrections_by_interaction = {
        correction["interaction_id"]: correction
        for correction in tables["today_interaction_corrections"]
    }
    expected: list[dict[str, Any]] = []
    terminal = {"completed", "partially_completed", "skipped", "replaced", "expired"}
    for suggestion in sorted(tables["today_suggestions"], key=lambda item: item["id"]):
        interactions = sorted(
            interactions_by_suggestion.get(suggestion["id"], []),
            key=lambda item: item["event_sequence"],
        )
        if interactions:
            latest = interactions[-1]
            correction = corrections_by_interaction.get(latest["id"])
            status = (
                correction["resulting_status"]
                if correction is not None
                else latest["resulting_status"]
            )
            updated_at = (
                correction["corrected_at"]
                if correction is not None
                else latest["occurred_at"]
            )
            expected.append(
                {
                    "suggestionId": suggestion["id"],
                    "status": status,
                    "eventSequence": latest["event_sequence"],
                    "latestInteractionId": latest["id"],
                    "terminal": status in terminal,
                    "updatedAt": updated_at,
                }
            )
        else:
            expected.append(
                {
                    "suggestionId": suggestion["id"],
                    "status": "suggested",
                    "eventSequence": 0,
                    "latestInteractionId": None,
                    "terminal": False,
                    "updatedAt": suggestion["created_at"],
                }
            )
    if checkpoint["states"] != expected:
        raise AppError(
            422,
            "PORTABLE_TODAY_CHECKPOINT_INVALID",
            "Today V2 current-state parity failed.",
        )


def _validate_curriculum_checkpoint(payload: dict[str, Any], schema_version: int) -> None:
    checkpoint = payload.get("curriculumCatalogCheckpoint")
    if schema_version < 3:
        if checkpoint is not None:
            raise AppError(
                422,
                "PORTABLE_SCHEMA_INVALID",
                "Portable schema versions before V3 cannot contain a Curriculum checkpoint.",
            )
        return
    expected_keys = {
        "cutoffAt",
        "cutoffSemantics",
        "policyVersion",
        "sourceHash",
        "catalogHash",
        "activeVersionReferences",
        "availabilityHashes",
        "inputHash",
    }
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != expected_keys
        or type(checkpoint["cutoffAt"]) is not int
        or checkpoint["cutoffAt"] <= 0
        or checkpoint["cutoffSemantics"] != "exclusive"
        or checkpoint["policyVersion"] != CURRICULUM_AVAILABILITY_POLICY
        or not isinstance(checkpoint["inputHash"], str)
        or len(checkpoint["inputHash"]) != 64
        or not isinstance(checkpoint["sourceHash"], str)
        or len(checkpoint["sourceHash"]) != 64
        or not isinstance(checkpoint["catalogHash"], str)
        or len(checkpoint["catalogHash"]) != 64
        or not isinstance(checkpoint["activeVersionReferences"], list)
        or not isinstance(checkpoint["availabilityHashes"], list)
        or checkpoint["sourceHash"] != content_hash(checkpoint["activeVersionReferences"])
        or checkpoint["inputHash"]
        != content_hash({key: value for key, value in checkpoint.items() if key != "inputHash"})
    ):
        raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Curriculum checkpoint is invalid.")
    availability_keys: set[str] = set()
    for item in checkpoint["availabilityHashes"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"unitDefinitionId", "inputHash"}
            or not isinstance(item["unitDefinitionId"], str)
            or item["unitDefinitionId"] in availability_keys
            or not isinstance(item["inputHash"], str)
            or len(item["inputHash"]) != 64
        ):
            raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Curriculum checkpoint is invalid.")
        availability_keys.add(item["unitDefinitionId"])


def _assert_curriculum_checkpoint_parity(db: Session, checkpoint: dict[str, Any] | None) -> None:
    if checkpoint is None:
        return
    restored_catalog = build_catalog(db, checkpoint["cutoffAt"])
    restored_references = serialize_catalog(restored_catalog)["activeVersionReferences"]
    restored_availability: list[dict[str, str]] = []
    for item in restored_catalog.units:
        unit = db.get(LearningUnitDefinition, item.unit_definition_id)
        if unit is None:
            raise AppError(
                422,
                "RESTORE_CURRICULUM_PARITY_FAILED",
                "The restored Curriculum catalog references a missing unit.",
            )
        restored_availability.append(
            {
                "unitDefinitionId": unit.id,
                "inputHash": build_unit_availability(db, unit, checkpoint["cutoffAt"]).input_hash,
            }
        )
    if (
        restored_catalog.input_hash != checkpoint["catalogHash"]
        or restored_references != checkpoint["activeVersionReferences"]
        or content_hash(restored_references) != checkpoint["sourceHash"]
        or restored_availability != checkpoint["availabilityHashes"]
    ):
        raise AppError(
            422,
            "RESTORE_CURRICULUM_PARITY_FAILED",
            "The rebuilt Curriculum catalog does not match the exported checkpoint.",
        )


def _validate_project_checkpoint(payload: dict[str, Any], schema_version: int) -> None:
    checkpoint = payload.get("projectCatalogCheckpoint")
    if schema_version < 4:
        if checkpoint is not None:
            raise AppError(
                422,
                "PORTABLE_SCHEMA_INVALID",
                "Portable schema versions before V4 cannot contain a Project checkpoint.",
            )
        return
    expected = {
        "cutoffAt",
        "cutoffSemantics",
        "policyVersion",
        "sourceHash",
        "catalogHash",
        "activeVersionReferences",
        "candidateHashes",
        "inputHash",
    }
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != expected
        or type(checkpoint["cutoffAt"]) is not int
        or checkpoint["cutoffAt"] <= 0
        or checkpoint["cutoffSemantics"] != "exclusive"
        or checkpoint["policyVersion"] != PROJECT_AVAILABILITY_POLICY
        or any(
            not isinstance(checkpoint[key], str) or len(checkpoint[key]) != 64
            for key in ("sourceHash", "catalogHash", "inputHash")
        )
        or not isinstance(checkpoint["activeVersionReferences"], list)
        or not isinstance(checkpoint["candidateHashes"], list)
        or checkpoint["sourceHash"] != content_hash(checkpoint["activeVersionReferences"])
        or checkpoint["inputHash"]
        != content_hash({key: value for key, value in checkpoint.items() if key != "inputHash"})
    ):
        raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Project checkpoint is invalid.")
    seen: set[str] = set()
    for item in checkpoint["candidateHashes"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"taskDefinitionId", "inputHash"}
            or not isinstance(item["taskDefinitionId"], str)
            or item["taskDefinitionId"] in seen
            or not isinstance(item["inputHash"], str)
            or len(item["inputHash"]) != 64
        ):
            raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Project checkpoint is invalid.")
        seen.add(item["taskDefinitionId"])


def _assert_project_checkpoint_parity(db: Session, checkpoint: dict[str, Any] | None) -> None:
    if checkpoint is None:
        return
    restored = build_project_catalog(db, checkpoint["cutoffAt"])
    references = [asdict(item) for item in restored.active_version_references]
    candidate_hashes = [
        {"taskDefinitionId": item.task_definition_id, "inputHash": item.input_hash}
        for item in restored.candidates
    ]
    if (
        restored.input_hash != checkpoint["catalogHash"]
        or references != checkpoint["activeVersionReferences"]
        or content_hash(references) != checkpoint["sourceHash"]
        or candidate_hashes != checkpoint["candidateHashes"]
    ):
        raise AppError(
            422,
            "RESTORE_PROJECT_PARITY_FAILED",
            "The rebuilt Project catalog does not match the exported checkpoint.",
        )


def _resolve_export_scope(db: Session, request: ExportRequest) -> ResolvedExportScope:
    profile = get_or_create_profile(db)
    today = local_date_for_ms(utc_now_ms(), profile.timezone)
    if request.range == "custom":
        start_date = date.fromisoformat(request.start_date or "")
        end_date = date.fromisoformat(request.end_date or "")
    elif request.range == "all":
        start_date = date(1, 1, 1)
        end_date = today
    else:
        days = {"7d": 7, "30d": 30, "90d": 90}[request.range]
        start_date = today - timedelta(days=days - 1)
        end_date = today
    if start_date > end_date:
        raise AppError(422, "EXPORT_RANGE_INVALID", "The export date range is invalid.")

    current = current_legacy_roadmap(db)
    active_version_id = current.active_version_id if current else None
    current_tracks = (
        db.scalars(select(Track).where(Track.roadmap_version_id == active_version_id)).all()
        if active_version_id
        else []
    )
    current_definitions = (
        db.scalars(
            select(CompetencyDefinition).where(
                CompetencyDefinition.roadmap_version_id == active_version_id
            )
        ).all()
        if active_version_id
        else []
    )
    known_track_ids = {item.id for item in current_tracks}
    known_identity_ids = {item.competency_identity_id for item in current_definitions}
    if set(request.track_ids) - known_track_ids:
        raise AppError(
            422,
            "EXPORT_SCOPE_INVALID",
            "A selected track does not belong to the active roadmap version.",
        )
    if set(request.competency_identity_ids) - known_identity_ids:
        raise AppError(
            422,
            "EXPORT_SCOPE_INVALID",
            "A selected competency does not belong to the active roadmap version.",
        )

    selected_identity_ids: set[str] | None = (
        set(request.competency_identity_ids) if request.competency_identity_ids else None
    )
    if request.track_ids:
        track_identity_ids = {
            item.competency_identity_id
            for item in current_definitions
            if item.track_id in request.track_ids
        }
        selected_identity_ids = (
            track_identity_ids
            if selected_identity_ids is None
            else selected_identity_ids & track_identity_ids
        )
    if request.current_phase_only:
        current_phase_identity_ids: set[str] = set()
        if current and current.current_phase_id:
            current_phase_identity_ids = {
                item.competency_identity_id
                for item in current_definitions
                if item.phase_id == current.current_phase_id
            }
        selected_identity_ids = (
            current_phase_identity_ids
            if selected_identity_ids is None
            else selected_identity_ids & current_phase_identity_ids
        )
    identities = {
        item.id: item
        for item in db.scalars(
            select(CompetencyIdentity).where(
                CompetencyIdentity.id.in_(request.competency_identity_ids)
            )
        ).all()
    }
    definitions_by_identity = {item.competency_identity_id: item for item in current_definitions}
    resolved_categories = list(request.categories) or [
        "roadmap",
        "analytics",
        "sessions",
        "verification",
        "reports",
        "settings",
    ]
    scope_payload = request.model_dump(mode="json")
    scope_payload.update(
        {
            "resolved_start_date": start_date.isoformat(),
            "resolved_end_date": end_date.isoformat(),
            "timezone": profile.timezone,
            "resolved_categories": resolved_categories,
            "selected_tracks": [
                {"id": item.id, "stable_key": item.stable_key, "title": item.title}
                for item in current_tracks
                if item.id in request.track_ids
            ],
            "selected_competencies": [
                {
                    "identity_id": identity_id,
                    "stable_key": identities[identity_id].stable_key,
                    "title": definitions_by_identity[identity_id].title,
                }
                for identity_id in request.competency_identity_ids
            ],
        }
    )
    return ResolvedExportScope(
        start_date=start_date,
        end_date=end_date,
        timezone=profile.timezone,
        selected_identity_ids=selected_identity_ids,
        scope_payload=scope_payload,
    )


def _roadmap_analysis_payload(
    db: Session, request: ExportRequest, selected_identity_ids: set[str] | None
) -> dict[str, Any] | None:
    from app.roadmap import serialize_current_roadmap

    current = current_legacy_roadmap(db)
    if current is None:
        return None
    serialized = serialize_current_roadmap(db, current)
    filtered_phases: list[dict[str, Any]] = []
    for phase in serialized["phases"]:
        if request.current_phase_only and phase["id"] != serialized["currentPhaseId"]:
            continue
        filtered_tracks: list[dict[str, Any]] = []
        for track in phase["tracks"]:
            if request.track_ids and track["id"] not in request.track_ids:
                continue
            competencies = [
                item
                for item in track["competencies"]
                if selected_identity_ids is None or item["identityId"] in selected_identity_ids
            ]
            if competencies or not request.competency_identity_ids:
                filtered_tracks.append({**track, "competencies": competencies})
        if filtered_tracks:
            filtered_phases.append({**phase, "tracks": filtered_tracks})
    return {**serialized, "phases": filtered_phases}


def _analysis_payload(db: Session, request: ExportRequest) -> dict[str, Any]:
    resolved = _resolve_export_scope(db, request)
    analytics = build_analytics(
        db,
        range_name=request.range,
        start_date=resolved.start_date,
        end_date=resolved.end_date,
        competency_identity_ids=resolved.selected_identity_ids,
        track_ids=set(request.track_ids) if request.track_ids else None,
    )
    start_ms, _ = local_day_bounds_ms(resolved.start_date, resolved.timezone)
    _, end_ms = local_day_bounds_ms(resolved.end_date, resolved.timezone)
    sessions_query = select(LearningSession).order_by(LearningSession.started_at)
    sessions_query = sessions_query.where(
        LearningSession.started_at >= start_ms,
        LearningSession.started_at < end_ms,
    )
    if resolved.selected_identity_ids is not None:
        sessions_query = sessions_query.where(
            LearningSession.competency_identity_id.in_(resolved.selected_identity_ids)
        )
    if request.track_ids:
        sessions_query = sessions_query.where(LearningSession.track_id.in_(request.track_ids))
    verifications_query = select(VerificationRecord).order_by(VerificationRecord.created_at)
    if resolved.selected_identity_ids is not None:
        verifications_query = verifications_query.where(
            VerificationRecord.competency_identity_id.in_(resolved.selected_identity_ids)
        )
    verifications_query = verifications_query.where(
        VerificationRecord.created_at >= start_ms,
        VerificationRecord.created_at < end_ms,
    )
    categories = set(request.categories)
    include_all = not categories
    payload: dict[str, Any] = {"scope": resolved.scope_payload}
    if include_all or "roadmap" in categories:
        payload["roadmap"] = _roadmap_analysis_payload(db, request, resolved.selected_identity_ids)
    if include_all or "analytics" in categories:
        payload["analytics"] = analytics
    if include_all or "sessions" in categories:
        payload["sessions"] = [_row_dict(item) for item in db.scalars(sessions_query).all()]
    if include_all or "verification" in categories:
        payload["verification"] = [
            _row_dict(item) for item in db.scalars(verifications_query).all()
        ]
    if include_all or "reports" in categories:
        payload["reports"] = [
            _row_dict(item)
            for item in db.scalars(
                select(GeneratedReport).where(
                    GeneratedReport.period_end >= resolved.start_date.isoformat(),
                    GeneratedReport.period_start <= resolved.end_date.isoformat(),
                )
            ).all()
        ]
    if include_all or "settings" in categories:
        safe_profile = db.get(DisciplineProfile, 1)
        payload["safeSettings"] = _row_dict(safe_profile) if safe_profile else None
    return payload


def _human_report(payload: dict[str, Any], created_at: str) -> str:
    scope = payload["scope"]
    analytics = payload.get("analytics", {})
    included_categories = ", ".join(scope["resolved_categories"])
    selected_tracks = (
        ", ".join(f"{item['title']} ({item['stable_key']})" for item in scope["selected_tracks"])
        or "All tracks"
    )
    selected_competencies = (
        ", ".join(
            f"{item['title']} ({item['stable_key']})" for item in scope["selected_competencies"]
        )
        or "All competencies"
    )
    return "\n".join(
        [
            "# Learning-Control-Center export",
            "",
            f"Exported: {created_at}",
            f"Range preset: {scope['range']}",
            (
                f"Resolved period: {scope['resolved_start_date']} to "
                f"{scope['resolved_end_date']} ({scope['timezone']})"
            ),
            f"Included categories: {included_categories}",
            f"Current phase only: {'yes' if scope['current_phase_only'] else 'no'}",
            f"Tracks: {selected_tracks}",
            f"Competencies: {selected_competencies}",
            "",
            "## Summary",
            "",
            f"- Total learning duration: {analytics.get('totalDurationMs', 'N/A')} ms",
            f"- Active days: {analytics.get('activeDays', 'N/A')}",
            f"- Reviews due: {analytics.get('reviewDebt', {}).get('count', 'N/A')}",
            "",
        ]
    )


def _envelope(
    package_type: str, payload: dict[str, Any], *, schema_version: int = 1
) -> dict[str, Any]:
    now = utc_now_ms()
    return {
        "schemaVersion": schema_version,
        "packageType": package_type,
        "packageId": str(uuid.uuid4()),
        "appVersion": "1.0.0",
        "createdAt": epoch_ms_to_rfc3339(now),
        "payload": payload,
    }


def create_operational_backup(
    db: Session, settings: Settings, purpose: str, source_engine: Engine | None = None
) -> OperationalBackup:
    settings.backup_directory.mkdir(parents=True, exist_ok=True)
    settings.backup_directory.chmod(0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = settings.backup_directory / f"lcc-{purpose}-{timestamp}.sqlite3"
    bound = source_engine or db.get_bind()
    backup_engine = bound if isinstance(bound, Engine) else bound.engine
    raw = backup_engine.raw_connection()
    try:
        source_connection = raw.driver_connection
        if source_connection is None:
            raise RuntimeError("The SQLite driver connection is unavailable.")
        target = sqlite3.connect(destination)
        try:
            source_connection.backup(target)
        finally:
            target.close()
    finally:
        raw.close()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    os.chmod(destination, 0o600)
    record = OperationalBackup(
        purpose=purpose,
        path=str(destination),
        checksum_sha256=digest,
        size_bytes=destination.stat().st_size,
    )
    db.add(record)
    db.flush()
    return record


def _insert_portable_tables(connection: Any, tables: dict[str, list[dict[str, Any]]]) -> None:
    roadmap_pointers: list[tuple[str, str | None, str | None, bool]] = []
    parent_pointers: list[tuple[str, str | None]] = []
    curriculum_version_pointers: list[tuple[str, str | None]] = []
    project_version_pointers: list[tuple[str, str | None]] = []
    learning_graph_version_pointers: list[tuple[str, str | None]] = []
    project_event_correction_pointers: list[tuple[str, str | None]] = []
    activity_supersession_pointers: list[tuple[str, str | None]] = []
    evidence_supersession_pointers: list[tuple[str, str | None]] = []
    evidence_replacement_pointers: list[tuple[str, str | None]] = []
    evidence_link_replacement_pointers: list[tuple[str, str | None]] = []
    recommendation_replay_pointers: list[tuple[str, str | None]] = []
    today_suggestion_replacement_pointers: list[tuple[str, str | None]] = []
    for model in PORTABLE_MODELS:
        table_name = _table(model).name
        rows = [dict(row) for row in tables.get(table_name, [])]
        if model is Roadmap:
            for row in rows:
                roadmap_pointers.append(
                    (
                        row["id"],
                        row.get("active_version_id"),
                        row.get("current_phase_id"),
                        row.get("is_current", False),
                    )
                )
                row["active_version_id"] = None
                row["current_phase_id"] = None
                row["is_current"] = False
        if model is CompetencyDefinition:
            for row in rows:
                parent_pointers.append((row["id"], row.get("parent_definition_id")))
                row["parent_definition_id"] = None
        if model is CurriculumVersion:
            for row in rows:
                curriculum_version_pointers.append((row["id"], row.get("supersedes_version_id")))
                row["supersedes_version_id"] = None
        if model is ProjectVersion:
            for row in rows:
                project_version_pointers.append((row["id"], row.get("supersedes_version_id")))
                row["supersedes_version_id"] = None
        if model is LearningGraphVersion:
            for row in rows:
                learning_graph_version_pointers.append(
                    (row["id"], row.get("supersedes_version_id"))
                )
                row["supersedes_version_id"] = None
        if model is ProjectEvent:
            for row in rows:
                project_event_correction_pointers.append((row["id"], row.get("corrects_event_id")))
                row["corrects_event_id"] = None
        if model is Activity:
            for row in rows:
                activity_supersession_pointers.append(
                    (row["id"], row.get("supersedes_activity_id"))
                )
                row["supersedes_activity_id"] = None
        if model is Evidence:
            for row in rows:
                evidence_supersession_pointers.append(
                    (row["id"], row.get("supersedes_evidence_id"))
                )
                row["supersedes_evidence_id"] = None
        if model is EvidenceRetraction:
            for row in rows:
                evidence_replacement_pointers.append(
                    (row["id"], row.get("replacement_evidence_id"))
                )
                row["replacement_evidence_id"] = None
        if model is EvidenceLinkRetraction:
            for row in rows:
                evidence_link_replacement_pointers.append(
                    (row["id"], row.get("replacement_link_id"))
                )
                row["replacement_link_id"] = None
        if model is RecommendationV2Run:
            for row in rows:
                recommendation_replay_pointers.append((row["id"], row.get("replay_of_run_id")))
                row["replay_of_run_id"] = None
        if model is TodaySuggestion:
            for row in rows:
                today_suggestion_replacement_pointers.append(
                    (row["id"], row.get("replaces_suggestion_id"))
                )
                row["replaces_suggestion_id"] = None
        if rows:
            connection.execute(insert(_table(model)), rows)
    for definition_id, parent_id in parent_pointers:
        if parent_id:
            connection.execute(
                _table(CompetencyDefinition)
                .update()
                .where(CompetencyDefinition.id == definition_id)
                .values(parent_definition_id=parent_id)
            )
    for curriculum_version_id, supersedes_id in curriculum_version_pointers:
        if supersedes_id:
            connection.execute(
                _table(CurriculumVersion)
                .update()
                .where(CurriculumVersion.id == curriculum_version_id)
                .values(supersedes_version_id=supersedes_id)
            )
    for project_version_id, supersedes_id in project_version_pointers:
        if supersedes_id:
            connection.execute(
                _table(ProjectVersion)
                .update()
                .where(ProjectVersion.id == project_version_id)
                .values(supersedes_version_id=supersedes_id)
            )
    for graph_version_id, supersedes_id in learning_graph_version_pointers:
        if supersedes_id:
            connection.execute(
                _table(LearningGraphVersion)
                .update()
                .where(LearningGraphVersion.id == graph_version_id)
                .values(supersedes_version_id=supersedes_id)
            )
    for project_event_id, corrects_id in project_event_correction_pointers:
        if corrects_id:
            connection.execute(
                _table(ProjectEvent)
                .update()
                .where(ProjectEvent.id == project_event_id)
                .values(corrects_event_id=corrects_id)
            )
    for activity_id, supersedes_id in activity_supersession_pointers:
        if supersedes_id:
            connection.execute(
                _table(Activity)
                .update()
                .where(Activity.id == activity_id)
                .values(supersedes_activity_id=supersedes_id)
            )
    for evidence_id, supersedes_id in evidence_supersession_pointers:
        if supersedes_id:
            connection.execute(
                _table(Evidence)
                .update()
                .where(Evidence.id == evidence_id)
                .values(supersedes_evidence_id=supersedes_id)
            )
    for retraction_id, replacement_id in evidence_replacement_pointers:
        if replacement_id:
            connection.execute(
                _table(EvidenceRetraction)
                .update()
                .where(EvidenceRetraction.id == retraction_id)
                .values(replacement_evidence_id=replacement_id)
            )
    for retraction_id, replacement_id in evidence_link_replacement_pointers:
        if replacement_id:
            connection.execute(
                _table(EvidenceLinkRetraction)
                .update()
                .where(EvidenceLinkRetraction.id == retraction_id)
                .values(replacement_link_id=replacement_id)
            )
    for run_id, replay_of_run_id in recommendation_replay_pointers:
        if replay_of_run_id:
            connection.execute(
                _table(RecommendationV2Run)
                .update()
                .where(RecommendationV2Run.id == run_id)
                .values(replay_of_run_id=replay_of_run_id)
            )
    for suggestion_id, replaces_suggestion_id in today_suggestion_replacement_pointers:
        if replaces_suggestion_id:
            connection.execute(
                _table(TodaySuggestion)
                .update()
                .where(TodaySuggestion.id == suggestion_id)
                .values(replaces_suggestion_id=replaces_suggestion_id)
            )
    for roadmap_id, roadmap_version_id, phase_id, is_current in roadmap_pointers:
        connection.execute(
            _table(Roadmap)
            .update()
            .where(Roadmap.id == roadmap_id)
            .values(
                active_version_id=roadmap_version_id,
                current_phase_id=phase_id,
                is_current=is_current,
            )
        )


def _clear_migration_seeded_portable_state(connection: Any) -> None:
    """Remove built-ins seeded by migrations before logical backup insertion."""
    connection.execute(_table(MigrationBackfillRun).delete())
    connection.execute(_table(DisciplineConfigurationEvent).delete())
    connection.execute(_table(ActivityCategoryVersion).delete())
    connection.execute(_table(CapabilityScaleLevel).delete())
    connection.execute(_table(CapabilityScaleDimension).delete())
    connection.execute(_table(CapabilityScaleVersion).delete())


def _legacy_scope_baseline(
    tables: dict[str, list[dict[str, Any]]], package_id: str
) -> list[dict[str, Any]]:
    current_roadmaps = [row for row in tables["roadmaps"] if row.get("is_current") is True]
    if len(current_roadmaps) != 1:
        return []
    roadmap = current_roadmaps[0]
    roadmap_id = roadmap.get("id")
    version_id = roadmap.get("active_version_id")
    phase_id = roadmap.get("current_phase_id")
    occurred_at = roadmap.get("updated_at")
    if not all(isinstance(value, str) for value in (roadmap_id, version_id, phase_id)):
        return []
    if type(occurred_at) is not int:
        return []
    event_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"lcc:scope-baseline:{package_id}:{roadmap_id}:{version_id}:{phase_id}",
        )
    )
    return [
        {
            "id": event_id,
            "roadmap_id": roadmap_id,
            "roadmap_version_id": version_id,
            "phase_id": phase_id,
            "source": "restore_baseline",
            "reason": "Current scope baseline from legacy portable backup",
            "occurred_at": occurred_at,
            "event_sequence": 1,
        }
    ]


def _normalize_portable_tables(
    payload: dict[str, Any], package_id: str, schema_version: int = 1
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    try:
        parsed = PortablePackagePayload.model_validate(payload)
    except ValidationError as exc:
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "The portable backup payload is invalid."
        ) from exc
    if parsed.portableScope is not None:
        scope = parsed.portableScope
        expected_scope_keys = {
            "requestedProjectIds",
            "includedProjectIds",
            "closureAddedProjectIds",
        }
        if (
            schema_version < 4
            or set(scope) != expected_scope_keys
            or any(not isinstance(scope[key], list) for key in expected_scope_keys)
            or any(
                not all(isinstance(item, str) and item for item in scope[key])
                for key in expected_scope_keys
            )
        ):
            raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Portable scope is invalid.")
        requested = set(scope["requestedProjectIds"])
        included = set(scope["includedProjectIds"])
        closure = set(scope["closureAddedProjectIds"])
        actual = {row.get("id") for row in parsed.tables.get("projects", [])}
        if (
            any(len(scope[key]) != len(set(scope[key])) for key in expected_scope_keys)
            or not requested <= included
            or closure != included - requested
            or included != actual
        ):
            raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Portable scope is inconsistent.")
    if schema_version == 2 and parsed.manifest != PORTABLE_V2_MANIFEST:
        raise AppError(
            422,
            "PORTABLE_MANIFEST_INVALID",
            (
                "The portable V2 manifest is missing or does not match the supported "
                "recovery contract."
            ),
        )
    if schema_version == 3 and parsed.manifest != PORTABLE_V3_MANIFEST:
        raise AppError(
            422,
            "PORTABLE_MANIFEST_INVALID",
            "The portable V3 manifest is missing or does not match the recovery contract.",
        )
    if schema_version == 4 and parsed.manifest != PORTABLE_V4_MANIFEST:
        raise AppError(
            422,
            "PORTABLE_MANIFEST_INVALID",
            "The portable V4 manifest is missing or does not match the recovery contract.",
        )
    if schema_version == 5 and parsed.manifest != PORTABLE_V5_MANIFEST:
        raise AppError(
            422,
            "PORTABLE_MANIFEST_INVALID",
            "The portable V5 manifest is missing or does not match the recovery contract.",
        )
    if schema_version == 6 and parsed.manifest != PORTABLE_V6_MANIFEST:
        raise AppError(
            422,
            "PORTABLE_MANIFEST_INVALID",
            "The portable V6 manifest is missing or does not match the recovery contract.",
        )
    if schema_version == 7 and parsed.manifest != PORTABLE_V7_MANIFEST:
        raise AppError(
            422,
            "PORTABLE_MANIFEST_INVALID",
            "The portable V7 manifest is missing or does not match the recovery contract.",
        )
    if schema_version == 8 and parsed.manifest != PORTABLE_V8_MANIFEST:
        raise AppError(
            422,
            "PORTABLE_MANIFEST_INVALID",
            "The portable V8 manifest is missing or does not match the recovery contract.",
        )
    if schema_version == 1 and parsed.manifest is not None:
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "A V1 portable package cannot contain a V2 manifest."
        )
    tables = {table_name: [dict(row) for row in rows] for table_name, rows in parsed.tables.items()}
    if schema_version == 1 and set(tables) & set(PORTABLE_V1_FORBIDDEN_TABLES):
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "A V1 portable package cannot contain V2 tables."
        )
    if schema_version == 2 and set(tables) & set(PORTABLE_V2_FORBIDDEN_TABLES):
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "A V2 portable package cannot contain V3 tables."
        )
    if schema_version == 3 and set(tables) & set(PORTABLE_V3_FORBIDDEN_TABLES):
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "A V3 portable package cannot contain V4 tables."
        )
    if schema_version == 4 and set(tables) & set(PORTABLE_V4_FORBIDDEN_TABLES):
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "A V4 portable package cannot contain V5 tables."
        )
    if schema_version == 5 and set(tables) & set(PORTABLE_V5_FORBIDDEN_TABLES):
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "A V5 portable package cannot contain V6 tables."
        )
    if schema_version == 6 and set(tables) & set(PORTABLE_V6_FORBIDDEN_TABLES):
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "A V6 portable package cannot contain V7 tables."
        )
    if schema_version == 7 and set(tables) & set(PORTABLE_V7_FORBIDDEN_TABLES):
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "A V7 portable package cannot contain V8 tables."
        )
    unknown = set(tables) - set(PORTABLE_BY_TABLE)
    v2_tables = set(PORTABLE_V2_FOUNDATION_TABLES)
    v3_tables = set(PORTABLE_V3_CURRICULUM_TABLES)
    v4_tables = set(PORTABLE_V4_PROJECT_TABLES)
    v5_tables = set(PORTABLE_V5_GRAPH_PROJECTION_TABLES)
    v6_tables = set(PORTABLE_V6_ANALYSIS_TABLES)
    v7_tables = set(PORTABLE_V7_RECOMMENDATION_TABLES)
    v8_tables = set(PORTABLE_V8_TODAY_TABLES)
    missing = set(PORTABLE_BY_TABLE) - set(tables)
    allowed_v1_missing = (
        v2_tables
        | v3_tables
        | v4_tables
        | v5_tables
        | v6_tables
        | v7_tables
        | v8_tables
        | {"roadmap_scope_events"}
    )
    legacy_without_scope_history = schema_version == 1 and "roadmap_scope_events" in missing
    valid_missing = (
        (schema_version == 1 and missing <= allowed_v1_missing)
        or (
            schema_version == 2
            and missing
            <= (v3_tables | v4_tables | v5_tables | v6_tables | v7_tables | v8_tables)
        )
        or (
            schema_version == 3
            and missing <= (v4_tables | v5_tables | v6_tables | v7_tables | v8_tables)
        )
        or (schema_version == 4 and missing <= (v5_tables | v6_tables | v7_tables | v8_tables))
        or (schema_version == 5 and missing <= (v6_tables | v7_tables | v8_tables))
        or (schema_version == 6 and missing <= (v7_tables | v8_tables))
        or (schema_version == 7 and missing <= v8_tables)
        or not missing
    )
    if unknown or not valid_missing:
        raise AppError(
            422,
            "PORTABLE_SCHEMA_INVALID",
            "Portable backup table coverage is invalid.",
            {"unknownTables": sorted(unknown), "missingTables": sorted(missing)},
        )
    if legacy_without_scope_history:
        tables["roadmap_scope_events"] = _legacy_scope_baseline(tables, package_id)
    if schema_version == 1:
        upgrade_v1_profile_competency_tables(tables)
        upgrade_v1_activity_session_tables(tables)
        upgrade_v1_evidence_tables(tables, import_package_id=package_id)
        for table_name in {"analysis_runs", "analysis_snapshots"}:
            tables[table_name] = []
        for row in tables.get("recommendation_snapshots", []):
            row.setdefault("analysis_snapshot_id", None)
        upgrade_v2_to_v3_tables(tables)
        upgrade_v3_to_v4_tables(tables)
        upgrade_v4_to_v5_tables(tables)
        upgrade_v5_to_v6_tables(tables)
        upgrade_v6_to_v7_tables(tables)
        upgrade_v7_to_v8_tables(tables)
    elif schema_version == 2:
        upgrade_v2_to_v3_tables(tables)
        upgrade_v3_to_v4_tables(tables)
        upgrade_v4_to_v5_tables(tables)
        upgrade_v5_to_v6_tables(tables)
        upgrade_v6_to_v7_tables(tables)
        upgrade_v7_to_v8_tables(tables)
    elif schema_version == 3:
        upgrade_v3_to_v4_tables(tables)
        upgrade_v4_to_v5_tables(tables)
        upgrade_v5_to_v6_tables(tables)
        upgrade_v6_to_v7_tables(tables)
        upgrade_v7_to_v8_tables(tables)
    elif schema_version == 4:
        upgrade_v4_to_v5_tables(tables)
        upgrade_v5_to_v6_tables(tables)
        upgrade_v6_to_v7_tables(tables)
        upgrade_v7_to_v8_tables(tables)
    elif schema_version == 5:
        upgrade_v5_to_v6_tables(tables)
        upgrade_v6_to_v7_tables(tables)
        upgrade_v7_to_v8_tables(tables)
    elif schema_version == 6:
        upgrade_v6_to_v7_tables(tables)
        upgrade_v7_to_v8_tables(tables)
    elif schema_version == 7:
        upgrade_v7_to_v8_tables(tables)
    return tables, legacy_without_scope_history


def _validate_portable_payload(
    payload: dict[str, Any], package_id: str, schema_version: int = 1
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    tables, legacy_without_scope_history = _normalize_portable_tables(
        payload, package_id, schema_version
    )
    _validate_capability_checkpoints(payload, tables)
    _validate_curriculum_checkpoint(payload, schema_version)
    _validate_project_checkpoint(payload, schema_version)
    _validate_roadmap_projection_checkpoint(payload, schema_version)
    _validate_analysis_v3_checkpoint(payload, tables, schema_version)
    _validate_recommendation_v2_checkpoint(payload, tables, schema_version)
    _validate_today_v2_checkpoint(payload, tables, schema_version)
    for table_name, rows in tables.items():
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise AppError(422, "PORTABLE_SCHEMA_INVALID", f"Table {table_name} has invalid rows.")
        expected_columns = {column.name for column in _table(PORTABLE_BY_TABLE[table_name]).columns}
        if any(set(row) != expected_columns for row in rows):
            raise AppError(
                422,
                "PORTABLE_SCHEMA_INVALID",
                f"Table {table_name} has missing or unknown fields.",
            )
    validate_portable_row_types(tables, PORTABLE_BY_TABLE)
    with tempfile.NamedTemporaryFile(prefix="lcc-validate-", suffix=".sqlite3") as temporary:
        validation_url = f"sqlite:///{temporary.name}"
        run_migrations(validation_url)
        validation_engine = create_database_engine(validation_url)
        try:
            with validation_engine.begin() as connection:
                try:
                    _clear_migration_seeded_portable_state(connection)
                    _insert_portable_tables(connection, tables)
                except SQLAlchemyError as exc:
                    raise AppError(
                        422,
                        "PORTABLE_DATA_INVALID",
                        "Portable backup values violate the canonical data model.",
                    ) from exc
                with Session(bind=connection) as validation_db:
                    rebuild_today_current_states(validation_db)
                    validation_db.flush()
                    validate_domain_integrity(connection)
                    _assert_curriculum_checkpoint_parity(
                        validation_db, payload.get("curriculumCatalogCheckpoint")
                    )
                    _assert_project_checkpoint_parity(
                        validation_db, payload.get("projectCatalogCheckpoint")
                    )
                    _assert_roadmap_projection_checkpoint_parity(
                        validation_db, payload.get("roadmapProjectionCheckpoint")
                    )
        finally:
            validation_engine.dispose()
    backfill_runs = {row["source_kind"]: row for row in tables["migration_backfill_runs"]}
    criterion_backfill = backfill_runs.get("v1_exit_criteria")
    activity_backfill = backfill_runs.get("v1_learning_sessions")
    evidence_backfill = backfill_runs.get("v1_evidence_sources")
    compatibility_conversions: dict[str, Any] = {}
    if schema_version == 1:
        if criterion_backfill is None or activity_backfill is None or evidence_backfill is None:
            raise AppError(
                422,
                "PORTABLE_DATA_INVALID",
                "The V1 compatibility conversion did not produce complete audit lineage.",
            )
        compatibility_conversions = {
            "competencyIdentitiesWithLegacyCreationUnknown": len(tables["competency_identities"]),
            "legacyCriterionAssertionsCreated": len(tables["legacy_criterion_assertions"]),
            "legacyCriterionSourceRowCount": criterion_backfill["source_row_count"],
            "legacyCriterionSourceHash": criterion_backfill["source_hash"],
            "legacyCriterionResultHash": criterion_backfill["result_hash"],
            "legacyActivitiesCreated": len(tables["activities"]),
            "legacySessionContributionsCreated": len(tables["session_contributions"]),
            "legacySessionSourceRowCount": activity_backfill["source_row_count"],
            "legacySessionSourceHash": activity_backfill["source_hash"],
            "legacySessionResultHash": activity_backfill["result_hash"],
            "legacyEvidenceCreated": len(tables["evidence"]),
            "legacyEvidenceLinksCreated": len(tables["evidence_links"]),
            "legacyEvidenceSourceRowCount": evidence_backfill["source_row_count"],
            "legacyEvidenceSourceHash": evidence_backfill["source_hash"],
            "legacyEvidenceResultHash": evidence_backfill["result_hash"],
            "nativeSemanticDefinitionsInferred": 0,
            "targetProfilesInferred": 0,
            "nativeCurriculaInferred": 0,
            "nativeProjectsInferred": 0,
            "initializedTodayV2Tables": len(PORTABLE_V8_TODAY_TABLES),
            "nativeTodayHistoryInferred": 0,
        }
    elif schema_version == 2:
        compatibility_conversions = {
            "initializedCurriculumTables": len(PORTABLE_V3_CURRICULUM_TABLES),
            "nativeCurriculaInferred": 0,
            "initializedProjectTables": len(PORTABLE_V4_PROJECT_TABLES),
            "nativeProjectsInferred": 0,
            "initializedLearningGraphProjectionTables": len(PORTABLE_V5_GRAPH_PROJECTION_TABLES),
            "nativeLearningGraphsInferred": 0,
            "initializedAnalysisV3Tables": len(PORTABLE_V6_ANALYSIS_TABLES),
            "nativeAnalysisHistoryInferred": 0,
            "initializedRecommendationV2Tables": len(PORTABLE_V7_RECOMMENDATION_TABLES),
            "nativeRecommendationHistoryInferred": 0,
            "initializedTodayV2Tables": len(PORTABLE_V8_TODAY_TABLES),
            "nativeTodayHistoryInferred": 0,
        }
    elif schema_version == 3:
        compatibility_conversions = {
            "initializedProjectTables": len(PORTABLE_V4_PROJECT_TABLES),
            "nativeProjectsInferred": 0,
            "initializedLearningGraphProjectionTables": len(PORTABLE_V5_GRAPH_PROJECTION_TABLES),
            "nativeLearningGraphsInferred": 0,
            "initializedAnalysisV3Tables": len(PORTABLE_V6_ANALYSIS_TABLES),
            "nativeAnalysisHistoryInferred": 0,
            "initializedRecommendationV2Tables": len(PORTABLE_V7_RECOMMENDATION_TABLES),
            "nativeRecommendationHistoryInferred": 0,
            "initializedTodayV2Tables": len(PORTABLE_V8_TODAY_TABLES),
            "nativeTodayHistoryInferred": 0,
        }
    elif schema_version == 4:
        compatibility_conversions = {
            "initializedLearningGraphProjectionTables": len(PORTABLE_V5_GRAPH_PROJECTION_TABLES),
            "nativeLearningGraphsInferred": 0,
            "initializedAnalysisV3Tables": len(PORTABLE_V6_ANALYSIS_TABLES),
            "nativeAnalysisHistoryInferred": 0,
            "initializedRecommendationV2Tables": len(PORTABLE_V7_RECOMMENDATION_TABLES),
            "nativeRecommendationHistoryInferred": 0,
            "initializedTodayV2Tables": len(PORTABLE_V8_TODAY_TABLES),
            "nativeTodayHistoryInferred": 0,
        }
    elif schema_version == 5:
        compatibility_conversions = {
            "initializedAnalysisV3Tables": len(PORTABLE_V6_ANALYSIS_TABLES),
            "nativeAnalysisHistoryInferred": 0,
            "initializedRecommendationV2Tables": len(PORTABLE_V7_RECOMMENDATION_TABLES),
            "nativeRecommendationHistoryInferred": 0,
            "initializedTodayV2Tables": len(PORTABLE_V8_TODAY_TABLES),
            "nativeTodayHistoryInferred": 0,
        }
    elif schema_version == 6:
        compatibility_conversions = {
            "initializedRecommendationV2Tables": len(PORTABLE_V7_RECOMMENDATION_TABLES),
            "nativeRecommendationHistoryInferred": 0,
            "initializedTodayV2Tables": len(PORTABLE_V8_TODAY_TABLES),
            "nativeTodayHistoryInferred": 0,
        }
    elif schema_version == 7:
        compatibility_conversions = {
            "initializedTodayV2Tables": len(PORTABLE_V8_TODAY_TABLES),
            "nativeTodayHistoryInferred": 0,
        }
    return tables, {
        "tableCounts": {name: len(rows) for name, rows in sorted(tables.items())},
        "portableCompatibility": (
            "legacy_scope_baseline" if legacy_without_scope_history else "current"
        ),
        "scopeHistoryBaselineAdded": bool(
            legacy_without_scope_history and tables["roadmap_scope_events"]
        ),
        "compatibilityConversions": compatibility_conversions,
    }


def _apply_roadmap_update(db: Session, payload: RoadmapCreate) -> None:
    from app.roadmap import apply_roadmap_payload

    apply_roadmap_payload(db, payload, scope_event_source="roadmap_import")


def _preflight_application(
    db: Session,
    operation: Callable[[Session], None],
    error_code: str,
    error_message: str,
) -> None:
    current_payload = _portable_payload(db)
    with tempfile.NamedTemporaryFile(prefix="lcc-preflight-", suffix=".sqlite3") as temporary:
        validation_url = f"sqlite:///{temporary.name}"
        run_migrations(validation_url)
        validation_engine = create_database_engine(validation_url)
        try:
            with Session(validation_engine) as validation_db:
                _clear_migration_seeded_portable_state(validation_db.connection())
                _insert_portable_tables(validation_db.connection(), current_payload["tables"])
                validation_db.flush()
                operation(validation_db)
                validation_db.flush()
                validate_domain_integrity(validation_db)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(422, error_code, error_message) from exc
        finally:
            validation_engine.dispose()


def _inspect_package(
    payload: ImportInspectRequest, settings: Settings, db: Session
) -> dict[str, Any]:
    package = payload.package.model_dump(mode="json")
    size = len(json.dumps(package, separators=(",", ":")).encode("utf-8"))
    if size > settings.max_import_bytes:
        raise AppError(413, "IMPORT_TOO_LARGE", "The import package is too large.")
    portable_package = payload.package.packageType in {"portable_logical_backup", "restore"}
    supported = (
        supports_portable_schema(payload.package.schemaVersion)
        if portable_package
        else payload.package.schemaVersion == 1
    )
    if not supported:
        raise AppError(
            422, "IMPORT_SCHEMA_UNSUPPORTED", "The import schema version is unsupported."
        )
    if portable_package and payload.package.schemaVersion == 1:
        read_v1_portable_package(package)
    if db.scalar(
        select(ImportRecord.id).where(ImportRecord.package_id == payload.package.packageId)
    ):
        raise AppError(409, "IMPORT_PACKAGE_DUPLICATE", "This package has already been applied.")
    summary: dict[str, Any] = {
        "packageType": payload.package.packageType,
        "packageId": payload.package.packageId,
        "sizeBytes": size,
    }
    if payload.package.packageType in {"portable_logical_backup", "restore"}:
        incoming_tables, validation_summary = _validate_portable_payload(
            payload.package.payload, payload.package.packageId, payload.package.schemaVersion
        )
        summary.update(validation_summary)
        existing_state = portable_state_presence(db, PORTABLE_MODELS)
        existing_tables = _portable_payload(db)["tables"]
        summary["mode"] = "empty_state_or_full_replacement"
        summary["authenticationPreserved"] = True
        summary["existingStateEmpty"] = not bool(existing_state)
        summary["replacementRequired"] = bool(existing_state)
        summary["existingPortableStateCounts"] = existing_state
        summary["replacementDiff"] = build_portable_replacement_diff(
            existing_tables, incoming_tables, PORTABLE_BY_TABLE
        )
    elif payload.package.packageType == "verification_update":
        try:
            verification_payload = VerificationUpdatePayload.model_validate(payload.package.payload)
        except ValidationError as exc:
            raise AppError(
                422,
                "VERIFICATION_UPDATE_INVALID",
                "The verification update payload is invalid.",
            ) from exc
        validated_records = verification_payload.verifications
        known_identities = set(
            db.scalars(
                select(CompetencyIdentity.id).where(
                    CompetencyIdentity.id.in_(
                        [item.competency_identity_id for item in validated_records]
                    )
                )
            ).all()
        )
        if any(item.competency_identity_id not in known_identities for item in validated_records):
            raise AppError(
                422, "VERIFICATION_REFERENCE_INVALID", "A referenced competency does not exist."
            )
        _preflight_application(
            db,
            lambda validation_db: _apply_verification_update(
                validation_db,
                verification_payload,
                import_package_id=payload.package.packageId,
            ),
            "VERIFICATION_UPDATE_INVALID",
            "The verification update cannot be applied to the current state.",
        )
        summary["verificationRecordsAdded"] = len(validated_records)
        summary["statusImplications"] = [
            {
                "competencyIdentityId": item.competency_identity_id,
                "result": item.result,
                "status": {"passed": "verified", "partial": "practicing", "failed": "needs_review"}[
                    item.result
                ],
            }
            for item in validated_records
        ]
    elif payload.package.packageType == "state_update":
        try:
            state_payload = StateUpdatePayload.model_validate(payload.package.payload)
        except ValidationError as exc:
            raise AppError(
                422, "STATE_UPDATE_INVALID", "The competency state update payload is invalid."
            ) from exc
        known_identities = set(db.scalars(select(CompetencyIdentity.id)).all())
        for item in state_payload.states:
            if item.competency_identity_id not in known_identities:
                raise AppError(422, "STATE_UPDATE_INVALID", "A competency state is invalid.")
        _preflight_application(
            db,
            lambda validation_db: _apply_state_update(
                validation_db,
                state_payload,
                import_package_id=payload.package.packageId,
            ),
            "STATE_UPDATE_INVALID",
            "The competency state update cannot be applied to the current state.",
        )
        summary["statesUpdated"] = len(state_payload.states)
        summary["stateChanges"] = [
            {
                "competencyIdentityId": item.competency_identity_id,
                "toStatus": item.status,
            }
            for item in state_payload.states
        ]
    else:
        try:
            roadmap_payload = RoadmapPackagePayload.model_validate(payload.package.payload)
            validated_roadmap = roadmap_payload.roadmap
            from app.roadmap import validate_roadmap_payload

            validate_roadmap_payload(validated_roadmap)
        except ValidationError as exc:
            raise AppError(
                422,
                "ROADMAP_PACKAGE_INVALID",
                "The roadmap package is invalid.",
            ) from exc
        _preflight_application(
            db,
            lambda validation_db: _apply_roadmap_update(validation_db, validated_roadmap),
            "ROADMAP_PACKAGE_INVALID",
            "The roadmap package conflicts with the current roadmap state.",
        )
        summary["roadmap"] = build_roadmap_diff(db, validated_roadmap, payload.package.packageType)
    digest = _package_digest(package)
    token = new_secret()
    _previews[payload.package.packageId] = Preview(
        digest=digest,
        token=token,
        expires_at=utc_now_ms() + 15 * 60 * 1000,
        summary=summary,
    )
    return {
        "valid": True,
        "dryRun": True,
        "summary": summary,
        "diff": summary,
        "confirmationToken": token,
        "expiresInMs": 15 * 60 * 1000,
    }


def _delete_portable_state(db: Session) -> None:
    for roadmap in db.scalars(select(Roadmap)).all():
        roadmap.active_version_id = None
        roadmap.current_phase_id = None
        roadmap.is_current = False
    db.flush()
    db.execute(_table(ProjectionInvalidation).delete())
    db.execute(_table(CompetencyReviewState).delete())
    db.execute(_table(CompetencyCapabilityState).delete())
    db.execute(_table(CompetencyDefinition).update().values(parent_definition_id=None))
    db.execute(_table(CurriculumVersion).update().values(supersedes_version_id=None))
    db.execute(_table(ProjectVersion).update().values(supersedes_version_id=None))
    db.execute(_table(LearningGraphVersion).update().values(supersedes_version_id=None))
    db.execute(_table(ProjectEvent).update().values(corrects_event_id=None))
    db.execute(_table(Activity).update().values(supersedes_activity_id=None))
    db.execute(_table(Evidence).update().values(supersedes_evidence_id=None))
    db.execute(_table(EvidenceRetraction).update().values(replacement_evidence_id=None))
    db.execute(_table(EvidenceLinkRetraction).update().values(replacement_link_id=None))
    db.execute(_table(RecommendationV2Run).update().values(replay_of_run_id=None))
    db.execute(
        _table(TodayInteraction).update().values(replacement_suggestion_id=None),
        execution_options={"synchronize_session": False},
    )
    db.execute(
        _table(TodaySuggestion).update().values(replaces_suggestion_id=None),
        execution_options={"synchronize_session": False},
    )
    db.flush()
    db.execute(_table(RoadmapProjectionCheckpoint).delete())
    db.execute(_table(RoadmapProjectionCache).delete())
    for item in list(db.identity_map.values()):
        if isinstance(item, AnalysisV3CurrentState):
            db.expunge(item)
    db.execute(_table(AnalysisV3CurrentState).delete())
    db.execute(_table(TodaySuggestionCurrentState).delete())
    for model in reversed(PORTABLE_MODELS):
        db.execute(_table(model).delete())
    db.flush()


def _current_scope(db: Session) -> tuple[str, str, str] | None:
    roadmap = current_legacy_roadmap(db)
    if roadmap is None or roadmap.active_version_id is None or roadmap.current_phase_id is None:
        return None
    return roadmap.id, roadmap.active_version_id, roadmap.current_phase_id


def _apply_portable_restore(
    db: Session,
    payload: dict[str, Any],
    replace_existing: bool,
    *,
    package_id: str = "direct-restore",
    schema_version: int = 1,
) -> None:
    expected_checkpoints = {
        (item["competencyIdentityId"], item["scopeKey"]): item["outputHash"]
        for item in payload.get("capabilityProjectionCheckpoints", [])
    }
    curriculum_checkpoint = payload.get("curriculumCatalogCheckpoint")
    project_checkpoint = payload.get("projectCatalogCheckpoint")
    roadmap_projection_checkpoint = payload.get("roadmapProjectionCheckpoint")
    analysis_checkpoint = payload.get("analysisV3CurrentCheckpoint")
    today_checkpoint = payload.get("todayV2CurrentCheckpoint")
    tables, legacy_without_scope_history = _normalize_portable_tables(
        payload, package_id, schema_version
    )
    previous_scope = _current_scope(db)
    existing_state = portable_state_presence(db, PORTABLE_MODELS)
    if existing_state and not replace_existing:
        raise AppError(
            409,
            "RESTORE_REPLACEMENT_CONFIRMATION_REQUIRED",
            "Existing learning state requires explicit full replacement.",
            {"existingPortableStateCounts": existing_state},
        )
    _delete_portable_state(db)
    _insert_portable_tables(db.connection(), tables)
    db.flush()
    rebuild_today_current_states(db)
    if today_checkpoint is not None and today_current_checkpoint(db) != today_checkpoint:
        raise AppError(
            422,
            "RESTORE_TODAY_PROJECTION_MISMATCH",
            "Restored Today V2 current state does not match its rebuild checkpoint.",
        )
    restored_analysis_states = (
        analysis_checkpoint.get("states", []) if isinstance(analysis_checkpoint, dict) else []
    )
    rebuild_purposes: set[str] = set()
    for state in restored_analysis_states:
        restored_snapshot = db.get(AnalysisSnapshot, state["snapshotId"])
        assert restored_snapshot is not None
        expected_completed_through = (
            datetime.now(ZoneInfo(restored_snapshot.timezone)).date() - timedelta(days=1)
        ).isoformat()
        _facts, _unknowns, restored_inputs = build_analysis_inputs(
            db, restored_snapshot.cutoff_at, str(state["purpose"])
        )
        exact_live_match = (
            state["status"] == "current"
            and state["sourceGeneration"] == analysis_source_generation(db)
            and restored_snapshot.completed_through_date == expected_completed_through
            and state["policyBundleHash"] == content_hash(analysis_policy_bundle())
            and restored_snapshot.input_hash == content_hash(restored_inputs)
        )
        pointer_status = "current" if exact_live_match else "stale"
        if pointer_status != "current":
            rebuild_purposes.add(str(state["purpose"]))
        db.add(
            AnalysisV3CurrentState(
                scope_key="learning-control",
                purpose=state["purpose"],
                run_id=state["runId"],
                snapshot_id=state["snapshotId"],
                status=pointer_status,
                exclusive_cutoff_at=restored_snapshot.cutoff_at,
                source_generation=state["sourceGeneration"],
                input_hash=restored_snapshot.input_hash,
                policy_bundle_hash=state["policyBundleHash"],
                updated_at=state["updatedAt"] if exact_live_match else utc_now_ms(),
            )
        )
    if not restored_analysis_states:
        rebuild_purposes.add("learning_control")
    for purpose in sorted(rebuild_purposes):
        db.add(
            ProjectionInvalidation(
                projection_kind="analysis",
                subject_type="analysis_scope",
                subject_id=f"learning-control:{purpose}",
                source_fact_id=f"portable-restore:{package_id}:{purpose}",
                target_policy_version=ANALYSIS_POLICY_VERSION,
                status="pending",
                attempt_count=0,
                requested_at=utc_now_ms(),
            )
        )
    db.flush()
    _assert_curriculum_checkpoint_parity(db, curriculum_checkpoint)
    _assert_project_checkpoint_parity(db, project_checkpoint)
    _assert_roadmap_projection_checkpoint_parity(db, roadmap_projection_checkpoint)
    restored_capability_baseline: dict[tuple[str, str], tuple[str, str]] = {}
    for run in db.scalars(
        select(CapabilityEvaluationRun).order_by(
            CapabilityEvaluationRun.generated_at, CapabilityEvaluationRun.id
        )
    ).all():
        if run.evidence_set_hash != capability_evidence_set_hash(
            db, run.competency_identity_id, run.dimension_id, run.cutoff_at
        ):
            raise AppError(
                422,
                "RESTORE_CAPABILITY_LINEAGE_INVALID",
                "Capability history does not match the restored authoritative Evidence facts.",
            )
        restored_capability_baseline[(run.competency_identity_id, run.scope_key)] = (
            run.evidence_set_hash,
            run.output_hash,
        )
    restored_scope = _current_scope(db)
    if (
        not legacy_without_scope_history
        and restored_scope is not None
        and restored_scope != previous_scope
    ):
        from app.roadmap import record_roadmap_scope_event

        record_roadmap_scope_event(
            db,
            roadmap_id=restored_scope[0],
            roadmap_version_id=restored_scope[1],
            phase_id=restored_scope[2],
            source="portable_restore",
            reason="Current scope activated by portable restore",
        )
    validate_domain_integrity(db)
    seed_capability_projections_from_history(db)
    db.flush()
    enqueue_full_capability_rebuild(db, source_fact_id=f"restore:{package_id}")
    db.flush()
    drain_projection_invalidations(db, atomic=True)
    for state in db.scalars(select(CompetencyCapabilityState)).all():
        baseline = restored_capability_baseline.get((state.competency_identity_id, state.scope_key))
        rebuilt_run = db.get(CapabilityEvaluationRun, state.evaluation_run_id)
        if (
            baseline is not None
            and rebuilt_run is not None
            and baseline[0] == rebuilt_run.evidence_set_hash
            and baseline[1] != rebuilt_run.output_hash
        ):
            raise AppError(
                422,
                "RESTORE_CAPABILITY_PARITY_FAILED",
                "Capability projection rebuild did not match retained immutable history.",
                {
                    "competencyIdentityId": state.competency_identity_id,
                    "scopeKey": state.scope_key,
                },
            )
        expected_output_hash = expected_checkpoints.get(
            (state.competency_identity_id, state.scope_key)
        )
        if expected_output_hash is not None and (
            rebuilt_run is None or rebuilt_run.output_hash != expected_output_hash
        ):
            raise AppError(
                422,
                "RESTORE_CAPABILITY_PARITY_FAILED",
                "Capability projection rebuild did not match the exported projection checkpoint.",
                {
                    "competencyIdentityId": state.competency_identity_id,
                    "scopeKey": state.scope_key,
                },
            )
    rebuilt_roadmap = rebuild_roadmap_projection(db)
    rebuilt_at = utc_now_ms()
    for invalidation in db.scalars(
        select(ProjectionInvalidation).where(
            ProjectionInvalidation.projection_kind == "roadmap_projection_v2",
            ProjectionInvalidation.status.in_(["pending", "running"]),
        )
    ).all():
        invalidation.status = "completed"
        invalidation.attempt_count += 1
        invalidation.started_at = invalidation.started_at or rebuilt_at
        invalidation.completed_at = rebuilt_at
        invalidation.error_json = None
    if roadmap_projection_checkpoint is not None and bool(
        rebuilt_roadmap.get("configured")
    ) != bool(roadmap_projection_checkpoint["configured"]):
        raise AppError(
            422,
            "RESTORE_ROADMAP_PROJECTION_PARITY_FAILED",
            "Roadmap Projection rebuild availability changed during restore.",
        )
    validate_domain_integrity(db)


def _apply_verification_update(
    db: Session, payload: VerificationUpdatePayload, *, import_package_id: str | None = None
) -> None:
    for item in payload.verifications:
        create_verification_with_evidence(
            db,
            item,
            origin_kind="import",
            lifecycle_source="import",
            lifecycle_reason_prefix="Imported verification result",
            import_package_id=import_package_id,
        )


def _apply_state_update(
    db: Session, payload: StateUpdatePayload, *, import_package_id: str | None = None
) -> None:
    for item in payload.states:
        if item.status == "verified":
            if item.verification is None:
                raise AppError(
                    422,
                    "VERIFICATION_RECORD_REQUIRED",
                    "Imported verified status requires a matching passed verification record.",
                )
            _apply_verification_update(
                db,
                VerificationUpdatePayload(verifications=[item.verification]),
                import_package_id=import_package_id,
            )
            continue
        transition_status(
            db,
            item.competency_identity_id,
            item.status,
            reason=item.reason,
            source="import",
        )


@router.post("/export")
async def export_data(
    payload: ExportRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    created_at = epoch_ms_to_rfc3339(utc_now_ms())
    if payload.purpose == "portable_logical_backup":
        result: Any = _envelope(
            payload.purpose,
            _portable_payload(db, set(payload.project_ids) if payload.project_ids else None),
            schema_version=PORTABLE_SCHEMA_CURRENT,
        )
    else:
        analysis = _analysis_payload(db, payload)
        result = (
            _envelope(payload.purpose, analysis)
            if payload.format == "json"
            else _human_report(analysis, created_at)
        )
    record = ExportRecord(
        export_type=payload.purpose,
        format=payload.format,
        scope_summary_json=payload.model_dump_json(),
    )
    db.add(record)
    db.commit()
    return {"purpose": payload.purpose, "format": payload.format, "content": result}


@router.post("/import/inspect")
async def inspect_import(
    payload: ImportInspectRequest,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    settings: Settings = Depends(get_settings_dependency),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    import_rule = RateLimitRule(
        attempts=settings.import_rate_limit_attempts,
        window_ms=settings.import_rate_limit_window_ms,
    )
    if not rate_limiter.check("import", auth.session.id, import_rule):
        raise AppError(429, "IMPORT_RATE_LIMITED", "Too many import requests. Try again later.")
    rate_limiter.add("import", auth.session.id)
    try:
        result = _inspect_package(payload, settings, db)
    except AppError:
        logger.warning("Import inspection rejected")
        raise
    logger.info("Import inspection accepted")
    return result


@router.post("/import/apply")
async def apply_import(
    payload: ImportApplyRequest,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> dict[str, Any]:
    import_rule = RateLimitRule(
        attempts=settings.import_rate_limit_attempts,
        window_ms=settings.import_rate_limit_window_ms,
    )
    if not rate_limiter.check("import", auth.session.id, import_rule):
        raise AppError(429, "IMPORT_RATE_LIMITED", "Too many import requests. Try again later.")
    rate_limiter.add("import", auth.session.id)
    package = payload.package.model_dump(mode="json")
    preview = _previews.get(payload.package.packageId)
    if (
        preview is None
        or preview.expires_at <= utc_now_ms()
        or preview.token != payload.confirmation_token
        or preview.digest != _package_digest(package)
    ):
        raise AppError(
            409,
            "IMPORT_CONFIRMATION_INVALID",
            "The validated import preview is missing, expired, or does not match.",
        )
    if db.scalar(select(ImportRecord).where(ImportRecord.package_id == payload.package.packageId)):
        raise AppError(409, "IMPORT_PACKAGE_DUPLICATE", "This package has already been applied.")
    _inspect_package(
        ImportInspectRequest(filename=payload.filename, package=payload.package), settings, db
    )
    db.rollback()
    try:
        with db.begin():
            backup = create_operational_backup(db, settings, "pre-import")
            if payload.package.packageType in {"portable_logical_backup", "restore"}:
                _apply_portable_restore(
                    db,
                    payload.package.payload,
                    payload.replace_existing,
                    package_id=payload.package.packageId,
                    schema_version=payload.package.schemaVersion,
                )
            elif payload.package.packageType == "verification_update":
                _apply_verification_update(
                    db,
                    VerificationUpdatePayload.model_validate(payload.package.payload),
                    import_package_id=payload.package.packageId,
                )
            elif payload.package.packageType == "state_update":
                _apply_state_update(
                    db,
                    StateUpdatePayload.model_validate(payload.package.payload),
                    import_package_id=payload.package.packageId,
                )
            else:
                roadmap_payload = RoadmapPackagePayload.model_validate(
                    payload.package.payload
                ).roadmap
                _apply_roadmap_update(db, roadmap_payload)
            db.add(
                ImportRecord(
                    package_id=payload.package.packageId,
                    import_type=payload.package.packageType,
                    schema_version=payload.package.schemaVersion,
                    source_filename=payload.filename,
                    dry_run_summary_json=json.dumps(preview.summary, separators=(",", ":")),
                    applied=True,
                    applied_at=utc_now_ms(),
                    pre_import_backup_reference=backup.path,
                )
            )
            validate_domain_integrity(db)
    except AppError:
        db.rollback()
        logger.warning("Import apply rejected: type=%s", payload.package.packageType)
        raise
    if payload.package.packageType in {"verification_update", "state_update"}:
        drain_projection_invalidations(db)
    _previews.pop(payload.package.packageId, None)
    logger.info("Import applied successfully: type=%s", payload.package.packageType)
    return {
        "applied": True,
        "packageId": payload.package.packageId,
        "packageType": payload.package.packageType,
        "authenticationPreserved": True,
    }


@router.post("/backups/operational", status_code=201)
async def create_backup_endpoint(
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> dict[str, Any]:
    record = create_operational_backup(db, settings, "manual")
    db.commit()
    logger.info("Operational backup created")
    return {
        "id": record.id,
        "createdAt": epoch_ms_to_rfc3339(record.created_at),
        "sizeBytes": record.size_bytes,
        "checksumSha256": record.checksum_sha256,
    }


@router.get("/history")
async def operation_history(
    limit: int = 50,
    offset: int = 0,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    bounded_limit = min(max(limit, 1), 200)
    bounded_offset = max(offset, 0)
    items: list[dict[str, Any]] = []
    for import_item in db.scalars(select(ImportRecord)).all():
        items.append(
            {
                "id": import_item.id,
                "operation": "import",
                "kind": import_item.import_type,
                "createdAt": import_item.created_at,
                "appliedAt": import_item.applied_at,
                "applied": import_item.applied,
                "sourceFilename": import_item.source_filename,
            }
        )
    for export_item in db.scalars(select(ExportRecord)).all():
        items.append(
            {
                "id": export_item.id,
                "operation": "export",
                "kind": export_item.export_type,
                "format": export_item.format,
                "createdAt": export_item.created_at,
            }
        )
    for backup_item in db.scalars(select(OperationalBackup)).all():
        items.append(
            {
                "id": backup_item.id,
                "operation": "backup",
                "kind": backup_item.purpose,
                "createdAt": backup_item.created_at,
                "sizeBytes": backup_item.size_bytes,
                "checksumSha256": backup_item.checksum_sha256,
            }
        )
    items.sort(key=lambda item: item["createdAt"], reverse=True)
    return serialize_api_instants(
        {
            "items": items[bounded_offset : bounded_offset + bounded_limit],
            "limit": bounded_limit,
            "offset": bounded_offset,
            "total": len(items),
        }
    )
