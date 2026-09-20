import {
  Background,
  BaseEdge,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getBezierPath,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  ChevronDown,
  ChevronRight,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Crosshair,
  GitBranch,
  List,
  LocateFixed,
  LockKeyhole,
  Map as MapIcon,
  Move,
  RefreshCw,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
} from 'lucide-react'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, apiV2 } from '../../api'
import {
  Button,
  CapabilitySummary,
  Dialog,
  EmptyState,
  ErrorState,
  LiveNotice,
  LoadingState,
  MutationError,
  PageHeader,
  StatusBadge,
  Surface,
  TextField,
} from '../../shared/components'
import type { CurriculumCatalogUnit, ProjectCatalogCandidateApi } from '../../shared/contracts/productCatalog'
import { adaptProjectCatalogCandidate } from '../../shared/contracts/productCatalog'
import type { RoadmapProjection } from './projection'
import { paths } from '../../shared/navigation/paths'
import {
  applyRoadmapVisibility,
  createRoadmapViewModel,
  type RoadmapLaneView,
  type RoadmapNodeView,
  type RoadmapVisibility,
} from './viewModel'

type JourneyNodeData = Record<string, unknown> & {
  node: RoadmapNodeView
  selected: boolean
  temporarilyRevealed: boolean
  editMode: boolean
  onSelect: (id: string) => void
}
type CapabilityScale = { levels: { id: string; stableKey: string; displayLabel: string }[] }
const relationshipOptions = [
  ['prerequisite', 'Prerequisites'],
  ['specialization', 'Specialization'],
  ['recommended_before', 'Recommended before'],
  ['supports', 'Supports'],
  ['related', 'Related'],
] as const

type RoadmapJourneyStage = 'foundation' | 'domain' | 'destination'

function roadmapJourneyStage(lane: RoadmapLaneView): RoadmapJourneyStage {
  if (lane.id === 'cross-domain-targets') return 'destination'
  if (lane.id.startsWith('graph-foundations') || lane.nodes.every((node) => !node.isTargeted)) return 'foundation'
  return 'domain'
}

function roadmapJourneyStageMeta(lane: RoadmapLaneView) {
  const stage = roadmapJourneyStage(lane)
  if (stage === 'foundation') return { rank: 0, label: '1 · Start here', detail: 'Shared foundation', border: 'border-l-moss' }
  if (stage === 'domain') return { rank: 1, label: '2 · Build capability', detail: 'Domain branch', border: 'border-l-sky-300' }
  return { rank: 2, label: '3 · Move toward targets', detail: 'Cross-domain destination', border: 'border-l-amber-300' }
}

function orderRoadmapLanesForJourney(lanes: RoadmapLaneView[]) {
  return [...lanes].sort((left, right) => {
    const rankDifference = roadmapJourneyStageMeta(left).rank - roadmapJourneyStageMeta(right).rank
    return rankDifference || left.orderIndex - right.orderIndex || left.id.localeCompare(right.id)
  })
}
function toneFor(node: RoadmapNodeView) {
  return node.tone === 'critical' ? 'critical' : node.tone === 'warning' ? 'warning' : node.tone === 'success' ? 'success' : node.tone === 'info' ? 'info' : 'unknown'
}

function RoadmapJourneyNodeView(props: NodeProps) {
  const data = props.data as JourneyNodeData
  const node = data.node
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('__measure') !== '1') return
    const scope = window as typeof window & { __LCC_ROADMAP_NODE_RENDERS__?: number }
    scope.__LCC_ROADMAP_NODE_RENDERS__ = (scope.__LCC_ROADMAP_NODE_RENDERS__ ?? 0) + 1
  })
  return (
    <article
      className={`roadmap-node ${data.selected ? 'roadmap-node-selected' : ''} ${data.temporarilyRevealed ? 'roadmap-node-revealed' : ''}`}
      data-roadmap-node-id={node.id}
      data-position-source={node.positionSource}
      data-current-summary={node.currentSummary}
      data-target-summary={node.targetSummary}
      data-target-status-summary={node.targetStatusSummary}
      data-target-rows={JSON.stringify(node.targetRows)}
      data-today={String(node.isToday)}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className="!size-2.5 !border-white !bg-moss" />
      <button
        type="button"
        className="flex h-full w-full flex-col p-3 text-left focus-visible:outline-offset-[-3px]"
        onClick={() => data.onSelect(node.id)}
        aria-label={`Open ${node.title}. Current: ${node.currentSummary}. Target: ${node.targetSummary}. ${node.targetStatusSummary}${node.isToday ? '. Today' : ''}${node.blockedLabel ? `. ${node.blockedLabel}` : ''}${data.temporarilyRevealed ? '. Focused path' : ''}`}
      >
        <span className="flex min-w-0 items-start justify-between gap-2">
          <span className="line-clamp-2 min-h-10 min-w-0 font-display text-sm font-semibold leading-5 text-ink">{node.title}</span>
          {node.isToday ? <Sparkles className="mt-0.5 size-4 shrink-0 text-status-today" aria-label="Today" /> : null}
        </span>
        <span className="line-clamp-1 text-[0.66rem] font-semibold uppercase tracking-wide text-ink/50">{node.layoutLane.title}</span>
        <span className="line-clamp-1 text-[0.7rem] text-ink/70"><strong>Current:</strong> {node.currentSummary}</span>
        <span className="line-clamp-1 text-[0.7rem] text-ink/70"><strong>Target:</strong> {node.targetSummary}</span>
        <span className="line-clamp-1 text-[0.64rem] text-ink/55">{node.targetStatusSummary}</span>
        <span className="mt-auto flex min-h-4 flex-wrap gap-1 text-[0.6rem] font-semibold uppercase tracking-wide">
          {data.temporarilyRevealed ? <span className="text-status-info">Focused path</span> : null}
          {node.isToday ? <span className="text-status-today">Today</span> : null}
          {node.blockedLabel ? <span className="text-status-critical">{node.blockedLabel}</span> : null}
          {node.targetRows.length === 1 && node.targetRows[0].comparison === 'Target reached' ? <span className="text-status-success">Target reached</span> : null}
          {node.targetRows.some((target) => target.reviewDue) ? <span className="text-status-warning">Review due</span> : null}
          {node.positionSource === 'user_override' ? <span className="text-status-warning">Manual</span> : null}
          {data.editMode ? <span className="text-status-warning">Move enabled</span> : null}
        </span>
      </button>
      <Handle type="source" position={Position.Right} isConnectable={false} className="!size-2.5 !border-white !bg-moss" />
    </article>
  )
}

export const RoadmapJourneyNode = memo(
  RoadmapJourneyNodeView,
  (previous, next) => previous.data === next.data,
)

