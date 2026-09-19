import { Background, Controls, ReactFlow, type Edge, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ChevronDown, ChevronRight, GitBranch, RefreshCw, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, apiV2 } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

type ProjectionNode = {
  id: string
  nodeKey: string
  semanticDefinitionId: string
  stableKey: string
  title: string
  profileDomain: { id: string; title: string; orderIndex: number } | null
  profileTarget: { priority: string; targetLevelOrdinal: number | null } | null
  capability: {
    scopes: {
      scopeKey: string
      assessmentStatus: string
      confidence: string
      freshness: string
      reviewDue: boolean | null
    }[]
  }
  position: { x: number; y: number }
  positionSource: string
  presentationParentId: string | null
  isCurrent: boolean
  isToday: boolean
}

type ProjectionEdge = {
  id: string
  edgeType: string
  source: string
  target: string
  visibleByDefault: boolean
  eligibilityAuthority: boolean
  satisfaction: { aggregate_state: string; unknown_reasons: string[] }
}

type Projection = {
  configured: boolean
  guidance?: string
  authority: string
  scopeKey?: string
  projectionPolicyVersion?: string
  layoutPolicyVersion?: string
  outputHash?: string
  relationshipVisibility?: Record<string, boolean>
  groups?: { id: string; title: string; orderIndex: number }[]
  nodes?: ProjectionNode[]
  edges?: ProjectionEdge[]
  legacyPhaseAuthority?: boolean
}

