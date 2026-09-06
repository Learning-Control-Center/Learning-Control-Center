import { Position, type Edge, type Node } from '@xyflow/react'

import type { Competency, Phase, Roadmap, Status } from '../types'

/**
 * Persisted position contract
 * ---------------------------
 * `position_x` / `position_y` stored on a competency definition are
 * phase-local integer coordinates: the node offset from the top-left corner
 * of its own phase container, not from the canvas origin. The renderer
 * converts between phase-local persisted coordinates and global canvas
 * coordinates through `toCanvasPosition` / `toPhaseLocalPosition`; phase
 * origins are a pure render-layer concern.
 *
 * Historical note: the pre-atlas UI persisted global canvas coordinates while
 * the import guide examples use phase-local-looking values, so stored values
 * are ambiguous. Stored values are used as phase-local offsets and are never
 * rewritten by the renderer.
 *
 * A non-null persisted position is a free-form visual override. It is honoured
 * even when it places the card outside the semantic phase container, including
 * at negative phase-local coordinates or over another visual phase region.
 * Competency nodes are top-level React Flow nodes rather than children of phase
 * nodes, so visual placement cannot change semantic phase membership. Stored
 * values are never migrated or reinterpreted; only an explicit drag or the
 * Reset Layout action (which clears them) changes storage.
 */

export const NODE_WIDTH = 232
export const NODE_HEIGHT = 104
export const CHILD_INDENT = 40
export const TREE_SIBLING_GAP = 10
export const TREE_GROUP_GAP = 20
export const TREE_PADDING_X = 16
export const TRACK_MIN_WIDTH = 300
export const TRACK_HEADER_HEIGHT = 46
export const TRACK_PADDING_BOTTOM = 16
export const TRACK_EMPTY_HEIGHT = 44
export const TRACK_GAP = 20
export const PHASE_HEADER_HEIGHT = 64
export const PHASE_PADDING_X = 16
export const PHASE_PADDING_TOP = 16
export const PHASE_PADDING_BOTTOM = 16
export const PHASE_EMPTY_HEIGHT = 96
export const PHASE_COLLAPSED_WIDTH = 300
export const PHASE_COLLAPSED_HEIGHT = PHASE_HEADER_HEIGHT + 54
export const PHASE_GAP = 44

export const statusColor: Record<Status, string> = {
  not_started: '#9ca39e',
  learning: '#3c82a0',
  practicing: '#bd7b2d',
  ready_for_verification: '#7357a5',
  verified: '#326653',
  needs_review: '#b24f5c',
}

export const TREE_SPINE_COLOR = '#86a493'
export const TREE_BRANCH_COLOR = '#a7b8ad'
export const PREREQ_REQUIRED_COLOR = '#326653'
export const PREREQ_RECOMMENDED_COLOR = '#93a69b'

type Point = { x: number; y: number }

export function toCanvasPosition(origin: Point, local: Point): Point {
  return { x: origin.x + local.x, y: origin.y + local.y }
}

export function toPhaseLocalPosition(origin: Point, canvas: Point): Point {
  return { x: Math.round(canvas.x - origin.x), y: Math.round(canvas.y - origin.y) }
}

export type TrackLayout = { trackId: string; title: string; y: number; height: number; width: number }

export type PhaseLayout = {
  phase: Phase
  origin: Point
  width: number
  height: number
  tracks: TrackLayout[]
  hasNodes: boolean
  totalCount: number
  verifiedCount: number
  collapsed: boolean
}

export type CompetencyNodeData = {
  competency: Competency
  depth: number
  childCount: number
  expanded: boolean
  /**
   * Visible prerequisites keyed off the currently visible graph; used for the
   * restrained on-card indicator. The detail panel remains the complete list.
   */
  prerequisiteCount: number
  /**
   * Selection is a transient interaction layer, not part of the geometry.
   * `computeRoadmapLayout` never sets it; the graph page applies it to the
   * affected nodes only, so selection does not rebuild the layout.
   */
  selected?: boolean
  statusColor: string
  onToggleExpand: (definitionId: string) => void
  [key: string]: unknown
}

