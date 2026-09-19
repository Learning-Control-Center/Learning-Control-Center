import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ProfileCapabilityPage } from './pages/ProfileCapabilityPage'

const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })

describe('Profile and capability context', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/api/v2/target-profiles')) {
          return json([
            {
              id: 'profile-1',
              stableKey: 'backend-profile',
              activeVersionId: 'version-1',
              versions: [
                {
                  versionId: 'version-1',
                  version: 2,
                  title: 'Backend engineer',
                  description: 'Target context',
                  targets: [
                    {
                      id: 'target-1',
                      stableKey: 'python-delivery',
                      competencyIdentityId: 'competency-1',
                      dimensionKey: null,
                      domainStableKey: 'engineering',
                      targetLevelStableKey: 'independent',
                      priority: 'core',
                    },
                  ],
                },
              ],
            },
          ])
        }
        if (url.endsWith('/api/v2/capabilities/competency-1')) {
          return json({
            competencyIdentityId: 'competency-1',
            states: [
              {
                scopeKey: 'overall',
                dimensionKey: null,
                capabilityLevelKey: 'guided',
                assessmentStatus: 'assessed',
                aggregateConfidence: 'medium',
                freshness: 'stale',
                reviewDue: true,
              },
            ],
          })
        }
        return json({ error: { code: 'NOT_FOUND', message: url } })
      }),
    )
  })

  it('keeps target intent distinct from current capability state', async () => {
    render(<ProfileCapabilityPage />)

    expect(await screen.findByRole('heading', { name: 'Profile & Capability' })).toBeInTheDocument()
    expect(screen.getByText(/Backend engineer · immutable version 2/)).toBeInTheDocument()
    expect(screen.getByText('independent')).toBeInTheDocument()
    expect(screen.getByText('Current: guided')).toBeInTheDocument()
    expect(screen.getByText(/Confidence medium · freshness stale · review due/)).toBeInTheDocument()
  })
})
