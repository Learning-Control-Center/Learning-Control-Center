import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AnalysisPage } from './features/insights/AnalysisPage'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const snapshot = {
  id: 'snapshot-current',
  runId: 'run-current',
  purpose: 'learning_control',
  generatedAt: 1_800_000_000_000,
  cutoffAt: 1_800_000_000_001,
  cutoffSemantics: 'exclusive',
  completedThroughDate: '2027-01-14',
  completeness: 'partial',
  inputHash: 'input-hash',
  outputHash: 'output-hash',
  policyVersions: { analysis: 'analysis-policy/v3.0' },
  lineage: {
    analysis_algorithm_version: 'analysis-algorithm/v3.0',
    analysis_policy_version: 'analysis-policy/v3.0',
    normalization_schema_version: 'analysis-normalization/v3.0',
  },
  gaps: [
    {
      targetIdentityId: 'target-1',
      competencyIdentityId: 'competency-1',
      dimensionKey: null,
      comparisonStatus: 'below_target',
      severity: 'high',
      reasonCodes: ['REQUIRED_CAPABILITY_GAP'],
    },
  ],
  normalizedFacts: [
    {
      stable_key: 'target|target-1',
      fact_type: 'target_state',
      subject_type: 'profile_target',
      subject_id: 'target-1',
      payload: { confidence: 'low', freshness: 'aging', reviewDue: true, readinessGates: [{ gateId: 'gate-1', predicates: [{ predicate_id: 'predicate-1' }] }] },
    },
    { stable_key: 'domain|domain-1', fact_type: 'allocation_state', subject_type: 'profile_domain', subject_id: 'domain-1', payload: { allocation: 'balanced' } },
    { stable_key: 'unit|unit-1', fact_type: 'curriculum_state', subject_type: 'curriculum_unit', subject_id: 'unit-1', payload: { readiness: 'ready' } },
    { stable_key: 'task|task-1', fact_type: 'project_state', subject_type: 'project_task', subject_id: 'task-1', payload: { readiness: 'ready' } },
  ],
  signals: [
    {
      stableKey: 'CAPABILITY_GAP|competency|competency-1|overall',
      type: 'CAPABILITY_GAP',
      subject: { subjectType: 'competency', subjectId: 'competency-1', dimensionKey: null },
      severity: 'high',
      reasonCodes: ['REQUIRED_CAPABILITY_GAP'],
      decisiveFacts: { comparisonStatus: 'below_target' },
    },
    {
      stableKey: 'READINESS_BLOCK|readiness_gate|gate-1|overall',
      type: 'READINESS_BLOCK',
      subject: { subjectType: 'readiness_gate', subjectId: 'gate-1', dimensionKey: null },
      severity: 'high',
      reasonCodes: ['READINESS_GATE_NOT_MET'],
      decisiveFacts: { state: 'not_met' },
    },
  ],
  unknownMarkers: [
    {
      fieldPath: 'learningGraph',
      subjectType: 'learning_graph',
      subjectId: 'active',
      reasonCode: 'LEARNING_GRAPH_MISSING',
    },
    {
      fieldPath: 'futureInput',
      subjectType: 'readiness_predicate',
      subjectId: 'predicate-1',
      reasonCode: 'FUTURE_REASON_CODE',
    },
    ...Array.from({ length: 4 }, (_, index) => ({
      fieldPath: `futureInput${index + 2}`,
      subjectType: 'readiness_predicate',
      subjectId: `predicate-${index + 2}`,
      reasonCode: 'FUTURE_REASON_CODE',
    })),
  ],
}

