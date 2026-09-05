import { MarkerType, type Edge, type Node } from '@xyflow/react'

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
export const NODE_HEIGHT = 112
export const PHASE_MIN_WIDTH = 360
export const PHASE_HEADER_HEIGHT = 64
export const PHASE_PADDING_X = 20
export const PHASE_PADDING_TOP = 12
export const PHASE_PADDING_BOTTOM = 18
export const LANE_LABEL_WIDTH = 148
export const NODE_GAP_X = 28
export const NODE_GAP_Y = 20
export const PHASE_GAP = 56
export const LANE_GAP = 14
export const EMPTY_LANE_HEIGHT = 44

export const statusColor: Record<Status, string> = {
  not_started: '#9ca39e',
  learning: '#3c82a0',
  practicing: '#bd7b2d',
  ready_for_verification: '#7357a5',
  verified: '#326653',
  needs_review: '#b24f5c',
}

type Point = { x: number; y: number }

export function toCanvasPosition(origin: Point, local: Point): Point {
  return { x: origin.x + local.x, y: origin.y + local.y }
}

export function toPhaseLocalPosition(origin: Point, canvas: Point): Point {
  return { x: Math.round(canvas.x - origin.x), y: Math.round(canvas.y - origin.y) }
}

export type LaneLayout = { trackId: string; title: string; y: number; height: number }

export type PhaseLayout = {
  phase: Phase
  origin: Point
  width: number
  height: number
  lanes: LaneLayout[]
  hasNodes: boolean
}

export type CompetencyNodeData = {
  competency: Competency
  childCount: number
  expanded: boolean
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
  title: string
  orderIndex: number
  isCurrent: boolean
  width: number
  height: number
  lanes: LaneLayout[]
  empty: boolean
  [key: string]: unknown
}

export type CompetencyFlowNode = Node<CompetencyNodeData, 'competency'>
export type PhaseFlowNode = Node<PhaseNodeData, 'phase'>
export type RoadmapFlowNode = CompetencyFlowNode | PhaseFlowNode

export type RoadmapLayout = {
  nodes: RoadmapFlowNode[]
  edges: Edge[]
  phases: PhaseLayout[]
  visible: Competency[]
  childrenByParent: Map<string, Competency[]>
  phaseByDefinition: Map<string, string>
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
): { all: Competency[]; visible: Competency[]; childrenByParent: Map<string, Competency[]> } {
  const all = roadmap.phases.flatMap((phase) => phase.tracks.flatMap((track) => track.competencies))
  const byDefinition = new Map(all.map((item) => [item.definitionId, item]))
  const childrenByParent = new Map<string, Competency[]>()
  all.forEach((item) => {
    if (item.parentDefinitionId) {
      childrenByParent.set(item.parentDefinitionId, [...(childrenByParent.get(item.parentDefinitionId) ?? []), item])
    }
  })
  const visible = all.filter((item) => {
    if (!item.parentDefinitionId) return true
    let parentId: string | null = item.parentDefinitionId
    while (parentId) {
      if (!expanded.has(parentId)) return false
      parentId = byDefinition.get(parentId)?.parentDefinitionId ?? null
    }
    return true
  })
  return { all, visible, childrenByParent }
}

type Block = { item: Competency; width: number; height: number; children: Block[] }

