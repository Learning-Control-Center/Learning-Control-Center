import { describe, expect, it } from 'vitest'

import {
  buildVisiblePrereqEdges,
  computeRoadmapLayout,
  computeVisibleRoadmap,
  mergeLayoutNodes,
  NODE_HEIGHT,
  toPhaseLocalPosition,
  type CompetencyFlowNode,
  type RoadmapLayout,
} from './components/roadmapLayout'
import type { Competency, Phase, Roadmap, Track } from './types'

let sequence = 0
function makeCompetency(overrides: Partial<Competency> = {}): Competency {
  sequence += 1
  return {
    identityId: `id-${sequence}`,
    definitionId: `def-${sequence}`,
    stableKey: `CORE.${sequence}`,
    title: `Competency ${sequence}`,
    parentDefinitionId: null,
    description: '',
    goal: '',
    status: 'not_started',
    priority: 'core',
    weight: 1,
    orderIndex: sequence,
    archived: false,
    mustUnderstand: [],
    mustBeAbleTo: [],
    prerequisites: [],
    exitCriteria: [],
    position: { x: null, y: null },
    ...overrides,
  }
}

function makeTrack(track: Track, competencies: Competency[]): Track {
  return { ...track, competencies }
}

function trackWith(id: string, title: string, orderIndex: number): Track {
  return { id, stableKey: `TRACK.${id}`, title, description: '', orderIndex, competencies: [] }
}

function phaseWith(id: string, title: string, orderIndex: number, tracks: Track[]): Phase {
  return {
    id,
    stableKey: `PHASE.${id}`,
    title,
    description: '',
    orderIndex,
    archived: false,
    isCurrent: false,
    tracks,
  }
}

function makeRoadmap(phases: Phase[]): Roadmap {
  return {
    id: 'roadmap-1',
    stableKey: 'ROADMAP',
    title: 'Roadmap',
    description: '',
    activeVersion: { id: 'version-1', version: '2', schemaVersion: 1 },
    currentPhaseId: phases[0]?.id ?? '',
    phases,
  }
}

const focusRef = { current: () => {} }

function layoutOptions(roadmap: Roadmap, expanded: ReadonlySet<string>, extras: Record<string, unknown> = {}) {
  return {
    roadmap,
    expanded,
    onToggleExpand: () => undefined,
    onTogglePhaseCollapse: () => undefined,
    focusPhaseRef: focusRef,
    onMakeCurrent: () => undefined,
    ...extras,
  }
}

function competencyNodes(layout: RoadmapLayout): CompetencyFlowNode[] {
  return layout.nodes.filter((node): node is CompetencyFlowNode => node.type === 'competency')
}