export type PhaseNodeData = {
  phaseId: string
  title: string
  isCurrent: boolean
  width: number
  height: number
  tracks: TrackLayout[]
  empty: boolean
  collapsed: boolean
  totalCount: number
  verifiedCount: number
  currentBusy: boolean
  onTogglePhaseCollapse: (phaseId: string) => void
  /**
   * Phase focus needs the live React Flow viewport instance, so the layout
   * receives a stable ref to the focus implementation rather than the
   * implementation itself. The ref identity never changes, keeping the layout
   * memo stable; the target is rebound by the graph on every render.
   */
  focusPhaseRef: { current: (phaseId: string) => void }
  onMakeCurrent: (phaseId: string) => void
  [key: string]: unknown
}

export type CompetencyFlowNode = Node<CompetencyNodeData, 'competency'>
export type PhaseFlowNode = Node<PhaseNodeData, 'phase'>
export type RoadmapFlowNode = CompetencyFlowNode | PhaseFlowNode

export type PrereqEdgeData = {
  kind: 'required' | 'recommended'
  sourceTitle: string
  targetTitle: string
  [key: string]: unknown
}

export type RoadmapFlowEdge = Edge<PrereqEdgeData>

export type RoadmapLayout = {
  nodes: RoadmapFlowNode[]
  edges: RoadmapFlowEdge[]
  phases: PhaseLayout[]
  visible: Competency[]
  childrenByParent: Map<string, Competency[]>
  phaseByDefinition: Map<string, string>
  /**
   * Every prerequisite edge whose endpoints are both displayable right now.
   * It is pure metadata: the graph page derives edge visibility from it, so
   * toggling or selecting prerequisites never touches geometry.
   */
  prereqEdges: RoadmapFlowEdge[]
  prerequisiteCountByDefinition: Map<string, number>
}

export function phaseOriginFor(layout: RoadmapLayout, definitionId: string): Point | null {
  const phaseId = layout.phaseByDefinition.get(definitionId)
  const phase = layout.phases.find((entry) => entry.phase.id === phaseId)
  return phase ? phase.origin : null
}

function byOrderThenKey(a: Competency, b: Competency): number {
  return a.orderIndex - b.orderIndex || a.stableKey.localeCompare(b.stableKey)
}

export function computeVisibleRoadmap(
  roadmap: Roadmap,
  expanded: ReadonlySet<string>,
  collapsedPhases?: ReadonlySet<string>,
): { all: Competency[]; visible: Competency[]; childrenByParent: Map<string, Competency[]> } {
  const collapsed = collapsedPhases ?? new Set<string>()
  const all = roadmap.phases.flatMap((phase) =>
    phase.tracks.flatMap((track) => track.competencies.map((item) => ({ item, phaseId: phase.id }))),
  )
  const byDefinition = new Map(all.map(({ item }) => [item.definitionId, item]))
  const phaseByDefinition = new Map(all.map(({ item, phaseId }) => [item.definitionId, phaseId]))
  const childrenByParent = new Map<string, Competency[]>()
  all.forEach(({ item }) => {
    if (item.parentDefinitionId) {
      childrenByParent.set(item.parentDefinitionId, [...(childrenByParent.get(item.parentDefinitionId) ?? []), item])
    }
  })
  const visible = all
    .filter(({ item }) => {
      // Phase collapse is semantic: membership is decided by phase_id, never
      // by where a manually positioned card happens to sit on the canvas.
      if (collapsed.has(phaseByDefinition.get(item.definitionId) ?? '')) return false
      if (!item.parentDefinitionId) return true
      let parentId: string | null = item.parentDefinitionId
      while (parentId) {
        if (!expanded.has(parentId)) return false
        parentId = byDefinition.get(parentId)?.parentDefinitionId ?? null
      }
      return true
    })
    .map(({ item }) => item)
  return { all: all.map(({ item }) => item), visible, childrenByParent }
}