function layoutLane(
  items: Competency[],
  childrenByParent: Map<string, Competency[]>,
  expanded: ReadonlySet<string>,
): { positions: Map<string, Point>; width: number; height: number } {
  const laneIds = new Set(items.map((item) => item.definitionId))
  const roots = items
    .filter((item) => !item.parentDefinitionId || !laneIds.has(item.parentDefinitionId))
    .sort(byOrderThenKey)
  const laneChildren = (item: Competency) =>
    (childrenByParent.get(item.definitionId) ?? [])
      .filter((child) => laneIds.has(child.definitionId))
      .sort(byOrderThenKey)

  const buildBlock = (item: Competency): Block => {
    const children = expanded.has(item.definitionId) ? laneChildren(item).map(buildBlock) : []
    const rowWidth =
      children.reduce((total, child) => total + child.width, 0) + NODE_GAP_X * Math.max(0, children.length - 1)
    return {
      item,
      width: Math.max(NODE_WIDTH, rowWidth),
      height:
        NODE_HEIGHT +
        (children.length ? NODE_GAP_Y + Math.max(...children.map((child) => child.height)) : 0),
      children,
    }
  }

  const positions = new Map<string, Point>()
  const place = (block: Block, x: number, y: number) => {
    positions.set(block.item.definitionId, { x, y })
    let childX = x
    block.children.forEach((child) => {
      place(child, childX, y + NODE_HEIGHT + NODE_GAP_Y)
      childX += child.width + NODE_GAP_X
    })
  }

  let cursor = 0
  let height = 0
  roots.map(buildBlock).forEach((block) => {
    place(block, cursor, 0)
    cursor += block.width + NODE_GAP_X
    height = Math.max(height, block.height)
  })
  return {
    positions,
    width: roots.length ? cursor - NODE_GAP_X : 0,
    height: roots.length ? height : EMPTY_LANE_HEIGHT,
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
): { positions: Map<string, Point>; lanes: LaneLayout[]; width: number; height: number; hasNodes: boolean } {
  const tracks = [...phase.tracks].sort((a, b) => a.orderIndex - b.orderIndex)
  const lanes: LaneLayout[] = []
  const slots = new Map<string, Point>()
  const items: Competency[] = []
  const nodeStartX = PHASE_PADDING_X + LANE_LABEL_WIDTH
  let laneY = PHASE_HEADER_HEIGHT + PHASE_PADDING_TOP
  let contentRight = PHASE_PADDING_X
  let contentBottom = laneY

  tracks.forEach((track) => {
    const laneItems = track.competencies
      .filter((item) => visible.has(item.definitionId))
      .sort(byOrderThenKey)
    items.push(...laneItems)
    const lane = layoutLane(laneItems, childrenByParent, expanded)
    lane.positions.forEach((position, definitionId) => {
      slots.set(definitionId, { x: position.x + nodeStartX, y: position.y + laneY })
    })
    lanes.push({ trackId: track.id, title: track.title, y: laneY, height: lane.height })
    contentRight = Math.max(contentRight, nodeStartX + lane.width)
    contentBottom = laneY + lane.height
    laneY += lane.height + LANE_GAP
  })

  // Phase geometry is defined only by the deterministic automatic layout.
  // Free-form visual overrides neither constrain nor inflate semantic phase
  // containers, keeping every phase origin stable across drag/reload.
  const contentWidth = Math.max(PHASE_MIN_WIDTH, contentRight + PHASE_PADDING_X)
  const contentHeight = (items.length ? contentBottom : laneY + 24) + PHASE_PADDING_BOTTOM
  const positions = resolvePhasePositions(items, slots)

  return {
    positions,
    lanes,
    width: contentWidth,
    height: contentHeight,
    hasNodes: items.length > 0,
  }
}

export function computeRoadmapLayout(options: {
  roadmap: Roadmap
  expanded: ReadonlySet<string>
  onToggleExpand: (definitionId: string) => void
}): RoadmapLayout {
  const { roadmap, expanded, onToggleExpand } = options
  const { all, visible, childrenByParent } = computeVisibleRoadmap(roadmap, expanded)
  const visibleIds = new Set(visible.map((item) => item.definitionId))
  const identityToDefinition = new Map(all.map((item) => [item.identityId, item.definitionId]))
  const titleByDefinition = new Map(all.map((item) => [item.definitionId, item.title]))

  const phases: PhaseLayout[] = []
  const phaseByDefinition = new Map<string, string>()
  const canvasPositions = new Map<string, Point>()
  let cursorX = 0
  ;[...roadmap.phases]
    .sort((a, b) => a.orderIndex - b.orderIndex)
    .forEach((phase) => {
      const laidOut = layoutPhase(phase, visibleIds, childrenByParent, expanded)
      const origin = { x: cursorX, y: 0 }
      laidOut.positions.forEach((local, definitionId) => {
        canvasPositions.set(definitionId, toCanvasPosition(origin, local))
        phaseByDefinition.set(definitionId, phase.id)
      })
      phases.push({
        phase,
        origin,
        width: laidOut.width,
        height: laidOut.height,
        lanes: laidOut.lanes,
        hasNodes: laidOut.hasNodes,
      })
      cursorX += laidOut.width + PHASE_GAP
    })

  const phaseNodes: PhaseFlowNode[] = phases.map((layout) => ({
    id: `phase-${layout.phase.id}`,
    type: 'phase',
    position: layout.origin,
    data: {
      title: layout.phase.title,
      orderIndex: layout.phase.orderIndex,
      isCurrent: layout.phase.isCurrent,
      width: layout.width,
      height: layout.height,
      lanes: layout.lanes,
      empty: !layout.hasNodes,
    },
    width: layout.width,
    height: layout.height,
    selectable: false,
    draggable: false,
    connectable: false,
    focusable: false,
    style: { zIndex: -1, pointerEvents: 'none' },
  }))

  const competencyNodes: CompetencyFlowNode[] = visible.map((item) => ({
    id: item.definitionId,
    type: 'competency',
    position: canvasPositions.get(item.definitionId) ?? { x: 0, y: 0 },
    data: {
      competency: item,
      childCount: (childrenByParent.get(item.definitionId) ?? []).length,
      expanded: expanded.has(item.definitionId),
      statusColor: statusColor[item.status],
      onToggleExpand,
    },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    ariaLabel: `${item.title}, status ${item.status.replaceAll('_', ' ')}, priority ${item.priority}`,
  }))

  const edges: Edge[] = visible.flatMap((item) => {
    const hierarchy: Edge[] =
      item.parentDefinitionId && visibleIds.has(item.parentDefinitionId)
        ? [
            {
              id: `parent-${item.parentDefinitionId}-${item.definitionId}`,
              source: item.parentDefinitionId,
              target: item.definitionId,
              type: 'smoothstep',
              sourceHandle: 'out-hierarchy',
              targetHandle: 'in-hierarchy',
              style: { stroke: '#b7c2ba', strokeWidth: 1.5 },
              ariaLabel: `${titleByDefinition.get(item.parentDefinitionId) ?? 'Parent'} contains ${item.title}`,
            },
          ]
        : []
    const prerequisites: Edge[] = item.prerequisites.flatMap((prerequisite) => {
      const source = identityToDefinition.get(prerequisite.identityId)
      if (!source || source === item.definitionId || !visibleIds.has(source)) return []
      const required = prerequisite.kind === 'required'
      const stroke = required ? '#326653' : '#93a69b'
      return [
        {
          id: `prereq-${source}-${item.definitionId}`,
          source,
          target: item.definitionId,
          type: 'smoothstep',
          sourceHandle: 'out-prereq',
          targetHandle: 'in-prereq',
          style: {
            stroke,
            strokeWidth: required ? 2 : 1.5,
            strokeDasharray: required ? undefined : '6 6',
          },
          markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 18, height: 18 },
          ariaLabel: `${titleByDefinition.get(source) ?? prerequisite.stableKey} is a ${prerequisite.kind} prerequisite for ${item.title}`,
        },
      ]
    })
    return [...hierarchy, ...prerequisites]
  })

  return {
    nodes: [...phaseNodes, ...competencyNodes],
    edges,
    phases,
    visible,
    childrenByParent,
    phaseByDefinition,
  }
}

type RenderPoint = { x: number; y: number }

function samePoint(a: RenderPoint, b: RenderPoint): boolean {
  return Math.abs(a.x - b.x) <= 0.01 && Math.abs(a.y - b.y) <= 0.01
}

function sameLane(a: LaneLayout, b: LaneLayout): boolean {
  return (
    a.trackId === b.trackId &&
    a.title === b.title &&
    a.y === b.y &&
    a.height === b.height
  )
}

function sameCompetencyData(a: CompetencyNodeData, b: CompetencyNodeData): boolean {
  return (
    a.childCount === b.childCount &&
    a.expanded === b.expanded &&
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
    a.orderIndex === b.orderIndex &&
    a.isCurrent === b.isCurrent &&
    a.width === b.width &&
    a.height === b.height &&
    a.empty === b.empty &&
    a.lanes.length === b.lanes.length &&
    a.lanes.every((lane, index) => sameLane(lane, b.lanes[index]))
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
