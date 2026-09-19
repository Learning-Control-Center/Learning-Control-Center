import { describe, expect, it } from 'vitest'

import { adaptRoadmapProjectionNode, type RoadmapProjectionNode } from './projection'

const baseNode: RoadmapProjectionNode = {
  id: 'competency-1',
  nodeKey: 'competency-1',
  semanticDefinitionId: 'definition-1',
  stableKey: 'capability.one',
  title: 'Capability one',
  profileDomain: null,
  profileTarget: null,
  capability: { scopes: [] },
  position: { x: 0, y: 0 },
  positionSource: 'roadmap-layout/v2.0',
  presentationParentId: null,
  isCurrent: false,
  isToday: false,
}

describe('Roadmap projection compatibility adapter', () => {
  it('adapts a frozen v2 node without inventing target truth', () => {
    const adapted = adaptRoadmapProjectionNode(baseNode)

    expect(adapted.profileTargets).toEqual([])
    expect(adapted.isTargeted).toBe(false)
    expect(adapted.layoutLane).toEqual({
      id: 'graph-foundations',
      title: 'Graph foundations',
      orderIndex: 999,
    })
  })

  it('preserves every v3 target and canonical lane', () => {
    const profileTargets = [
      {
        id: 'target-a',
        priority: 'core',
        targetLevelOrdinal: 2,
        dimensionKey: 'reading',
      },
      {
        id: 'target-b',
        priority: 'important',
        targetLevelOrdinal: 3,
        dimensionKey: 'speaking',
      },
    ]
    const adapted = adaptRoadmapProjectionNode({
      ...baseNode,
      profileTargets,
      isTargeted: true,
      layoutLane: { id: 'cross-domain-targets', title: 'Cross-domain targets', orderIndex: 0 },
      positionSource: 'roadmap-layout/v3.0',
    })

    expect(adapted.profileTargets).toEqual(profileTargets)
    expect(adapted.isTargeted).toBe(true)
    expect(adapted.layoutLane.id).toBe('cross-domain-targets')
  })
})
