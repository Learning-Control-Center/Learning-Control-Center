import type { AuthorityState, PresentationAuthority } from '../../shared/contracts/authority'

export type AuthoritySurface = 'today' | 'roadmap' | 'recommendation'
export type AuthorityDestination = 'legacy_live' | 'v2' | 'legacy_read_only'

function presentationFor(state: AuthorityState, surface: AuthoritySurface): PresentationAuthority {
  if (surface === 'today') return state.todayPresentation
  if (surface === 'roadmap') return state.roadmapPresentation
  return state.recommendationPresentation
}

export function resolveAuthorityDestination(state: AuthorityState, surface: AuthoritySurface): AuthorityDestination {
  const presentation = presentationFor(state, surface)
  if (state.canonicalLearningAuthority === 'legacy_v1') {
    if (presentation !== 'legacy_v1') throw new Error('Invalid legacy authority presentation state.')
    return 'legacy_live'
  }
  if (presentation === 'v2') return 'v2'
  if (presentation === 'v1_read_only') return 'legacy_read_only'
  throw new Error('Invalid V2 authority presentation state.')
}
