import { AlertTriangle, CheckCircle2, Download, FileJson, FileText, Upload } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, downloadText } from '../api'
import type { Roadmap } from '../types'

type Purpose = 'analysis_snapshot' | 'human_report' | 'portable_logical_backup'
type Preview = {
  valid: boolean
  dryRun: boolean
  summary: Record<string, unknown>
  diff: Record<string, unknown>
  confirmationToken: string
}
type Operation = {
  id: string
  operation: 'import' | 'export' | 'backup'
  kind: string
  createdAt: string
  applied?: boolean
  sourceFilename?: string
}

const purposes = [
  ['analysis_snapshot', 'Analysis Snapshot', FileJson, 'Selective JSON for external analysis.'],
  ['human_report', 'Human Report', FileText, 'Selective, readable Markdown.'],
  [
    'portable_logical_backup',
    'Portable Logical Backup',
    FileJson,
    'Complete secret-free application state.',
  ],
] as const

export function TransferPage() {
  const [purpose, setPurpose] = useState<Purpose>('analysis_snapshot')
  const [range, setRange] = useState('30d')
  const [categories, setCategories] = useState<string[]>([
    'analytics',
    'sessions',
    'verification',
  ])
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [currentPhaseOnly, setCurrentPhaseOnly] = useState(false)
  const [trackIds, setTrackIds] = useState<string[]>([])
  const [competencyIdentityIds, setCompetencyIdentityIds] = useState<string[]>([])
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null)
  const [packageValue, setPackageValue] = useState<Record<string, unknown> | null>(null)
  const [filename, setFilename] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [replaceExisting, setReplaceExisting] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [history, setHistory] = useState<Operation[]>([])
  const format = purpose === 'human_report' ? 'markdown' : 'json'
  const replacementRestore =
    packageValue?.packageType === 'portable_logical_backup' ||
    packageValue?.packageType === 'restore'

  const loadHistory = useCallback(async () => {
    const result = await api<{ items: Operation[] }>('/import-export/history?limit=12')
    setHistory(result.items)
  }, [])

  useEffect(() => {
    void loadHistory()
    void api<{ roadmap?: Roadmap }>('/roadmap/current').then((value) =>
      setRoadmap(value.roadmap ?? null),
    )
  }, [loadHistory])

  const exportData = async () => {
    setError('')
    setMessage('')
    const selectiveScope =
      purpose === 'portable_logical_backup'
        ? {
            range: 'all',
            start_date: null,
            end_date: null,
            current_phase_only: false,
            track_ids: [],
            competency_identity_ids: [],
            categories: [],
          }
        : {
            range,
            start_date: range === 'custom' ? startDate : null,
            end_date: range === 'custom' ? endDate : null,
            current_phase_only: currentPhaseOnly,
            track_ids: trackIds,
            competency_identity_ids: competencyIdentityIds,
            categories,
          }
    try {
      const result = await api<{ content: string | Record<string, unknown> }>(
        '/import-export/export',
        {
          method: 'POST',
          body: JSON.stringify({ purpose, format, ...selectiveScope }),
        },
      )
      const text =
        typeof result.content === 'string'
          ? result.content
          : JSON.stringify(result.content, null, 2)
      downloadText(
        text,
        `lcc-${purpose}-${new Date().toISOString().slice(0, 10)}.${format === 'json' ? 'json' : 'md'}`,
        format === 'json' ? 'application/json' : 'text/markdown',
      )
      setMessage('Export prepared without authentication secrets.')
      await loadHistory()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The export could not be prepared.')
    }
  }

  const readFile = async (file: File) => {
    setError('')
    setPreview(null)
    setMessage('')
    try {
      setPackageValue(JSON.parse(await file.text()) as Record<string, unknown>)
      setFilename(file.name)
    } catch {
      setError('The selected file is not valid JSON.')
    }
  }

  const inspect = async () => {
    if (!packageValue) return
    try {
      setPreview(
        await api<Preview>('/import-export/import/inspect', {
          method: 'POST',
          body: JSON.stringify({ filename, package: packageValue }),
        }),
      )
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The package is invalid.')
    }
  }

  const apply = async () => {
    if (!packageValue || !preview) return
    try {
      await api('/import-export/import/apply', {
        method: 'POST',
        body: JSON.stringify({
          filename,
          package: packageValue,
          confirmation_token: preview.confirmationToken,
          replace_existing: replaceExisting,
        }),
      })
      setMessage('Import applied transactionally.')
      setPreview(null)
      setPackageValue(null)
      await loadHistory()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The import could not be applied.')
    }
  }

  const competencies =
    roadmap?.phases.flatMap((phase) =>
      phase.tracks.flatMap((track) => track.competencies),
    ) ?? []
  const tracks = roadmap?.phases.flatMap((phase) => phase.tracks) ?? []

  return (
    <div className="mx-auto w-full max-w-[88rem]">
      <header className="mb-7">
        <p className="eyebrow mb-2">Portable by design</p>
        <h1 className="page-title">Import / Export</h1>
        <p className="mt-2 text-sm text-ink/60">
          Purpose determines format. Mutating packages never apply before validation and diff review.
        </p>
      </header>
      {error ? (
        <p className="mb-5 rounded-xl bg-rose-50 p-3 text-sm text-rose-800" role="alert">
          {error}
        </p>
      ) : null}
      {message ? (
        <p
          className="mb-5 flex items-center gap-2 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800"
          role="status"
        >
          <CheckCircle2 className="size-4" />
          {message}
        </p>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-2">
        <section className="surface p-5 sm:p-6">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-moss/10 text-moss">
              <Download className="size-5" />
            </span>
            <div>
              <h2 className="font-display text-xl font-semibold">Export</h2>
              <p className="text-sm text-ink/55">Choose a purpose, then its valid scope.</p>
            </div>
          </div>
          <div className="mt-6 grid gap-3">
            {purposes.map(([value, label, Icon, detail]) => (
              <button
                key={value}
                className={`flex items-start gap-3 rounded-xl border p-4 text-left ${purpose === value ? 'border-moss bg-moss/5' : 'border-ink/10 hover:border-moss/30'}`}
                onClick={() => setPurpose(value)}
                type="button"
              >
                <Icon className="mt-0.5 size-5 shrink-0 text-moss" />
                <span>
                  <span className="block font-semibold">{label}</span>
                  <span className="mt-1 block text-xs leading-5 text-ink/55">{detail}</span>
                </span>
              </button>
            ))}
          </div>
          {purpose !== 'portable_logical_backup' ? (
            <div className="mt-5">
              <label className="text-sm font-medium">
                Date range
                <select
                  className="field mt-2"
                  value={range}
                  onChange={(event) => setRange(event.target.value)}
                >
                  {['7d', '30d', '90d', 'all', 'custom'].map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
              </label>
              {range === 'custom' ? (
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-xs font-medium">
                    Start date
                    <input
                      className="field mt-1"
                      type="date"
                      value={startDate}
                      onChange={(event) => setStartDate(event.target.value)}
                      required
                    />
                  </label>
                  <label className="text-xs font-medium">
                    End date
                    <input
                      className="field mt-1"
                      type="date"
                      value={endDate}
                      onChange={(event) => setEndDate(event.target.value)}
                      required
                    />
                  </label>
                </div>
              ) : null}
              <label className="mt-4 flex min-h-11 items-center gap-2 rounded-xl border border-ink/10 px-3 text-sm">
                <input
                  type="checkbox"
                  checked={currentPhaseOnly}
                  onChange={(event) => setCurrentPhaseOnly(event.target.checked)}
                />
                Current phase only
              </label>
              {roadmap ? (
                <>
                  <FilterDetails
                    label="Track filters"
                    selectedCount={trackIds.length}
                    items={tracks.map((track) => ({ id: track.id, label: track.title }))}
                    selected={trackIds}
                    setSelected={setTrackIds}
                  />
                  <FilterDetails
                    label="Competency filters"
                    selectedCount={competencyIdentityIds.length}
                    items={competencies.map((item) => ({
                      id: item.identityId,
                      label: item.title,
                    }))}
                    selected={competencyIdentityIds}
                    setSelected={setCompetencyIdentityIds}
                    tall
                  />
                </>
              ) : null}
              <fieldset className="mt-4">
                <legend className="text-sm font-medium">Included categories</legend>
                <div className="mt-2 flex flex-wrap gap-2">
                  {['analytics', 'sessions', 'verification', 'reports', 'settings'].map((item) => (
                    <label
                      key={item}
                      className="flex min-h-11 items-center gap-2 rounded-lg border border-ink/10 px-3 text-xs capitalize"
                    >
                      <input
                        type="checkbox"
                        checked={categories.includes(item)}
                        onChange={(event) =>
                          setCategories((current) =>
                            event.target.checked
                              ? [...current, item]
                              : current.filter((entry) => entry !== item),
                          )
                        }
                      />
                      {item}
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>
          ) : (
            <p className="mt-5 rounded-xl bg-copper/10 p-4 text-sm leading-6 text-copper">
              Portable backups always include all supported learning state. Users, password hashes,
              live authentication sessions, CSRF secrets, and server secrets are never included.
            </p>
          )}
          <button className="button-primary mt-6 w-full" onClick={() => void exportData()}>
            <Download className="size-4" />
            Export {format.toUpperCase()}
          </button>
        </section>
        <section className="surface p-5 sm:p-6">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-copper/10 text-copper">
              <Upload className="size-5" />
            </span>
            <div>
              <h2 className="font-display text-xl font-semibold">Import</h2>
              <p className="text-sm text-ink/55">File → validate → diff → confirm → apply.</p>
            </div>
          </div>
          <label className="mt-6 flex min-h-40 cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-ink/20 bg-white/40 p-6 text-center hover:border-moss/50 focus-within:border-moss focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-moss">
            <Upload className="mb-3 size-6 text-moss" />
            <span className="font-semibold">{filename || 'Choose a JSON package'}</span>
            <span className="mt-1 text-xs text-ink/55">Selecting a file never mutates data.</span>
            <input
              className="sr-only"
              type="file"
              accept="application/json,.json"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void readFile(file)
              }}
            />
          </label>
          {packageValue && !preview ? (
            <button className="button-primary mt-5 w-full" onClick={() => void inspect()}>
              Validate and preview
            </button>
          ) : null}
          {preview ? (
            <div className="mt-5">
              <div className="rounded-xl border border-moss/20 bg-moss/5 p-4">
                <p className="flex items-center gap-2 font-semibold text-moss">
                  <CheckCircle2 className="size-4" />
                  Package valid · dry run complete
                </p>
                <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-ink/70">
                  {JSON.stringify(preview.diff, null, 2)}
                </pre>
              </div>
              {replacementRestore ? (
                <label className="mt-4 flex items-start gap-3 rounded-xl bg-amber-50 p-4 text-sm text-amber-900">
                  <input
                    className="mt-1 size-4"
                    type="checkbox"
                    checked={replaceExisting}
                    onChange={(event) => setReplaceExisting(event.target.checked)}
                  />
                  <span>
                    <strong>Allow full replacement restore</strong>
                    <span className="mt-1 block text-xs leading-5">
                      All existing portable learning state will be replaced. Authentication remains
                      unchanged. Merge restore is unavailable.
                    </span>
                  </span>
                </label>
              ) : null}
              <button className="button-primary mt-4 w-full" onClick={() => void apply()}>
                <AlertTriangle className="size-4" />
                Confirm and apply
              </button>
            </div>
          ) : null}
        </section>
      </div>
      <section className="surface mt-5 p-5 sm:p-6">
        <p className="eyebrow">Operation history</p>
        <div className="mt-4 divide-y divide-ink/10">
          {history.length ? (
            history.map((item) => (
              <article
                className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
                key={item.id}
              >
                <span>
                  <strong className="capitalize">{item.operation}</strong> ·{' '}
                  {item.kind.replaceAll('_', ' ')}
                </span>
                <time className="text-xs text-ink/55" dateTime={item.createdAt}>
                  {new Date(item.createdAt).toLocaleString()}
                </time>
              </article>
            ))
          ) : (
            <p className="py-3 text-sm text-ink/55">No transfer or backup operations recorded yet.</p>
          )}
        </div>
      </section>
    </div>
  )
}

function FilterDetails({
  label,
  selectedCount,
  items,
  selected,
  setSelected,
  tall = false,
}: {
  label: string
  selectedCount: number
  items: { id: string; label: string }[]
  selected: string[]
  setSelected: React.Dispatch<React.SetStateAction<string[]>>
  tall?: boolean
}) {
  return (
    <details className="mt-3 rounded-xl border border-ink/10 p-3">
      <summary className="cursor-pointer text-sm font-medium">
        {label} · {selectedCount || 'all'}
      </summary>
      <div className={`mt-3 space-y-2 overflow-auto ${tall ? 'max-h-48' : 'max-h-40'}`}>
        {items.map((item) => (
          <label className="flex min-h-11 items-center gap-2 text-sm" key={item.id}>
            <input
              type="checkbox"
              checked={selected.includes(item.id)}
              onChange={(event) =>
                setSelected((current) =>
                  event.target.checked
                    ? [...current, item.id]
                    : current.filter((id) => id !== item.id),
                )
              }
            />
            {item.label}
          </label>
        ))}
      </div>
    </details>
  )
}