type Subtree = { item: Competency; height: number; children: Subtree[] }

/**
 * Lays one track out as a hierarchy-first tree. Every parent sits directly
 * above its children; children are indented one level under it. Within a
 * sibling group each later child (and its subtree) is indented one extra
 * step, so lineage is readable from position alone and the structural
 * connector can run orthogonally: a vertical spine down the shared indent
 * plus a short horizontal branch into each child card.
 */
function layoutTrackItems(
  items: Competency[],
  childrenByParent: Map<string, Competency[]>,
  expanded: ReadonlySet<string>,
): { positions: Map<string, Point>; depthById: Map<string, number>; width: number; height: number } {
  const trackIds = new Set(items.map((item) => item.definitionId))
  const roots = items
    .filter((item) => !item.parentDefinitionId || !trackIds.has(item.parentDefinitionId))
    .sort(byOrderThenKey)
  const trackChildren = (item: Competency) =>
    (childrenByParent.get(item.definitionId) ?? [])
      .filter((child) => trackIds.has(child.definitionId))
      .sort(byOrderThenKey)

  const buildSubtree = (item: Competency): Subtree => {
    const children = expanded.has(item.definitionId) ? trackChildren(item).map(buildSubtree) : []
    const childrenHeight =
      children.reduce((total, child) => total + child.height, 0) +
      TREE_SIBLING_GAP * Math.max(0, children.length - 1)
    return {
      item,
      height: NODE_HEIGHT + (children.length ? TREE_GROUP_GAP + childrenHeight : 0),
      children,
    }
  }

  const positions = new Map<string, Point>()
  const depthById = new Map<string, number>()
  let maxRight = 0

  const placeChildren = (children: Subtree[], parentY: number, baseX: number, baseDepth: number) => {
    let childY = parentY + NODE_HEIGHT + TREE_GROUP_GAP
    children.forEach((child, index) => {
      const x = baseX + (index > 0 ? CHILD_INDENT : 0)
      const depth = baseDepth + (index > 0 ? 1 : 0)
      positions.set(child.item.definitionId, { x, y: childY })
      depthById.set(child.item.definitionId, depth)
      maxRight = Math.max(maxRight, x + NODE_WIDTH)
      placeChildren(child.children, childY, x + CHILD_INDENT, depth + 1)
      childY += child.height + TREE_SIBLING_GAP
    })
  }

  let cursorY = 0
  roots.map(buildSubtree).forEach((subtree, index) => {
    const x = index > 0 ? CHILD_INDENT : 0
    positions.set(subtree.item.definitionId, { x, y: cursorY })
    depthById.set(subtree.item.definitionId, index > 0 ? 1 : 0)
    maxRight = Math.max(maxRight, x + NODE_WIDTH)
    placeChildren(subtree.children, cursorY, x + CHILD_INDENT, index > 0 ? 2 : 1)
    cursorY += subtree.height + TREE_SIBLING_GAP
  })

  return {
    positions,
    depthById,
    width: roots.length ? maxRight : 0,
    height: roots.length ? cursorY - TREE_SIBLING_GAP : 0,
  }
}

function persistedPosition(item: Competency): Point | null {
  return item.position.x === null || item.position.y === null ? null : { x: item.position.x, y: item.position.y }
}

/**
 * Resolves the final phase-local position for every item. Manual positions are
 * exact visual overrides; automatic slots remain deterministic when no manual
 * position exists. Intentional overlap is preserved rather than silently
 * moving a manually positioned card during rendering.
 */
function resolvePhasePositions(items: Competency[], slots: Map<string, Point>): Map<string, Point> {
  return new Map(items.map((item) => {
    const slot = slots.get(item.definitionId) ?? { x: 0, y: 0 }
    return [item.definitionId, persistedPosition(item) ?? slot]
  }))
}

