import { MarkerType, type CoordinateExtent, type Edge, type Node } from '@xyflow/react'

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
 * Validation policy: a persisted position is honoured at render time only
 * when the whole competency card fits inside the usable content area of its
 * phase container — below the phase header, to the right of the track-label
 * gutter, and inside the container's deterministic width and height. Invalid
 * stored values (negative coordinates, header/gutter overlaps, values beyond
 * the container) are ignored for rendering and the node falls back to its
 * deterministic automatic slot position. Stored values are never migrated or
 * reinterpreted; only an explicit drag (which stores within-bounds values) or
 * the Reset Layout action (which clears them) changes storage.
 */

export const NODE_WIDTH = 232
export const NODE_HEIGHT = 112
export const PHASE_MIN_WIDTH = 360
export const PHASE_HEADER_HEIGHT = 64
export const PHASE_PADDING_X = 20
export const PHASE_PADDING_TOP = 12
export const PHASE_PADDING_BOTTOM = 18
export const PHASE_GAP = 56
export const LANE_LABEL_WIDTH = 148
export const LANE_GAP = 14
export const NODE_GAP_X = 28
export const NODE_GAP_Y = 20
export const EMPTY_LANE_HEIGHT = 44

/** Phase-local content area where competency cards may render and be dragged. */
export type ContentBounds = { minX: number; minY: number; maxX: number; maxY: number }

export function usableContentBounds(containerWidth: number, containerHeight: number): ContentBounds {
  return {
    minX: PHASE_PADDING_X + LANE_LABEL_WIDTH,
    minY: PHASE_HEADER_HEIGHT + PHASE_PADDING_TOP,
    maxX: containerWidth - PHASE_PADDING_X - NODE_WIDTH,
    maxY: containerHeight - PHASE_PADDING_BOTTOM - NODE_HEIGHT,
  }
}

export function containsPoint(bounds: ContentBounds, point: Point): boolean {
  return point.x >= bounds.minX && point.y >= bounds.minY && point.x <= bounds.maxX && point.y <= bounds.maxY
}

export function clampToContentBounds(bounds: ContentBounds, point: Point): Point {
  return {
    x: Math.min(Math.max(point.x, bounds.minX), Math.max(bounds.minX, bounds.maxX)),
    y: Math.min(Math.max(point.y, bounds.minY), Math.max(bounds.minY, bounds.maxY)),
  }
}

export const statusColor: Record<Status, string> = {
  not_started: '#9ca39e',
  learning: '#3c82a0',
  practicing: '#bd7b2d',
  ready_for_verification: '#7357a5',
  verified: '#326653',
  needs_review: '#b24f5c',
}

type Point = { x: number; y: number }
type Rect = { x: number; y: number; width: number; height: number }

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

function intersects(a: Rect, b: Rect): boolean {
  return (
    a.x < b.x + b.width + NODE_GAP_X / 2 &&
    a.x + a.width + NODE_GAP_X / 2 > b.x &&
    a.y < b.y + b.height + NODE_GAP_Y / 2 &&
    a.y + a.height + NODE_GAP_Y / 2 > b.y
  )
}

function persistedPosition(item: Competency): Point | null {
  return item.position.x === null || item.position.y === null ? null : { x: item.position.x, y: item.position.y }
}

/**
 * Resolves the final phase-local position for every item. Persisted positions
 * pass through only when the caller validated them against the usable content
 * area (`persisted` returns non-null); everything else uses the deterministic
 * slot position. Valid persisted positions are de-collided against each other
 * and against slot positions without ever touching stored values.
 */
