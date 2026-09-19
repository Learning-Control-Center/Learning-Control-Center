import { describe, expect, it } from 'vitest'

import type { RoadmapProjection } from './projection'
import { applyRoadmapVisibility, createRoadmapViewModel, prerequisiteClosure } from './viewModel'

const node = (id: string, overrides: Record<string, unknown> = {}) => ({
  id, nodeKey: id, semanticDefinitionId: `definition-${id}`, stableKey: `capability.${id}`,
  title: `Capability ${id}`, profileDomain: { id: 'domain', title: 'Engineering', orderIndex: 0 },
  profileTarget: null, profileTargets: [], capability: { scopes: [] }, canonicalPosition: { x: 0, y: 0 },
  position: { x: 0, y: 0 }, positionSource: 'roadmap-layout/v3.0', presentationParentId: null,
  isCurrent: false, isTargeted: false, isToday: false,
  layoutLane: { id: 'domain', title: 'Engineering', orderIndex: 0 }, ...overrides,
})

const projection: RoadmapProjection = {
  configured: true, authority: 'v2_projection',
  nodes: [node('a'), node('b', { presentationParentId: 'a' }), node('c', {
    isTargeted: true, isToday: true,
    profileTargets: [{ priority: 'core', targetLevelOrdinal: 3, targetLevelTitle: 'Independent' }],
  })],
  edges: [
    { id: 'ab', edgeType: 'prerequisite', source: 'a', target: 'b', visibleByDefault: false, eligibilityAuthority: true, satisfaction: { aggregate_state: 'met', unknown_reasons: [] } },
    { id: 'bc', edgeType: 'prerequisite', source: 'b', target: 'c', visibleByDefault: false, eligibilityAuthority: true, satisfaction: { aggregate_state: 'unknown', unknown_reasons: ['CAPABILITY_UNKNOWN'] } },
  ],
}

const state = { query: '', targetedOnly: false, todayOnly: false, attentionOnly: false, showPrerequisites: false, collapsedLanes: new Set<string>(), collapsedBranches: new Set<string>(), focusedNodeId: null }

describe('Roadmap journey view model', () => {
  it('retains explicit Unknown and target facts without calculating capability truth', () => {
    expect(createRoadmapViewModel(projection).nodeById.get('c')).toMatchObject({ targetLabel: 'Independent', capabilityLabel: 'Current capability unknown', blockedLabel: 'Prerequisite status unknown', isToday: true })
  })

  it('builds bounded multi-target summaries without reducing per-dimension truth', () => {
    const multiTarget: RoadmapProjection = {
      configured: true,
      authority: 'v2_projection',
      nodes: [node('multi', {
        profileTargets: [
          { id: 'speaking-target', priority: 'critical', dimensionId: 'speaking', dimensionTitle: 'Speaking', targetLevelOrdinal: 3, targetLevelTitle: 'B1', comparisonStateReference: { snapshotId: 'snapshot', gapStableKey: 'speaking-gap', comparisonStatus: 'met' } },
          { id: 'reading-target', priority: 'important', dimensionId: 'reading', dimensionTitle: 'Reading', targetLevelOrdinal: 4, targetLevelTitle: 'B2', comparisonStateReference: { snapshotId: 'snapshot', gapStableKey: 'reading-gap', comparisonStatus: 'unknown' } },
        ],
        capability: { scopes: [
          { scopeKey: 'dimension:speaking', assessmentStatus: 'assessed', confidence: 'high', freshness: 'current', reviewDue: false, levelTitle: 'B1' },
          { scopeKey: 'dimension:reading', assessmentStatus: 'unknown', confidence: 'unknown', freshness: 'unknown', reviewDue: true },
        ] },
      })],
      edges: [],
    }
    const item = createRoadmapViewModel(multiTarget).nodes[0]
    expect(item).toMatchObject({
      currentSummary: '2 target dimensions · 1 known · 1 Unknown',
      targetSummary: '2 target levels · 1 reached · 0 gaps',
      targetStatusSummary: '2 targets · 1 review due',
      needsAttention: true,
      hasUnknown: true,
    })
    expect(item.targetRows.map((target) => [target.dimension, target.current, target.target])).toEqual([
      ['Speaking', 'B1', 'B1'],
      ['Reading', 'Current capability unknown', 'B2'],
    ])
    expect(applyRoadmapVisibility(createRoadmapViewModel(multiTarget), { ...state, attentionOnly: true }).nodes).toHaveLength(1)
  })

  it('computes the complete hard-prerequisite closure', () => {
    expect([...prerequisiteClosure('c', projection.edges ?? [])].sort()).toEqual(['a', 'b', 'c'])
  })

  it('temporarily reveals a focused path after filters and collapse with no dangling edges', () => {
    const model = createRoadmapViewModel(projection)
    const visible = applyRoadmapVisibility(model, { ...state, targetedOnly: true, collapsedLanes: new Set(['domain']), collapsedBranches: new Set(['a']), focusedNodeId: 'c' })
    expect(visible.nodes.map((item) => item.id)).toEqual(['a', 'b', 'c'])
    expect(visible.edges.map((item) => item.id)).toEqual(['ab', 'bc'])
    expect(visible.edges.every((edge) => visible.nodes.some((item) => item.id === edge.source) && visible.nodes.some((item) => item.id === edge.target))).toBe(true)
  })

  it('applies search, Today, attention, and branch filters deterministically', () => {
    const model = createRoadmapViewModel(projection)
    expect(applyRoadmapVisibility(model, { ...state, query: 'capability c' }).nodes.map((item) => item.id)).toEqual(['c'])
    expect(applyRoadmapVisibility(model, { ...state, todayOnly: true }).nodes.map((item) => item.id)).toEqual(['c'])
    expect(applyRoadmapVisibility(model, { ...state, attentionOnly: true }).nodes.map((item) => item.id)).toEqual(['c'])
    expect(applyRoadmapVisibility(model, { ...state, collapsedBranches: new Set(['a']) }).nodes.map((item) => item.id)).toEqual(['a', 'c'])
  })
})
