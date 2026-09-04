export type Status =
  | 'not_started'
  | 'learning'
  | 'practicing'
  | 'ready_for_verification'
  | 'verified'
  | 'needs_review'

export type ExitCriterion = {
  id: string
  stableKey: string
  text: string
  required: boolean
  weight: number | null
  state: 'not_met' | 'partial' | 'met'
}

export type Competency = {
  definitionId: string
  identityId: string
  stableKey: string
  parentDefinitionId: string | null
  title: string
  description: string
  goal: string
  priority: 'core' | 'important' | 'supporting' | 'optional'
  weight: number
  orderIndex: number
  archived: boolean
  position: { x: number | null; y: number | null }
  status: Status
  prerequisites: { identityId: string; stableKey: string; kind: 'required' | 'recommended' }[]
  mustUnderstand: string[]
  mustBeAbleTo: string[]
  exitCriteria: ExitCriterion[]
}

export type Track = {
  id: string
  stableKey: string
  title: string
  description: string
  orderIndex: number
  competencies: Competency[]
}

export type Phase = {
  id: string
  stableKey: string
  title: string
  description: string
  orderIndex: number
  archived: boolean
  isCurrent: boolean
  tracks: Track[]
}

export type Roadmap = {
  id: string
  stableKey: string
  title: string
  description: string
  activeVersion: { id: string; version: string; schemaVersion: number }
  currentPhaseId: string
  phases: Phase[]
}

export type Session = {
  id: string
  competencyIdentityId: string | null
  trackId: string | null
  sessionMode: 'manual' | 'timed'
  timedState: 'running' | 'paused' | 'completed' | 'cancelled' | null
  activityType: string
  assistanceMode: string
  startedAt: string
  endedAt: string | null
  accumulatedDurationMs: number
  activeSince: string | null
  durationMs: number | null
  difficulty: number | null
  outcome: string | null
  notes: string | null
}

export type RecommendationItem = {
  competencyIdentityId: string
  stableKey: string
  title: string
  activity: 'learning' | 'independent_practice' | 'review' | 'verification' | 'research'
  suggestedDurationMs: number
  reasonCodes: string[]
  activityReasonCode: string
  explanation: Record<string, unknown>
}

export type Recommendation = {
  recommendationVersion: number
  generatedAt: string
  localDate?: string
  setupRequired: boolean
  guidance?: string
  limitedTelemetry?: boolean
  todayCompletedDurationMs?: number
  todayTargetDurationMs?: number | null
  primary: RecommendationItem | null
  secondary: RecommendationItem | null
  snapshotId?: string
}