function relationshipColor(edgeType: string, state: string) {
  return edgeType === 'prerequisite'
    ? state === 'met' ? '#3f7258' : state === 'unknown' ? '#735f8e' : '#a0453f'
    : edgeType === 'specialization' ? '#326653' : edgeType === 'supports' ? '#4d6f91' : '#7c857f'
}

export function RoadmapJourneyEdge(props: EdgeProps) {
  const [path, labelX, labelY] = getBezierPath(props)
  const edgeType = String(props.data?.edgeType ?? 'relationship')
  const state = String(props.data?.state ?? '')
  const isPrerequisite = edgeType === 'prerequisite'
  const stroke = relationshipColor(edgeType, state)
  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={props.markerEnd}
        style={{ stroke, strokeWidth: isPrerequisite ? 2.2 : 1.4, strokeDasharray: edgeType === 'related' ? '5 5' : edgeType === 'recommended_before' ? '8 4' : undefined }}
      />
      <foreignObject x={labelX - 55} y={labelY - 13} width={110} height={26} className="pointer-events-none overflow-visible">
        <span className="block rounded-full border border-ink/10 bg-white/95 px-2 py-1 text-center text-[0.62rem] font-semibold text-ink/65 shadow-sm">
          {edgeType.replaceAll('_', ' ')}{isPrerequisite ? ` · ${state}` : ''}
        </span>
      </foreignObject>
    </>
  )
}

const nodeTypes = { journey: RoadmapJourneyNode }
const edgeTypes = { journey: RoadmapJourneyEdge }

