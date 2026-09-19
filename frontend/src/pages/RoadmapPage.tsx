import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  getViewportForBounds,
  useNodesState,
  type EdgeTypes,
  type NodeMouseHandler,
  type NodeTypes,
  type OnNodeDrag,
  type OnNodesChange,
  type OnInit,
  type ReactFlowInstance,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ChevronRight, Link2, ListTree, Maximize2, Network, RotateCcw, Search, ShieldCheck, Upload, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'
import { CompetencyNode, PhaseContainerNode, TreeEdge } from '../components/RoadmapNodes'
import {
  buildVisiblePrereqEdges,
  computeRoadmapLayout,
  mergeLayoutNodes,
  phaseOriginFor,
  toPhaseLocalPosition,
  type RoadmapFlowNode,
  type RoadmapLayout,
} from '../components/roadmapLayout'
import { StatusBadge } from '../components/StatusBadge'
import type { Competency, ExitCriterion, Roadmap, Status } from '../types'

type RoadmapResponse = { configured: boolean; guidance?: string; roadmap?: Roadmap }

type Notice = { id: number; text: string; tone: 'success' | 'error' }

const roadmapNodeTypes: NodeTypes = { competency: CompetencyNode, phase: PhaseContainerNode }
const roadmapEdgeTypes: EdgeTypes = { tree: TreeEdge }
const fitViewOptions = { padding: 0.14 }