describe('computeVisibleRoadmap', () => {
  it('hides descendants of collapsed parents at every depth', () => {
    const parent = makeCompetency({ definitionId: 'p', stableKey: 'CORE.P' })
    const child = makeCompetency({ definitionId: 'c', stableKey: 'CORE.C', parentDefinitionId: 'p' })
    const grandchild = makeCompetency({ definitionId: 'g', stableKey: 'CORE.G', parentDefinitionId: 'c' })
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [makeTrack(trackWith('t1', 'Track', 0), [parent, child, grandchild])])])

    const expanded = computeVisibleRoadmap(roadmap, new Set(['p', 'c']))
    expect(expanded.visible.map((item) => item.definitionId)).toEqual(['p', 'c', 'g'])

    const collapsed = computeVisibleRoadmap(roadmap, new Set(['p']))
    expect(collapsed.visible.map((item) => item.definitionId)).toEqual(['p', 'c'])
  })

  it('lays out a child whose parent lives in another phase inside its own phase', () => {
    const parent = makeCompetency({ definitionId: 'p', stableKey: 'CORE.P' })
    const child = makeCompetency({ definitionId: 'c', stableKey: 'CORE.C', parentDefinitionId: 'p' })
    const roadmap = makeRoadmap([
      phaseWith('ph1', 'One', 0, [makeTrack(trackWith('t1', 'T1', 0), [parent])]),
      phaseWith('ph2', 'Two', 1, [makeTrack(trackWith('t2', 'T2', 0), [child])]),
    ])

    // Cross-phase children follow the same expansion rule as any child: the
    // subtree stays hidden until the parent is expanded.
    expect(computeVisibleRoadmap(roadmap, new Set()).visible.map((item) => item.definitionId)).toEqual(['p'])

    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set(['p'])))
    expect(competencyNodes(layout).map((node) => node.id)).toEqual(['p', 'c'])
    // The child is anchored to its own phase, not nested under the parent.
    expect(layout.phaseByDefinition.get('c')).toBe('ph2')
    const phaseTwo = layout.phases.find((entry) => entry.phase.id === 'ph2')
    const childNode = competencyNodes(layout).find((node) => node.id === 'c')
    expect(childNode?.position.x ?? 0).toBeGreaterThanOrEqual(phaseTwo?.origin.x ?? Number.MAX_SAFE_INTEGER)
    // Cross-phase hierarchy still renders an edge when both sides are visible.
    expect(layout.edges.some((edge) => edge.id === 'parent-p-c')).toBe(true)
  })
})

