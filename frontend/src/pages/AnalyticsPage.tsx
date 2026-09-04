import { BarChart3, CalendarDays, Clock3, Scale, ShieldCheck, Waves } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ApiError, api, formatDuration, formatRatio } from '../api'
import { ErrorState, LoadingState } from '../components/PageState'

type Distribution = { key: string; label?: string; durationMs: number; ratio: number | null }
type Analytics = {
  analyticsVersion: number
  range: { name: string; startDate: string; endDate: string }
  totalDurationMs: number
  activeDays: number
  weeklyTarget: { currentWeekActiveDays: number; targetActiveDays: number }
  durationAdherence: { localDate: string; durationMs: number; adherence: number | null }[]
  distributions: {
    activity: Distribution[]
    assistance: Distribution[]
    track: Distribution[]
  }
  independentCoding: {
    independentDurationMs: number
    practicalDurationMs: number
    ratio: number | null
  }
  regularity: { coefficientOfVariation: number | null; band: string | null }
  workloadTrend: { short: { differenceMs: number; changeRatio: number | null } }
  gaps: {
    latestCompletedGapDays: number | null
    longestCompletedGapDays: number | null
    ongoingGapDays: number | null
  }
  coverage: {
    verifiedCore: number
    applicableCore: number
    verifiedImportant: number
    applicableImportant: number
    weightedVerificationCoverage: number | null
  }
  reviewDebt: {
    count: number
    weightedCount: number
    items: { stableKey: string; daysOverdue: number }[]
  }
}

const palette = ['#326653', '#74a88c', '#c47745', '#5f7990', '#8271a6', '#b7a463']