export function RoadmapPage() {
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null)
  const [selected, setSelected] = useState<Competency | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [listMode, setListMode] = useState(false)
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [collapsedPhases, setCollapsedPhases] = useState<Set<string>>(new Set())
  const [showPrereqs, setShowPrereqs] = useState(false)
  const [currentBusyPhaseId, setCurrentBusyPhaseId] = useState<string | null>(null)
  const [resetOpen, setResetOpen] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)
  const [fitNonce, setFitNonce] = useState(0)
  const [notice, setNotice] = useState<Notice | null>(null)
  const noticeTimer = useRef<number | undefined>(undefined)
  // Phase focus needs the React Flow viewport instance, which only exists once
  // the graph mounts. The layout memo receives this ref so the phase-node data
  // callback stays referentially stable; RoadmapGraph rebinds the target.
  const focusPhaseRef = useRef<(phaseId: string) => void>(() => {})

  const notify = useCallback((text: string, tone: Notice['tone'] = 'success') => {
    setNotice({ id: Date.now(), text, tone })
  }, [])

  useEffect(() => {
    if (!notice) return
    window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNotice(null), 2600)
    return () => window.clearTimeout(noticeTimer.current)
  }, [notice])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await api<RoadmapResponse>('/roadmap/current')
      setRoadmap(response.roadmap ?? null)
      if (response.roadmap) {
        const refreshedCompetencies = response.roadmap.phases.flatMap((phase) =>
          phase.tracks.flatMap((track) => track.competencies),
        )
        setSelected((current) =>
          current
            ? refreshedCompetencies.find((item) => item.identityId === current.identityId) ?? null
            : null,
        )
        // Default to an expanded graph: every competency that parents children
        // starts expanded, so the full hierarchy is visible on first load.
        const parents = new Set(
          refreshedCompetencies
            .filter((item) => item.parentDefinitionId !== null)
            .map((item) => item.parentDefinitionId as string),
        )
        setExpanded(parents)
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The roadmap could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const onToggleExpand = useCallback((definitionId: string) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(definitionId)) next.delete(definitionId)
      else next.add(definitionId)
      return next
    })
  }, [])

  const onTogglePhaseCollapse = useCallback((phaseId: string) => {
    setCollapsedPhases((current) => {
      const next = new Set(current)
      if (next.has(phaseId)) next.delete(phaseId)
      else next.add(phaseId)
      return next
    })
  }, [])

  const confirmResetLayout = useCallback(async () => {
    setResetBusy(true)
    try {
      const result = await api<{ cleared: number }>('/roadmap/layout/reset', { method: 'POST' })
      await load()
      setFitNonce((value) => value + 1)
      notify(
        result.cleared > 0
          ? `Layout reset · cleared ${result.cleared} saved ${result.cleared === 1 ? 'position' : 'positions'}`
          : 'Layout reset · no saved positions to clear',
      )
    } catch (caught) {
      notify(caught instanceof ApiError ? caught.message : 'The layout could not be reset.', 'error')
    } finally {
      setResetBusy(false)
      setResetOpen(false)
    }
  }, [load, notify])

  const onMakeCurrent = useCallback(
    async (phaseId: string) => {
      setCurrentBusyPhaseId(phaseId)
      try {
        await api(`/roadmap/current-phase/${phaseId}`, { method: 'PUT' })
        await load()
        notify('Current phase updated')
      } catch (caught) {
        notify(caught instanceof ApiError ? caught.message : 'The current phase could not be changed.', 'error')
      } finally {
        setCurrentBusyPhaseId(null)
      }
    },
    [load, notify],
  )

  const layout = useMemo(
    () =>
      roadmap
        ? computeRoadmapLayout({
            roadmap,
            expanded,
            collapsedPhases,
            currentBusyPhaseId,
            onToggleExpand,
            onTogglePhaseCollapse,
            focusPhaseRef,
            onMakeCurrent,
          })
        : null,
    [
      roadmap,
      expanded,
      collapsedPhases,
      currentBusyPhaseId,
      onToggleExpand,
      onTogglePhaseCollapse,
      onMakeCurrent,
    ],
  )

  if (loading) return <LoadingState label="Mapping competencies" />
  if (error) return <ErrorState message={error} retry={() => void load()} />
  if (!roadmap) {
    return (
      <div className="w-full">
        <RoadmapHeader />
        <div className="mx-auto w-full max-w-[104rem]">
          <EmptyState
            title="No roadmap configured"
            detail="Import a roadmap package to establish phases, tracks, competencies, and stable identities."
            className="lg:min-h-72 2xl:min-h-80"
            action={
              <Link className="button-primary mt-2" to="/data-transfer">
                <Upload className="size-4" aria-hidden="true" />
                Import roadmap
              </Link>
            }
          />
        </div>
      </div>
    )
  }

  return (
    <div className="w-full">
      <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow mb-2">Version {roadmap.activeVersion.version}</p>
          <h1 className="page-title">{roadmap.title}</h1>
          <p className="mt-2 text-sm text-ink/60">Competencies, dependencies, and durable evidence.</p>
        </div>
        <button className="button-secondary" onClick={() => setListMode((value) => !value)}>
          {listMode ? <Network className="size-4" /> : <ListTree className="size-4" />}
          {listMode ? 'Graph view' : 'Accessible list'}
        </button>
      </header>
      {listMode || !layout ? (
        <RoadmapList roadmap={roadmap} query={query} setQuery={setQuery} select={setSelected} />
      ) : (
        <RoadmapGraph
          layout={layout}
          selectedId={selected?.definitionId ?? null}
          select={setSelected}
          onToggleExpand={onToggleExpand}
          onTogglePhaseCollapse={onTogglePhaseCollapse}
          focusPhaseRef={focusPhaseRef}
          onRequestReset={() => setResetOpen(true)}
          resetBusy={resetBusy}
          showPrereqs={showPrereqs}
          onTogglePrereqs={() => setShowPrereqs((value) => !value)}
          notice={notice}
          onNotice={notify}
          fitNonce={fitNonce}
        />
      )}
      <AnimatePresence>{selected ? <CompetencyPanel competency={selected} close={() => setSelected(null)} refresh={load} /> : null}</AnimatePresence>
      <AnimatePresence>
        {resetOpen ? (
          <ResetLayoutDialog
            busy={resetBusy}
            onCancel={() => setResetOpen(false)}
            onConfirm={() => void confirmResetLayout()}
          />
        ) : null}
      </AnimatePresence>
    </div>
  )
}

