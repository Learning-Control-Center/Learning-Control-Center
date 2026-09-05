import { describe, expect, it } from 'vitest'

import {
  NODE_GAP_Y,
  NODE_HEIGHT,
  PHASE_GAP,
  computeRoadmapLayout,
  mergeLayoutNodes,
  toCanvasPosition,
  toPhaseLocalPosition,
  type CompetencyFlowNode,
} from './components/roadmapLayout'
import type { Competency, Phase, Roadmap, Track } from './types'

let sequence = 0

function competency(partial: Partial<Competency> & { definitionId: string }): Competency {
  sequence += 1
  return {
    identityId: `identity-${partial.definitionId}`,
    stableKey: `key.${partial.definitionId}`,
    parentDefinitionId: null,
    title: `Competency ${partial.definitionId}`,
    description: '',
    goal: '',
    priority: 'core',
    weight: 3,
    orderIndex: sequence,
    archived: false,
    position: { x: null, y: null },
    status: 'not_started',
    prerequisites: [],
    mustUnderstand: [],
    mustBeAbleTo: [],
    exitCriteria: [],
    ...partial,
  }
}

function track(partial: Partial<Track> & { id: string }): Track {
  return { stableKey: `track-${partial.id}`, title: `Track ${partial.id}`, description: '', orderIndex: 0, competencies: [], ...partial }
}

function phase(partial: Partial<Phase> & { id: string }): Phase {
  return { stableKey: `phase-${partial.id}`, title: `Phase ${partial.id}`, description: '', orderIndex: 0, archived: false, isCurrent: false, tracks: [], ...partial }
}

function buildRoadmap(): Roadmap {
  const a = competency({ definitionId: 'a', orderIndex: 0 })
  const b = competency({ definitionId: 'b', orderIndex: 1, parentDefinitionId: 'a' })
  const c = competency({ definitionId: 'c', orderIndex: 2, position: { x: 500, y: 300 } })
  const d = competency({ definitionId: 'd', orderIndex: 3, position: { x: 500, y: 300 } })
  const e = competency({ definitionId: 'e', orderIndex: 0 })
  const f = competency({
    definitionId: 'f',
    orderIndex: 1,
    parentDefinitionId: 'e',
    prerequisites: [{ identityId: 'identity-a', stableKey: 'key.a', kind: 'required' }],
  })
  const g = competency({
    definitionId: 'g',
    orderIndex: 2,
    prerequisites: [{ identityId: 'identity-b', stableKey: 'key.b', kind: 'recommended' }],
  })
  return {
    id: 'roadmap',
    stableKey: 'roadmap',
    title: 'Test roadmap',
    description: '',
    activeVersion: { id: 'version', version: '1', schemaVersion: 1 },
    currentPhaseId: 'p2',
    phases: [
      phase({
        id: 'p1',
        orderIndex: 0,
        tracks: [
          track({ id: 't1', orderIndex: 0, competencies: [a, b, c] }),
          track({ id: 't2', orderIndex: 1, competencies: [d] }),
        ],
      }),
      phase({
        id: 'p2',
        orderIndex: 1,
        isCurrent: true,
        tracks: [track({ id: 't3', orderIndex: 0, competencies: [e, f, g] })],
      }),
    ],
  }
}

const noop = () => undefined

function nodePosition(layout: ReturnType<typeof computeRoadmapLayout>, id: string) {
  const node = layout.nodes.find((entry) => entry.id === id)
  if (!node) throw new Error(`Missing node ${id}`)
  return node.position
}

