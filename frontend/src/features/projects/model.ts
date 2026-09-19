export type Project = { id: string; stableKey: string; activeVersionId: string | null; lifecycleState: string }
export type ProjectTask = { id: string; identityId: string; stableKey: string; title: string; description: string; preferredDurationMs: number | null }
export type ProjectCriterion = { id: string; stableKey: string; title: string; description?: string }
export type ProjectTarget = { taskDefinitionId: string | null; projectCriterionDefinitionId?: string | null; semanticDefinitionId: string; intendedOutcome: string; role: string }
export type ProjectVersion = { id: string; version: number; title: string; description: string; effectiveAt: string; tasks: ProjectTask[]; criteria: ProjectCriterion[]; targets?: ProjectTarget[] }
export type ProjectEvent = { id: string; eventType: string; taskIdentityId: string | null; taskLifecycleState: string | null; blockerKey: string | null; occurredAt: string }
export type Evaluation = { id: string; state: string; evaluatedAt: string; evidenceIds: string[] }
