import {
  adaptRoadmapProjectionNode,
  type CompatibleRoadmapProjectionNode,
  type RoadmapProfileTarget,
  type RoadmapProjection,
  type RoadmapProjectionEdge,
} from './projection'

export type RoadmapTone = 'success' | 'warning' | 'critical' | 'unknown' | 'info' | 'neutral'

export type RoadmapNodeView = CompatibleRoadmapProjectionNode & {
  targetRows: RoadmapTargetRow[]
  targetLabel: string
  currentSummary: string
  targetSummary: string
  targetStatusSummary: string
  capabilityLabel: string
  confidenceLabel: string
  freshnessLabel: string
  blockedLabel: string | null
  needsAttention: boolean
  hasUnknown: boolean
  tone: RoadmapTone
  searchText: string
}

export type RoadmapTargetRow = {
  id: string
  dimension: string
  domain: string
  current: string
  target: string
  comparison: string
  confidence: string
  freshness: string
  reviewDue: boolean
  priority: string
}

export type RoadmapLaneView = {
  id: string
  title: string
  orderIndex: number
  nodes: RoadmapNodeView[]
}

export type RoadmapViewModel = {
  nodes: RoadmapNodeView[]
  nodeById: Map<string, RoadmapNodeView>
  edges: RoadmapProjectionEdge[]
  lanes: RoadmapLaneView[]
}

function capabilityForTarget(node: CompatibleRoadmapProjectionNode, target: RoadmapProfileTarget) {
  if (!node.capability.scopes.length) return undefined
  if (target.dimensionId) {
    return node.capability.scopes.find((scope) => scope.scopeKey === `dimension:${target.dimensionId}`)
  }
  if (target.dimensionKey) {
    return node.capability.scopes.find((scope) => scope.scopeKey.endsWith(`:${target.dimensionKey}`))
  }
  return node.capability.scopes.find((scope) => scope.scopeKey === 'overall')
}

function comparisonLabel(value: string | undefined) {
  if (!value) return 'Gap unknown'
  if (['met', 'target_reached', 'at_or_above', 'at_target', 'above_target'].includes(value)) return 'Target reached'
  if (['not_met', 'below_target', 'gap'].includes(value)) return 'Gap remains'
  return titleCase(value, 'Gap unknown')
}