describe('computeRoadmapLayout', () => {
  it('lays phase containers out left-to-right and flags the current phase', () => {
    const layout = computeRoadmapLayout({ roadmap: buildRoadmap(), expanded: new Set(), onToggleExpand: noop })
    expect(layout.phases).toHaveLength(2)
    expect(layout.phases[0].origin).toEqual({ x: 0, y: 0 })
    expect(layout.phases[1].origin.y).toBe(0)
    expect(layout.phases[1].origin.x).toBe(layout.phases[0].width + PHASE_GAP)
    expect(layout.phases[1].phase.isCurrent).toBe(true)
    const phaseNodes = layout.nodes.filter((node) => node.type === 'phase')
    expect(phaseNodes.map((node) => node.id)).toEqual(['phase-p1', 'phase-p2'])
  })

  it('stacks track lanes vertically within a phase container', () => {
    const layout = computeRoadmapLayout({ roadmap: buildRoadmap(), expanded: new Set(), onToggleExpand: noop })
    const [first, second] = layout.phases[0].lanes
    expect(layout.phases[0].lanes.map((lane) => lane.trackId)).toEqual(['t1', 't2'])
    expect(second.y).toBeGreaterThan(first.y + first.height - 1)
  })

  it('places expanded children below their parent and hides them when collapsed', () => {
    const expanded = computeRoadmapLayout({ roadmap: buildRoadmap(), expanded: new Set(['a', 'e']), onToggleExpand: noop })
    const parent = nodePosition(expanded, 'a')
    const child = nodePosition(expanded, 'b')
    expect(child.x).toBe(parent.x)
    expect(child.y).toBe(parent.y + NODE_HEIGHT + NODE_GAP_Y)

    const collapsed = computeRoadmapLayout({ roadmap: buildRoadmap(), expanded: new Set(), onToggleExpand: noop })
    expect(collapsed.visible.map((item) => item.definitionId)).not.toContain('b')
    expect(collapsed.nodes.some((node) => node.id === 'b')).toBe(false)
  })

  it('honours stored positions verbatim as phase-local coordinates', () => {
    const layout = computeRoadmapLayout({ roadmap: buildRoadmap(), expanded: new Set(), onToggleExpand: noop })
    const position = nodePosition(layout, 'c')
    expect(position).toEqual({ x: layout.phases[0].origin.x + 500, y: 300 })
  })

  it('de-collides render output without mutating stored positions', () => {
    const roadmap = buildRoadmap()
    const before = JSON.stringify(roadmap)
    const layout = computeRoadmapLayout({ roadmap, expanded: new Set(), onToggleExpand: noop })
    const c = nodePosition(layout, 'c')
    const d = nodePosition(layout, 'd')
    expect(d).not.toEqual(c)
    expect(JSON.stringify(roadmap)).toBe(before)
  })

  it('distinguishes required, recommended, and hierarchy edges without always-on animation', () => {
    const layout = computeRoadmapLayout({ roadmap: buildRoadmap(), expanded: new Set(['a', 'e']), onToggleExpand: noop })
    const required = layout.edges.find((edge) => edge.id === 'prereq-a-f')
    const recommended = layout.edges.find((edge) => edge.id === 'prereq-b-g')
    const hierarchy = layout.edges.find((edge) => edge.id === 'parent-e-f')
    expect(required?.style?.stroke).toBe('#326653')
    expect(required?.style?.strokeDasharray).toBeUndefined()
    expect(recommended?.style?.strokeDasharray).toBe('6 6')
    expect(hierarchy?.sourceHandle).toBe('out-hierarchy')
    expect(hierarchy?.targetHandle).toBe('in-hierarchy')
    expect(layout.edges.every((edge) => !edge.animated)).toBe(true)
  })

  it('drops prerequisite edges whose source is not visible', () => {
    const layout = computeRoadmapLayout({ roadmap: buildRoadmap(), expanded: new Set(['e']), onToggleExpand: noop })
    expect(layout.edges.some((edge) => edge.id === 'prereq-b-g')).toBe(false)
    expect(layout.edges.some((edge) => edge.id === 'prereq-a-f')).toBe(true)
  })

  it('renders an empty phase as a labelled container', () => {
    const roadmap = buildRoadmap()
    roadmap.phases.push(phase({ id: 'p3', orderIndex: 2 }))
    const layout = computeRoadmapLayout({ roadmap, expanded: new Set(), onToggleExpand: noop })
    const emptyPhase = layout.nodes.find((node) => node.id === 'phase-p3')
    expect(emptyPhase?.data.empty).toBe(true)
    expect(layout.phases[2].origin.x).toBeGreaterThan(layout.phases[1].origin.x)
  })

  it('keeps competency nodes interactive and exposes expand affordance data', () => {
    const layout = computeRoadmapLayout({ roadmap: buildRoadmap(), expanded: new Set(['a']), onToggleExpand: noop })
    const parent = layout.nodes.find((node): node is CompetencyFlowNode => node.id === 'a')
    const leaf = layout.nodes.find((node): node is CompetencyFlowNode => node.id === 'c')
    expect(parent?.data.childCount).toBe(1)
    expect(parent?.data.expanded).toBe(true)
    expect(leaf?.data.childCount).toBe(0)
    expect(parent?.draggable).not.toBe(false)
    expect(layout.nodes.find((node) => node.id === 'phase-p1')?.draggable).toBe(false)
  })
})

