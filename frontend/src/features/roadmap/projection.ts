export type RoadmapProfileTarget = {
  id?: string
  identityId?: string
  stableKey?: string
  competencyIdentityId?: string
  dimensionKey?: string | null
  dimensionId?: string | null
  dimensionTitle?: string | null
  profileDomain?: { id: string; stableKey?: string; title: string; orderIndex: number }
  priority: string
  targetLevelId?: string
  targetLevelKey?: string
  targetLevelTitle?: string
  targetLevelOrdinal: number | null
  scaleVersionId?: string
  scaleStableKey?: string
  scaleVersion?: string
  targetDate?: string | null
  targetMonth?: string | null
  comparisonStateReference?: {
    snapshotId: string
    gapStableKey: string
    comparisonStatus: string
  } | null
}

export type RoadmapProjectionNode = {
  id: string
  nodeKey: string
  semanticDefinitionId: string
  stableKey: string
  title: string
  description?: string | null
  profileDomain: { id: string; title: string; orderIndex: number } | null
  profileTarget: RoadmapProfileTarget | null
  profileTargets?: RoadmapProfileTarget[]
  capability: {
    scopes: {
      scopeKey: string
      assessmentStatus: string
      confidence: string
      freshness: string
      reviewDue: boolean | null
      levelId?: string | null
      levelKey?: string | null
      levelTitle?: string | null
    }[]
  }
  canonicalPosition?: { x: number; y: number }
  position: { x: number; y: number }
  positionSource: string
  presentationParentId: string | null
  isCurrent: boolean
  isTargeted?: boolean
  isToday: boolean
  layoutLane?: { id: string; title: string; orderIndex: number }
  layoutColumn?: number
  layoutRow?: number
}

export type RoadmapProjectionEdge = {
  id: string
  edgeType: 'prerequisite' | 'recommended_before' | 'supports' | 'specialization' | 'related'
  source: string
  target: string
  visibleByDefault: boolean
  eligibilityAuthority: boolean
  satisfaction: {
    aggregate_state: string
    unknown_reasons: string[]
    criterion_states?: { criterionDefinitionId: string; state: string }[]
  }
}

export type RoadmapProjection = {
  configured: boolean
  guidance?: string
  authority: string
  scopeKey?: string
  projectionPolicyVersion?: string
  layoutPolicyVersion?: string
  outputHash?: string
  relationshipVisibility?: Record<string, boolean>
  nodes?: RoadmapProjectionNode[]
  edges?: RoadmapProjectionEdge[]
  legacyPhaseAuthority?: boolean
}

export type CompatibleRoadmapProjectionNode = RoadmapProjectionNode & {
  profileTargets: RoadmapProfileTarget[]
  isTargeted: boolean
  layoutLane: { id: string; title: string; orderIndex: number }
}

export function adaptRoadmapProjectionNode(
  node: RoadmapProjectionNode,
): CompatibleRoadmapProjectionNode {
  const targets = node.profileTargets ?? (node.profileTarget ? [node.profileTarget] : [])
  return {
    ...node,
    profileTargets: targets,
    isTargeted: node.isTargeted ?? node.isCurrent,
    layoutLane:
      node.layoutLane ??
      (node.profileDomain
        ? {
            id: node.profileDomain.id,
            title: node.profileDomain.title,
            orderIndex: node.profileDomain.orderIndex,
          }
        : { id: 'graph-foundations', title: 'Graph foundations', orderIndex: 999 }),
  }
}