function layoutPhase(
  phase: Phase,
  visible: ReadonlySet<string>,
  childrenByParent: Map<string, Competency[]>,
  expanded: ReadonlySet<string>,
  collapsed: boolean,
): {
  positions: Map<string, Point>
  depthById: Map<string, number>
  tracks: TrackLayout[]
  width: number
  height: number
  hasNodes: boolean
} {
  if (collapsed) {
    return {
      positions: new Map(),
      depthById: new Map(),
      tracks: [],
      width: PHASE_COLLAPSED_WIDTH,
      height: PHASE_COLLAPSED_HEIGHT,
      hasNodes: false,
    }
  }

  const tracks = [...phase.tracks].sort((a, b) => a.orderIndex - b.orderIndex)
  const trackLayouts: TrackLayout[] = []
  const slots = new Map<string, Point>()
  const depthById = new Map<string, number>()
  const items: Competency[] = []
  let trackY = PHASE_HEADER_HEIGHT + PHASE_PADDING_TOP
  let contentRight = PHASE_PADDING_X + TRACK_MIN_WIDTH
  let contentBottom = trackY

  tracks.forEach((track) => {
    const trackItems = track.competencies
      .filter((item) => visible.has(item.definitionId))
      .sort(byOrderThenKey)
    items.push(...trackItems)
    const tree = layoutTrackItems(trackItems, childrenByParent, expanded)
    const trackWidth = Math.max(TRACK_MIN_WIDTH, tree.width + TREE_PADDING_X * 2)
    const treeHeight =
      TRACK_HEADER_HEIGHT + (tree.height > 0 ? tree.height + TRACK_PADDING_BOTTOM : TRACK_EMPTY_HEIGHT)
    tree.positions.forEach((position, definitionId) => {
      slots.set(definitionId, {
        x: position.x + PHASE_PADDING_X + TREE_PADDING_X,
        y: position.y + trackY + TRACK_HEADER_HEIGHT,
      })
    })
    tree.depthById.forEach((depth, definitionId) => depthById.set(definitionId, depth))
    trackLayouts.push({ trackId: track.id, title: track.title, y: trackY, height: treeHeight, width: trackWidth })
    contentRight = Math.max(contentRight, PHASE_PADDING_X + trackWidth)
    contentBottom = trackY + treeHeight
    trackY += treeHeight + TRACK_GAP
  })

  // Phase geometry is defined only by the deterministic automatic layout.
  // Free-form visual overrides neither constrain nor inflate semantic phase
  // containers, keeping every phase origin stable across drag/reload.
  const width = contentRight + PHASE_PADDING_X
  const height =
    (items.length ? contentBottom : PHASE_HEADER_HEIGHT + PHASE_EMPTY_HEIGHT) + PHASE_PADDING_BOTTOM
  const positions = resolvePhasePositions(items, slots)

  return { positions, depthById, tracks: trackLayouts, width, height, hasNodes: items.length > 0 }
}

