export type CurriculumCatalogUnit = {
  curriculumId: string
  curriculumStableKey?: string
  curriculumVersionId?: string
  unitDefinitionId: string
  unitStableKey: string
  kind: string
  title: string
  description: string
  orderIndex?: number
  status?: string
  durationRangeMs: [number, number, number] | null
  action?: { instructions?: string | null; resourceReference?: string | null; verificationMethod?: string | null }
  targets?: { semanticDefinitionId: string; intendedLearningOutcome: string; role: string }[]
  requirements: { stableKey: string; requirementType: string; effect: string }[]
  evidenceOpportunities: { stableKey: string; evidenceKind: string; requiresActualActivity?: boolean; requiresArtifact?: boolean }[]
}

export type CurriculumAvailability = {
  availabilityState: 'met' | 'not_met' | 'unknown'
  readinessState: 'met' | 'not_met' | 'unknown'
  candidateUsabilityState: 'met' | 'not_met' | 'unknown'
  requirements: { stableKey: string; state: string; reasonCode: string }[]
  targetSuitability: { targetId: string; semanticDefinitionId?: string; state: string; reasonCode: string }[]
}

export type ProjectCatalogCandidateApi = {
  project_id: string
  project_version_id?: string
  task_definition_id: string
  task_identity_id: string
  task_stable_key: string
  title: string
  lifecycle_state: string
  availability_state: string
  readiness_state: string
  candidate_usability_state: string
  actionable_blocker_keys: string[]
  duration_range_ms: [number, number, number] | null
  target_facts?: { semantic_definition_id?: string; semanticDefinitionId?: string }[]
}

export type ProjectCatalogCandidate = {
  projectId: string
  projectVersionId?: string
  taskDefinitionId: string
  taskIdentityId: string
  title: string
  lifecycleState: string
  availabilityState: string
  readinessState: string
  usabilityState: string
  blockerKeys: string[]
  durationRangeMs: [number, number, number] | null
  semanticDefinitionIds: string[]
}

export function adaptProjectCatalogCandidate(item: ProjectCatalogCandidateApi): ProjectCatalogCandidate {
  return {
    projectId: item.project_id,
    projectVersionId: item.project_version_id,
    taskDefinitionId: item.task_definition_id,
    taskIdentityId: item.task_identity_id,
    title: item.title,
    lifecycleState: item.lifecycle_state,
    availabilityState: item.availability_state,
    readinessState: item.readiness_state,
    usabilityState: item.candidate_usability_state,
    blockerKeys: item.actionable_blocker_keys,
    durationRangeMs: item.duration_range_ms,
    semanticDefinitionIds: (item.target_facts ?? []).flatMap((target) => target.semanticDefinitionId ?? target.semantic_definition_id ?? []),
  }
}