function titleCase(value: string | null | undefined, fallback: string) {
  if (!value) return fallback
  return value.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function targetSummary(targets: RoadmapProfileTarget[]) {
  if (!targets.length) return 'Path prerequisite'
  const labels = targets.map((target) => {
    const dimension = target.dimensionTitle ?? target.dimensionKey
    const level = target.targetLevelTitle ?? target.targetLevelKey ?? (
      target.targetLevelOrdinal == null ? 'target level unknown' : `level ${target.targetLevelOrdinal}`
    )
    return `${dimension ? `${dimension}: ` : ''}${level}`
  })
  return labels.join(' · ')
}

function boundedTargetSummaries(targetRows: RoadmapTargetRow[], capabilityLabel: string) {
  if (!targetRows.length) {
    return {
      currentSummary: capabilityLabel,
      targetSummary: 'Path prerequisite',
      targetStatusSummary: 'No direct Profile target',
    }
  }
  if (targetRows.length === 1) {
    return {
      currentSummary: targetRows[0].current,
      targetSummary: targetRows[0].target,
      targetStatusSummary: `${targetRows[0].comparison} · ${targetRows[0].confidence} · ${targetRows[0].freshness}`,
    }
  }
  const unknown = targetRows.filter((target) => target.current === 'Current capability unknown').length
  const reached = targetRows.filter((target) => target.comparison === 'Target reached').length
  const gaps = targetRows.filter((target) => target.comparison === 'Gap remains').length
  const reviewDue = targetRows.filter((target) => target.reviewDue).length
  return {
    currentSummary: `${targetRows.length} target dimensions · ${targetRows.length - unknown} known · ${unknown} Unknown`,
    targetSummary: `${targetRows.length} target levels · ${reached} reached · ${gaps} gap${gaps === 1 ? '' : 's'}`,
    targetStatusSummary: `${targetRows.length} targets${reviewDue ? ` · ${reviewDue} review due` : ''}`,
  }
}

export function createRoadmapViewModel(projection: RoadmapProjection, levelTitles: ReadonlyMap<string, { key: string; title: string }> = new Map()): RoadmapViewModel {
  const edges = projection.edges ?? []
  const prerequisiteState = new Map<string, string[]>()
  for (const edge of edges) {
    if (edge.edgeType !== 'prerequisite' || edge.satisfaction.aggregate_state === 'met') continue
    const values = prerequisiteState.get(edge.target) ?? []
    values.push(edge.satisfaction.aggregate_state)
    prerequisiteState.set(edge.target, values)
  }

  const nodes = (projection.nodes ?? []).map(adaptRoadmapProjectionNode).map((node): RoadmapNodeView => {
    const scope = node.capability.scopes[0]
    const targetRows = node.profileTargets.map((target, index): RoadmapTargetRow => {
      const targetScope = capabilityForTarget(node, target)
      const level = targetScope?.levelId ? levelTitles.get(targetScope.levelId) : undefined
      return {
        id: target.identityId ?? target.id ?? `${node.id}:${target.dimensionKey ?? 'overall'}:${index}`,
        dimension: target.dimensionTitle ?? target.dimensionKey ?? 'Overall',
        domain: target.profileDomain?.title ?? node.layoutLane.title,
        current: level?.title ?? targetScope?.levelTitle ?? targetScope?.levelKey ?? 'Current capability unknown',
        target: target.targetLevelTitle ?? target.targetLevelKey ?? (target.targetLevelOrdinal == null ? 'Target level unknown' : `Level ${target.targetLevelOrdinal}`),
        comparison: comparisonLabel(target.comparisonStateReference?.comparisonStatus),
        confidence: titleCase(targetScope?.confidence, 'Confidence unknown'),
        freshness: titleCase(targetScope?.freshness, 'Freshness unknown'),
        reviewDue: Boolean(targetScope?.reviewDue),
        priority: titleCase(target.priority, 'Priority unknown'),
      }
    })
    const capabilityLabel = targetRows.length === 1
      ? targetRows[0].current
      : (scope?.levelId ? levelTitles.get(scope.levelId)?.title : undefined) ?? scope?.levelTitle ?? scope?.levelKey ?? 'Capability varies by target'
    const confidenceLabel = titleCase(scope?.confidence, 'Confidence unknown')
    const freshnessLabel = titleCase(scope?.freshness, 'Freshness unknown')
    const blocked = prerequisiteState.get(node.id)
    const blockedLabel = blocked?.length
      ? blocked.some((state) => state === 'unknown')
        ? 'Prerequisite status unknown'
        : 'Blocked by prerequisites'
      : null
    const hasUnknown = Boolean(
      blocked?.some((state) => state === 'unknown') ||
      targetRows.some((target) => target.current === 'Current capability unknown' || !['Target reached', 'Gap remains'].includes(target.comparison)) ||
      node.capability.scopes.some((candidate) => candidate.assessmentStatus === 'unknown'),
    )
    const needsAttention = Boolean(
      blockedLabel ||
      hasUnknown ||
      targetRows.some((target) => target.reviewDue) ||
      node.capability.scopes.some((candidate) => candidate.reviewDue),
    )
    const tone: RoadmapTone = blockedLabel
      ? blocked?.some((state) => state === 'unknown') ? 'unknown' : 'critical'
      : targetRows.some((target) => target.reviewDue) || scope?.reviewDue ? 'warning'
      : targetRows.length > 0 && targetRows.every((target) => target.comparison === 'Target reached') ? 'success'
      : targetRows.some((target) => target.comparison === 'Gap remains') ? 'warning'
      : targetRows.some((target) => target.current === 'Current capability unknown') ? 'unknown'
      : scope?.assessmentStatus === 'assessed' || scope?.assessmentStatus === 'evaluated' ? 'success'
      : scope ? 'info' : 'unknown'
    const targetLabel = targetSummary(node.profileTargets)
    const summaries = boundedTargetSummaries(targetRows, capabilityLabel)
    return {
      ...node,
      targetRows,
      targetLabel,
      ...summaries,
      capabilityLabel,
      confidenceLabel,
      freshnessLabel,
      blockedLabel,
      needsAttention,
      hasUnknown,
      tone,
      searchText: [
        node.title,
        node.description,
        node.stableKey,
        node.layoutLane.title,
        targetLabel,
        capabilityLabel,
      ].filter(Boolean).join(' ').toLowerCase(),
    }
  })
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const lanes = [...new Map(nodes.map((node) => [node.layoutLane.id, node.layoutLane])).values()]
    .sort((left, right) => left.orderIndex - right.orderIndex || left.id.localeCompare(right.id))
    .map((lane) => ({ ...lane, nodes: nodes.filter((node) => node.layoutLane.id === lane.id).sort((left, right) => (left.layoutColumn ?? 0) - (right.layoutColumn ?? 0) || (left.layoutRow ?? 0) - (right.layoutRow ?? 0) || left.stableKey.localeCompare(right.stableKey)) }))
  return { nodes, nodeById, edges, lanes }
}

export type RoadmapVisibility = {
  query: string
  targetedOnly: boolean
  todayOnly: boolean
  attentionOnly: boolean
  showPrerequisites: boolean
  relationshipTypes?: ReadonlySet<string>
  collapsedLanes: ReadonlySet<string>
  collapsedBranches: ReadonlySet<string>
  focusedNodeId: string | null
}

export type VisibleRoadmap = {
  nodes: RoadmapNodeView[]
  edges: RoadmapProjectionEdge[]
  temporarilyRevealed: Set<string>
  searchRevealed: Set<string>
  laneCounts: Map<string, { visible: number; total: number }>
}

export function prerequisiteClosure(focusedNodeId: string, edges: RoadmapProjectionEdge[]) {
  const incoming = new Map<string, string[]>()
  for (const edge of edges) {
    if (edge.edgeType !== 'prerequisite') continue
    incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge.source])
  }
  const closure = new Set([focusedNodeId])
  const queue = [focusedNodeId]
  while (queue.length) {
    const current = queue.shift()!
    for (const source of incoming.get(current) ?? []) {
      if (closure.has(source)) continue
      closure.add(source)
      queue.push(source)
    }
  }
  return closure
}

