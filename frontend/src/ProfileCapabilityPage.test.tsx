import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

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
                    {
                      id: 'target-2', stableKey: 'python-testing', competencyIdentityId: 'competency-1', dimensionKey: 'testing', domainStableKey: 'engineering', targetLevelStableKey: 'independent', priority: 'supporting',
                    },
                  ],
                  readinessGates: [
                    { id: 'gate-met', stableKey: 'met', title: 'Portfolio ready', effect: 'display_only', orderIndex: 0, milestoneStableKey: null, targetStableKeys: [] },
                    { id: 'gate-not-met', stableKey: 'not-met', title: 'Review incomplete', effect: 'blocks_readiness', orderIndex: 1, milestoneStableKey: null, targetStableKeys: [] },
                    { id: 'gate-unknown', stableKey: 'unknown', title: 'Evidence pending', effect: 'display_only', orderIndex: 2, milestoneStableKey: null, targetStableKeys: [] },
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
        if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [{ id: 'competency-1', semanticDefinitionId: 'semantic-1', stableKey: 'python', title: 'Python delivery', profileTargets: [{ id: 'target-1', dimensionKey: null, targetLevelTitle: 'Independent', priority: 'core', targetLevelOrdinal: 3 }, { id: 'target-2', dimensionKey: 'testing', dimensionTitle: 'Testing', targetLevelTitle: 'Independent', priority: 'supporting', targetLevelOrdinal: 3 }], capability: { scopes: [{ scopeKey: 'overall', levelTitle: 'Guided', levelKey: 'guided', assessmentStatus: 'assessed', confidence: 'medium', freshness: 'stale', reviewDue: true }, { scopeKey: 'dimension:testing', levelTitle: null, levelKey: null, assessmentStatus: 'unassessed', confidence: 'unknown', freshness: 'unknown', reviewDue: false }] } }], edges: [] })
        if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
        if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
        if (url.endsWith('/api/v2/analysis/current')) return json({ configured: true, snapshot: { normalizedFacts: [{ fact_type: 'target_state', payload: { readinessGates: [{ gateId: 'gate-met', state: 'met' }, { gateId: 'gate-not-met', state: 'not_met' }, { gateId: 'gate-unknown', state: 'unknown' }] } }] } })
        return json({ error: { code: 'NOT_FOUND', message: url } })
      }),
    )
  })

  it('keeps target intent distinct from current capability state', async () => {
    render(<MemoryRouter><ProfileCapabilityPage /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Profile' })).toBeInTheDocument()
    expect(screen.getByText(/Backend engineer · immutable version 2/)).toBeInTheDocument()
    expect(screen.getAllByText('Independent')).toHaveLength(2)
    expect(screen.getByText('Current: Guided')).toBeInTheDocument()
    expect(screen.getByText('Confidence medium')).toBeInTheDocument()
    expect(screen.getByText('Freshness stale')).toBeInTheDocument()
    expect(screen.getByText('Current: Unassessed')).toBeInTheDocument()
    expect(screen.getByText('Portfolio ready')).toBeInTheDocument()
    expect(screen.getByText('Review incomplete')).toBeInTheDocument()
    expect(screen.getByText('Evidence pending')).toBeInTheDocument()
  })

  it('keeps a prerequisite-path competency detail reachable with Evidence, criteria, history, and lineage', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/target-profiles')) return json([{ id: 'profile-1', stableKey: 'profile', activeVersionId: 'version-1', versions: [{ versionId: 'version-1', version: 1, title: 'Engineer', description: '', targets: [] }] }])
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [{ id: 'path-1', semanticDefinitionId: 'definition-1', stableKey: 'path', title: 'Path prerequisite', capability: { scopes: [{ scopeKey: 'overall', levelTitle: 'Familiar', levelKey: 'familiar', assessmentStatus: 'assessed', confidence: 'medium', freshness: 'current', reviewDue: false }] } }], edges: [] })
      if (url.endsWith('/api/v2/capabilities/path-1')) return json({ competencyIdentityId: 'path-1', states: [{ scopeKey: 'overall', dimensionKey: null, capabilityLevelKey: 'familiar', assessmentStatus: 'assessed', aggregateConfidence: 'medium', freshness: 'current', reviewDue: false, reviewReasons: [], lastMeaningfulEvidenceAt: '2026-01-01T00:00:00Z' }] })
      if (url.includes('/api/v2/capabilities/path-1/history')) return json({ runs: [{ id: 'run-1', scopeKey: 'overall', cutoffAt: '2026-01-02T00:00:00Z', assessmentStatus: 'unassessed', aggregateConfidence: 'unknown', decisiveEvidenceIds: ['evidence-1'], reasons: ['insufficient_coverage'], criterionPolicyVersion: 'criterion/v1', capabilityPolicyVersion: 'capability/v1', evidencePolicyVersion: 'evidence/v1', downgradePolicyVersion: 'downgrade/v1', outputHash: 'hash' }], events: [], criterionResults: [{ id: 'result-1', runId: 'run-1', criterionDefinitionId: 'criterion-1', state: 'unknown', decisiveEvidenceIds: ['evidence-1'] }], reviewEvents: [] })
      if (url.endsWith('/api/v2/competencies/path-1/definitions')) return json([{ id: 'definition-1', definitionVersion: 2, title: 'Path prerequisite', description: '', scope: '', effectiveAt: '2026-01-01T00:00:00Z', supersedesDefinitionId: 'definition-0', scaleStableKey: 'technical', scaleVersion: 'v1', criteria: [{ id: 'criterion-1', stableKey: 'criterion', levelStableKey: 'familiar', dimensionKey: null, requirementType: 'required', description: 'Explain the prerequisite' }] }])
      if (url.includes('/api/v2/evidence?')) return json({ items: [{ id: 'evidence-1', title: 'Reviewed explanation', description: null, evidenceType: 'verification', sourceType: 'verification_record', strength: 'moderate', independence: 'independent', sourceConfidence: 'high', occurredAt: '2026-01-01T00:00:00Z', createdAt: '2026-01-01T00:00:00Z', policyVersion: 'evidence/v1', retracted: false, invalidated: false, redacted: false, links: [{ id: 'link-1', competencyIdentityId: 'path-1', effect: 'supports', relevance: 'primary', retracted: true }] }] })
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      if (url.endsWith('/api/v2/analysis/current')) return json({ configured: false, snapshot: null })
      return json({})
    }))
    render(<MemoryRouter initialEntries={['/profile/competencies/path-1']}><Routes><Route path="/profile/competencies/:competencyIdentityId" element={<ProfileCapabilityPage />} /></Routes></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Path prerequisite', level: 1 })).toBeInTheDocument()
    expect(screen.getByText(/prerequisite or supporting capability/)).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Evidence history' })).toBeInTheDocument()
    expect(screen.getAllByText('Current: Familiar').length).toBeGreaterThan(0)
    expect(screen.queryByText('Current: familiar')).not.toBeInTheDocument()
    expect(screen.getByText('Reviewed explanation')).toBeInTheDocument()
    expect(screen.getByText('Evidence record active')).toBeInTheDocument()
    expect(screen.getByText('Competency attribution retracted')).toBeInTheDocument()
    expect(screen.getByText('Explain the prerequisite')).toBeInTheDocument()
    expect(screen.getByText(/Evaluation cutoff/)).toBeInTheDocument()
    expect(screen.getByText('Technical lineage')).toBeInTheDocument()
  })
})