function RoadmapHeader() {
  return (
    <header className="mb-7">
      <p className="eyebrow mb-2">Competency architecture</p>
      <h1 className="page-title">Roadmap</h1>
      <p className="mt-2 text-sm text-ink/60">
        Navigate phases, dependencies, verification criteria, and durable learning progress.
      </p>
    </header>
  )
}

const RoadmapGraphCanvas = memo(function RoadmapGraphCanvas({
  nodes,
  edges,
  onNodesChange,
  onNodeClick,
  onNodeDoubleClick,
  onNodeDragStop,
  onInit,
}: {
  nodes: RoadmapFlowNode[]
  edges: RoadmapLayout['edges']
  onNodesChange: OnNodesChange<RoadmapFlowNode>
  onNodeClick: NodeMouseHandler
  onNodeDoubleClick: NodeMouseHandler
  onNodeDragStop: OnNodeDrag
  onInit: OnInit<RoadmapFlowNode>
}) {
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={roadmapNodeTypes}
      edgeTypes={roadmapEdgeTypes}
      elementsSelectable={false}
      onNodesChange={onNodesChange}
      onNodeClick={onNodeClick}
      onNodeDoubleClick={onNodeDoubleClick}
      onNodeDragStop={onNodeDragStop}
      onInit={onInit}
      fitView
      fitViewOptions={fitViewOptions}
      minZoom={0.15}
      maxZoom={1.8}
    >
      <Background color="#aab5ad" gap={28} size={1} />
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        zoomable
        nodeColor={(node) =>
          node.type === 'competency' ? String(node.data?.statusColor ?? '#326653') : 'rgba(20,33,28,0.08)'
        }
      />
    </ReactFlow>
  )
})

