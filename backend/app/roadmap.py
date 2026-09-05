from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.domain import has_required_dependency_cycle, transition_status
from app.errors import AppError
from app.models import (
    CompetencyAbilityItem,
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyState,
    CompetencyStatusEvent,
    CompetencyUnderstandingItem,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    Phase,
    Roadmap,
    RoadmapScopeEvent,
    RoadmapVersion,
    Track,
    VerificationEvidence,
    VerificationRecord,
)
from app.schemas import ExitCriterionStateUpdate, RoadmapCreate, StatusUpdate
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(prefix="/roadmap", tags=["roadmap"])


def serialize_current_roadmap(db: Session, roadmap: Roadmap) -> dict[str, Any]:
    if roadmap.active_version_id is None:
        raise AppError(409, "ROADMAP_STATE_INVALID", "The current roadmap has no active version.")
    version = db.get(RoadmapVersion, roadmap.active_version_id)
    if version is None:
        raise AppError(409, "ROADMAP_STATE_INVALID", "The active roadmap version is missing.")
    phases = db.scalars(
        select(Phase)
        .where(Phase.roadmap_version_id == roadmap.active_version_id)
        .order_by(Phase.order_index)
    ).all()
    tracks = db.scalars(
        select(Track)
        .where(Track.roadmap_version_id == roadmap.active_version_id)
        .order_by(Track.order_index)
    ).all()
    definitions = db.scalars(
        select(CompetencyDefinition)
        .where(CompetencyDefinition.roadmap_version_id == roadmap.active_version_id)
        .order_by(CompetencyDefinition.order_index)
    ).all()
    identity_ids = [definition.competency_identity_id for definition in definitions]
    identities = {
        item.id: item
        for item in db.scalars(
            select(CompetencyIdentity).where(CompetencyIdentity.id.in_(identity_ids))
        ).all()
    }
    states = {
        item.competency_identity_id: item
        for item in db.scalars(
            select(CompetencyState).where(CompetencyState.competency_identity_id.in_(identity_ids))
        ).all()
    }
    prerequisites: dict[str, list[dict[str, str]]] = defaultdict(list)
    for prerequisite in db.scalars(
        select(CompetencyPrerequisite).where(
            CompetencyPrerequisite.competency_definition_id.in_(
                [definition.id for definition in definitions]
            )
        )
    ).all():
        prerequisites[prerequisite.competency_definition_id].append(
            {
                "identityId": prerequisite.prerequisite_competency_identity_id,
                "stableKey": identities[
                    prerequisite.prerequisite_competency_identity_id
                ].stable_key,
                "kind": prerequisite.kind,
            }
        )
    understanding: dict[str, list[str]] = defaultdict(list)
    for understanding_item in db.scalars(
        select(CompetencyUnderstandingItem)
        .where(
            CompetencyUnderstandingItem.competency_definition_id.in_(
                [item.id for item in definitions]
            )
        )
        .order_by(CompetencyUnderstandingItem.order_index)
    ).all():
        understanding[understanding_item.competency_definition_id].append(understanding_item.text)
    abilities: dict[str, list[str]] = defaultdict(list)
    for ability_item in db.scalars(
        select(CompetencyAbilityItem)
        .where(
            CompetencyAbilityItem.competency_definition_id.in_([item.id for item in definitions])
        )
        .order_by(CompetencyAbilityItem.order_index)
    ).all():
        abilities[ability_item.competency_definition_id].append(ability_item.text)
    criteria: dict[str, list[dict[str, Any]]] = defaultdict(list)
    criterion_definitions = db.scalars(
        select(ExitCriterionDefinition).where(
            ExitCriterionDefinition.competency_definition_id.in_([item.id for item in definitions])
        )
    ).all()
    criterion_identities = {
        item.id: item
        for item in db.scalars(
            select(ExitCriterionIdentity).where(
                ExitCriterionIdentity.id.in_(
                    [item.exit_criterion_identity_id for item in criterion_definitions]
                )
            )
        ).all()
    }
    for criterion_definition in criterion_definitions:
        identity = criterion_identities[criterion_definition.exit_criterion_identity_id]
        criteria[criterion_definition.competency_definition_id].append(
            {
                "id": identity.id,
                "stableKey": identity.stable_key,
                "text": criterion_definition.text,
                "required": criterion_definition.required,
                "weight": criterion_definition.weight,
                "state": identity.current_state,
            }
        )
    tracks_by_phase: dict[str, list[Track]] = defaultdict(list)
    for track in tracks:
        tracks_by_phase[track.phase_id].append(track)
    definitions_by_track: dict[str, list[CompetencyDefinition]] = defaultdict(list)
    for definition in definitions:
        definitions_by_track[definition.track_id].append(definition)

    return {
        "id": roadmap.id,
        "stableKey": roadmap.stable_key,
        "title": roadmap.title,
        "description": roadmap.description,
        "activeVersion": {
            "id": version.id,
            "version": version.version,
            "schemaVersion": version.schema_version,
        },
        "currentPhaseId": roadmap.current_phase_id,
        "phases": [
            {
                "id": phase.id,
                "stableKey": phase.stable_key,
                "title": phase.title,
                "description": phase.description,
                "orderIndex": phase.order_index,
                "archived": phase.archived,
                "isCurrent": phase.id == roadmap.current_phase_id,
                "tracks": [
                    {
                        "id": track.id,
                        "stableKey": track.stable_key,
                        "title": track.title,
                        "description": track.description,
                        "orderIndex": track.order_index,
                        "competencies": [
                            {
                                "definitionId": definition.id,
                                "identityId": definition.competency_identity_id,
                                "stableKey": identities[
                                    definition.competency_identity_id
                                ].stable_key,
                                "parentDefinitionId": definition.parent_definition_id,
                                "title": definition.title,
                                "description": definition.description,
                                "goal": definition.goal,
                                "priority": definition.priority,
                                "weight": definition.weight,
                                "orderIndex": definition.order_index,
                                "archived": definition.archived,
                                "position": {
                                    "x": definition.position_x,
                                    "y": definition.position_y,
                                },
                                "status": states[definition.competency_identity_id].current_status,
                                "prerequisites": prerequisites[definition.id],
                                "mustUnderstand": understanding[definition.id],
                                "mustBeAbleTo": abilities[definition.id],
                                "exitCriteria": criteria[definition.id],
                            }
                            for definition in definitions_by_track[track.id]
                        ],
                    }
                    for track in tracks_by_phase[phase.id]
                ],
            }
            for phase in phases
        ],
    }