function resolvePhasePositions(
  items: Competency[],
  slots: Map<string, Point>,
  persisted: (item: Competency) => Point | null,
): Map<string, Point> {
  const ordered = [...items].sort((a, b) => {
    const persistedA = persisted(a) ? 0 : 1
    const persistedB = persisted(b) ? 0 : 1
    if (persistedA !== persistedB) return persistedA - persistedB
    const slotA = slots.get(a.definitionId) ?? { x: 0, y: 0 }
    const slotB = slots.get(b.definitionId) ?? { x: 0, y: 0 }
    return slotA.y - slotB.y || slotA.x - slotB.x
  })
  const placed: Rect[] = []
  const resolved = new Map<string, Point>()
  ordered.forEach((item) => {
    const slot = slots.get(item.definitionId) ?? { x: 0, y: 0 }
    const candidate = persisted(item) ?? slot
    let { x, y } = candidate
    let attempts = 0
    while (
      attempts < 240 &&
      placed.some((rect) => intersects(rect, { x, y, width: NODE_WIDTH, height: NODE_HEIGHT }))
    ) {
      x += NODE_WIDTH + NODE_GAP_X
      attempts += 1
      if (attempts % 12 === 0) {
        x = PHASE_PADDING_X + LANE_LABEL_WIDTH
        y += NODE_HEIGHT + NODE_GAP_Y
      }
    }
    placed.push({ x, y, width: NODE_WIDTH, height: NODE_HEIGHT })
    resolved.set(item.definitionId, { x, y })
  })
  return resolved
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

  // Deterministic container geometry first: lane content defines the size,
  // independent of any persisted positions. Validation below then accepts a
  // persisted position only when the whole card fits inside this box, so
  // invalid stored values can neither escape the container nor inflate it.
  const contentWidth = Math.max(PHASE_MIN_WIDTH, contentRight + PHASE_PADDING_X)
  const contentHeight = (items.length ? contentBottom : laneY + 24) + PHASE_PADDING_BOTTOM
  const usable = usableContentBounds(contentWidth, contentHeight)
  const validatedPersisted = (item: Competency): Point | null => {
    const persisted = persistedPosition(item)
    return persisted && containsPoint(usable, persisted) ? persisted : null
  }

  const positions = resolvePhasePositions(items, slots, validatedPersisted)
  positions.forEach((position) => {
    contentRight = Math.max(contentRight, position.x + NODE_WIDTH)
    contentBottom = Math.max(contentBottom, position.y + NODE_HEIGHT)
  })

  return {
    positions,
    lanes,
    width: Math.max(contentWidth, contentRight + PHASE_PADDING_X),
    height: (items.length ? contentBottom : laneY + 24) + PHASE_PADDING_BOTTOM,
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
  const phasesById = new Map(layout.phases.map((phase) => [phase.phase.id, phase]))
  return layout.nodes.map((layoutNode) => {
    const existing = previousById.get(layoutNode.id)
    if (layoutNode.type === 'competency') {
      const extent = dragExtentFor(layout, layoutNode, phasesById)
      if (existing === undefined || existing.type !== 'competency') {
        return { ...selectNode(layoutNode, layoutNode.id === selectedId), extent }
      }
      const dragged = draggedLocal.get(layoutNode.id)
      const origin = dragged ? phaseOriginFor(layout, layoutNode.id) : null
      const position = dragged && origin ? { x: origin.x + dragged.x, y: origin.y + dragged.y } : layoutNode.position
      const selected = layoutNode.id === selectedId
      const unchanged =
        samePoint(existing.position, position) &&
        existing.data.selected === selected &&
        sameExtent(existing.extent, extent) &&
        sameCompetencyData(existing.data, layoutNode.data)
      if (unchanged) return existing
      return { ...existing, position, extent, data: { ...layoutNode.data, selected } }
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

function dragExtentFor(
  layout: RoadmapLayout,
  layoutNode: CompetencyFlowNode,
  phasesById: Map<string, PhaseLayout>,
): CoordinateExtent | undefined {
  const phaseId = layout.phaseByDefinition.get(layoutNode.id)
  const phase = phaseId ? phasesById.get(phaseId) : undefined
  if (!phase) return undefined
  const origin = phase.origin
  return [
    [origin.x + PHASE_PADDING_X + LANE_LABEL_WIDTH, origin.y + PHASE_HEADER_HEIGHT + PHASE_PADDING_TOP],
    [origin.x + phase.width - PHASE_PADDING_X, origin.y + phase.height - PHASE_PADDING_BOTTOM],
  ]
}

function sameExtent(a: CoordinateExtent | 'parent' | null | undefined, b: CoordinateExtent | undefined): boolean {
  if (typeof a !== 'object' || a === null || b === undefined) return a === b
  return (
    Math.abs(a[0][0] - b[0][0]) <= 0.01 &&
    Math.abs(a[0][1] - b[0][1]) <= 0.01 &&
    Math.abs(a[1][0] - b[1][0]) <= 0.01 &&
    Math.abs(a[1][1] - b[1][1]) <= 0.01
  )
}

function selectNode(node: CompetencyFlowNode, selected: boolean): CompetencyFlowNode {
  return { ...node, data: { ...node.data, selected } }
}
