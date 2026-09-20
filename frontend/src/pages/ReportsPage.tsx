import { ChevronRight, FileText } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api, formatDuration } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'
import { PageHeader, ProvenanceNotice } from '../shared/components'
import { paths } from '../shared/navigation/paths'

type Report = {
  id: string
  type: string
  periodStart: string
  periodEnd: string
  generatedAt: string
  analyticsVersion: number
  payload: {
    totalDurationMs: number
    activeDays: number
    signals: { code: string }[]
  }
  markdown: string
}

type MarkdownBlock =
  | { kind: 'heading'; level: 2 | 3; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'list'; items: string[] }

function parseReportMarkdown(markdown: string): MarkdownBlock[] {
  const lines = markdown.replaceAll('\r\n', '\n').split('\n')
  const firstSection = lines.findIndex((line) => /^##\s+/.test(line))
  let index = firstSection >= 0 ? firstSection : 0
  const blocks: MarkdownBlock[] = []

  while (index < lines.length) {
    const line = lines[index].trim()
    if (!line) {
      index += 1
      continue
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      blocks.push({
        kind: 'heading',
        level: heading[1].length >= 3 ? 3 : 2,
        text: heading[2],
      })
      index += 1
      continue
    }
    if (/^-\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^-\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^-\s+/, ''))
        index += 1
      }
      blocks.push({ kind: 'list', items })
      continue
    }
    const paragraph = [line]
    index += 1
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^#{1,6}\s+/.test(lines[index].trim()) &&
      !/^-\s+/.test(lines[index].trim())
    ) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    blocks.push({ kind: 'paragraph', text: paragraph.join(' ') })
  }
  return blocks
}

export function ReportDocument({ markdown }: { markdown: string }) {
  const blocks = parseReportMarkdown(markdown)
  return (
    <div className="report-document" aria-label="Generated report content">
      {blocks.map((block, index) => {
        if (block.kind === 'heading') {
          return block.level === 2 ? (
            <h3 className="mt-8 font-display text-xl font-semibold first:mt-0" key={index}>
              {block.text}
            </h3>
          ) : (
            <h4 className="mt-6 font-display text-lg font-semibold" key={index}>
              {block.text}
            </h4>
          )
        }
        if (block.kind === 'list') {
          return (
            <ul className="mt-3 space-y-2 text-sm leading-7 text-ink/70" key={index}>
              {block.items.map((item, itemIndex) => (
                <li className="flex gap-3" key={`${itemIndex}-${item}`}>
                  <span className="mt-[0.7rem] size-1.5 shrink-0 rounded-full bg-moss" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          )
        }
        return (
          <p className="mt-3 text-sm leading-7 text-ink/70" key={index}>
            {block.text}
          </p>
        )
      })}
    </div>
  )
}

export function ReportsPage() {
  const [reports, setReports] = useState<Report[]>([])
  const [selected, setSelected] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const reportResponse = await api<{ items: Report[] }>('/reports?limit=100')
      setReports(reportResponse.items)
      setSelected((current) => current ?? reportResponse.items[0] ?? null)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Reports could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (loading) return <LoadingState label="Loading immutable report snapshots" />
  if (error) return <div className="space-y-6"><PageHeader eyebrow="Read-only V1 compatibility" title="Generated V1 Reports" description="Immutable report snapshots preserved with their original Analytics version." /><ErrorState message={error} retry={() => void load()} /></div>

  return (
    <div className="mx-auto w-full max-w-[78rem]">
      <header className="mb-7">
          <p className="eyebrow mb-2">Read-only V1 compatibility</p>
          <h1 className="page-title">Generated V1 Reports</h1>
          <p className="mt-2 text-sm text-ink/65">
          Immutable snapshots retain their original V1 Analytics meaning. Current reflection is edited from Today or Activity, outside this archive.
          </p>
      </header>
      <div className="mb-5"><ProvenanceNotice title="Immutable generated history"><p>Changing range filters elsewhere cannot rewrite these documents. Reflection editing remains outside this read-only archive.</p></ProvenanceNotice></div>
      <div className="grid gap-5 xl:grid-cols-[20rem_minmax(0,1fr)]">
        <aside className="surface overflow-hidden">
          <div className="border-b border-ink/10 p-5">
            <p className="eyebrow">Report archive</p>
          </div>
          <div className="max-h-[48rem] overflow-y-auto">
            {reports.length ? (
              reports.map((report) => (
                <button
                  key={report.id}
                  aria-pressed={selected?.id === report.id}
                  className={`flex w-full items-center justify-between gap-3 border-b border-ink/10 p-4 text-left transition ${selected?.id === report.id ? 'bg-moss/10' : 'hover:bg-white/60'}`}
                  onClick={() => setSelected(report)}
                >
                  <div>
                    <p className="text-sm font-semibold capitalize">{report.type} report</p>
                    <p className="mt-1 text-xs text-ink/55">
                      {report.periodStart} → {report.periodEnd}
                    </p>
                  </div>
                  <ChevronRight className="size-4 text-ink/45" />
                </button>
              ))
            ) : (
              <p className="p-5 text-sm text-ink/60">
                Reports appear after completed periods contain learning data.
              </p>
            )}
          </div>
        </aside>
        <div className="grid gap-5">
          {selected ? (
            <article className="surface p-6 sm:p-8">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-ink/10 pb-6">
                <div>
                  <p className="eyebrow">Analytics v{selected.analyticsVersion}</p>
                  <h2 className="mt-3 font-display text-3xl font-semibold capitalize">
                    {selected.type} learning report
                  </h2>
                  <p className="mt-2 text-sm text-ink/55">
                    {selected.periodStart} to {selected.periodEnd}
                  </p>
                  <p className="mt-1 text-sm text-ink/55">Generated {new Date(selected.generatedAt).toLocaleString()}</p>
                  {selected.type === 'daily' ? <Link className="mt-3 inline-flex text-sm font-medium underline" to={`${paths.today}?reflectionDate=${encodeURIComponent(selected.periodEnd)}#daily-reflection`}>Open reflection for {selected.periodEnd} on Today</Link> : null}
                </div>
                <FileText className="size-6 text-moss" />
              </div>
              <div className="my-7 grid gap-4 sm:grid-cols-2">
                <div className="rounded-xl bg-ink/5 p-4">
                  <p className="text-xs text-ink/55">Total duration</p>
                  <p className="mt-2 font-display text-2xl font-semibold">
                    {formatDuration(selected.payload.totalDurationMs)}
                  </p>
                </div>
                <div className="rounded-xl bg-ink/5 p-4">
                  <p className="text-xs text-ink/55">Active days</p>
                  <p className="mt-2 font-display text-2xl font-semibold">
                    {selected.payload.activeDays}
                  </p>
                </div>
              </div>
              <ReportDocument markdown={selected.markdown} />
            </article>
          ) : (
            <EmptyState title="No report selected" detail="Choose a generated snapshot from the archive." />
          )}
        </div>
      </div>
    </div>
  )
}