export function computeRoadmapLayout(options: {
  roadmap: Roadmap
  expanded: ReadonlySet<string>
  collapsedPhases?: ReadonlySet<string>
  currentBusyPhaseId?: string | null
  onToggleExpand: (definitionId: string) => void
  onTogglePhaseCollapse: (phaseId: string) => void
  focusPhaseRef: { current: (phaseId: string) => void }
  onMakeCurrent: (phaseId: string) => void
}): RoadmapLayout {
  const {
    roadmap,
    expanded,
    collapsedPhases = new Set<string>(),
    currentBusyPhaseId = null,
    onToggleExpand,
    onTogglePhaseCollapse,
    focusPhaseRef,
    onMakeCurrent,
  } = options
  const { all, visible, childrenByParent } = computeVisibleRoadmap(roadmap, expanded, collapsedPhases)
  const visibleIds = new Set(visible.map((item) => item.definitionId))
  const identityToDefinition = new Map(all.map((item) => [item.identityId, item]))
  const titleByDefinition = new Map(all.map((item) => [item.definitionId, item.title]))
  const displayablePrereqCount = new Map<string, number>()
  visible.forEach((item) => {
    item.prerequisites.forEach((prerequisite) => {
      const source = identityToDefinition.get(prerequisite.identityId)
      if (!source || source.definitionId === item.definitionId || !visibleIds.has(source.definitionId)) return
      displayablePrereqCount.set(item.definitionId, (displayablePrereqCount.get(item.definitionId) ?? 0) + 1)
    })
  })

  const phases: PhaseLayout[] = []
  const phaseByDefinition = new Map<string, string>()
  const canvasPositions = new Map<string, Point>()
  const depthByDefinition = new Map<string, number>()
  let cursorX = 0
  ;[...roadmap.phases]
    .sort((a, b) => a.orderIndex - b.orderIndex)
    .forEach((phase) => {
      const collapsed = collapsedPhases.has(phase.id)
      const laidOut = layoutPhase(phase, visibleIds, childrenByParent, expanded, collapsed)
      const origin = { x: cursorX, y: 0 }
      laidOut.positions.forEach((local, definitionId) => {
        canvasPositions.set(definitionId, toCanvasPosition(origin, local))
        phaseByDefinition.set(definitionId, phase.id)
      })
      laidOut.depthById.forEach((depth, definitionId) => depthByDefinition.set(definitionId, depth))
      const allItems = phase.tracks.flatMap((track) => track.competencies)
      phases.push({
        phase,
        origin,
        width: laidOut.width,
        height: laidOut.height,
        tracks: laidOut.tracks,
        hasNodes: laidOut.hasNodes,
        totalCount: allItems.length,
        verifiedCount: allItems.filter((item) => item.status === 'verified').length,
        collapsed,
      })
      cursorX += laidOut.width + PHASE_GAP
    })

  const phaseNodes: PhaseFlowNode[] = phases.map((layout) => ({
    id: `phase-${layout.phase.id}`,
    type: 'phase',
    position: layout.origin,
    data: {
      phaseId: layout.phase.id,
      title: layout.phase.title,
      isCurrent: layout.phase.isCurrent,
      width: layout.width,
      height: layout.height,
      tracks: layout.tracks,
      empty: layout.totalCount === 0,
      collapsed: layout.collapsed,
      totalCount: layout.totalCount,
      verifiedCount: layout.verifiedCount,
      currentBusy: currentBusyPhaseId === layout.phase.id,
      onTogglePhaseCollapse,
      focusPhaseRef,
      onMakeCurrent,
    },
    width: layout.width,
    height: layout.height,
    selectable: false,
    draggable: false,
    connectable: false,
    focusable: false,
    style: { zIndex: -1 },
  }))

  const competencyNodes: CompetencyFlowNode[] = visible.map((item) => ({
    id: item.definitionId,
    type: 'competency',
    position: canvasPositions.get(item.definitionId) ?? { x: 0, y: 0 },
    data: {
      competency: item,
      depth: depthByDefinition.get(item.definitionId) ?? 0,
      childCount: (childrenByParent.get(item.definitionId) ?? []).length,
      expanded: expanded.has(item.definitionId),
      prerequisiteCount: displayablePrereqCount.get(item.definitionId) ?? 0,
      statusColor: statusColor[item.status],
      onToggleExpand,
    },
    // Static handle descriptors let React Flow resolve edge endpoints without
    // waiting for DOM measurement, so edges render on first mount even where
    // the initial ResizeObserver notification is throttled or starved
    // (background tabs, automation). Measured handle bounds replace these once
    // real observation lands.
    handles: [
      { id: 'in-hierarchy', type: 'target', position: Position.Top, x: NODE_WIDTH / 2, y: 0, width: 1, height: 1 },
      { id: 'in-prereq', type: 'target', position: Position.Left, x: 0, y: NODE_HEIGHT / 2, width: 1, height: 1 },
      { id: 'out-hierarchy', type: 'source', position: Position.Bottom, x: NODE_WIDTH / 2, y: NODE_HEIGHT, width: 1, height: 1 },
      { id: 'out-prereq', type: 'source', position: Position.Right, x: NODE_WIDTH, y: NODE_HEIGHT / 2, width: 1, height: 1 },
    ],
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    ariaLabel: `${item.title}, status ${item.status.replaceAll('_', ' ')}, priority ${item.priority}`,
  }))

  const hierarchyEdges: RoadmapFlowEdge[] = visible.flatMap((item) => {
    if (!item.parentDefinitionId || !visibleIds.has(item.parentDefinitionId)) return []
    const parentTitle = titleByDefinition.get(item.parentDefinitionId)
    if (!parentTitle) return []
    return [
      {
        id: `parent-${item.parentDefinitionId}-${item.definitionId}`,
        source: item.parentDefinitionId,
        target: item.definitionId,
        type: 'tree',
        ariaLabel: `${parentTitle} contains ${item.title}`,
      },
    ]
  })

  const prereqEdges: RoadmapFlowEdge[] = visible.flatMap((item) =>
    item.prerequisites.flatMap((prerequisite) => {
      const source = identityToDefinition.get(prerequisite.identityId)
      if (!source || source.definitionId === item.definitionId || !visibleIds.has(source.definitionId)) return []
      return [
        {
          id: `prereq-${source.definitionId}-${item.definitionId}`,
          source: source.definitionId,
          target: item.definitionId,
          type: 'smoothstep',
          sourceHandle: 'out-prereq',
          targetHandle: 'in-prereq',
          data: {
            kind: prerequisite.kind,
            sourceTitle: titleByDefinition.get(source.definitionId) ?? prerequisite.stableKey,
            targetTitle: item.title,
          },
        },
      ]
    }),
  )

  const prerequisiteCountByDefinition = displayablePrereqCount

  return {
    nodes: [...phaseNodes, ...competencyNodes],
    edges: hierarchyEdges,
    phases,
    visible,
    childrenByParent,
    phaseByDefinition,
    prereqEdges,
    prerequisiteCountByDefinition,
  }
}