export function AnalyticsPage() {
  const [range, setRange] = useState('30d')
  const [data, setData] = useState<Analytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api<Analytics>(`/analytics?range=${range}`))
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Analytics could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => {
    void load()
  }, [load])

  if (loading) return <LoadingState label="Calculating deterministic metrics" />
  if (error || !data) {
    return <ErrorState message={error || 'Analytics are unavailable.'} retry={() => void load()} />
  }

  return (
    <div className="mx-auto w-full max-w-[112rem]">
      <header className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow mb-2">What has happened?</p>
          <h1 className="page-title">Analytics</h1>
          <p className="mt-2 text-sm text-ink/60">
            Exact, explainable measurements. Undefined ratios remain N/A.
          </p>
        </div>
        <div className="flex rounded-xl border border-ink/10 bg-white/60 p-1" aria-label="Analytics range">
          {['7d', '30d', '90d', 'all'].map((item) => (
            <button
              key={item}
              className={`min-h-11 rounded-lg px-3 text-sm font-medium ${range === item ? 'bg-ink text-white' : 'text-ink/65 hover:bg-white'}`}
              onClick={() => setRange(item)}
            >
              {item}
            </button>
          ))}
        </div>
      </header>
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Metric
          icon={Clock3}
          label="Learning time"
          value={formatDuration(data.totalDurationMs)}
          detail={`${data.range.startDate} → ${data.range.endDate}`}
        />
        <Metric
          icon={CalendarDays}
          label="Active days"
          value={String(data.activeDays)}
          detail={`${data.weeklyTarget.currentWeekActiveDays} / ${data.weeklyTarget.targetActiveDays} this week`}
        />
        <Metric
          icon={ShieldCheck}
          label="Weighted verified"
          value={formatRatio(data.coverage.weightedVerificationCoverage)}
          detail={`${data.coverage.verifiedCore} / ${data.coverage.applicableCore} core`}
        />
        <Metric
          icon={Scale}
          label="Independent practice"
          value={formatRatio(data.independentCoding.ratio)}
          detail={`${formatDuration(data.independentCoding.independentDurationMs)} independent`}
        />
        <Metric
          icon={Waves}
          label="Regularity"
          value={data.regularity.band?.replaceAll('_', ' ') ?? 'N/A'}
          detail={
            data.regularity.coefficientOfVariation == null
              ? 'Insufficient data'
              : `CV ${data.regularity.coefficientOfVariation.toFixed(2)}`
          }
        />
      </section>
      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <ChartCard title="Daily duration" description="Exact qualifying learning time by local day.">
          {data.durationAdherence.length ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={data.durationAdherence}>
                <CartesianGrid stroke="#dfe5dd" vertical={false} />
                <XAxis
                  dataKey="localDate"
                  tick={{ fontSize: 11 }}
                  tickFormatter={(value) => value.slice(5)}
                />
                <YAxis
                  tick={{ fontSize: 11 }}
                  tickFormatter={(value) => `${Math.round(value / 3_600_000)}h`}
                />
                <Tooltip formatter={(value) => formatDuration(Number(value))} />
                <Bar dataKey="durationMs" fill="#326653" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <ChartEmpty message="No qualifying learning sessions in this period." />
          )}
        </ChartCard>
        <ChartCard title="Activity mix" description="Time-bearing sessions by concrete logged activity.">
          {data.distributions.activity.length ? (
            <div className="grid items-center gap-4 sm:grid-cols-[minmax(0,1fr)_12rem]">
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie
                    data={data.distributions.activity}
                    dataKey="durationMs"
                    nameKey="key"
                    innerRadius={70}
                    outerRadius={108}
                    paddingAngle={2}
                  >
                    {data.distributions.activity.map((entry, index) => (
                      <Cell key={entry.key} fill={palette[index % palette.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => formatDuration(Number(value))} />
                </PieChart>
              </ResponsiveContainer>
              <AccessibleLegend items={data.distributions.activity} />
            </div>
          ) : (
            <ChartEmpty message="No activity data in this period." />
          )}
        </ChartCard>
        <ChartCard
          title="Assistance distribution"
          description="AI assistance is telemetry, not a failure condition."
        >
          {data.distributions.assistance.length ? (
            <AccessibleBars items={data.distributions.assistance} />
          ) : (
            <ChartEmpty compact message="No assistance data in this period." />
          )}
        </ChartCard>
        <ChartCard title="Review debt" description="Freshness signals never mutate competency status.">
          <div className="flex items-end justify-between border-b border-ink/10 pb-5">
            <div>
              <p className="font-display text-4xl font-semibold">{data.reviewDebt.count}</p>
              <p className="mt-1 text-sm text-ink/60">competencies due</p>
            </div>
            <p className="text-sm text-ink/60">Weighted count {data.reviewDebt.weightedCount}</p>
          </div>
          <ul className="mt-4 space-y-3">
            {data.reviewDebt.items.slice(0, 6).map((item) => (
              <li className="flex items-center justify-between text-sm" key={item.stableKey}>
                <span className="font-mono text-xs">{item.stableKey}</span>
                <span>{item.daysOverdue}d overdue</span>
              </li>
            ))}
            {!data.reviewDebt.items.length ? (
              <li className="text-sm text-ink/60">No review debt in the current roadmap scope.</li>
            ) : null}
          </ul>
        </ChartCard>
      </div>
    </div>
  )
}

function Metric({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof Clock3
  label: string
  value: string
  detail: string
}) {
  return (
    <article className="surface p-5">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink/55">{label}</p>
        <Icon className="size-4 text-moss" />
      </div>
      <p className="mt-4 font-display text-2xl font-semibold capitalize">{value}</p>
      <p className="mt-1 truncate text-xs text-ink/55">{detail}</p>
    </article>
  )
}

function ChartCard({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <section className="surface min-w-0 p-5 sm:p-6">
      <div className="mb-5">
        <p className="font-display text-xl font-semibold">{title}</p>
        <p className="mt-1 text-sm text-ink/55">{description}</p>
      </div>
      {children}
    </section>
  )
}

function ChartEmpty({ message, compact = false }: { message: string; compact?: boolean }) {
  return (
    <div
      className={`grid place-items-center rounded-xl border border-dashed border-ink/15 bg-ink/[0.025] px-5 text-center ${compact ? 'min-h-36' : 'min-h-[17.5rem]'}`}
    >
      <div>
        <BarChart3 className="mx-auto size-5 text-moss/70" aria-hidden="true" />
        <p className="mt-3 text-sm font-medium text-ink/70">No data</p>
        <p className="mt-1 text-sm text-ink/55">{message}</p>
      </div>
    </div>
  )
}

function AccessibleLegend({ items }: { items: Distribution[] }) {
  return (
    <ul className="space-y-3">
      {items.map((item, index) => (
        <li className="flex items-center justify-between gap-3 text-xs" key={item.key}>
          <span className="flex items-center gap-2 capitalize">
            <span
              className="size-2.5 rounded-full"
              style={{ background: palette[index % palette.length] }}
            />
            {item.key.replaceAll('_', ' ')}
          </span>
          <span className="font-mono">{formatRatio(item.ratio)}</span>
        </li>
      ))}
    </ul>
  )
}

function AccessibleBars({ items }: { items: Distribution[] }) {
  return (
    <div className="space-y-4">
      {items.map((item) => (
        <div key={item.key}>
          <div className="mb-1.5 flex items-center justify-between text-xs">
            <span className="capitalize">{item.key.replaceAll('_', ' ')}</span>
            <span className="font-mono">
              {formatDuration(item.durationMs)} · {formatRatio(item.ratio)}
            </span>
          </div>
          <div className="h-2 rounded-full bg-ink/10">
            <div
              className="h-full rounded-full bg-moss"
              style={{ width: `${(item.ratio ?? 0) * 100}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
