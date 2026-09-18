import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RecommendationsV2Page } from './pages/RecommendationsV2Page'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const run = {
  id: 'recommendation-run-1',
  analysisSnapshotId: 'analysis-snapshot-1',
  generatedAt: 1_800_000_000_000,
  cutoffAt: 1_800_000_000_001,
  availableTimeMs: null,
  algorithmVersion: 'recommendation-algorithm/v2.0',
  policyVersions: {
    score: 'recommendation-score-policy/v1',
    duration: 'recommendation-duration-policy/v1',
  },
  inputHash: 'input-hash',
  outputHash: 'output-hash',
  portfolio: [
    {
      candidateId: 'candidate-1',
      stableId: 'curriculum|unit-1',
      candidateType: 'curriculum_unit',
      title: 'Practice deterministic policies',
      description: 'Work through the authored exercise.',
      expectedLearningValue: 'very_high',
      expectedLearningValueReasons: ['REQUIRED_GAP_AND_MISSING_EVIDENCE_MODE'],
      score: 72,
      rank: 1,
      scoreComponents: [
        { code: 'TARGET_PRIORITY', value: 20 },
        { code: 'PRIMARY_NEED', value: 24 },
        { code: 'DEADLINE_PRESSURE', value: 0 },
        { code: 'ALLOCATION_BALANCE', value: 8 },
        { code: 'NEGLECT_OR_STALL', value: 3 },
        { code: 'EXPECTED_LEARNING_VALUE', value: 12 },
        { code: 'CONTEXT_COST', value: -2 },
      ],
      portfolioRole: 'primary',
      decisionReason: 'SELECTED_PRIMARY',
      durationRangeMs: [600_000, 1_200_000, 1_800_000],
      advisoryDurationMs: 1_200_000,
      durationReason: null,
      reasons: [
        {
          code: 'SELECTED_PRIMARY',
          title: 'Selected as Primary',
          renderedText: 'Selected as Primary.',
          scoreContribution: null,
        },
      ],
    },
  ],
}

describe('Recommendation V2 minimum surface', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/api/v2/recommendations/history')) return json([run])
        if (url.endsWith('/api/v2/analysis/current')) {
          return json({ configured: true, status: 'current', snapshot: { id: 'analysis-snapshot-1' } })
        }
        if (url.endsWith('/api/v2/recommendations/runs') && init?.method === 'POST') {
          const body = JSON.parse(String(init.body)) as { available_time_ms: number | null }
          expect(body.available_time_ms).toBeNull()
          return json({ ...run, id: 'recommendation-run-2' }, 201)
        }
        throw new Error(`Unexpected URL: ${url}`)
      }),
    )
  })

  it('shows policy explanations and generates a new unknown-window run explicitly', async () => {
    const user = userEvent.setup()
    render(<RecommendationsV2Page />)

    expect(await screen.findByRole('heading', { name: 'Recommendation V2' })).toBeInTheDocument()
    expect(await screen.findByText('Practice deterministic policies')).toBeInTheDocument()
    expect(screen.getByText('Unknown — no total-window constraint')).toBeInTheDocument()
    expect(screen.getByText('recommendation-score-policy/v1')).toBeInTheDocument()
    expect(screen.getByText('+0')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Generate new run' }))
    expect(await screen.findByText('Practice deterministic policies')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith(
      '/api/v2/recommendations/runs',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
