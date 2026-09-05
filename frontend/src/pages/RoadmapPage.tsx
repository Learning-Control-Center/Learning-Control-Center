import { Background, Controls, MiniMap, ReactFlow, useNodesState, type NodeTypes } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ChevronRight, ListTree, Network, Search, ShieldCheck, Upload, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'
import { CompetencyNode, PhaseContainerNode } from '../components/RoadmapNodes'
import {
  computeRoadmapLayout,
  phaseOriginFor,
  toPhaseLocalPosition,
  type RoadmapFlowNode,
  type RoadmapLayout,
} from '../components/roadmapLayout'
import { StatusBadge } from '../components/StatusBadge'
import type { Competency, ExitCriterion, Roadmap, Status } from '../types'

type RoadmapResponse = { configured: boolean; guidance?: string; roadmap?: Roadmap }

const roadmapNodeTypes: NodeTypes = { competency: CompetencyNode, phase: PhaseContainerNode }

export function RoadmapPage() {
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null)
  const [selected, setSelected] = useState<Competency | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [listMode, setListMode] = useState(false)
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

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
        const parents = new Set(
          response.roadmap.phases.flatMap((phase) =>
            phase.tracks.flatMap((track) =>
              track.competencies.filter((item) => item.parentDefinitionId === null).map((item) => item.definitionId),
            ),
          ),
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

  const layout = useMemo(
    () =>
      roadmap
        ? computeRoadmapLayout({
            roadmap,
            expanded,
            selectedId: selected?.definitionId ?? null,
            onToggleExpand,
          })
        : null,
    [roadmap, expanded, selected?.definitionId, onToggleExpand],
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
              <Link className="button-primary mt-2" to="/transfer">
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
        <RoadmapGraph layout={layout} select={setSelected} onToggleExpand={onToggleExpand} />
      )}
      <AnimatePresence>{selected ? <CompetencyPanel competency={selected} close={() => setSelected(null)} refresh={load} /> : null}</AnimatePresence>
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

function RoadmapGraph({ layout, select, onToggleExpand }: {
  layout: RoadmapLayout
  select: (item: Competency) => void
  onToggleExpand: (definitionId: string) => void
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<RoadmapFlowNode>(layout.nodes)
  const draggedLocal = useRef(new Map<string, { x: number; y: number }>())

  useEffect(() => {
    setNodes(
      layout.nodes.map((node) => {
        if (node.type !== 'competency') return node
        const local = draggedLocal.current.get(node.id)
        const origin = local ? phaseOriginFor(layout, node.id) : null
        return local && origin
          ? { ...node, position: { x: origin.x + local.x, y: origin.y + local.y } }
          : node
      }),
    )
  }, [layout, setNodes])

  return (
    <section className="surface relative h-[min(72vh,48rem)] min-h-[32rem] overflow-hidden 2xl:h-[min(76vh,56rem)]" aria-label="Interactive competency roadmap">
      <ReactFlow
        nodes={nodes}
        edges={layout.edges}
        nodeTypes={roadmapNodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(_event, node) => {
          const competency = layout.visible.find((item) => item.definitionId === node.id)
          if (competency) select(competency)
        }}
        onNodeDoubleClick={(_event, node) => {
          if (layout.childrenByParent.has(node.id)) onToggleExpand(node.id)
        }}
        onNodeDragStop={(_event, node) => {
          const origin = phaseOriginFor(layout, node.id)
          if (!origin) return
          const local = toPhaseLocalPosition(origin, node.position)
          draggedLocal.current.set(node.id, local)
          void api(`/roadmap/competencies/${node.id}/position`, {
            method: 'PUT',
            body: JSON.stringify(local),
          })
        }}
        fitView
        fitViewOptions={{ padding: 0.14 }}
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
      <div className="pointer-events-none absolute right-3 top-3 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-x-4 gap-y-1 rounded-full bg-white/90 px-3.5 py-2 text-xs text-ink/60 shadow">
        <span className="flex items-center gap-1.5">
          <svg width="26" height="6" aria-hidden="true"><line x1="1" y1="3" x2="25" y2="3" stroke="#326653" strokeWidth="2" /></svg>
          Required prerequisite
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="26" height="6" aria-hidden="true"><line x1="1" y1="3" x2="25" y2="3" stroke="#93a69b" strokeWidth="1.5" strokeDasharray="5 4" /></svg>
          Recommended
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="26" height="6" aria-hidden="true"><line x1="1" y1="3" x2="25" y2="3" stroke="#b7c2ba" strokeWidth="1.5" /></svg>
          Contains
        </span>
      </div>
      <p className="pointer-events-none absolute bottom-3 left-1/2 max-w-[calc(100%-1.5rem)] -translate-x-1/2 rounded-full bg-white/90 px-3 py-1.5 text-center text-xs text-ink/60 shadow">
        Select a node for details · double-click a parent or use its chevron to expand or collapse · dragging saves the node's place in its phase
      </p>
    </section>
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