@router.get("/current")
async def current_roadmap(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, Any]:
    roadmap = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
    if roadmap is None:
        return {"configured": False, "guidance": "Import a roadmap package to begin."}
    return {"configured": True, "roadmap": serialize_current_roadmap(db, roadmap)}


def validate_roadmap_payload(payload: RoadmapCreate) -> None:
    phase_keys = [phase.stable_key for phase in payload.phases]
    track_keys = [track.stable_key for phase in payload.phases for track in phase.tracks]
    if (
        len(phase_keys) != len(set(phase_keys))
        or payload.current_phase_stable_key not in phase_keys
    ):
        raise AppError(422, "ROADMAP_PHASE_INVALID", "Roadmap phase keys are invalid.")
    if len(track_keys) != len(set(track_keys)):
        raise AppError(
            422, "ROADMAP_TRACK_INVALID", "Track stable keys must be unique per version."
        )
    competency_inputs = [
        item for phase in payload.phases for track in phase.tracks for item in track.competencies
    ]
    stable_keys = [item.stable_key for item in competency_inputs]
    if len(stable_keys) != len(set(stable_keys)):
        raise AppError(
            422, "COMPETENCY_KEY_DUPLICATE", "Competency stable keys must be unique per version."
        )
    stable_key_set = set(stable_keys)
    edges: dict[str, set[str]] = {}
    parent_edges: dict[str, set[str]] = {}
    for item in competency_inputs:
        all_dependencies = item.prerequisite_stable_keys + item.recommended_prerequisite_stable_keys
        if any(key not in stable_key_set for key in all_dependencies):
            raise AppError(422, "PREREQUISITE_INVALID", "A prerequisite stable key is missing.")
        edges[item.stable_key] = set(item.prerequisite_stable_keys)
        if item.parent_stable_key is not None:
            if item.parent_stable_key not in stable_key_set:
                raise AppError(422, "PARENT_COMPETENCY_INVALID", "A parent competency is missing.")
            parent_edges[item.stable_key] = {item.parent_stable_key}
    if has_required_dependency_cycle(edges):
        raise AppError(422, "REQUIRED_DEPENDENCY_CYCLE", "Required prerequisites contain a cycle.")
    if has_required_dependency_cycle(parent_edges):
        raise AppError(
            422, "COMPETENCY_HIERARCHY_CYCLE", "The competency hierarchy contains a cycle."
        )