function branchDescendants(root: string, nodes: RoadmapNodeView[]) {
  const result = new Set<string>()
  const queue = [root]
  while (queue.length) {
    const parent = queue.shift()!
    for (const node of nodes) {
      if (node.presentationParentId !== parent || result.has(node.id)) continue
      result.add(node.id)
      queue.push(node.id)
    }
  }
  return result
}

export function applyRoadmapVisibility(model: RoadmapViewModel, state: RoadmapVisibility): VisibleRoadmap {
  const query = state.query.trim().toLowerCase()
  const visibleIds = new Set(model.nodes.filter((node) => {
    if (query && !node.searchText.includes(query)) return false
    if (state.targetedOnly && !node.isTargeted) return false
    if (state.todayOnly && !node.isToday) return false
    if (state.attentionOnly && !node.needsAttention) return false
    if (state.collapsedLanes.has(node.layoutLane.id)) return false
    return true
  }).map((node) => node.id))

  for (const branch of state.collapsedBranches) {
    for (const descendant of branchDescendants(branch, model.nodes)) visibleIds.delete(descendant)
  }

  const searchRevealed = new Set<string>()
  if (query) {
    for (const node of model.nodes) {
      if (!node.searchText.includes(query)) continue
      searchRevealed.add(node.id)
      let parentId = node.presentationParentId
      while (parentId) {
        searchRevealed.add(parentId)
        parentId = model.nodeById.get(parentId)?.presentationParentId ?? null
      }
    }
    for (const id of searchRevealed) visibleIds.add(id)
  }

  const temporarilyRevealed = state.focusedNodeId
    ? prerequisiteClosure(state.focusedNodeId, model.edges)
    : new Set<string>()
  if (state.focusedNodeId) {
    const hardPath = new Set(temporarilyRevealed)
    for (const id of hardPath) {
      let parentId = model.nodeById.get(id)?.presentationParentId ?? null
      while (parentId) {
        temporarilyRevealed.add(parentId)
        parentId = model.nodeById.get(parentId)?.presentationParentId ?? null
      }
    }
    for (const edge of model.edges) {
      const sourceOnPath = hardPath.has(edge.source)
      const targetOnPath = hardPath.has(edge.target)
      if (edge.edgeType === 'specialization' && (sourceOnPath || targetOnPath)) {
        temporarilyRevealed.add(edge.source)
        temporarilyRevealed.add(edge.target)
      }
      if (sourceOnPath && model.nodeById.get(edge.target)?.isToday) temporarilyRevealed.add(edge.target)
      if (targetOnPath && model.nodeById.get(edge.source)?.isToday) temporarilyRevealed.add(edge.source)
    }
  }
  for (const id of temporarilyRevealed) visibleIds.add(id)

  const nodes = model.nodes.filter((node) => visibleIds.has(node.id))
  const edges = model.edges.filter((edge) => {
    if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) return false
    if (edge.edgeType === 'prerequisite') {
      return state.showPrerequisites || (
        state.focusedNodeId !== null &&
        temporarilyRevealed.has(edge.source) &&
        temporarilyRevealed.has(edge.target)
      )
    }
    return state.relationshipTypes?.has(edge.edgeType) ?? edge.visibleByDefault
  })
  const laneCounts = new Map(model.lanes.map((lane) => [lane.id, {
    visible: nodes.filter((node) => node.layoutLane.id === lane.id).length,
    total: lane.nodes.length,
  }]))
  return { nodes, edges, temporarilyRevealed, searchRevealed, laneCounts }
}
