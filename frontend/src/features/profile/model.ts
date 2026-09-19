import type { RoadmapProfileTarget, RoadmapProjectionNode } from '../../shared/contracts/roadmapProjection'

export type ProfileTarget = { id: string; stableKey: string; competencyIdentityId: string; dimensionKey: string | null; domainStableKey: string; targetLevelStableKey: string; priority: string; targetDate?: string | null; targetMonth?: string | null }
export type ProfileDomain = { stableKey: string; title: string; description: string; orderIndex: number; minimumPercent?: number | null; maximumPercent?: number | null }
export type Milestone = { id: string; stableKey: string; title: string; description: string; targetDate: string | null; orderIndex: number; targetStableKeys: string[] }
export type ReadinessGate = { id: string; stableKey: string; title: string; effect: string; orderIndex: number; milestoneStableKey: string | null; targetStableKeys: string[] }
export type ProfileVersion = { versionId: string; version: number; title: string; description: string; targetHorizon?: string | null; targets: ProfileTarget[]; domains?: ProfileDomain[]; milestones?: Milestone[]; readinessGates?: ReadinessGate[] }
export type ProfileSummary = { id: string; stableKey: string; activeVersionId: string | null; versions: ProfileVersion[] }

export type CapabilityState = {
  scopeKey: string
  semanticDefinitionId?: string
  dimensionKey: string | null
  capabilityLevelId?: string | null
  capabilityLevelKey: string | null
  levelTitle?: string | null
  assessmentStatus: string
  aggregateConfidence: string
  freshness: string
  reviewDue: boolean
  reviewReasons?: string[]
  lastMeaningfulEvidenceAt?: string | null
  lastEvaluatedAt?: string
  evaluationRunId?: string
}
export type Capability = { competencyIdentityId: string; states: CapabilityState[]; readinessFloor?: { label: string; levelKey: string } | null }
export type CapabilityHistory = {
  runs: { id: string; scopeKey: string; cutoffAt: string; assessmentStatus: string; aggregateConfidence: string; decisiveEvidenceIds: string[]; reasons: string[]; criterionPolicyVersion: string; capabilityPolicyVersion: string; evidencePolicyVersion: string; downgradePolicyVersion: string; outputHash: string }[]
  events: { id: string; scopeKey: string; sequence: number; causeCode: string; evaluationRunId: string }[]
  criterionResults: { id: string; runId: string; criterionDefinitionId: string; state: string; decisiveEvidenceIds: string[] }[]
  reviewEvents: { id: string; scopeKey: string; sequence: number; newFreshness: string; newReviewDue: boolean; reasonCodes: string[]; freshnessPolicyVersion: string; evaluationRunId: string }[]
}
export type SemanticDefinition = { id: string; definitionVersion: number; title: string; description: string; scope: string; effectiveAt: string; supersedesDefinitionId: string | null; scaleStableKey: string; scaleVersion: string; criteria: { id: string; stableKey: string; levelStableKey: string; dimensionKey: string | null; requirementType: string; description: string }[] }
export type EvidenceLink = { id: string; competencyIdentityId: string; criterionIdentityId?: string | null; criterionDefinitionId?: string | null; effect: string; relevance: string; retracted: boolean; retraction?: { reason: string; createdAt: string } | null }
export type EvidenceRecord = { id: string; title: string; description: string | null; evidenceType: string; sourceType: string; strength: string | null; strengthUnknownReason?: string | null; independence: string | null; independenceUnknownReason?: string | null; sourceConfidence: string | null; sourceConfidenceUnknownReason?: string | null; occurredAt: string | null; occurredAtUnknownReason?: string | null; createdAt: string; policyVersion: string; retracted: boolean; invalidated: boolean; redacted: boolean; links: EvidenceLink[] }

export function projectionTargetFor(node: RoadmapProjectionNode | undefined, target: ProfileTarget): RoadmapProfileTarget | undefined {
  return (node?.profileTargets ?? (node?.profileTarget ? [node.profileTarget] : [])).find((item) =>
    (item.id === target.id || item.identityId === target.id || item.stableKey === target.stableKey)
    && (item.dimensionKey ?? null) === target.dimensionKey,
  )
}

export function projectionCapability(node: RoadmapProjectionNode | undefined, dimensionKey: string | null): CapabilityState | null {
  const scope = node?.capability?.scopes.find((item) => dimensionKey ? item.scopeKey === `dimension:${dimensionKey}` : item.scopeKey === 'overall')
  if (!scope) return null
  return {
    scopeKey: scope.scopeKey,
    dimensionKey,
    capabilityLevelKey: scope.levelKey ?? null,
    levelTitle: scope.levelTitle,
    assessmentStatus: scope.assessmentStatus,
    aggregateConfidence: scope.confidence,
    freshness: scope.freshness,
    reviewDue: Boolean(scope.reviewDue),
  }
}