function useCompactViewport() {
  const [compact, setCompact] = useState(() => window.matchMedia?.('(max-width: 1279px)').matches ?? false)
  useEffect(() => {
    const media = window.matchMedia?.('(max-width: 1279px)')
    if (!media) return
    const update = () => setCompact(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  return compact
}

function useReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!media) return
    const update = () => setReducedMotion(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  return reducedMotion
}

function RoadmapCanvasControls({ selectedIds }: { selectedIds: string[] }) {
  const flow = useReactFlow<Node>()
  const reducedMotion = useReducedMotion()
  const duration = reducedMotion ? 0 : 250
  const pan = (x: number, y: number) => { const viewport = flow.getViewport(); void flow.setViewport({ ...viewport, x: viewport.x + x, y: viewport.y + y }, { duration: reducedMotion ? 0 : 150 }) }
  return (
    <div className="absolute bottom-3 left-3 z-10 flex flex-wrap gap-2" aria-label="Map viewport controls" data-motion-duration-ms={duration}>
      <Button variant="secondary" className="bg-white/95 text-xs" onClick={() => void flow.fitView({ padding: 0.12, duration })}>
        <LocateFixed className="size-4" aria-hidden="true" /> Fit all
      </Button>
      <Button variant="secondary" className="bg-white/95 text-xs" disabled={!selectedIds.length} onClick={() => void flow.fitView({ nodes: selectedIds.map((id) => ({ id })), padding: 0.35, duration })}>
        <Crosshair className="size-4" aria-hidden="true" /> Fit path
      </Button>
      <Button variant="secondary" className="bg-white/95 text-xs" onClick={() => flow.setViewport({ x: 0, y: 0, zoom: 1 }, { duration })}>
        <RotateCcw className="size-4" aria-hidden="true" /> Reset viewport
      </Button>
      <span className="flex rounded-xl border border-ink/15 bg-white/95" role="group" aria-label="Pan map">
        <button className="grid size-11 place-items-center" onClick={() => pan(120, 0)} aria-label="Pan left"><ArrowLeft className="size-4" /></button>
        <button className="grid size-11 place-items-center" onClick={() => pan(0, 100)} aria-label="Pan up"><ArrowUp className="size-4" /></button>
        <button className="grid size-11 place-items-center" onClick={() => pan(0, -100)} aria-label="Pan down"><ArrowDown className="size-4" /></button>
        <button className="grid size-11 place-items-center" onClick={() => pan(-120, 0)} aria-label="Pan right"><ArrowRight className="size-4" /></button>
      </span>
      <span className="flex rounded-xl border border-ink/15 bg-white/95" role="group" aria-label="Zoom map">
        <button className="grid size-11 place-items-center text-lg font-semibold" onClick={() => void flow.zoomOut({ duration })} aria-label="Zoom out">−</button>
        <button className="grid size-11 place-items-center text-lg font-semibold" onClick={() => void flow.zoomIn({ duration })} aria-label="Zoom in">+</button>
      </span>
    </div>
  )
}

function RoadmapDetail({ node, relationships, relatedLearning, relatedProjects, editMode, onClose, onEditPosition, onToggleBranch, branchCollapsed, branchHiddenCount }: {
  node: RoadmapNodeView
  relationships: Array<{ id: string; type: string; direction: string; otherTitle: string; state: string; reasons: string[]; criterionStates: Array<{ criterionDefinitionId: string; state: string }> }>
  relatedLearning: Array<{ curriculumId: string; title: string }>
  relatedProjects: Array<{ projectId: string; title: string }>
  editMode: boolean
  onClose: () => void
  onEditPosition: () => void
  onToggleBranch: () => void
  branchCollapsed: boolean
  branchHiddenCount: number
}) {
  return (
    <div className="space-y-5" data-roadmap-detail-settled={node.id}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">{node.layoutLane.title}</p>
          <h2 id={`roadmap-detail-heading-${node.id}`} tabIndex={-1} className="mt-1 font-display text-2xl font-semibold">{node.title}</h2>
        </div>
        <Button variant="quiet" aria-label="Close details" onClick={onClose}><X className="size-5" /></Button>
      </div>
      {node.description ? <p className="text-sm leading-6 text-ink/65">{node.description}</p> : null}
      <section aria-labelledby={`overview-${node.id}`}>
        <h3 id={`overview-${node.id}`} className="text-sm font-semibold uppercase tracking-wide text-ink/55">Overview</h3>
        {node.targetRows.length ? <ul className="mt-2 space-y-2 text-sm">{node.targetRows.map((target) => (
          <li key={target.id} className="rounded-xl border border-ink/10 p-3">
            <p className="font-semibold">{target.domain} · {target.dimension}</p>
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-ink/65"><div><dt className="text-xs font-semibold uppercase">Current</dt><dd>{target.current}</dd></div><div><dt className="text-xs font-semibold uppercase">Target</dt><dd>{target.target}</dd></div><div><dt className="text-xs font-semibold uppercase">Comparison</dt><dd>{target.comparison}</dd></div><div><dt className="text-xs font-semibold uppercase">Priority</dt><dd>{target.priority}</dd></div><div><dt className="text-xs font-semibold uppercase">Confidence</dt><dd>{target.confidence}</dd></div><div><dt className="text-xs font-semibold uppercase">Freshness</dt><dd>{target.freshness}{target.reviewDue ? ' · review due' : ''}</dd></div></dl>
          </li>
        ))}</ul> : <p className="mt-2 text-sm text-ink/60">This capability is on a prerequisite path rather than a direct Profile target.</p>}
      </section>
      <section aria-labelledby={`path-${node.id}`}>
        <h3 id={`path-${node.id}`} className="text-sm font-semibold uppercase tracking-wide text-ink/55">Path</h3>
        {relationships.length ? <ul className="mt-2 space-y-2 text-sm">{relationships.map((relationship) => <li key={relationship.id} className="rounded-xl border border-ink/10 p-3"><p><span className="font-semibold">{relationship.type.replaceAll('_', ' ')}</span> · {relationship.direction} <span className="font-semibold">{relationship.otherTitle}</span></p><p className="mt-1 text-ink/60">State: {relationship.state.replaceAll('_', ' ')}</p>{relationship.reasons.length ? <p className="mt-1 text-status-unknown">Why unknown: {relationship.reasons.map((reason) => reason.replaceAll('_', ' ').toLowerCase()).join(', ')}</p> : null}</li>)}</ul> : <p className="mt-2 text-sm text-ink/60">No visible relationships for this item. Use relationship filters to disclose more context.</p>}
      </section>
      <section aria-labelledby={`evidence-${node.id}`}>
        <h3 id={`evidence-${node.id}`} className="text-sm font-semibold uppercase tracking-wide text-ink/55">Capability basis</h3>
        {node.capability.scopes.length ? <ul className="mt-2 space-y-2 text-sm">{node.capability.scopes.map((scope) => <li key={scope.scopeKey}><CapabilitySummary state={scope} /></li>)}</ul> : <p className="mt-2 text-sm text-status-unknown">Capability is Unknown for this item.</p>}
        {relationships.some((relationship) => relationship.criterionStates.length) ? <div className="mt-3"><p className="text-xs font-semibold uppercase tracking-wide text-ink/55">Prerequisite criteria</p><ul className="mt-2 space-y-1 text-sm">{relationships.flatMap((relationship) => relationship.criterionStates.map((criterion) => <li key={`${relationship.id}:${criterion.criterionDefinitionId}`} className="rounded-lg border border-ink/10 px-3 py-2">{relationship.direction} {relationship.otherTitle}: <span className="font-semibold">{criterion.state.replaceAll('_', ' ')}</span></li>))}</ul></div> : null}
        <p className="mt-2 text-xs leading-5 text-ink/55">Individual evidence records are not included in the Roadmap projection. This view reports its authoritative capability and prerequisite states without inferring evidence.</p>
      </section>
      <section aria-labelledby={`related-${node.id}`}>
        <h3 id={`related-${node.id}`} className="text-sm font-semibold uppercase tracking-wide text-ink/55">Roadmap context</h3>
        <div className="mt-2 flex flex-wrap gap-2">
          {node.isToday ? <StatusBadge label="Recommended for Today" tone="today" /> : null}
          {node.blockedLabel ? <StatusBadge label={node.blockedLabel} tone={toneFor(node)} /> : <StatusBadge label="Prerequisites met or not required" tone="success" />}
          <StatusBadge label={node.positionSource === 'user_override' ? 'Manual position' : 'Canonical position'} tone={node.positionSource === 'user_override' ? 'warning' : 'neutral'} />
        </div>
        <p className="mt-2 text-sm text-ink/60">This projection contains journey status and presentation position only; it does not assert Curriculum or Project associations.</p>
      </section>
      <div className="flex flex-wrap gap-2">
        <Link className="button-primary" to={paths.competency(node.id)}>Open competency detail</Link>
        {relatedLearning.length ? relatedLearning.map((item) => <Link key={item.curriculumId} className="button-secondary" to={paths.curriculum(item.curriculumId)}>Learn: {item.title}</Link>) : <Link className="button-secondary" to={paths.learn}>Browse learning</Link>}
        {relatedProjects.length ? relatedProjects.map((item) => <Link key={item.projectId} className="button-secondary" to={paths.project(item.projectId)}>Project: {item.title}</Link>) : <Link className="button-secondary" to={paths.projects}>Browse Projects</Link>}
        {branchHiddenCount ? <Button variant="secondary" onClick={onToggleBranch} aria-expanded={!branchCollapsed}><GitBranch className="size-4" />{branchCollapsed ? `Expand ${branchHiddenCount} branch items` : `Collapse branch (${branchHiddenCount} descendants)`}</Button> : null}
        <Button variant="secondary" onClick={onEditPosition}><Move className="size-4" />Set X/Y position</Button>
      </div>
      {!editMode ? <p className="text-xs text-ink/50">Turn on Edit layout to drag nodes. X/Y editing is always available for keyboard and touch.</p> : null}
      <details className="rounded-xl border border-ink/10 p-3 text-sm"><summary className="cursor-pointer font-semibold">Technical identity</summary><dl className="mt-3 grid gap-2 break-all text-xs text-ink/60"><div><dt className="font-semibold">Stable key</dt><dd>{node.stableKey}</dd></div><div><dt className="font-semibold">Semantic definition</dt><dd>{node.semanticDefinitionId}</dd></div></dl></details>
    </div>
  )
}

function PositionDialog({ node, open, onDismiss, onSave }: {
  node: RoadmapNodeView | null
  open: boolean
  onDismiss: () => void
  onSave: (position: { x: number; y: number }) => Promise<void>
}) {
  const [x, setX] = useState('0')
  const [y, setY] = useState('0')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const xRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!open || !node) return
    setX(String(Math.round(node.position.x)))
    setY(String(Math.round(node.position.y)))
    setError('')
  }, [node, open])
  if (!node) return null
  const submit = async () => {
    const position = { x: Number(x), y: Number(y) }
    if (!x.trim() || !y.trim() || !Number.isFinite(position.x) || !Number.isFinite(position.y)) {
      setError('Enter finite numeric X and Y coordinates.')
      xRef.current?.focus()
      return
    }
    setSaving(true)
    setError('')
    try { await onSave({ x: Math.round(position.x), y: Math.round(position.y) }); onDismiss() }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The position could not be saved.'); xRef.current?.focus() }
    finally { setSaving(false) }
  }
  const step = (value: string, delta: number, setter: (value: string) => void) => setter(String((Number(value) || 0) + delta))
  return <Dialog open={open} label={`Set position for ${node.title}`} onDismiss={onDismiss} className="absolute left-1/2 top-1/2 max-h-[calc(100dvh-1rem)] w-[min(92vw,30rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto overscroll-contain rounded-2xl bg-white p-6 shadow-xl"><div className="space-y-5"><div><p className="eyebrow">Manual layout</p><h2 className="font-display text-2xl font-semibold">Set X/Y position</h2><p className="mt-2 text-sm text-ink/60">Coordinates affect presentation only. They never change learning order or eligibility.</p></div>{error ? <MutationError>{error}</MutationError> : null}<div className="grid grid-cols-2 gap-3"><div><TextField ref={xRef} label="X coordinate" type="number" value={x} error={error ? 'Enter a numeric coordinate.' : undefined} onChange={(event) => setX(event.target.value)} /><div className="mt-2 flex" role="group" aria-label="Adjust X coordinate"><button className="button-secondary size-11 p-0" onClick={() => step(x, -16, setX)} aria-label="Decrease X">−</button><button className="button-secondary size-11 p-0" onClick={() => step(x, 16, setX)} aria-label="Increase X">+</button></div></div><div><TextField label="Y coordinate" type="number" value={y} error={error ? 'Enter a numeric coordinate.' : undefined} onChange={(event) => setY(event.target.value)} /><div className="mt-2 flex" role="group" aria-label="Adjust Y coordinate"><button className="button-secondary size-11 p-0" onClick={() => step(y, -16, setY)} aria-label="Decrease Y">−</button><button className="button-secondary size-11 p-0" onClick={() => step(y, 16, setY)} aria-label="Increase Y">+</button></div></div></div><div className="sticky bottom-0 flex justify-end gap-2 bg-white py-1"><Button variant="secondary" onClick={onDismiss}>Cancel</Button><Button disabled={saving} onClick={() => void submit()}>{saving ? 'Saving…' : 'Save position'}</Button></div></div></Dialog>
}

