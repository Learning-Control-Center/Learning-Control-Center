import { Handle, Position, type NodeProps } from '@xyflow/react'
import { ChevronDown } from 'lucide-react'

import {
  LANE_LABEL_WIDTH,
  NODE_HEIGHT,
  NODE_WIDTH,
  PHASE_HEADER_HEIGHT,
  PHASE_PADDING_X,
  type CompetencyFlowNode,
  type PhaseFlowNode,
} from './roadmapLayout'

const handleClass = '!size-2 !border-0 !bg-ink/25'

export function CompetencyNode({ data }: NodeProps<CompetencyFlowNode>) {
  const { competency, childCount, expanded, selected, statusColor, onToggleExpand } = data
  return (
    <div
      className={`flex flex-col justify-between rounded-2xl border-2 bg-[#fffdf8] p-3.5 text-left shadow-[0_12px_30px_rgba(20,33,28,.09)] ${
        selected ? 'ring-2 ring-moss ring-offset-2 ring-offset-parchment' : ''
      }`}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT, borderColor: statusColor }}
    >
      <Handle type="target" position={Position.Top} id="in-hierarchy" className={handleClass} />
      <Handle type="target" position={Position.Left} id="in-prereq" className={handleClass} />
      <Handle type="source" position={Position.Bottom} id="out-hierarchy" className={handleClass} />
      <Handle type="source" position={Position.Right} id="out-prereq" className={handleClass} />
      <div className="min-w-0">
        <p className="text-[0.65rem] font-semibold uppercase tracking-wider text-ink/45">
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
        </span>
        {childCount > 0 ? (
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
  const { title, orderIndex, isCurrent, width, height, lanes, empty } = data
  return (
    <div
      className={`rounded-3xl border ${
        isCurrent ? 'border-moss/60 bg-fern/10 shadow-soft' : 'border-ink/10 bg-white/45'
      }`}
      style={{ width, height }}
    >
      <header className="flex items-center gap-3 border-b border-ink/10 px-5" style={{ height: PHASE_HEADER_HEIGHT }}>
        <div className="min-w-0">
          <p className="font-mono text-[0.65rem] font-semibold uppercase tracking-[0.17em] text-moss">
            Phase {orderIndex + 1}
          </p>
          <p className="truncate font-display text-base font-semibold text-ink">{title}</p>
        </div>
        {isCurrent ? (
          <span className="ml-auto shrink-0 rounded-full bg-moss px-2.5 py-1 text-xs font-semibold text-white">
            Current phase
          </span>
        ) : null}
      </header>
      <div className="relative">
        {lanes.map((lane, index) => (
          <div
            key={lane.trackId}
            className={`absolute ${index > 0 ? 'border-t border-ink/5' : ''}`}
            style={{
              top: lane.y - PHASE_HEADER_HEIGHT,
              height: lane.height,
              left: PHASE_PADDING_X,
              right: PHASE_PADDING_X,
            }}
          >
            <span
              className="absolute left-0 top-1/2 -translate-y-1/2 truncate text-[0.7rem] font-semibold uppercase tracking-wider text-ink/40"
              style={{ width: LANE_LABEL_WIDTH - 16 }}
              title={lane.title}
            >
              {lane.title}
            </span>
          </div>
        ))}
        {empty ? (
          <p className="absolute left-5 top-6 text-sm text-ink/40">No competencies in this phase yet.</p>
        ) : null}
      </div>
    </div>
  )
}