def record_roadmap_scope_event(
    db: Session,
    *,
    roadmap_id: str,
    roadmap_version_id: str,
    phase_id: str,
    source: str,
    reason: str,
    occurred_at: int | None = None,
) -> RoadmapScopeEvent:
    latest = db.scalar(
        select(RoadmapScopeEvent).order_by(RoadmapScopeEvent.event_sequence.desc()).limit(1)
    )
    sequence = (latest.event_sequence if latest else 0) + 1
    event_time = occurred_at if occurred_at is not None else utc_now_ms()
    if latest is not None:
        event_time = max(event_time, latest.occurred_at)
    event = RoadmapScopeEvent(
        roadmap_id=roadmap_id,
        roadmap_version_id=roadmap_version_id,
        phase_id=phase_id,
        source=source,
        reason=reason,
        occurred_at=event_time,
        event_sequence=sequence,
    )
    db.add(event)
    db.flush()
    return event


def apply_roadmap_payload(
    db: Session,
    payload: RoadmapCreate,
    *,
    scope_event_source: str = "roadmap_apply",
) -> Roadmap:
    validate_roadmap_payload(payload)

    existing_current = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
    existing_same = db.scalar(select(Roadmap).where(Roadmap.stable_key == payload.stable_key))
    if existing_current is not None and existing_current.id != getattr(existing_same, "id", None):
        existing_current.is_current = False
        existing_current.active_version_id = None
        existing_current.current_phase_id = None
        db.flush()
    if existing_same is not None:
        duplicate_version = db.scalar(
            select(RoadmapVersion).where(
                RoadmapVersion.roadmap_id == existing_same.id,
                RoadmapVersion.version == payload.version,
            )
        )
        if duplicate_version is not None:
            raise AppError(409, "ROADMAP_VERSION_DUPLICATE", "This roadmap version already exists.")
        roadmap = existing_same
        roadmap.title = payload.title
        roadmap.description = payload.description
        roadmap.is_current = True
    else:
        roadmap = Roadmap(
            stable_key=payload.stable_key,
            title=payload.title,
            description=payload.description,
            is_current=True,
        )
        db.add(roadmap)
        db.flush()
    version = RoadmapVersion(
        roadmap_id=roadmap.id,
        version=payload.version,
        schema_version=1,
        changelog=payload.changelog,
        source=payload.source,
    )
    db.add(version)
    db.flush()

    phases: dict[str, Phase] = {}
    tracks: dict[str, Track] = {}
    for phase_input in payload.phases:
        phase = Phase(
            roadmap_version_id=version.id,
            stable_key=phase_input.stable_key,
            title=phase_input.title,
            description=phase_input.description,
            order_index=phase_input.order_index,
        )
        db.add(phase)
        db.flush()
        phases[phase_input.stable_key] = phase
        for track_input in phase_input.tracks:
            track = Track(
                roadmap_version_id=version.id,
                phase_id=phase.id,
                stable_key=track_input.stable_key,
                title=track_input.title,
                description=track_input.description,
                order_index=track_input.order_index,
            )
            db.add(track)
            db.flush()
            tracks[track_input.stable_key] = track

    identities: dict[str, CompetencyIdentity] = {}
    definitions: dict[str, CompetencyDefinition] = {}
    for phase_input in payload.phases:
        for track_input in phase_input.tracks:
            for item in track_input.competencies:
                identity = db.scalar(
                    select(CompetencyIdentity).where(
                        CompetencyIdentity.stable_key == item.stable_key
                    )
                )
                if identity is None:
                    identity = CompetencyIdentity(stable_key=item.stable_key)
                    db.add(identity)
                    db.flush()
                    db.add(
                        CompetencyState(
                            competency_identity_id=identity.id, current_status="not_started"
                        )
                    )
                    db.add(
                        CompetencyStatusEvent(
                            competency_identity_id=identity.id,
                            from_status=None,
                            to_status="not_started",
                            reason="Competency introduced",
                            source="roadmap",
                        )
                    )
                identities[item.stable_key] = identity
                definition = CompetencyDefinition(
                    competency_identity_id=identity.id,
                    roadmap_version_id=version.id,
                    phase_id=phases[phase_input.stable_key].id,
                    track_id=tracks[track_input.stable_key].id,
                    title=item.title,
                    description=item.description,
                    goal=item.goal,
                    priority=item.priority,
                    weight=item.weight,
                    order_index=item.order_index,
                    position_x=item.position_x,
                    position_y=item.position_y,
                )
                db.add(definition)
                db.flush()
                definitions[item.stable_key] = definition

    for phase_input in payload.phases:
        for track_input in phase_input.tracks:
            for item in track_input.competencies:
                definition = definitions[item.stable_key]
                if item.parent_stable_key:
                    if item.parent_stable_key not in definitions:
                        raise AppError(
                            422, "PARENT_COMPETENCY_INVALID", "A parent competency is missing."
                        )
                    definition.parent_definition_id = definitions[item.parent_stable_key].id
                for index, value in enumerate(item.must_understand):
                    db.add(
                        CompetencyUnderstandingItem(
                            competency_definition_id=definition.id, text=value, order_index=index
                        )
                    )
                for index, value in enumerate(item.must_be_able_to):
                    db.add(
                        CompetencyAbilityItem(
                            competency_definition_id=definition.id, text=value, order_index=index
                        )
                    )
                for stable_key in item.prerequisite_stable_keys:
                    db.add(
                        CompetencyPrerequisite(
                            competency_definition_id=definition.id,
                            prerequisite_competency_identity_id=identities[stable_key].id,
                            kind="required",
                        )
                    )
                for stable_key in item.recommended_prerequisite_stable_keys:
                    db.add(
                        CompetencyPrerequisite(
                            competency_definition_id=definition.id,
                            prerequisite_competency_identity_id=identities[stable_key].id,
                            kind="recommended",
                        )
                    )
                for criterion in item.exit_criteria:
                    criterion_identity = db.scalar(
                        select(ExitCriterionIdentity).where(
                            ExitCriterionIdentity.competency_identity_id
                            == identities[item.stable_key].id,
                            ExitCriterionIdentity.stable_key == criterion.stable_key,
                        )
                    )
                    if criterion_identity is None:
                        criterion_identity = ExitCriterionIdentity(
                            competency_identity_id=identities[item.stable_key].id,
                            stable_key=criterion.stable_key,
                            current_state="not_met",
                        )
                        db.add(criterion_identity)
                        db.flush()
                    db.add(
                        ExitCriterionDefinition(
                            exit_criterion_identity_id=criterion_identity.id,
                            competency_definition_id=definition.id,
                            text=criterion.text,
                            required=criterion.required,
                            weight=criterion.weight,
                        )
                    )

    roadmap.active_version_id = version.id
    roadmap.current_phase_id = phases[payload.current_phase_stable_key].id
    db.flush()
    record_roadmap_scope_event(
        db,
        roadmap_id=roadmap.id,
        roadmap_version_id=version.id,
        phase_id=phases[payload.current_phase_stable_key].id,
        source=scope_event_source,
        reason="Roadmap version activated",
    )
    return roadmap


