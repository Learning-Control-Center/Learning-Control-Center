import {
  Handle,
  Position,
  getBezierPath,
  type EdgeProps,
  type NodeProps,
} from '@xyflow/react'
import { ChevronDown, ChevronUp, Crosshair, Link2, Pin } from 'lucide-react'

import {
  CHILD_INDENT,
  NODE_HEIGHT,
  NODE_WIDTH,
  PHASE_HEADER_HEIGHT,
  PHASE_PADDING_X,
  TRACK_HEADER_HEIGHT,
  TREE_BRANCH_COLOR,
  TREE_SPINE_COLOR,
  type CompetencyFlowNode,
  type PhaseFlowNode,
} from './roadmapLayout'

const handleClass = '!size-2 !border-0 !bg-ink/25'

const depthTone = ['bg-[#fffdf8]', 'bg-[#faf7ef]', 'bg-[#f6f2e8]'] as const

export function CompetencyNode({ data }: NodeProps<CompetencyFlowNode>) {
  const { competency, depth, childCount, expanded, prerequisiteCount, selected, statusColor, onToggleExpand } = data
  const hasChildren = childCount > 0
  const surface = depthTone[Math.min(depth, depthTone.length - 1)]
  return (
    <div
      className={`flex flex-col justify-between rounded-xl border border-ink/15 border-l-[3px] p-3 text-left shadow-[0_8px_22px_rgba(20,33,28,.07)] ${
        expanded && hasChildren ? 'bg-fern/10' : surface
      } ${selected ? 'ring-2 ring-moss ring-offset-2 ring-offset-parchment' : ''}`}
      style={{
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        borderLeftColor: hasChildren ? statusColor : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} id="in-hierarchy" className={handleClass} />
      <Handle type="target" position={Position.Left} id="in-prereq" className={handleClass} />
      <Handle type="source" position={Position.Bottom} id="out-hierarchy" className={handleClass} />
      <Handle type="source" position={Position.Right} id="out-prereq" className={handleClass} />
      <div className="min-w-0">
        <p className="text-[0.62rem] font-semibold uppercase tracking-wider text-ink/45">
          {competency.priority}
        </p>
        <p className="mt-1 line-clamp-2 font-display text-sm font-semibold leading-5 text-ink">
          {competency.title}
        </p>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 text-xs text-ink/55">
          <span
            className="size-2 shrink-0 rounded-full"
            style={{ backgroundColor: statusColor }}
            aria-hidden="true"
          />
          <span className="truncate">{competency.status.replaceAll('_', ' ')}</span>
          {prerequisiteCount > 0 ? (
            <span
              className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-ink/[0.05] px-1.5 py-0.5 text-[0.6rem] font-semibold text-ink/50"
              title={`${prerequisiteCount} prerequisite ${prerequisiteCount === 1 ? 'link' : 'links'} in the current view. Select the node to reveal them.`}
              aria-label={`${prerequisiteCount} prerequisite ${prerequisiteCount === 1 ? 'link' : 'links'} in the current view`}
            >
              <Link2 className="size-2.5" aria-hidden="true" />
              {prerequisiteCount}
            </span>
          ) : null}
        </span>
        {hasChildren ? (
          <button
            type="button"
            className="nodrag inline-flex shrink-0 items-center gap-0.5 rounded-full border border-ink/15 bg-white px-1.5 py-0.5 text-[0.65rem] font-semibold text-ink/60 transition hover:border-moss/40 hover:text-ink"
            onClick={(event) => {
              event.stopPropagation()
              onToggleExpand(competency.definitionId)
            }}
            aria-expanded={expanded}
            aria-label={`${expanded ? 'Collapse' : 'Expand'} ${childCount} child ${
              childCount === 1 ? 'competency' : 'competencies'
            } of ${competency.title}`}
          >
            {childCount}
            <ChevronDown
              className={`size-3 transition-transform ${expanded ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
          </button>
        ) : null}
      </div>
    </div>
  )
}

export function PhaseContainerNode({ data }: NodeProps<PhaseFlowNode>) {
  const {
    title,
    isCurrent,
    width,
    height,
    tracks,
    empty,
    collapsed,
    totalCount,
    verifiedCount,
    currentBusy,
    phaseId,
    onTogglePhaseCollapse,
    focusPhaseRef,
    onMakeCurrent,
  } = data
  const progressLabel = `${verifiedCount} of ${totalCount} verified`
  return (
    <div
      className={`rounded-2xl border ${
        isCurrent ? 'border-moss/60 bg-fern/10 shadow-soft' : 'border-ink/10 bg-white/45'
      }`}
      style={{ width, height }}
    >
      <header
        className="flex items-center gap-2 border-b border-ink/10 px-4"
        style={{ height: PHASE_HEADER_HEIGHT }}
      >
        <div className="min-w-0">
          <p className="truncate font-display text-lg font-semibold text-ink">{title}</p>
          <p className="text-xs text-ink/50">
            {collapsed ? `${totalCount} ${totalCount === 1 ? 'competency' : 'competencies'} hidden` : progressLabel}
          </p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {isCurrent ? (
            <span className="rounded-full bg-moss px-2.5 py-1 text-xs font-semibold text-white">Current</span>
          ) : (
            <button
              type="button"
              className="rounded-full border border-ink/15 bg-white/80 px-2.5 py-1 text-xs font-semibold text-ink/55 transition hover:border-moss/40 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => onMakeCurrent(phaseId)}
              disabled={currentBusy}
              aria-label={`Make ${title} the current phase`}
            >
              <Pin className="mr-1 inline size-3" aria-hidden="true" />
              {currentBusy ? 'Setting...' : 'Make current'}
            </button>
          )}
          <button
            type="button"
            className="nodrag grid size-8 place-items-center rounded-lg text-ink/55 transition hover:bg-ink/5 hover:text-ink"
            onClick={() => focusPhaseRef.current(phaseId)}
            aria-label={`Focus phase ${title}`}
            title="Focus phase"
          >
            <Crosshair className="size-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            className="nodrag grid size-8 place-items-center rounded-lg text-ink/55 transition hover:bg-ink/5 hover:text-ink"
            onClick={() => onTogglePhaseCollapse(phaseId)}
            aria-expanded={!collapsed}
            aria-label={collapsed ? `Expand phase ${title}` : `Collapse phase ${title}`}
            title={collapsed ? 'Expand phase' : 'Collapse phase'}
          >
            {collapsed ? (
              <ChevronDown className="size-4" aria-hidden="true" />
            ) : (
              <ChevronUp className="size-4" aria-hidden="true" />
            )}
          </button>
        </div>
      </header>
      {collapsed ? (
        <p className="px-4 pt-4 text-sm text-ink/45">
          Hidden from the canvas. Positions and progress are preserved.
        </p>
      ) : (
        <div className="relative">
          {tracks.map((track) => (
            <div
              key={track.trackId}
              className="absolute rounded-xl border border-ink/[0.07] bg-white/35"
              style={{
                top: track.y - PHASE_HEADER_HEIGHT,
                height: track.height,
                left: PHASE_PADDING_X,
                width: track.width,
              }}
            >
              <p
                className="truncate px-3 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.15em] text-ink/40"
                style={{ lineHeight: `${TRACK_HEADER_HEIGHT}px` }}
                title={track.title}
              >
                {track.title}
              </p>
            </div>
          ))}
          {empty ? (
            <p className="absolute left-4 top-6 text-sm text-ink/40">No competencies in this phase yet.</p>
          ) : null}
        </div>
      )}
    </div>
  )
}

/**
 * Structural hierarchy connector. Sibling children share one vertical spine
 * drawn down the group's indent channel, and each child connects to it with a
 * short horizontal branch. Cross-phase children (rare; a parent defined in a
 * different phase) fall back to an unobtrusive smoothstep line.
 */
export function TreeEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
}: EdgeProps) {
  const vertical = targetX >= sourceX + CHILD_INDENT - 1 && targetX < sourceX + NODE_WIDTH
  if (!vertical) {
    // A manually positioned child can sit anywhere on the canvas, including
    // above or beside its parent. Keep the structural hint soft and neutral
    // in that case instead of forcing an orthogonal route across the canvas.
    const [path] = getBezierPath({
      sourceX,
      sourceY,
      sourcePosition,
      targetX,
      targetY,
      targetPosition,
    })
    return (
      <path
        id={id}
        className="react-flow__edge-path"
        d={path}
        fill="none"
        stroke={TREE_BRANCH_COLOR}
        strokeWidth={1.25}
        strokeDasharray="3 4"
        markerEnd={markerEnd}
      />
    )
  }
  const spineX = targetX - CHILD_INDENT / 2
  return (
    <g fill="none" strokeLinecap="round">
      <path
        id={id}
        className="react-flow__edge-path"
        d={`M ${sourceX} ${sourceY} L ${spineX} ${sourceY} L ${spineX} ${targetY} L ${targetX} ${targetY}`}
        stroke={TREE_BRANCH_COLOR}
        strokeWidth={1.25}
        markerEnd={markerEnd}
      />
      <path
        d={`M ${sourceX} ${sourceY} L ${spineX} ${sourceY} L ${spineX} ${targetY}`}
        stroke={TREE_SPINE_COLOR}
        strokeWidth={1.75}
      />
    </g>
  )
}
