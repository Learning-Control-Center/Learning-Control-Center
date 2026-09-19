export type CanonicalLearningAuthority = 'legacy_v1' | 'v2'
export type PresentationAuthority = 'legacy_v1' | 'v2' | 'v1_read_only'

export type AuthorityState = {
  canonicalLearningAuthority: CanonicalLearningAuthority
  recommendationPresentation: PresentationAuthority
  roadmapPresentation: PresentationAuthority
  todayPresentation: PresentationAuthority
  eventSequence: number
  stateHash: string
  updatedAt: string
  policyVersion: string
}