describe('Analysis V3 diagnostic surface', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/api/v2/analysis/current')) {
          return json({ configured: true, status: 'stale', snapshot })
        }
        if (url.endsWith('/api/v2/analysis/history')) {
          return json([
            {
              id: snapshot.id,
              runId: snapshot.runId,
              purpose: snapshot.purpose,
              generatedAt: snapshot.generatedAt,
              cutoffAt: snapshot.cutoffAt,
              completeness: snapshot.completeness,
              outputHash: snapshot.outputHash,
            },
            {
              id: 'snapshot-old',
              runId: 'run-old',
              purpose: 'candidate_readiness',
              generatedAt: 1_700_000_000_000,
              cutoffAt: 1_700_000_000_001,
              completeness: 'complete',
              outputHash: 'old-output',
            },
          ])
        }
        if (url.endsWith('/api/v2/analysis/snapshots/snapshot-old')) {
          return json({
            ...snapshot,
            id: 'snapshot-old',
            purpose: 'candidate_readiness',
            completeness: 'complete',
            signals: [],
            gaps: [],
            unknownMarkers: [],
          })
        }
        if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [{ id: 'competency-1', semanticDefinitionId: 'definition-1', title: 'Deterministic delivery', capability: { scopes: [] }, profileTargets: [{ id: 'target-1' }] }], edges: [] })
        if (url.endsWith('/api/v2/target-profiles')) return json([{ versions: [{ domains: [{ id: 'domain-1', title: 'Systems engineering' }], readinessGates: [{ id: 'gate-1', title: 'Production readiness' }] }] }])
        if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [{ unitDefinitionId: 'unit-1', title: 'Policy practice', curriculumId: 'curriculum-1' }] })
        if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [{ task_definition_id: 'task-1', title: 'Ship deterministic workflow', project_id: 'project-1' }] })
        throw new Error(`Unexpected URL: ${url}`)
      }),
    )
  })

  it('shows current diagnosis and inspects immutable history without generating', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><AnalysisPage /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Analysis' })).toBeInTheDocument()
    expect(await screen.findByText('CAPABILITY GAP')).toBeInTheDocument()
    expect((await screen.findAllByText('Unknown — details unavailable')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Current capability is below a required target').length).toBeGreaterThan(0)
    expect(screen.getByText('No active Learning Graph is available')).toBeInTheDocument()
    expect(screen.getAllByText('Deterministic delivery').length).toBeGreaterThan(0)
    expect(screen.getByText('Production readiness')).toBeInTheDocument()
    expect(screen.getByText('Production readiness requirement')).toBeInTheDocument()
    expect(screen.getByText('Normalized diagnostic facts')).toBeInTheDocument()
    expect(screen.getByText('Show 2 more Unknown inputs')).toBeInTheDocument()
    expect(screen.getByText(/target state · deterministic delivery target/i)).toBeInTheDocument()
    expect(screen.getByText(/allocation state · systems engineering/i)).toBeInTheDocument()
    expect(screen.getByText(/curriculum state · policy practice/i)).toBeInTheDocument()
    expect(screen.getByText(/project state · ship deterministic workflow/i)).toBeInTheDocument()
    expect(screen.getByText(/"comparisonStatus": "below_target"/)).toBeInTheDocument()
    expect(screen.getByText('stale')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /candidate readiness/i }))
    expect(await screen.findByText('No diagnostic signals in this snapshot.')).toBeInTheDocument()
    expect(screen.getAllByText(/historical snapshot/i)).not.toHaveLength(0)
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/analysis/runs'),
      expect.anything(),
    )
  })

  it('explicitly generates a current Analysis snapshot without requiring a raw ID', async () => {
    let currentCalls = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/analysis/current')) {
        currentCalls += 1
        return json({ configured: true, status: currentCalls > 1 ? 'current' : 'stale', snapshot })
      }
      if (url.endsWith('/api/v2/analysis/history')) return json([{ id: snapshot.id, runId: snapshot.runId, purpose: snapshot.purpose, generatedAt: snapshot.generatedAt, cutoffAt: snapshot.cutoffAt, completeness: snapshot.completeness, outputHash: snapshot.outputHash }])
      if (url.endsWith('/api/v2/analysis/runs') && init?.method === 'POST') return json(snapshot)
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
      if (url.endsWith('/api/v2/target-profiles')) return json([])
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<MemoryRouter><AnalysisPage /></MemoryRouter>)

    await userEvent.click(await screen.findByRole('button', { name: 'Generate current Analysis' }))
    expect(await screen.findByText('A current deterministic Analysis snapshot was generated explicitly.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('current')).toBeInTheDocument())
    expect(fetch).toHaveBeenCalledWith('/api/v2/analysis/runs', expect.objectContaining({ method: 'POST' }))
  })

  it('keeps the authoritative generated snapshot when its follow-up refresh fails', async () => {
    let currentCalls = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/analysis/current')) {
        currentCalls += 1
        return currentCalls === 1
          ? json({ configured: true, status: 'missing', snapshot: null })
          : json({ error: { code: 'UNAVAILABLE', message: 'Refresh failed after Analysis generation.' } }, 503)
      }
      if (url.endsWith('/api/v2/analysis/history')) return json([])
      if (url.endsWith('/api/v2/analysis/runs') && init?.method === 'POST') return json(snapshot)
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
      if (url.endsWith('/api/v2/target-profiles')) return json([])
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<MemoryRouter><AnalysisPage /></MemoryRouter>)

    await userEvent.click(await screen.findByRole('button', { name: 'Generate Analysis' }))
    expect(await screen.findByText('Refresh failed after Analysis generation.')).toBeInTheDocument()
    expect(screen.getByText('CAPABILITY GAP')).toBeInTheDocument()
    expect(screen.getByText('current')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate current Analysis' })).toBeEnabled()
  })
})
