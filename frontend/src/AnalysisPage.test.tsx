import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AnalysisPage } from './pages/AnalysisPage'

const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    status: 200,
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
      payload: { confidence: 'low', freshness: 'aging', reviewDue: true },
    },
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
  ],
  unknownMarkers: [
    {
      fieldPath: 'learningGraph',
      subjectType: 'learning_graph',
      subjectId: 'active',
      reasonCode: 'LEARNING_GRAPH_MISSING',
    },
  ],
}

describe('Analysis V3 read-only surface', () => {
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
        throw new Error(`Unexpected URL: ${url}`)
      }),
    )
  })

  it('shows current diagnosis and inspects immutable history without generating', async () => {
    const user = userEvent.setup()
    render(<AnalysisPage />)

    expect(await screen.findByRole('heading', { name: 'Analysis V3' })).toBeInTheDocument()
    expect(await screen.findByText('CAPABILITY GAP')).toBeInTheDocument()
    expect(await screen.findByText('LEARNING_GRAPH_MISSING')).toBeInTheDocument()
    expect(screen.getByText('Normalized diagnostic facts')).toBeInTheDocument()
    expect(screen.getByText(/target state · target-1/i)).toBeInTheDocument()
    expect(screen.getByText(/"comparisonStatus": "below_target"/)).toBeInTheDocument()
    expect(screen.getByText('stale')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /candidate readiness/i }))
    expect(await screen.findByText('No diagnostic signals in this snapshot.')).toBeInTheDocument()
    expect(screen.getByText('historical snapshot')).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/analysis/runs'),
      expect.anything(),
    )
  })
})