function RoadmapGraph({
  layout,
  selectedId,
  select,
  onToggleExpand,
  onTogglePhaseCollapse,
  focusPhaseRef,
  onRequestReset,
  resetBusy,
  showPrereqs,
  onTogglePrereqs,
  notice,
  onNotice,
  fitNonce,
}: {
  layout: RoadmapLayout
  selectedId: string | null
  select: (item: Competency) => void
  onToggleExpand: (definitionId: string) => void
  onTogglePhaseCollapse: (phaseId: string) => void
  focusPhaseRef: { current: (phaseId: string) => void }
  onRequestReset: () => void
  resetBusy: boolean
  showPrereqs: boolean
  onTogglePrereqs: () => void
  notice: Notice | null
  onNotice: (text: string, tone?: Notice['tone']) => void
  fitNonce: number
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<RoadmapFlowNode>([])
  const draggedLocal = useRef(new Map<string, { x: number; y: number }>())
  const layoutRef = useRef(layout)
  layoutRef.current = layout
  const flowRef = useRef<ReactFlowInstance<RoadmapFlowNode> | null>(null)

  useEffect(() => {
    setNodes((previous) => mergeLayoutNodes(previous, layout, draggedLocal.current, selectedId))
  }, [layout, selectedId, setNodes])

  const edges = useMemo(
    () => [...layout.edges, ...buildVisiblePrereqEdges(layout.prereqEdges, showPrereqs, selectedId)],
    [layout, showPrereqs, selectedId],
  )

  useEffect(() => {
    if (fitNonce === 0) return
    const frame = requestAnimationFrame(() => {
      void flowRef.current?.fitView(fitViewOptions).catch(() => {
        // jsdom test viewports have zero size; real browsers never fail here.
      })
    })
    return () => cancelAnimationFrame(frame)
  }, [fitNonce])

  const onInit = useCallback<OnInit<RoadmapFlowNode>>((instance) => {
    flowRef.current = instance
  }, [])

  const fitGraph = useCallback(() => {
    void flowRef.current?.fitView(fitViewOptions).catch(() => {
      // jsdom test viewports have zero size; real browsers never fail here.
    })
  }, [])

  const focusPhase = useCallback((phaseId: string) => {
    const instance = flowRef.current
    if (!instance) return
    const currentLayout = layoutRef.current
    const phaseLayout = currentLayout.phases.find((entry) => entry.phase.id === phaseId)
    if (!phaseLayout) return
    const bounds = phaseLayout.collapsed
      ? {
          x: phaseLayout.origin.x,
          y: phaseLayout.origin.y,
          width: phaseLayout.width,
          height: phaseLayout.height,
        }
      : {
          x: phaseLayout.origin.x - 12,
          y: phaseLayout.origin.y - 12,
          width: phaseLayout.width + 24,
          height: phaseLayout.height + 24,
        }
    // Focus is an instant, deterministic viewport jump: it must not depend on
    // animation frames or d3 transitions (both can starve in throttled or
    // automated environments), and it is inherently reduced-motion safe.
    const paneElement = document.querySelector<HTMLElement>('.react-flow')
    const rect = paneElement?.getBoundingClientRect()
    if (!rect || rect.width === 0 || rect.height === 0) return
    const target = getViewportForBounds(bounds, rect.width, rect.height, 0.15, 1.8, 0.12)
    void instance.setViewport(target)
  }, [])
  focusPhaseRef.current = focusPhase

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_event, node) => {
      const currentLayout = layoutRef.current
      const competency = currentLayout.visible.find((item) => item.definitionId === node.id)
      if (competency) select(competency)
    },
    [select],
  )

  const onNodeDoubleClick = useCallback<NodeMouseHandler>(
    (_event, node) => {
      if (layoutRef.current.childrenByParent.has(node.id)) onToggleExpand(node.id)
    },
    [onToggleExpand],
  )

  const onNodeDragStop = useCallback<OnNodeDrag>(
    (_event, node) => {
      const currentLayout = layoutRef.current
      const origin = phaseOriginFor(currentLayout, node.id)
      if (!origin) return
      const local = toPhaseLocalPosition(origin, node.position)
      draggedLocal.current.set(node.id, local)
      void api(`/roadmap/competencies/${node.id}/position`, {
        method: 'PUT',
        body: JSON.stringify(local),
      })
        .then(() => onNotice('Position saved'))
        .catch(() => onNotice('Position could not be saved', 'error'))
    },
    [onNotice],
  )

  return (
    <section className="surface relative h-[min(72vh,48rem)] min-h-[32rem] overflow-hidden 2xl:h-[min(76vh,56rem)]" aria-label="Interactive competency roadmap">
      <RoadmapGraphCanvas
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onNodeDragStop={onNodeDragStop}
        onInit={onInit}
      />
      <PhaseNavigator layout={layout} onFocus={(phaseId) => focusPhaseRef.current(phaseId)} onToggleCollapse={onTogglePhaseCollapse} />
      <div className="absolute left-3 top-3 z-10 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-ink/15 bg-white/95 px-3.5 py-1.5 text-xs font-semibold text-ink/70 shadow transition hover:border-moss/40 hover:text-ink"
          onClick={fitGraph}
        >
          <Maximize2 className="size-3.5" aria-hidden="true" />
          Fit view
        </button>
        <button
          type="button"
          aria-pressed={showPrereqs}
          className={`inline-flex min-h-8 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs font-semibold shadow transition ${
            showPrereqs
              ? 'border-moss/50 bg-fern/20 text-ink'
              : 'border-ink/15 bg-white/95 text-ink/70 hover:border-moss/40 hover:text-ink'
          }`}
          onClick={onTogglePrereqs}
        >
          <Link2 className="size-3.5" aria-hidden="true" />
          Prerequisites
        </button>
        <button
          type="button"
          className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-ink/15 bg-white/95 px-3.5 py-1.5 text-xs font-semibold text-ink/70 shadow transition hover:border-rose-300 hover:text-rose-900 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={onRequestReset}
          disabled={resetBusy}
        >
          <RotateCcw className="size-3.5" aria-hidden="true" />
          Reset layout
        </button>
      </div>
      {notice ? (
        <div
          key={notice.id}
          role="status"
          aria-live="polite"
          className={`pointer-events-none absolute left-1/2 top-4 z-10 -translate-x-1/2 whitespace-nowrap rounded-full px-4 py-1.5 text-xs font-semibold shadow ${
            notice.tone === 'error' ? 'bg-rose-100 text-rose-900' : 'bg-moss text-white'
          }`}
        >
          {notice.text}
        </div>
      ) : null}
      <div className="pointer-events-none absolute bottom-3 right-3 z-10 max-w-[calc(100%-1.5rem)] rounded-full bg-white/90 px-3.5 py-2 text-xs text-ink/60 shadow">
        <span className="mr-4 inline-flex items-center gap-1.5">
          <svg width="26" height="10" aria-hidden="true">
            <line x1="1" y1="9" x2="1" y2="5" stroke="#86a493" strokeWidth="1.75" />
            <line x1="1" y1="5" x2="25" y2="5" stroke="#a7b8ad" strokeWidth="1.25" />
          </svg>
          Tree structure
        </span>
        <span className="mr-4 inline-flex items-center gap-1.5">
          <svg width="26" height="6" aria-hidden="true"><line x1="1" y1="3" x2="25" y2="3" stroke="#326653" strokeWidth="2" /></svg>
          Required
        </span>
        <span className="inline-flex items-center gap-1.5">
          <svg width="26" height="6" aria-hidden="true"><line x1="1" y1="3" x2="25" y2="3" stroke="#93a69b" strokeWidth="1.5" strokeDasharray="5 4" /></svg>
          Recommended
        </span>
      </div>
      <p className="pointer-events-none absolute bottom-3 left-1/2 max-w-[calc(100%-1.5rem)] -translate-x-1/2 rounded-full bg-white/90 px-3 py-1.5 text-center text-xs text-ink/60 shadow">
        Select a node for details and its prerequisite links · double-click a parent or use its chevron to expand or collapse · dragging saves a free-form canvas position
      </p>
    </section>
  )
}