describe('computeRoadmapLayout', () => {
  it('parents expand to reveal children and collapse to hide subtrees', () => {
    const parent = makeCompetency({ definitionId: 'p', stableKey: 'CORE.P' })
    const child = makeCompetency({ definitionId: 'c', stableKey: 'CORE.C', parentDefinitionId: 'p' })
    const grandchild = makeCompetency({ definitionId: 'g', stableKey: 'CORE.G', parentDefinitionId: 'c' })
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [makeTrack(trackWith('t1', 'Track', 0), [parent, child, grandchild])])])

    const expandedLayout = computeRoadmapLayout(layoutOptions(roadmap, new Set(['p', 'c'])))
    expect(competencyNodes(expandedLayout).map((node) => node.id)).toEqual(['p', 'c', 'g'])

    const collapsedLayout = computeRoadmapLayout(layoutOptions(roadmap, new Set(['p'])))
    expect(competencyNodes(collapsedLayout).map((node) => node.id)).toEqual(['p', 'c'])
    expect(collapsedLayout.edges).toHaveLength(1)
    expect(collapsedLayout.edges[0]?.id).toBe('parent-p-c')
  })

  it('keeps hierarchy edges as tree edges and prerequisites off the default canvas', () => {
    const dependency = makeCompetency({ definitionId: 'dep', stableKey: 'CORE.DEP' })
    const parent = makeCompetency({
      definitionId: 'p',
      stableKey: 'CORE.P',
      prerequisites: [{ identityId: dependency.identityId, stableKey: dependency.stableKey, kind: 'required' }],
    })
    const child = makeCompetency({ definitionId: 'c', stableKey: 'CORE.C', parentDefinitionId: 'p' })
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [makeTrack(trackWith('t1', 'Track', 0), [dependency, parent, child])])])

    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set(['p'])))
    expect(layout.edges).toHaveLength(1)
    expect(layout.edges[0]?.type).toBe('tree')
    expect(layout.edges.some((edge) => edge.id.startsWith('prereq-'))).toBe(false)
    expect(layout.prereqEdges).toHaveLength(1)
    expect(layout.prereqEdges[0]?.data?.kind).toBe('required')
    expect(competencyNodes(layout).find((node) => node.id === 'p')?.data.prerequisiteCount).toBe(1)
  })

  it('lays each phase out as visible track sections with stacked track geometry', () => {
    const firstTrack = makeTrack(trackWith('t1', 'First', 0), [makeCompetency(), makeCompetency()])
    const secondTrack = makeTrack(trackWith('t2', 'Second', 1), [makeCompetency()])
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [firstTrack, secondTrack])])

    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set()))
    const phase = layout.phases[0]
    expect(phase).toBeDefined()
    expect(phase?.tracks.map((track) => track.title)).toEqual(['First', 'Second'])
    expect((phase?.tracks[1]?.y ?? 0) > (phase?.tracks[0]?.y ?? 0)).toBe(true)
    const ids = firstTrack.competencies.map((item) => item.definitionId)
    const first = competencyNodes(layout).find((node) => node.id === ids[0])
    const second = competencyNodes(layout).find((node) => node.id === ids[1])
    expect((second?.position.y ?? 0) - (first?.position.y ?? 0)).toBeGreaterThanOrEqual(NODE_HEIGHT)
  })

  it('places children indented beneath their parent for a hierarchy-first reading', () => {
    const parent = makeCompetency({ definitionId: 'p', stableKey: 'CORE.P' })
    const firstChild = makeCompetency({ definitionId: 'c1', stableKey: 'CORE.C1', parentDefinitionId: 'p' })
    const secondChild = makeCompetency({ definitionId: 'c2', stableKey: 'CORE.C2', parentDefinitionId: 'p' })
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [makeTrack(trackWith('t1', 'Track', 0), [parent, firstChild, secondChild])])])

    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set(['p'])))
    const nodes = competencyNodes(layout)
    const parentNode = nodes.find((node) => node.id === 'p')
    const firstChildNode = nodes.find((node) => node.id === 'c1')
    const secondChildNode = nodes.find((node) => node.id === 'c2')
    expect(parentNode).toBeDefined()
    expect((firstChildNode?.position.y ?? 0) > (parentNode?.position.y ?? 0)).toBe(true)
    expect((firstChildNode?.position.x ?? 0) > (parentNode?.position.x ?? 0)).toBe(true)
    expect((secondChildNode?.position.x ?? 0) > (firstChildNode?.position.x ?? 0)).toBe(true)
    expect((secondChildNode?.position.y ?? 0) - (firstChildNode?.position.y ?? 0)).toBeGreaterThanOrEqual(NODE_HEIGHT)
    expect(secondChildNode?.data.depth ?? 0).toBeGreaterThan(firstChildNode?.data.depth ?? 0)
  })

  it('never overlaps siblings within a track', () => {
    const items = Array.from({ length: 8 }, (_, index) => makeCompetency({ orderIndex: index }))
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [makeTrack(trackWith('t1', 'Track', 0), items)])])
    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set()))
    const rects = competencyNodes(layout).map((node) => ({
      x0: node.position.x,
      x1: node.position.x + (node.width ?? 0),
      y0: node.position.y,
      y1: node.position.y + (node.height ?? 0),
    }))
    rects.forEach((a, index) => {
      rects.slice(index + 1).forEach((b) => {
        const overlapX = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)
        const overlapY = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0)
        expect(overlapX <= 0 || overlapY <= 0).toBe(true)
      })
    })
  })

  it('phase collapse hides every node in that phase by phase_id, regardless of manual position', () => {
    const anchor = makeCompetency({ definitionId: 'a', stableKey: 'CORE.A' })
    const drifted = makeCompetency({ definitionId: 'b', stableKey: 'CORE.B', position: { x: 1200, y: -400 } })
    const other = makeCompetency({ definitionId: 'c', stableKey: 'CORE.C' })
    const roadmap = makeRoadmap([
      phaseWith('ph1', 'One', 0, [makeTrack(trackWith('t1', 'T1', 0), [anchor, drifted])]),
      phaseWith('ph2', 'Two', 1, [makeTrack(trackWith('t2', 'T2', 0), [other])]),
    ])

    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set(), { collapsedPhases: new Set(['ph1']) }))
    expect(competencyNodes(layout).map((node) => node.id)).toEqual(['c'])
    const collapsed = layout.phases.find((entry) => entry.phase.id === 'ph1')
    const open = layout.phases.find((entry) => entry.phase.id === 'ph2')
    expect(collapsed?.collapsed).toBe(true)
    expect(collapsed?.hasNodes).toBe(false)
    expect(collapsed?.tracks).toEqual([])
    expect(open?.collapsed).toBe(false)
    expect(open?.origin.x ?? 0).toBeGreaterThan(collapsed?.width ?? 0)
    const collapsedNode = layout.nodes.find((node) => node.id === 'phase-ph1')
    expect(collapsedNode?.data.collapsed).toBe(true)
  })

  it('keeps phase geometry identical whether prerequisites are disclosed or not', () => {
    const dependency = makeCompetency({ definitionId: 'dep', stableKey: 'CORE.DEP' })
    const dependent = makeCompetency({
      definitionId: 'p',
      stableKey: 'CORE.P',
      prerequisites: [{ identityId: dependency.identityId, stableKey: dependency.stableKey, kind: 'recommended' }],
    })
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [makeTrack(trackWith('t1', 'Track', 0), [dependency, dependent])])])

    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set()))
    const geometryBefore = competencyNodes(layout).map((node) => ({ id: node.id, position: node.position }))
    expect(buildVisiblePrereqEdges(layout.prereqEdges, false, null)).toHaveLength(0)
    expect(buildVisiblePrereqEdges(layout.prereqEdges, true, null)).toHaveLength(1)
    expect(buildVisiblePrereqEdges(layout.prereqEdges, false, 'p')).toHaveLength(1)
    expect(buildVisiblePrereqEdges(layout.prereqEdges, false, 'unrelated')).toHaveLength(0)

    const layoutAfter = computeRoadmapLayout(layoutOptions(roadmap, new Set()))
    expect(competencyNodes(layoutAfter).map((node) => ({ id: node.id, position: node.position }))).toEqual(geometryBefore)
  })

  it('preserves node object identity for nodes whose geometry and data are unchanged', () => {
    const items = [
      makeCompetency({ definitionId: 'a', stableKey: 'CORE.A', status: 'learning' }),
      makeCompetency({ definitionId: 'b', stableKey: 'CORE.B' }),
    ]
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [makeTrack(trackWith('t1', 'Track', 0), items)])])
    const onToggleExpand = () => undefined
    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set(), { onToggleExpand }))

    const first = mergeLayoutNodes([], layout, new Map(), null)
    const second = mergeLayoutNodes(first, layout, new Map(), null)
    expect(second.every((node, index) => node === first[index])).toBe(true)

    const byId = (nodes: typeof first, id: string) => nodes.find((node) => node.id === id)
    const selected = mergeLayoutNodes(first, layout, new Map(), 'b')
    expect(byId(selected, 'a')).toBe(byId(first, 'a'))
    expect(byId(selected, 'b')).not.toBe(byId(first, 'b'))
    expect(byId(selected, 'b')?.data.selected).toBe(true)

    const dragged = mergeLayoutNodes(selected, layout, new Map([['a', { x: 40, y: 60 }]]), 'b')
    expect(byId(dragged, 'b')).toBe(byId(selected, 'b'))
    expect(byId(dragged, 'a')).not.toBe(byId(selected, 'a'))
    expect(byId(dragged, 'a')?.position).toEqual({ x: 40, y: 60 })

    const statusesChanged = computeRoadmapLayout(
      layoutOptions(
        makeRoadmap([
          phaseWith('ph1', 'Foundations', 0, [
            makeTrack(trackWith('t1', 'Track', 0), [
              { ...items[0], status: 'verified' as const },
              items[1],
            ]),
          ]),
        ]),
        new Set(),
        { onToggleExpand },
      ),
    )
    const merged = mergeLayoutNodes(first, statusesChanged, new Map(), null)
    expect(byId(merged, 'a')).not.toBe(byId(first, 'a'))
    expect(byId(merged, 'b')).toBe(byId(first, 'b'))
  })

  it('converts phase-local persisted coordinates to canvas coordinates', () => {
    const phaseOneItems = [makeCompetency({ definitionId: 'a', position: { x: 24, y: 96 } })]
    const phaseTwoItems = [makeCompetency({ definitionId: 'b', position: { x: 40, y: 120 } })]
    const roadmap = makeRoadmap([
      phaseWith('ph1', 'One', 0, [makeTrack(trackWith('t1', 'T1', 0), phaseOneItems)]),
      phaseWith('ph2', 'Two', 1, [makeTrack(trackWith('t2', 'T2', 0), phaseTwoItems)]),
    ])
    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set()))
    const phaseOne = layout.phases.find((phase) => phase.phase.id === 'ph1')
    const phaseTwo = layout.phases.find((phase) => phase.phase.id === 'ph2')
    expect(phaseOne?.origin).toEqual({ x: 0, y: 0 })
    expect(phaseTwo?.origin.x).toBeGreaterThan(0)
    const first = competencyNodes(layout).find((node) => node.id === 'a')
    const second = competencyNodes(layout).find((node) => node.id === 'b')
    expect(first?.position).toEqual({ x: 24, y: 96 })
    expect(second?.position).toEqual({ x: (phaseTwo?.origin.x ?? 0) + 40, y: 120 })
    expect(toPhaseLocalPosition(phaseTwo?.origin ?? { x: 0, y: 0 }, second?.position ?? { x: 0, y: 0 })).toEqual({ x: 40, y: 120 })
  })

  it('treats manual positions as free-form visual overrides, even outside the phase bounds', () => {
    const anchored = makeCompetency({ definitionId: 'a', position: { x: -120, y: -48 } })
    const freeform = makeCompetency({ definitionId: 'b', position: { x: 900, y: 640 } })
    const roadmap = makeRoadmap([phaseWith('ph1', 'One', 0, [makeTrack(trackWith('t1', 'T1', 0), [anchored, freeform])])])
    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set()))
    const nodes = competencyNodes(layout)
    expect(nodes.find((node) => node.id === 'a')?.position).toEqual({ x: -120, y: -48 })
    expect(nodes.find((node) => node.id === 'b')?.position).toEqual({ x: 900, y: 640 })
    const phase = layout.phases[0]
    expect(phase).toBeDefined()
    expect(phase?.width ?? 0).toBeLessThan(900)
    expect(phase?.hasNodes).toBe(true)
    expect(competencyNodes(layout).some((node) => node.position.x < 0)).toBe(true)
  })

  it('applies manual positions exactly without resolving overlaps against automatic slots', () => {
    const first = makeCompetency({ definitionId: 'a', position: { x: 40, y: 80 } })
    const second = makeCompetency({ definitionId: 'b', position: { x: 48, y: 88 } })
    const roadmap = makeRoadmap([phaseWith('ph1', 'One', 0, [makeTrack(trackWith('t1', 'T1', 0), [first, second])])])
    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set()))
    const nodes = competencyNodes(layout)
    expect(nodes.find((node) => node.id === 'a')?.position).toEqual({ x: 40, y: 80 })
    expect(nodes.find((node) => node.id === 'b')?.position).toEqual({ x: 48, y: 88 })
  })

  it('uses deterministic ordering for tracks and competencies within a phase', () => {
    const firstTrack = makeTrack(trackWith('t1', 'B', 1), [makeCompetency({ orderIndex: 2 })])
    const secondTrack = makeTrack(trackWith('t2', 'A', 0), [makeCompetency({ orderIndex: 1 })])
    const roadmap = makeRoadmap([phaseWith('ph1', 'Foundations', 0, [firstTrack, secondTrack])])
    const layout = computeRoadmapLayout(layoutOptions(roadmap, new Set()))
    expect(layout.phases[0]?.tracks[0]?.title).toBe('A')
    expect(layout.phases[0]?.tracks[1]?.title).toBe('B')
  })
})