export function RoadmapV2Page() {
  const [projection, setProjection] = useState<Projection | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [showPrerequisites, setShowPrerequisites] = useState(false)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const value = await apiV2<Projection>('/roadmap-projection/current')
      setProjection(value)
      setShowPrerequisites(Boolean(value.relationshipVisibility?.prerequisite))
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Roadmap V2 could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => void load(), [load])

  const mutate = async (action: 'rebuild' | 'reset') => {
    if (!projection?.scopeKey && action === 'reset') return
    setWorking(true)
    setError('')
    try {
      const value = await apiV2<Projection | { projection: Projection }>(
        action === 'rebuild'
          ? '/roadmap-projection/rebuild'
          : `/roadmap-projection/${projection?.scopeKey}/positions`,
        { method: action === 'rebuild' ? 'POST' : 'DELETE' },
      )
      setProjection('projection' in value ? value.projection : value)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Roadmap V2 could not be updated.')
    } finally {
      setWorking(false)
    }
  }

  const groups = useMemo(() => {
    const nodes = projection?.nodes ?? []
    const grouped = (projection?.groups ?? []).map((group) => ({
      ...group,
      nodes: nodes.filter((node) => node.profileDomain?.id === group.id),
    }))
    const ungrouped = nodes.filter((node) => node.profileDomain === null)
    return ungrouped.length
      ? [...grouped, { id: 'graph-foundations', title: 'Graph foundations', orderIndex: 999, nodes: ungrouped }]
      : grouped
  }, [projection])

  const savePosition = async (node: Node) => {
    if (!projection?.scopeKey) return
    try {
      await apiV2(`/roadmap-projection/${projection.scopeKey}/positions/${node.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          node_key: node.id,
          position_x: Math.round(node.position.x),
          position_y: Math.round(node.position.y),
        }),
      })
      setProjection((current) => current ? {
        ...current,
        nodes: current.nodes?.map((item) => item.id === node.id
          ? { ...item, position: { x: Math.round(node.position.x), y: Math.round(node.position.y) }, positionSource: 'user_override' }
          : item),
      } : current)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The position could not be saved.')
    }
  }

  if (loading) return <LoadingState label="Building the V2 learning projection" />
  if (error && !projection) return <ErrorState message={error} retry={() => void load()} />
  if (!projection?.configured) {
    return <EmptyState title="Roadmap V2 is not configured" detail={projection?.guidance ?? ''} />
  }

  const visibleEdges = (projection.edges ?? []).filter(
    (edge) => edge.edgeType !== 'prerequisite' || showPrerequisites,
  )
  const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  const hiddenNodeIds = new Set(
    groups
      .filter((group) => collapsed.has(group.id))
      .flatMap((group) => group.nodes.map((node) => node.id)),
  )
  const flowNodes: Node[] = (projection.nodes ?? [])
    .filter((node) => !hiddenNodeIds.has(node.id))
    .map((node) => ({
      id: node.id,
      position: node.position,
      data: {
        label: `${node.presentationParentId ? '↳ ' : ''}${node.title}${node.isToday ? ' · Today' : ''}\n${node.capability.scopes[0]?.assessmentStatus ?? 'Capability unknown'}`,
      },
      ariaLabel: `${node.title}, ${node.profileDomain?.title ?? 'graph foundation'}${node.presentationParentId ? ', specialization child' : ''}${node.isToday ? ', recommended for Today' : ''}`,
      style: {
        width: 220,
        borderRadius: 16,
        border: node.isToday ? '3px solid #9a5c28' : node.profileTarget ? '2px solid #3f7258' : '1px solid #b8b4a7',
        background: node.isToday ? '#fff4df' : '#fffdf7',
        whiteSpace: 'pre-line',
      },
    }))
  const visibleNodeIds = new Set(flowNodes.map((node) => node.id))
  const flowEdges: Edge[] = visibleEdges
    .filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.edgeType.replaceAll('_', ' '),
      animated: !prefersReducedMotion && edge.edgeType === 'prerequisite' && edge.satisfaction.aggregate_state !== 'met',
    }))
  return (
    <section className="space-y-6" aria-labelledby="roadmap-v2-heading">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-fern">Canonical V2 projection</p>
          <h1 id="roadmap-v2-heading" className="font-display text-3xl font-semibold text-ink">Roadmap Projection</h1>
          <p className="mt-2 max-w-3xl text-sm text-ink/65">
            Derived from the active Target Profile and native Learning Graph. Legacy phases never decide eligibility here.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="button-secondary" disabled={working} onClick={() => void mutate('rebuild')}>
            <RefreshCw className="size-4" aria-hidden="true" /> Rebuild
          </button>
          <button className="button-secondary" disabled={working} onClick={() => void mutate('reset')}>
            <RotateCcw className="size-4" aria-hidden="true" /> Reset layout
          </button>
        </div>
      </header>
      {error ? <ErrorState message={error} retry={() => void load()} /> : null}
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-4">
        <label className="flex min-h-11 items-center gap-3 text-sm font-medium text-ink">
          <input
            type="checkbox"
            checked={showPrerequisites}
            onChange={(event) => setShowPrerequisites(event.target.checked)}
          />
          Show prerequisite relationships
        </label>
        <p className="font-mono text-xs text-ink/55">
          {projection.projectionPolicyVersion} · {projection.layoutPolicyVersion}
        </p>
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          <div className="panel h-[38rem] overflow-hidden" aria-label="Roadmap Projection graph">
            <ReactFlow
              key={`${projection.outputHash ?? projection.scopeKey}:${showPrerequisites}:${[...collapsed].sort().join(',')}`}
              defaultNodes={flowNodes}
              defaultEdges={flowEdges}
              fitView
              minZoom={0.25}
              onNodeDragStop={(_event, node) => void savePosition(node)}
            >
              <Background />
              <Controls />
            </ReactFlow>
          </div>
          {groups.map((group) => {
            const isCollapsed = collapsed.has(group.id)
            return (
              <article key={group.id} className="panel overflow-hidden">
                <button
                  className="flex min-h-12 w-full items-center justify-between border-b border-ink/10 px-5 py-3 text-left"
                  onClick={() => setCollapsed((current) => {
                    const next = new Set(current)
                    if (next.has(group.id)) next.delete(group.id)
                    else next.add(group.id)
                    return next
                  })}
                  aria-expanded={!isCollapsed}
                >
                  <span className="font-display text-lg font-semibold">{group.title}</span>
                  {isCollapsed ? <ChevronRight className="size-4" /> : <ChevronDown className="size-4" />}
                </button>
                {!isCollapsed ? (
                  <p className="px-5 py-3 text-sm text-ink/60">
                    {group.nodes.length} nodes · specialization hierarchy and saved positions shown in the graph
                  </p>
                ) : null}
              </article>
            )
          })}
        </div>
        <aside className="panel h-fit p-5">
          <div className="mb-4 flex items-center gap-2">
            <GitBranch className="size-4 text-fern" aria-hidden="true" />
            <h2 className="font-display font-semibold">Relationships</h2>
          </div>
          <ul className="space-y-3 text-sm">
            {visibleEdges.map((edge) => (
              <li key={edge.id} className="rounded-xl border border-ink/10 p-3">
                <p className="font-medium">{edge.edgeType.replaceAll('_', ' ')}</p>
                <p className="mt-1 text-xs text-ink/55">{edge.satisfaction.aggregate_state}</p>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </section>
  )
}