export function RoadmapJourneyPage() {
  const compact = useCompactViewport()
  const flow = useReactFlow<Node>()
  const [searchParams, setSearchParams] = useSearchParams()
  const searchParamsRef = useRef(searchParams)
  searchParamsRef.current = searchParams
  const [projection, setProjection] = useState<RoadmapProjection | null>(null)
  const [levelTitles, setLevelTitles] = useState<Map<string, { key: string; title: string }>>(new Map())
  const [curriculumUnits, setCurriculumUnits] = useState<CurriculumCatalogUnit[]>([])
  const [projectCandidates, setProjectCandidates] = useState<ReturnType<typeof adaptProjectCatalogCandidate>[]>([])
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [collapsedLanes, setCollapsedLanes] = useState<Set<string>>(new Set())
  const [collapsedBranches, setCollapsedBranches] = useState<Set<string>>(new Set())
  const [editMode, setEditMode] = useState(false)
  const [mapActivated, setMapActivated] = useState(false)
  const [mapReady, setMapReady] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [positionNode, setPositionNode] = useState<RoadmapNodeView | null>(null)
  const [dragPositions, setDragPositions] = useState<Map<string, { x: number; y: number }>>(new Map())
  const previousView = useRef<string | null>(null)
  const previousSelectedId = useRef<string | null>(null)
  const mapExitRef = useRef<HTMLButtonElement | null>(null)
  const mapActivateRef = useRef<HTMLButtonElement | null>(null)
  const flowNodeCache = useRef(new Map<string, Node>())
  const laneNodeCache = useRef(new Map<string, Node>())
  const flowEdgeCache = useRef(new Map<string, Edge>())

  useEffect(() => {
    if (searchParams.get('__measure') !== '1') return
    const scope = window as typeof window & { __LCC_ROADMAP_COMMITS__?: number }
    scope.__LCC_ROADMAP_COMMITS__ = (scope.__LCC_ROADMAP_COMMITS__ ?? 0) + 1
  })

  const updateParam = useCallback((key: string, value?: string) => {
    setSearchParams(() => {
      const next = new URLSearchParams(searchParamsRef.current)
      if (value) next.set(key, value); else next.delete(key)
      return next
    }, { replace: key !== 'focus' })
  }, [setSearchParams])

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [nextProjection, scales] = await Promise.all([
        apiV2<RoadmapProjection>('/roadmap-projection/current'),
        apiV2<CapabilityScale[]>('/capability-scales'),
      ])
      setProjection(nextProjection)
      setLevelTitles(new Map(scales.flatMap((scale) => scale.levels).map((level) => [level.id, { key: level.stableKey, title: level.displayLabel }])))
      void Promise.allSettled([
        apiV2<{ units: CurriculumCatalogUnit[] }>('/curricula/catalog/active'),
        apiV2<{ candidates: ProjectCatalogCandidateApi[] }>('/projects/catalog/current'),
      ]).then(([curricula, projects]) => {
        if (curricula.status === 'fulfilled') setCurriculumUnits(curricula.value.units ?? [])
        if (projects.status === 'fulfilled') setProjectCandidates((projects.value.candidates ?? []).map(adaptProjectCatalogCandidate))
      })
    }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'The learning roadmap could not be loaded.') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => void load(), [load])

  const model = useMemo(() => projection ? createRoadmapViewModel(projection, levelTitles) : null, [levelTitles, projection])
  const selectedId = searchParams.get('focus')
  const selected = selectedId && model ? model.nodeById.get(selectedId) ?? null : null
  const view = searchParams.get('view') === 'map' ? 'map' : 'outline'
  useEffect(() => {
    if (previousView.current && previousView.current !== view) {
      window.requestAnimationFrame(() => {
        if (selectedId && !(compact && view === 'map' && !mapActivated)) {
          const destination = document.querySelector<HTMLElement>(`[data-roadmap-node-id="${CSS.escape(selectedId)}"] button`)
          ;(destination ?? document.getElementById(view === 'map' ? 'roadmap-map-heading' : 'roadmap-outline-heading'))?.focus()
        } else if (compact && view === 'map' && !mapActivated) {
          mapActivateRef.current?.focus()
        } else {
          document.getElementById(view === 'map' ? 'roadmap-map-heading' : 'roadmap-outline-heading')?.focus()
        }
      })
    }
    previousView.current = view
  }, [compact, mapActivated, selectedId, view])
  useEffect(() => {
    if (!selected || compact) return
    window.requestAnimationFrame(() => document.getElementById(`roadmap-detail-heading-${selected.id}`)?.focus({ preventScroll: true }))
  }, [compact, selected])
  useEffect(() => {
    const previous = previousSelectedId.current
    previousSelectedId.current = selectedId
    if (compact || !previous || selectedId) return
    window.requestAnimationFrame(() => {
      const destination = document.querySelector<HTMLElement>(`[data-roadmap-node-id="${CSS.escape(previous)}"] button`)
      ;(destination ?? document.getElementById('main-content'))?.focus({ preventScroll: true })
    })
  }, [compact, selectedId])
  useEffect(() => {
    if (!compact || !mapActivated) return
    mapExitRef.current?.focus()
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault(); setMapActivated(false)
      window.requestAnimationFrame(() => mapActivateRef.current?.focus())
    }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [compact, mapActivated])
  const relationshipParam = searchParams.get('relationships')
  const defaultRelationshipTypes = ['specialization']
  const relationshipTypes = new Set(relationshipParam === null
    ? defaultRelationshipTypes
    : relationshipParam === 'none' ? [] : relationshipParam.split(',').filter(Boolean))
  const visibilityState: RoadmapVisibility = {
    query: searchParams.get('q') ?? '',
    targetedOnly: searchParams.get('targets') === '1',
    todayOnly: searchParams.get('today') === '1',
    attentionOnly: searchParams.get('attention') === '1',
    showPrerequisites: relationshipTypes.has('prerequisite'),
    relationshipTypes,
    collapsedLanes,
    collapsedBranches,
    focusedNodeId: selectedId,
  }
  const visible = model ? applyRoadmapVisibility(model, visibilityState) : null
  const toggleRelationship = (key: string) => {
    const next = new Set(relationshipTypes)
    if (next.has(key)) next.delete(key); else next.add(key)
    updateParam('relationships', next.size ? relationshipOptions.map(([value]) => value).filter((value) => next.has(value)).join(',') : 'none')
  }

  const selectNode = useCallback((id: string) => {
    updateParam('focus', id)
    setNotice(`${model?.nodeById.get(id)?.title ?? 'Roadmap item'} selected. Details opened.`)
  }, [model?.nodeById, updateParam])
  const closeSelected = useCallback(() => {
    const id = selectedId
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('focus')
      if (compact && view === 'map') next.set('view', 'map')
      return next
    })
    if (!id) return
    window.requestAnimationFrame(() => {
      const destination = document.querySelector<HTMLElement>(`[data-roadmap-node-id="${CSS.escape(id)}"] button`)
      ;(destination ?? document.getElementById('main-content'))?.focus({ preventScroll: true })
    })
  }, [compact, selectedId, setSearchParams, view])
  const returnToOverview = useCallback(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('focus')
      next.delete('q')
      return next
    }, { replace: true })
    setNotice('Returned to the saved overview. Collapsed lanes and branches were preserved.')
  }, [setSearchParams])
  const savePosition = useCallback(async (node: RoadmapNodeView, position: { x: number; y: number }) => {
    if (!projection?.scopeKey) throw new Error('The active Roadmap scope is unavailable.')
    await apiV2(`/roadmap-projection/${projection.scopeKey}/positions/${node.id}`, { method: 'PUT', body: JSON.stringify({ node_key: node.id, position_x: Math.round(position.x), position_y: Math.round(position.y) }) })
    setProjection((current) => current ? { ...current, nodes: current.nodes?.map((item) => item.id === node.id ? { ...item, position, positionSource: 'user_override' } : item) } : current)
    setDragPositions((current) => { const next = new Map(current); next.delete(node.id); return next })
    setNotice(`${node.title} position saved.`)
  }, [projection?.scopeKey])
  const resetLayout = async () => {
    if (!projection?.scopeKey) return
    setWorking(true); setError('')
    try {
      const response = await apiV2<{ projection: RoadmapProjection; clearedCount: number }>(`/roadmap-projection/${projection.scopeKey}/positions`, { method: 'DELETE' })
      setProjection(response.projection); setDragPositions(new Map()); setResetOpen(false); setNotice(`Canonical layout restored. ${response.clearedCount} manual position${response.clearedCount === 1 ? '' : 's'} removed.`)
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'The layout could not be reset.') }
    finally { setWorking(false) }
  }
  const rebuild = async () => {
    setWorking(true); setError('')
    try { setProjection(await apiV2<RoadmapProjection>('/roadmap-projection/rebuild', { method: 'POST' })); setNotice('Roadmap rebuilt from current canonical facts.') }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'The Roadmap could not be rebuilt.') }
    finally { setWorking(false) }
  }

  if (loading) return <LoadingState label="Building your learning journey" />
  if (error && !projection) return <ErrorState message={error} retry={() => void load()} />
  if (!projection?.configured) return <EmptyState title="Your learning journey is not configured" detail={projection?.guidance ?? 'Activate a Target Profile and Learning Graph to begin.'} />
  if (!model || !visible) return null

  const journeyLanes = orderRoadmapLanesForJourney(model.lanes)

  const laneNodes: Node[] = model.lanes.flatMap((lane) => {
    const nodes = visible.nodes.filter((node) => node.layoutLane.id === lane.id)
    if (!nodes.length) return []
    const minX = Math.min(...nodes.map((node) => node.position.x)) - 40
    const minY = Math.min(...nodes.map((node) => node.position.y)) - 52
    const maxX = Math.max(...nodes.map((node) => node.position.x)) + 280
    const maxY = Math.max(...nodes.map((node) => node.position.y)) + 180
    const id = `lane:${lane.id}`
    const width = maxX - minX
    const height = maxY - minY
    const previous = laneNodeCache.current.get(id)
    if (previous && previous.position.x === minX && previous.position.y === minY && previous.style?.width === width && previous.style?.height === height) return [previous]
    const next: Node = { id, position: { x: minX, y: minY }, data: { label: `${lane.title} branch` }, draggable: false, selectable: false, connectable: false, focusable: false, zIndex: -10, style: { width, height, borderRadius: 24, border: '2px solid rgba(50,102,83,.2)', background: 'rgba(50,102,83,.035)', color: '#326653', fontSize: 14, fontWeight: 700, padding: 12, textAlign: 'left' } }
    laneNodeCache.current.set(id, next)
    return [next]
  })
  const journeyNodes: Node[] = visible.nodes.map((node) => {
    const position = dragPositions.get(node.id) ?? node.position
    const selectedState = node.id === selectedId
    const revealed = visible.temporarilyRevealed.has(node.id) || visible.searchRevealed.has(node.id)
    const previous = flowNodeCache.current.get(node.id)
    const previousData = previous?.data as JourneyNodeData | undefined
    if (previous && previousData?.node === node && previousData.selected === selectedState && previousData.temporarilyRevealed === revealed && previousData.editMode === editMode && previous.position.x === position.x && previous.position.y === position.y) return previous
    const next: Node = { id: node.id, type: 'journey', position, draggable: editMode, data: { node, selected: selectedState, temporarilyRevealed: revealed, editMode, onSelect: selectNode }, ariaLabel: `${node.title}, ${node.layoutLane.title}, ${node.targetLabel}, ${node.capabilityLabel}`, style: { width: 240, height: 140 } }
    flowNodeCache.current.set(node.id, next)
    return next
  })
  const flowNodes: Node[] = [...laneNodes, ...journeyNodes]
  const flowEdges: Edge[] = visible.edges.map((edge) => {
    const state = edge.satisfaction.aggregate_state
    const color = relationshipColor(edge.edgeType, state)
    const previous = flowEdgeCache.current.get(edge.id)
    if (previous && previous.source === edge.source && previous.target === edge.target && previous.data?.state === state && previous.data?.edgeType === edge.edgeType) return previous
    const next: Edge = { id: edge.id, source: edge.source, target: edge.target, type: 'journey', data: { edgeType: edge.edgeType, state, reasons: edge.satisfaction.unknown_reasons }, markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color }, focusable: false }
    flowEdgeCache.current.set(edge.id, next)
    return next
  })
  const closureIds = [...visible.temporarilyRevealed]
  const selectedRelationships = selected ? visible.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id).map((edge) => ({
    id: edge.id,
    type: edge.edgeType,
    direction: edge.source === selected.id ? 'leads to' : 'comes from',
    otherTitle: model.nodeById.get(edge.source === selected.id ? edge.target : edge.source)?.title ?? 'Unknown capability',
    state: edge.edgeType === 'prerequisite' ? edge.satisfaction.aggregate_state : 'contextual',
    reasons: edge.satisfaction.unknown_reasons ?? [],
    criterionStates: edge.satisfaction.criterion_states ?? [],
  })) : []
  const branchHiddenCount = selected ? model.nodes.filter((candidate) => {
    let parent = candidate.presentationParentId
    while (parent) { if (parent === selected.id) return true; parent = model.nodeById.get(parent)?.presentationParentId ?? null }
    return false
  }).length : 0
  const selectedLearning = selected ? curriculumUnits.filter((unit) => unit.targets?.some((target) => target.semanticDefinitionId === selected.semanticDefinitionId)).map((unit) => ({ curriculumId: unit.curriculumId, title: unit.title })) : []
  const selectedProjects = selected ? projectCandidates.filter((item) => item.semanticDefinitionIds.includes(selected.semanticDefinitionId)).map((item) => ({ projectId: item.projectId, title: item.title })) : []
  const toggleSet = (setter: React.Dispatch<React.SetStateAction<Set<string>>>, id: string) => setter((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next })

  return (
    <div className="page-stack" data-roadmap-ready={view === 'outline' || mapReady ? 'true' : undefined}>
      <PageHeader eyebrow="Canonical V2 journey" title="Roadmap" description="Start with shared foundations, build through domain and specialization branches, then move toward your targets." actions={!compact ? <><Button variant="secondary" disabled={working} onClick={() => void rebuild()}><RefreshCw className="size-4" />Rebuild</Button><Button variant="secondary" disabled={working} onClick={() => setResetOpen(true)}><RotateCcw className="size-4" />Reset layout</Button></> : undefined} />
      <LiveNotice>{notice}</LiveNotice>
      {error ? <MutationError>{error}</MutationError> : null}

      <Surface className="space-y-4 p-4" aria-label="Roadmap controls">
        <div className="grid gap-3 lg:grid-cols-[minmax(15rem,1fr)_auto_auto] lg:items-end">
          <label className="block"><span className="mb-1.5 block text-sm font-semibold">Search the journey</span><span className="relative block"><Search className="pointer-events-none absolute left-3 top-3.5 size-4 text-ink/45" /><input className="field pl-10" value={visibilityState.query} onChange={(event) => updateParam('q', event.target.value || undefined)} onKeyDown={(event) => { if (event.key !== 'Enter' || !visible.nodes.length) return; event.preventDefault(); selectNode(visible.nodes.find((node) => node.searchText.includes(visibilityState.query.toLowerCase()))?.id ?? visible.nodes[0].id) }} placeholder="Competency, domain, target…" /></span></label>
          <div className="flex rounded-xl border border-ink/15 bg-white/60 p-1" role="group" aria-label="Roadmap view">
            <button className={`button-quiet ${view === 'map' ? 'bg-ink text-white hover:bg-ink hover:text-white' : ''}`} aria-pressed={view === 'map'} onClick={() => updateParam('view', 'map')}><MapIcon className="size-4" />Map</button>
            <button className={`button-quiet ${view === 'outline' ? 'bg-ink text-white hover:bg-ink hover:text-white' : ''}`} aria-pressed={view === 'outline'} onClick={() => updateParam('view', 'outline')}><List className="size-4" />Outline</button>
          </div>
          {!compact ? <Button variant={editMode ? 'primary' : 'secondary'} aria-pressed={editMode} onClick={() => setEditMode((current) => !current)}><Move className="size-4" />{editMode ? 'Finish editing' : 'Edit layout'}</Button> : null}
        </div>
        <details className="rounded-xl border border-ink/10 bg-white/45 p-3 xl:contents" open={!compact ? true : undefined}>
          <summary className="min-h-11 cursor-pointer py-2 text-sm font-semibold xl:hidden"><span className="inline-flex items-center gap-2"><SlidersHorizontal className="size-4" />More Roadmap controls</span></summary>
          <div className="mt-3 space-y-3 xl:mt-0 xl:contents">
            {compact ? <div className="flex flex-wrap gap-2"><Button variant={editMode ? 'primary' : 'secondary'} aria-pressed={editMode} onClick={() => setEditMode((current) => !current)}><Move className="size-4" />{editMode ? 'Finish editing' : 'Edit layout'}</Button><Button variant="secondary" disabled={working} onClick={() => void rebuild()}><RefreshCw className="size-4" />Rebuild</Button><Button variant="secondary" disabled={working} onClick={() => setResetOpen(true)}><RotateCcw className="size-4" />Reset layout</Button></div> : null}
            <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm" aria-label="Roadmap filters"><span className="inline-flex items-center gap-2 font-semibold"><SlidersHorizontal className="size-4" />Show</span>{[
              ['targets', 'Targets only', visibilityState.targetedOnly], ['today', 'Today', visibilityState.todayOnly], ['attention', 'Needs attention', visibilityState.attentionOnly],
            ].map(([key, label, checked]) => <label key={String(key)} className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={Boolean(checked)} onChange={(event) => updateParam(String(key), event.target.checked ? '1' : undefined)} />{label}</label>)}</div>
            <fieldset className="border-t border-ink/10 pt-3"><legend className="text-sm font-semibold">Relationships</legend><div className="mt-1 flex flex-wrap gap-x-5 gap-y-1 text-sm">{relationshipOptions.map(([key, label]) => <label key={key} className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={relationshipTypes.has(key)} onChange={() => toggleRelationship(key)} />{label}</label>)}</div></fieldset>
            <div className="flex flex-wrap items-center gap-2 border-t border-ink/10 pt-3" aria-label="Status and relationship legend"><StatusBadge label="Target" tone="info" /><StatusBadge label="Today" tone="today" /><StatusBadge label="Blocked / Not met" tone="critical" /><StatusBadge label="Unknown" tone="unknown" /><StatusBadge label="Review due" tone="warning" /><span className="text-xs text-ink/55">Solid arrows: prerequisite/specialization · dashed: recommended/related</span>{selectedId || visibilityState.query ? <Button variant="quiet" onClick={returnToOverview}>Return to overview</Button> : null}</div>
          </div>
        </details>
      </Surface>

      <nav className={`${compact && view === 'outline' ? 'hidden' : 'flex'} gap-2 overflow-x-auto pb-1`} aria-label="Roadmap domain branches">{journeyLanes.map((lane) => {
        const counts = visible.laneCounts.get(lane.id)!
        const collapsed = collapsedLanes.has(lane.id)
        const temporarilyOpen = visible.nodes.some((node) => node.layoutLane.id === lane.id && (visible.temporarilyRevealed.has(node.id) || visible.searchRevealed.has(node.id)))
        const expanded = !collapsed || temporarilyOpen
        const targets = lane.nodes.filter((node) => node.isTargeted).length
        const today = lane.nodes.filter((node) => node.isToday).length
        const unknown = lane.nodes.filter((node) => node.hasUnknown).length
        const blocked = lane.nodes.filter((node) => node.blockedLabel?.startsWith('Blocked')).length
        const hiddenRelationships = model.edges.filter((edge) => (lane.nodes.some((node) => node.id === edge.source) || lane.nodes.some((node) => node.id === edge.target)) && !visible.edges.some((item) => item.id === edge.id)).length
        const stage = roadmapJourneyStageMeta(lane)
        return <button key={lane.id} className={`min-h-16 shrink-0 rounded-xl border border-l-4 px-4 py-2 text-left ${stage.border} ${expanded ? 'border-moss/25 bg-moss/5' : 'border-ink/15 bg-white/50'}`} onClick={() => toggleSet(setCollapsedLanes, lane.id)} aria-expanded={expanded}><span className="block text-[0.65rem] font-semibold uppercase tracking-wide text-moss">{stage.label} · {stage.detail}</span><span className="font-semibold">{lane.title}</span><span className="ml-2 text-xs text-ink/50">{counts.visible}/{counts.total}</span><span className="mt-1 block text-[0.65rem] text-ink/55">{targets} targets · {today} Today · {blocked} Not met · {unknown} Unknown · {hiddenRelationships} hidden relationships{collapsed && temporarilyOpen ? ' · temporarily open' : ''}</span></button>
      })}</nav>

      {visible.nodes.length === 0 ? <EmptyState title="No journey items match" detail="Clear one or more filters or search terms to show the route again." action={<Button variant="secondary" onClick={() => setSearchParams({ view })}>Clear filters</Button>} /> : <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_23rem]">
        <div className="min-w-0" role="region" aria-label="Roadmap journey view">
          {view === 'map' ? <div className="space-y-3"><Surface className="flex items-start gap-3 border-l-4 border-moss p-4"><MapIcon className="mt-0.5 size-5 shrink-0 text-moss" /><div><h2 id="roadmap-map-heading" tabIndex={-1} className="font-display text-lg font-semibold">Map overview</h2><p className="mt-1 text-sm leading-6 text-ink/65">Use this spatial view to inspect branches and relationships. Outline is the clearest step-by-step reading of the learning journey; focus a target here to isolate its prerequisite path.</p></div></Surface><div className="surface relative h-[min(72vh,48rem)] min-h-[32rem] overflow-hidden" aria-labelledby="roadmap-map-heading" data-roadmap-map-surface>
            {compact && !mapActivated ? <div className="absolute inset-0 z-30 flex items-center justify-center bg-paper/95 p-8 text-center"><div><MapIcon className="mx-auto size-9 text-moss" /><h3 className="mt-3 font-display text-xl font-semibold">Interact with map</h3><p className="mt-2 max-w-sm text-sm text-ink/60">The page scrolls normally until you activate pan and zoom. Outline remains the recommended mobile view.</p><button ref={mapActivateRef} className="button-primary mt-4" onClick={() => setMapActivated(true)}>Interact with map</button></div></div> : null}
            <div className="h-full" ref={(element) => { if (!element) return; if (compact && !mapActivated) { element.setAttribute('inert', ''); element.setAttribute('aria-hidden', 'true') } else { element.removeAttribute('inert'); element.removeAttribute('aria-hidden') } }}>
            <ReactFlow<Node>
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              fitView
              fitViewOptions={{ padding: 0.12 }}
              minZoom={0.18}
              maxZoom={1.8}
              nodesDraggable={editMode}
              nodesFocusable={false}
              nodesConnectable={false}
              elementsSelectable
              panOnScroll={!compact || mapActivated}
              zoomOnScroll={!compact || mapActivated}
              preventScrolling={!compact || mapActivated}
              onNodeDrag={(_event, node) => setDragPositions((current) => new Map(current).set(node.id, node.position))}
              onNodeDragStop={(_event, dragged) => { const node = model.nodeById.get(dragged.id); if (!node) return; void savePosition(node, { x: Math.round(dragged.position.x), y: Math.round(dragged.position.y) }).catch((caught) => { flowNodeCache.current.delete(node.id); flow.updateNode(node.id, { position: node.position }); setDragPositions((current) => { const next = new Map(current); next.delete(node.id); return next }); setError(caught instanceof Error ? caught.message : 'The position could not be saved. The previous position was restored.') }) }}
              onInit={() => setMapReady(true)}
              onlyRenderVisibleElements={searchParams.get('__geometry') !== 'all'}
              proOptions={{ hideAttribution: true }}
            ><Background gap={28} color="#d9d7cf" /><RoadmapCanvasControls selectedIds={closureIds} /></ReactFlow></div>
            {compact && mapActivated ? <button ref={mapExitRef} className="button-secondary absolute right-3 top-3 z-20 bg-white/95" onClick={() => { setMapActivated(false); window.requestAnimationFrame(() => mapActivateRef.current?.focus()) }}>Done interacting</button> : null}
          </div></div> : <div className="space-y-4" aria-labelledby="roadmap-outline-heading"><h2 id="roadmap-outline-heading" tabIndex={-1} className="sr-only">Learning journey outline</h2>{journeyLanes.map((lane) => { const laneNodes = visible.nodes.filter((node) => node.layoutLane.id === lane.id); const collapsed = collapsedLanes.has(lane.id); const temporarilyOpen = laneNodes.some((node) => visible.temporarilyRevealed.has(node.id) || visible.searchRevealed.has(node.id)); const expanded = !collapsed || temporarilyOpen; const stage = roadmapJourneyStageMeta(lane); return <section key={lane.id} className={`surface overflow-hidden border-l-4 ${stage.border}`}><button className="flex min-h-16 w-full items-center justify-between gap-3 border-b border-ink/10 px-4 py-3 text-left" onClick={() => toggleSet(setCollapsedLanes, lane.id)} aria-expanded={expanded}><span><span className="block text-[0.65rem] font-semibold uppercase tracking-[0.14em] text-moss">{stage.label} · {stage.detail}</span><span className="mt-1 inline-block font-display text-lg font-semibold">{lane.title}</span><span className="ml-2 text-xs text-ink/50">{laneNodes.length} shown{collapsed && temporarilyOpen ? ' · temporarily revealed' : ''}</span></span>{expanded ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}</button>{expanded ? <ol className="divide-y divide-ink/10">{laneNodes.map((node) => { const revealed = visible.temporarilyRevealed.has(node.id) || visible.searchRevealed.has(node.id); return <li key={node.id} className={`${node.presentationParentId ? 'border-l-4 border-moss/25 pl-4 sm:ml-6' : ''}`} data-roadmap-node-id={node.id} data-current-summary={node.currentSummary} data-target-summary={node.targetSummary} data-target-status-summary={node.targetStatusSummary} data-target-rows={JSON.stringify(node.targetRows)} data-today={String(node.isToday)}>{node.presentationParentId ? <p className="px-4 pt-2 text-[0.65rem] font-semibold uppercase tracking-wide text-moss">Specialization branch</p> : null}<button className="grid min-h-20 w-full gap-2 px-4 py-3 text-left sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center" onClick={() => selectNode(node.id)} aria-current={node.id === selectedId ? 'true' : undefined}><span><span className="block font-semibold">{node.title}</span><span className="mt-1 block text-sm text-ink/60">Current: {node.currentSummary} · Target: {node.targetSummary} · {node.targetStatusSummary}</span></span><span className="flex flex-wrap gap-1"><StatusBadge label={node.isTargeted ? 'Target' : 'Path'} tone={node.isTargeted ? 'info' : 'neutral'} />{revealed ? <StatusBadge label="Focused path" tone="info" /> : null}{node.isToday ? <StatusBadge label="Today" tone="today" /> : null}{node.blockedLabel ? <StatusBadge label={node.blockedLabel} tone={toneFor(node)} /> : null}{node.targetRows.some((target) => target.reviewDue) ? <StatusBadge label="Review due" tone="warning" /> : null}{node.positionSource === 'user_override' ? <StatusBadge label="Manual position" tone="warning" /> : null}</span></button></li> })}</ol> : null}</section>})}<Surface className="p-4"><h3 className="font-display text-lg font-semibold">Visible relationships</h3>{visible.edges.length ? <ul className="mt-3 space-y-2 text-sm">{visible.edges.map((edge) => <li key={edge.id} className="rounded-xl border border-ink/10 p-3"><span className="font-semibold">{model.nodeById.get(edge.source)?.title}</span> → <span className="font-semibold">{model.nodeById.get(edge.target)?.title}</span><span className="block text-ink/60">{edge.edgeType.replaceAll('_', ' ')} · {edge.edgeType === 'prerequisite' ? edge.satisfaction.aggregate_state : 'contextual'}{edge.satisfaction.unknown_reasons?.length ? ` · ${edge.satisfaction.unknown_reasons.map((reason) => reason.replaceAll('_', ' ').toLowerCase()).join(', ')}` : ''}</span></li>)}</ul> : <p className="mt-2 text-sm text-ink/60">No relationships are visible under the current filters.</p>}</Surface></div>}
        </div>
        {selected && !compact ? <aside className="surface hidden h-fit max-h-[calc(100vh-3rem)] overflow-y-auto p-5 xl:sticky xl:top-6 xl:block" aria-label="Roadmap item details"><RoadmapDetail node={selected} relationships={selectedRelationships} relatedLearning={selectedLearning} relatedProjects={selectedProjects} editMode={editMode} onClose={closeSelected} onEditPosition={() => setPositionNode(selected)} branchCollapsed={collapsedBranches.has(selected.id)} branchHiddenCount={branchHiddenCount} onToggleBranch={() => toggleSet(setCollapsedBranches, selected.id)} /></aside> : !selected ? <aside className="surface hidden h-fit p-5 text-sm text-ink/60 xl:block"><LockKeyhole className="mb-3 size-5 text-moss" /><h2 className="font-display text-lg font-semibold text-ink">Choose a journey item</h2><p className="mt-2 leading-6">Open any item to compare current capability with its target, inspect prerequisites, and manage presentation position.</p></aside> : null}
      </div>}

      <Dialog open={Boolean(selected && compact && !positionNode)} label={selected ? `Details for ${selected.title}` : 'Roadmap item details'} onDismiss={closeSelected} className="absolute inset-x-2 bottom-[max(0.5rem,env(safe-area-inset-bottom))] max-h-[calc(100dvh-1rem-env(safe-area-inset-bottom))] overflow-y-auto overscroll-contain rounded-2xl bg-white p-5 shadow-xl">{selected ? <RoadmapDetail node={selected} relationships={selectedRelationships} relatedLearning={selectedLearning} relatedProjects={selectedProjects} editMode={editMode} onClose={closeSelected} onEditPosition={() => setPositionNode(selected)} branchCollapsed={collapsedBranches.has(selected.id)} branchHiddenCount={branchHiddenCount} onToggleBranch={() => toggleSet(setCollapsedBranches, selected.id)} /> : null}</Dialog>

      <PositionDialog node={positionNode} open={Boolean(positionNode)} onDismiss={() => setPositionNode(null)} onSave={(position) => positionNode ? savePosition(positionNode, position) : Promise.resolve()} />
      <Dialog open={resetOpen} label="Reset Roadmap layout" onDismiss={() => setResetOpen(false)} className="absolute left-1/2 top-1/2 max-h-[calc(100dvh-1rem)] w-[min(92vw,32rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto overscroll-contain rounded-2xl bg-white p-6 shadow-xl"><div className="space-y-4"><h2 className="font-display text-2xl font-semibold">Restore canonical layout?</h2><p className="text-sm leading-6 text-ink/65">This removes {model.nodes.filter((node) => node.positionSource === 'user_override').length} saved manual position(s) in scope <span className="break-all font-mono text-xs">{projection.scopeKey}</span>. It does not change the Learning Graph, Profile, capability, or eligibility.</p>{error ? <MutationError>{error}</MutationError> : null}<div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setResetOpen(false)}>Cancel</Button><Button disabled={working} onClick={() => void resetLayout()}>{working ? 'Resetting…' : 'Reset layout'}</Button></div></div></Dialog>
    </div>
  )
}

export function RoadmapJourneyPageWithProvider() {
  return <ReactFlowProvider><RoadmapJourneyPage /></ReactFlowProvider>
}
