import { describe, expect, it } from 'vitest'

import type { AuthorityState, PresentationAuthority } from '../../shared/contracts/authority'
import {
  resolveAuthorityDestination,
  type AuthorityDestination,
  type AuthoritySurface,
} from './authorityResolver'

const surfaces: readonly AuthoritySurface[] = ['today', 'roadmap', 'recommendation']

function authorityState(
  canonical: AuthorityState['canonicalLearningAuthority'],
  today: PresentationAuthority,
  roadmap: PresentationAuthority,
  recommendation: PresentationAuthority,
): AuthorityState {
  return {
    canonicalLearningAuthority: canonical,
    todayPresentation: today,
    roadmapPresentation: roadmap,
    recommendationPresentation: recommendation,
    eventSequence: 1,
    stateHash: 'a'.repeat(64),
    updatedAt: '2026-09-19T00:00:00.000Z',
    policyVersion: 'learning-control-authority-policy/v1',
  }
}

describe('authority route resolver', () => {
  it('resolves the complete legacy authority row to live legacy surfaces', () => {
    const state = authorityState('legacy_v1', 'legacy_v1', 'legacy_v1', 'legacy_v1')
    for (const surface of surfaces) {
      expect(resolveAuthorityDestination(state, surface)).toBe('legacy_live')
    }
  })

  it('resolves every independent valid V2 presentation combination', () => {
    const presentations = ['v2', 'v1_read_only'] as const
    const expected: Record<(typeof presentations)[number], AuthorityDestination> = {
      v2: 'v2',
      v1_read_only: 'legacy_read_only',
    }

    for (const today of presentations) {
      for (const roadmap of presentations) {
        for (const recommendation of presentations) {
          const state = authorityState('v2', today, roadmap, recommendation)
          expect(resolveAuthorityDestination(state, 'today')).toBe(expected[today])
          expect(resolveAuthorityDestination(state, 'roadmap')).toBe(expected[roadmap])
          expect(resolveAuthorityDestination(state, 'recommendation')).toBe(expected[recommendation])
        }
      }
    }
  })

  it.each(['v2', 'v1_read_only'] as const)(
    'fails closed when legacy canonical authority selects %s presentation',
    (invalidPresentation) => {
      for (const surface of surfaces) {
        const presentations: Record<AuthoritySurface, PresentationAuthority> = {
          today: 'legacy_v1',
          roadmap: 'legacy_v1',
          recommendation: 'legacy_v1',
        }
        presentations[surface] = invalidPresentation
        const state = authorityState(
          'legacy_v1',
          presentations.today,
          presentations.roadmap,
          presentations.recommendation,
        )
        expect(() => resolveAuthorityDestination(state, surface)).toThrow(
          'Invalid legacy authority presentation state.',
        )
      }
    },
  )

  it('fails closed when V2 canonical authority selects a live legacy presentation', () => {
    for (const surface of surfaces) {
      const presentations: Record<AuthoritySurface, PresentationAuthority> = {
        today: 'v2',
        roadmap: 'v2',
        recommendation: 'v2',
      }
      presentations[surface] = 'legacy_v1'
      const state = authorityState(
        'v2',
        presentations.today,
        presentations.roadmap,
        presentations.recommendation,
      )
      expect(() => resolveAuthorityDestination(state, surface)).toThrow(
        'Invalid V2 authority presentation state.',
      )
    }
  })
})