type RenderPoint = { x: number; y: number }

function samePoint(a: RenderPoint, b: RenderPoint): boolean {
  return Math.abs(a.x - b.x) <= 0.01 && Math.abs(a.y - b.y) <= 0.01
}

function sameTrack(a: TrackLayout, b: TrackLayout): boolean {
  return (
    a.trackId === b.trackId &&
    a.title === b.title &&
    a.y === b.y &&
    a.height === b.height &&
    a.width === b.width
  )
}

function sameCompetencyData(a: CompetencyNodeData, b: CompetencyNodeData): boolean {
  return (
    a.childCount === b.childCount &&
    a.expanded === b.expanded &&
    a.depth === b.depth &&
    a.prerequisiteCount === b.prerequisiteCount &&
    a.statusColor === b.statusColor &&
    a.onToggleExpand === b.onToggleExpand &&
    a.competency.status === b.competency.status &&
    a.competency.priority === b.competency.priority &&
    a.competency.title === b.competency.title
  )
}

function samePhaseData(a: PhaseNodeData, b: PhaseNodeData): boolean {
  return (
    a.title === b.title &&
    a.isCurrent === b.isCurrent &&
    a.width === b.width &&
    a.height === b.height &&
    a.empty === b.empty &&
    a.collapsed === b.collapsed &&
    a.totalCount === b.totalCount &&
    a.verifiedCount === b.verifiedCount &&
    a.currentBusy === b.currentBusy &&
    a.tracks.length === b.tracks.length &&
    a.tracks.every((track, index) => sameTrack(track, b.tracks[index])) &&
    a.onTogglePhaseCollapse === b.onTogglePhaseCollapse &&
    a.focusPhaseRef === b.focusPhaseRef &&
    a.onMakeCurrent === b.onMakeCurrent
  )
}