@router.post("", status_code=201)
async def create_roadmap(
    payload: RoadmapCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    roadmap = apply_roadmap_payload(db, payload)
    db.commit()
    return serialize_current_roadmap(db, roadmap)


@router.put("/current-phase/{phase_id}")
async def set_current_phase(
    phase_id: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    roadmap = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
    phase = db.get(Phase, phase_id)
    if (
        roadmap is None
        or phase is None
        or phase.roadmap_version_id != roadmap.active_version_id
        or phase.archived
    ):
        raise AppError(422, "CURRENT_PHASE_INVALID", "The selected phase cannot be current.")
    if roadmap.current_phase_id == phase.id:
        return {"currentPhaseId": phase.id}
    previous_phase = db.get(Phase, roadmap.current_phase_id)
    roadmap.current_phase_id = phase.id
    db.flush()
    record_roadmap_scope_event(
        db,
        roadmap_id=roadmap.id,
        roadmap_version_id=phase.roadmap_version_id,
        phase_id=phase.id,
        source="current_phase_change",
        reason=(
            f"Current phase changed from "
            f"{previous_phase.stable_key if previous_phase else 'unknown'} "
            f"to {phase.stable_key}"
        ),
    )
    db.commit()
    return {"currentPhaseId": phase.id}


@router.put("/competencies/{competency_identity_id}/status")
async def set_status(
    competency_identity_id: str,
    payload: StatusUpdate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if payload.status == "verified":
        raise AppError(
            422,
            "DIRECT_VERIFIED_FORBIDDEN",
            "Verified status requires a passed verification record.",
        )
    state = transition_status(
        db,
        competency_identity_id,
        payload.status,
        reason=payload.reason,
        source="user",
    )
    db.commit()
    return {"competencyIdentityId": competency_identity_id, "status": state.current_status}


@router.put("/exit-criteria/{criterion_identity_id}")
async def set_exit_criterion_state(
    criterion_identity_id: str,
    payload: ExitCriterionStateUpdate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    criterion = db.get(ExitCriterionIdentity, criterion_identity_id)
    if criterion is None:
        raise AppError(404, "EXIT_CRITERION_NOT_FOUND", "The exit criterion does not exist.")
    criterion.current_state = payload.state
    db.commit()
    return {"id": criterion.id, "state": criterion.current_state}


@router.put("/competencies/{definition_id}/position")
async def set_position(
    definition_id: str,
    payload: dict[str, int],
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    definition = db.get(CompetencyDefinition, definition_id)
    if definition is None:
        raise AppError(404, "COMPETENCY_NOT_FOUND", "The competency does not exist.")
    if set(payload) != {"x", "y"} or not all(isinstance(value, int) for value in payload.values()):
        raise AppError(422, "POSITION_INVALID", "A graph position requires integer x and y values.")
    definition.position_x = payload["x"]
    definition.position_y = payload["y"]
    db.commit()
    return {"definitionId": definition.id, "position": payload}


@router.get("/competencies/{competency_identity_id}/history")
async def competency_history(
    competency_identity_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    events = db.scalars(
        select(CompetencyStatusEvent)
        .where(CompetencyStatusEvent.competency_identity_id == competency_identity_id)
        .order_by(CompetencyStatusEvent.created_at.desc())
    ).all()
    records = db.scalars(
        select(VerificationRecord)
        .where(VerificationRecord.competency_identity_id == competency_identity_id)
        .order_by(VerificationRecord.created_at.desc())
    ).all()
    evidence = db.scalars(
        select(VerificationEvidence).where(
            VerificationEvidence.verification_record_id.in_([item.id for item in records])
        )
    ).all()
    evidence_by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in evidence:
        evidence_by_record[item.verification_record_id].append(
            {
                "id": item.id,
                "kind": item.kind,
                "reference": item.reference,
                "description": item.description,
            }
        )
    return {
        "statusEvents": [
            {
                "id": event.id,
                "fromStatus": event.from_status,
                "toStatus": event.to_status,
                "reason": event.reason,
                "source": event.source,
                "verificationRecordId": event.verification_record_id,
                "createdAt": epoch_ms_to_rfc3339(event.created_at),
            }
            for event in events
        ],
        "verificationRecords": [
            {
                "id": record.id,
                "source": record.verification_source,
                "method": record.method,
                "result": record.result,
                "confidence": record.confidence,
                "reviewerLabel": record.reviewer_label,
                "evidenceSummary": record.evidence_summary,
                "notes": record.notes,
                "createdAt": epoch_ms_to_rfc3339(record.created_at),
                "evidence": evidence_by_record[record.id],
            }
            for record in records
        ],
    }
