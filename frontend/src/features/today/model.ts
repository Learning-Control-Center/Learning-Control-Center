export type TodayInteraction = { id: string; type: string; sessionId: string | null; correction: { id: string } | null }
export type ActivityRelation = { id: string; activityId: string; type: 'matched' | 'partially_matched' | 'replaced' }
export type TodaySuggestion = {
  id: string
  recommendationRunId: string
  recommendationId: string
  candidateId: string
  portfolioRole: 'primary' | 'complementary' | 'maintenance'
  advisoryDurationMs: number | null
  durationRangeMs: [number, number, number] | null
  presentation: {
    title: string
    description: string
    candidateType: string
    reasonSummary: string
    reasons: { code: string; title?: string; text: string; facts?: unknown }[]
    source?: { type: string; entityId: string; versionId: string | null }
    competencyIdentityId?: string | null
    targetIdentityId?: string | null
    servedTargetIdentityIds?: string[]
  }
  timezone: string
  expiresAt: string
  presentationExpired: boolean
  status: string
  terminal: boolean
  interactions: TodayInteraction[]
  activityRelations: ActivityRelation[]
}
export type TodayGeneration = { id: string; localDate: string; timezone: string; generationSequence: number; generatedAt: string; regeneration: boolean; suggestions: TodaySuggestion[] }
export type CurrentToday = { generation: TodayGeneration | null; continuingStartedSuggestions: TodaySuggestion[] }