/**
 * Merges freshly computed layout nodes into the React Flow node state while
 * preserving the object identity of every node whose geometry and rendered
 * data are unchanged. React Flow skips re-renders for reused node objects, so
 * interactions that touch only a couple of nodes (selection, panel open and
 * close) no longer re-render the whole graph. Nodes whose geometry or data
 * actually changed (drag positions, expand/collapse, moved phases) are
 * replaced individually.
 *
 * Selection is applied here rather than inside `computeRoadmapLayout`: the
 * layout function stays geometry-only, so detail-panel state cannot
 * invalidate it.
 */
export function mergeLayoutNodes(
  previous: RoadmapFlowNode[],
  layout: RoadmapLayout,
  draggedLocal: ReadonlyMap<string, RenderPoint>,
  selectedId: string | null,
): RoadmapFlowNode[] {
  const previousById = new Map(previous.map((node) => [node.id, node]))
  return layout.nodes.map((layoutNode) => {
    const existing = previousById.get(layoutNode.id)
    if (layoutNode.type === 'competency') {
      if (existing === undefined || existing.type !== 'competency') {
        return selectNode(layoutNode, layoutNode.id === selectedId)
      }
      const dragged = draggedLocal.get(layoutNode.id)
      const origin = dragged ? phaseOriginFor(layout, layoutNode.id) : null
      const position = dragged && origin ? { x: origin.x + dragged.x, y: origin.y + dragged.y } : layoutNode.position
      const selected = layoutNode.id === selectedId
      const unchanged =
        samePoint(existing.position, position) &&
        existing.data.selected === selected &&
        sameCompetencyData(existing.data, layoutNode.data)
      if (unchanged) return existing
      return { ...existing, position, data: { ...layoutNode.data, selected } }
    }
    if (existing === undefined || existing.type !== 'phase') {
      return layoutNode
    }
    const unchanged =
      samePoint(existing.position, layoutNode.position) &&
      existing.width === layoutNode.width &&
      existing.height === layoutNode.height &&
      samePhaseData(existing.data, layoutNode.data)
    if (unchanged) return existing
    return {
      ...existing,
      position: layoutNode.position,
      width: layoutNode.width,
      height: layoutNode.height,
      data: layoutNode.data,
    }
  })
}

function selectNode(node: CompetencyFlowNode, selected: boolean): CompetencyFlowNode {
  return { ...node, data: { ...node.data, selected } }
}

/**
 * Derives the currently displayable prerequisite edges. This is a pure
 * visibility projection over `layout.prereqEdges`: geometry is never
 * recomputed for disclosure changes. Selected-node edges are emphasised and
 * labelled; a global reveal renders every edge subordinate to the tree.
 */
export function buildVisiblePrereqEdges(
  prereqEdges: RoadmapFlowEdge[],
  showAll: boolean,
  selectedId: string | null,
): RoadmapFlowEdge[] {
  return prereqEdges
    .filter((edge) => showAll || edge.source === selectedId || edge.target === selectedId)
    .map((edge) => {
      const required = edge.data?.kind === 'required'
      const focused = selectedId !== null && (edge.source === selectedId || edge.target === selectedId)
      const stroke = required ? PREREQ_REQUIRED_COLOR : PREREQ_RECOMMENDED_COLOR
      return {
        ...edge,
        style: {
          stroke,
          strokeWidth: focused ? 2 : required ? 1.75 : 1.25,
          strokeDasharray: required ? undefined : '5 5',
          opacity: showAll && !focused ? 0.6 : 1,
        },
        label: focused ? (required ? 'required' : 'recommended') : undefined,
        labelStyle: { fill: '#3d4b44', fontSize: 11, fontWeight: 600 },
        labelBgStyle: { fill: '#fbfaf6', fillOpacity: 0.9 },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 4,
        zIndex: focused ? 10 : 0,
      }
    })
}