describe('coordinate conversion', () => {
  it('round-trips between phase-local and canvas coordinates', () => {
    const origin = { x: 412, y: 0 }
    const local = { x: 168, y: 208 }
    expect(toPhaseLocalPosition(origin, toCanvasPosition(origin, local))).toEqual(local)
  })
})

describe('mergeLayoutNodes', () => {
  it('reuses unchanged node objects across selection-only changes', () => {
    const roadmap = buildRoadmap()
    const expanded = new Set(['a', 'e'])
    const layout = computeRoadmapLayout({ roadmap, expanded, onToggleExpand: noop })
    const seeded = mergeLayoutNodes([], layout, new Map(), null)

    const afterSelectA = mergeLayoutNodes(seeded, layout, new Map(), 'a')
    expect(afterSelectA.find((node) => node.id === 'a')?.data.selected).toBe(true)
    expect(seeded.find((node) => node.id === 'a')?.data.selected).toBe(false)

    const afterSelectC = mergeLayoutNodes(afterSelectA, layout, new Map(), 'c')
    expect(afterSelectC.find((node) => node.id === 'a')?.data.selected).toBe(false)
    expect(afterSelectC.find((node) => node.id === 'c')?.data.selected).toBe(true)

    for (const id of ['b', 'd', 'e', 'f', 'g', 'phase-p1', 'phase-p2']) {
      expect(afterSelectC.find((node) => node.id === id)).toBe(seeded.find((node) => node.id === id))
    }
  })

  it('does not recompute the geometry layout for a selection-only merge', () => {
    const roadmap = buildRoadmap()
    const expanded = new Set(['a', 'e'])
    const layout = computeRoadmapLayout({ roadmap, expanded, onToggleExpand: noop })
    const seeded = mergeLayoutNodes([], layout, new Map(), null)
    const merged = mergeLayoutNodes(seeded, layout, new Map(), 'g')
    for (const node of merged) {
      const original = seeded.find((entry) => entry.id === node.id)
      if (node.type === 'competency' && node.id !== 'g') {
        expect(original?.data.expanded).toBe(node.data.expanded)
        expect(original?.position).toEqual(node.position)
      }
    }
    expect(merged.find((node) => node.id === 'g')?.data.selected).toBe(true)
  })

  it('keeps unchanged identities when expanding changes the geometry', () => {
    const roadmap = buildRoadmap()
    const collapsedLayout = computeRoadmapLayout({ roadmap, expanded: new Set(), onToggleExpand: noop })
    const seeded = mergeLayoutNodes([], collapsedLayout, new Map(), null)

    const expandedLayout = computeRoadmapLayout({ roadmap, expanded: new Set(['a']), onToggleExpand: noop })
    const merged = mergeLayoutNodes(seeded, expandedLayout, new Map(), null)

    expect(merged.some((node) => node.id === 'b')).toBe(true)
    expect(merged.find((node) => node.id === 'a')?.data.expanded).toBe(true)
    expect(merged.find((node) => node.id === 'a')).not.toBe(seeded.find((node) => node.id === 'a'))
    for (const id of ['c', 'd', 'e', 'g', 'phase-p2']) {
      expect(merged.find((node) => node.id === id)).toBe(seeded.find((node) => node.id === id))
    }
  })

  it('drops collapsed children from the merged node set', () => {
    const roadmap = buildRoadmap()
    const expandedLayout = computeRoadmapLayout({ roadmap, expanded: new Set(['a', 'e']), onToggleExpand: noop })
    const seeded = mergeLayoutNodes([], expandedLayout, new Map(), null)
    const collapsedLayout = computeRoadmapLayout({ roadmap, expanded: new Set(['e']), onToggleExpand: noop })
    const merged = mergeLayoutNodes(seeded, collapsedLayout, new Map(), null)
    expect(merged.some((node) => node.id === 'b')).toBe(false)
    expect(merged.find((node) => node.id === 'a')?.data.expanded).toBe(false)
  })

  it('re-applies in-flight drag overrides when merging', () => {
    const roadmap = buildRoadmap()
    const layout = computeRoadmapLayout({ roadmap, expanded: new Set(), onToggleExpand: noop })
    const seeded = mergeLayoutNodes([], layout, new Map(), null)
    const dragged = new Map([['c', { x: 999, y: 42 }]])
    const merged = mergeLayoutNodes(seeded, layout, dragged, null)
    expect(merged.find((node) => node.id === 'c')?.position).toEqual({
      x: layout.phases[0].origin.x + 999,
      y: 42,
    })
    expect(merged.find((node) => node.id === 'd')).toBe(seeded.find((node) => node.id === 'd'))
  })
})