function PhaseNavigator({
  layout,
  onFocus,
  onToggleCollapse,
}: {
  layout: RoadmapLayout
  onFocus: (phaseId: string) => void
  onToggleCollapse: (phaseId: string) => void
}) {
  return (
    <nav
      aria-label="Phases"
      className="absolute left-1/2 top-3 z-10 flex max-w-[calc(100%-24rem)] -translate-x-1/2 items-center gap-1 overflow-x-auto rounded-full border border-ink/10 bg-white/90 px-1.5 py-1 shadow"
    >
      {layout.phases.map((phaseLayout, index) => {
        const phase = phaseLayout.phase
        return (
          <div key={phase.id} className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => onFocus(phase.id)}
              title={`${phase.title} · ${phaseLayout.verifiedCount} of ${phaseLayout.totalCount} verified`}
              aria-label={`Go to phase ${phase.title}`}
              className={`inline-flex min-h-7 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold transition ${
                phase.isCurrent ? 'bg-moss/10 text-moss' : 'text-ink/60 hover:bg-ink/5 hover:text-ink'
              }`}
            >
              <span className="max-w-36 truncate">{phase.title}</span>
              <span className="text-[0.65rem] font-normal text-ink/45">
                {phaseLayout.verifiedCount}/{phaseLayout.totalCount}
              </span>
              {phase.isCurrent ? (
                <span className="size-1.5 rounded-full bg-moss" aria-hidden="true" />
              ) : null}
            </button>
            <button
              type="button"
              onClick={() => onToggleCollapse(phase.id)}
              aria-expanded={!phaseLayout.collapsed}
              aria-label={phaseLayout.collapsed ? `Expand ${phase.title}` : `Collapse ${phase.title}`}
              title={phaseLayout.collapsed ? 'Expand phase' : 'Collapse phase'}
              className="grid size-6 place-items-center rounded-full text-ink/40 transition hover:bg-ink/5 hover:text-ink"
            >
              <ChevronRight
                className={`size-3.5 transition-transform ${phaseLayout.collapsed ? '' : 'rotate-90'}`}
                aria-hidden="true"
              />
            </button>
            {index < layout.phases.length - 1 ? (
              <span className="mx-0.5 h-4 w-px bg-ink/10" aria-hidden="true" />
            ) : null}
          </div>
        )
      })}
    </nav>
  )
}

function RoadmapList({ roadmap, query, setQuery, select }: { roadmap: Roadmap; query: string; setQuery: (value: string) => void; select: (item: Competency) => void }) {
  return (
    <section className="surface p-5 sm:p-6">
      <label className="relative block max-w-lg">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink/40" />
        <span className="sr-only">Search competencies</span>
        <input className="field pl-10" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search title or stable key" />
      </label>
      <div className="mt-6 space-y-7">
        {roadmap.phases.map((phase) => (
          <div key={phase.id}>
            <div className="mb-3 flex items-center gap-3">
              <h2 className="font-display text-xl font-semibold">{phase.title}</h2>
              {phase.isCurrent ? <span className="rounded-full bg-moss px-2.5 py-1 text-xs text-white">Current</span> : null}
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {phase.tracks.flatMap((track) =>
                track.competencies
                  .filter((item) => `${item.title} ${item.stableKey}`.toLowerCase().includes(query.toLowerCase()))
                  .map((item) => (
                    <button key={item.definitionId} className="flex min-h-16 items-center justify-between gap-4 rounded-xl border border-ink/10 bg-white/55 p-4 text-left hover:border-moss/30" onClick={() => select(item)}>
                      <div><p className="font-semibold">{item.title}</p><p className="mt-1 font-mono text-xs text-ink/40">{item.stableKey}</p></div>
                      <StatusBadge status={item.status} />
                    </button>
                  )),
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function CompetencyPanel({ competency, close, refresh }: { competency: Competency; close: () => void; refresh: () => Promise<void> }) {
  const [saving, setSaving] = useState(false)
  const [displayStatus, setDisplayStatus] = useState<Status>(competency.status)
  const [criterionStates, setCriterionStates] = useState<Record<string, ExitCriterion['state']>>(
    Object.fromEntries(competency.exitCriteria.map((item) => [item.id, item.state])),
  )
  const [verificationResult, setVerificationResult] = useState<'passed' | 'partial' | 'failed'>('passed')
  const [method, setMethod] = useState('Self assessment against exit criteria')
  const [evidenceSummary, setEvidenceSummary] = useState('')
  const [history, setHistory] = useState<Array<{ id: string; source: string; method: string; result: string; evidenceSummary: string | null; createdAt: string }>>([])
  const [actionError, setActionError] = useState('')
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [close])
  useEffect(() => {
    setDisplayStatus(competency.status)
    setCriterionStates(Object.fromEntries(competency.exitCriteria.map((item) => [item.id, item.state])))
  }, [competency])
  const loadHistory = useCallback(async () => {
    const response = await api<{ items: typeof history }>(`/verification?competency_identity_id=${encodeURIComponent(competency.identityId)}`)
    setHistory(response.items)
  }, [competency.identityId])
  useEffect(() => {
    void loadHistory().catch(() => setActionError('Verification history could not be loaded.'))
  }, [loadHistory])
  const updateStatus = async (status: Status) => {
    if (status === 'verified') return
    setSaving(true)
    setActionError('')
    const previous = displayStatus
    setDisplayStatus(status)
    try {
      await api(`/roadmap/competencies/${competency.identityId}/status`, {
        method: 'PUT',
        body: JSON.stringify({ status, reason: 'Explicit user update from competency detail' }),
      })
      await refresh()
    } catch (caught) {
      setDisplayStatus(previous)
      setActionError(caught instanceof ApiError ? caught.message : 'The status could not be updated.')
    } finally {
      setSaving(false)
    }
  }
  const selfVerify = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setActionError('')
    try {
      await api('/verification', {
        method: 'POST',
        body: JSON.stringify({
          competency_identity_id: competency.identityId,
          verification_source: 'self',
          method,
          result: verificationResult,
          evidence_summary: evidenceSummary || null,
          evidence: [],
        }),
      })
      setEvidenceSummary('')
      await Promise.all([refresh(), loadHistory()])
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : 'The verification could not be recorded.')
    } finally {
      setSaving(false)
    }
  }
  return (
    <>
      <motion.button className="fixed inset-0 z-30 bg-ink/25" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={close} aria-label="Close competency detail" />
      <motion.aside className="fixed inset-y-0 right-0 z-40 w-full max-w-xl overflow-y-auto bg-[#fbfaf6] p-6 shadow-2xl sm:p-8" initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }} transition={{ duration: 0.23 }} role="dialog" aria-modal="true" aria-label={`${competency.title} details`}>
        <button className="absolute right-4 top-4 grid size-10 place-items-center rounded-xl hover:bg-ink/5" onClick={close} aria-label="Close"><X className="size-5" /></button>
        <p className="eyebrow pr-12">{competency.stableKey}</p>
        <h2 className="mt-3 pr-12 font-display text-3xl font-semibold">{competency.title}</h2>
        <div className="mt-4 flex flex-wrap items-center gap-2"><StatusBadge status={competency.status} /><span className="rounded-full bg-ink/5 px-3 py-1 text-xs">{competency.priority} · weight {competency.weight}</span></div>
        {competency.goal ? <DetailSection title="Goal"><p>{competency.goal}</p></DetailSection> : null}
        <DetailSection title="Must understand"><BulletList items={competency.mustUnderstand} /></DetailSection>
        <DetailSection title="Must be able to"><BulletList items={competency.mustBeAbleTo} /></DetailSection>
        <DetailSection title="Prerequisites"><BulletList items={competency.prerequisites.map((item) => `${item.stableKey} · ${item.kind}`)} /></DetailSection>
        <DetailSection title="Exit criteria">
          <div className="space-y-2">{competency.exitCriteria.length ? competency.exitCriteria.map((item) => <label key={item.id} className="flex items-start gap-3 rounded-xl border border-ink/10 p-3"><input type="checkbox" className="mt-1 size-4" checked={criterionStates[item.id] === 'met'} onChange={async (event) => { const next = event.target.checked ? 'met' : 'not_met'; const previous = criterionStates[item.id]; setCriterionStates((current) => ({ ...current, [item.id]: next })); setActionError(''); try { await api(`/roadmap/exit-criteria/${item.id}`, { method: 'PUT', body: JSON.stringify({ state: next }) }); await refresh() } catch (caught) { setCriterionStates((current) => ({ ...current, [item.id]: previous })); setActionError(caught instanceof ApiError ? caught.message : 'The exit criterion could not be updated.') } }} /><span className="text-sm leading-6">{item.text}{item.required ? ' (required)' : ''}</span></label>) : <p className="text-sm text-ink/45">No exit criteria in this definition.</p>}</div>
        </DetailSection>
        <DetailSection title="Explicit status">
          <p className="mb-3 text-xs leading-5 text-ink/50">Ready for verification is explicit. Verified requires a passed verification record.</p>
          <select className="field" disabled={saving} value={displayStatus} onChange={(event) => void updateStatus(event.target.value as Status)}>
            {(['not_started', 'learning', 'practicing', 'ready_for_verification', 'needs_review'] as Status[]).map((status) => <option key={status} value={status}>{status.replaceAll('_', ' ')}</option>)}
            {competency.status === 'verified' ? <option value="verified">verified</option> : null}
          </select>
        </DetailSection>
        <DetailSection title="Self-verification">
          <form className="space-y-3" onSubmit={(event) => void selfVerify(event)}>
            <p className="text-xs leading-5 text-ink/50">Record an auditable result. A pass is the only action here that marks the competency verified.</p>
            <label className="block font-medium">Result<select className="field mt-2" value={verificationResult} onChange={(event) => setVerificationResult(event.target.value as typeof verificationResult)}><option value="passed">Passed</option><option value="partial">Partial</option><option value="failed">Failed</option></select></label>
            <label className="block font-medium">Method<input className="field mt-2" value={method} onChange={(event) => setMethod(event.target.value)} required /></label>
            <label className="block font-medium">Evidence summary<textarea className="field mt-2 min-h-24 resize-y" value={evidenceSummary} onChange={(event) => setEvidenceSummary(event.target.value)} placeholder="What demonstrates the result?" /></label>
            {actionError ? <p className="rounded-xl bg-rose-50 p-3 text-xs text-rose-800" role="alert">{actionError}</p> : null}
            <button className="button-primary w-full" disabled={saving}><ShieldCheck className="size-4" />Record self-verification</button>
          </form>
          <div className="mt-5 space-y-2" aria-label="Verification history">
            {history.length ? history.map((item) => <article className="rounded-xl border border-ink/10 p-3" key={item.id}><div className="flex items-center justify-between gap-3"><strong className="capitalize">{item.result}</strong><time className="text-xs text-ink/40" dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleDateString()}</time></div><p className="mt-1 text-xs">{item.source} · {item.method}</p>{item.evidenceSummary ? <p className="mt-2 text-xs text-ink/50">{item.evidenceSummary}</p> : null}</article>) : <p className="text-xs text-ink/45">No verification records yet.</p>}
          </div>
        </DetailSection>
      </motion.aside>
    </>
  )
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) { return <section className="mt-8 border-t border-ink/10 pt-6"><h3 className="mb-3 font-display text-lg font-semibold">{title}</h3><div className="text-sm leading-6 text-ink/65">{children}</div></section> }
function BulletList({ items }: { items: string[] }) { return items.length ? <ul className="space-y-2">{items.map((item) => <li className="flex gap-2" key={item}><ChevronRight className="mt-1 size-4 shrink-0 text-moss" />{item}</li>)}</ul> : <p className="text-ink/45">None specified.</p> }

function ResetLayoutDialog({ busy, onCancel, onConfirm }: { busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const cancelButton = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    cancelButton.current?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [busy, onCancel])
  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <motion.button
        type="button"
        className="absolute inset-0 bg-ink/25"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={busy ? undefined : onCancel}
        disabled={busy}
        aria-label="Cancel reset layout"
      />
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-labelledby="reset-layout-title"
        aria-describedby="reset-layout-description"
        className="relative w-full max-w-md rounded-2xl bg-[#fbfaf6] p-6 shadow-2xl"
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 8 }}
        transition={{ duration: 0.16 }}
      >
        <h2 id="reset-layout-title" className="font-display text-xl font-semibold">Reset layout?</h2>
        <div id="reset-layout-description" className="mt-3 space-y-2 text-sm leading-6 text-ink/65">
          <p>
            This clears only manually saved node positions and returns the roadmap to its automatic layout.
          </p>
          <p>Progress, sessions, and verification records are not affected.</p>
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <button ref={cancelButton} type="button" className="button-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="button-primary bg-rose-900 hover:bg-rose-950"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'Resetting...' : 'Reset layout'}
          </button>
        </div>
      </motion.div>
    </div>
  )
}
