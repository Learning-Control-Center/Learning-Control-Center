import { DatabaseBackup, LocateFixed, Save, ShieldCheck } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { ApiError, api, formatDuration } from '../api'
import { ErrorState, LoadingState } from '../components/PageState'

type Discipline = {
  weeklyTargetActiveDays: number
  targetDurationMsPerActiveDay: number | null
  timezone: string
  updatedAt: string
}

function isValidTimezone(value: string) {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value }).format()
    return true
  } catch {
    return false
  }
}

export function SettingsPage() {
  const deviceTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const timezoneOptions = useMemo(() => {
    const supported =
      typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []
    return Array.from(new Set(['UTC', deviceTimezone, ...supported])).sort()
  }, [deviceTimezone])
  const [profile, setProfile] = useState<Discipline | null>(null)
  const [days, setDays] = useState('5')
  const [minutes, setMinutes] = useState('60')
  const [timezone, setTimezone] = useState(deviceTimezone)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    void api<Discipline>('/settings/discipline')
      .then((value) => {
        setProfile(value)
        setDays(String(value.weeklyTargetActiveDays))
        setMinutes(
          value.targetDurationMsPerActiveDay
            ? String(value.targetDurationMsPerActiveDay / 60_000)
            : '',
        )
        setTimezone(value.timezone)
      })
      .catch((caught) =>
        setError(caught instanceof ApiError ? caught.message : 'Settings could not be loaded.'),
      )
  }, [])

  if (error && !profile) return <ErrorState message={error} />
  if (!profile) return <LoadingState label="Loading settings" />

  const save = async () => {
    setError('')
    setMessage('')
    if (!isValidTimezone(timezone)) {
      setError('Choose a valid IANA timezone, such as Europe/Istanbul or UTC.')
      return
    }
    try {
      const value = await api<Discipline>('/settings/discipline', {
        method: 'PUT',
        body: JSON.stringify({
          weekly_target_active_days: Number(days),
          target_duration_ms_per_active_day: minutes ? Number(minutes) * 60_000 : null,
          timezone,
        }),
      })
      setProfile(value)
      setMessage('Discipline settings saved.')
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Settings could not be saved.')
    }
  }

  const backup = async () => {
    setError('')
    setMessage('')
    try {
      const value = await api<{ sizeBytes: number; checksumSha256: string }>(
        '/import-export/backups/operational',
        { method: 'POST' },
      )
      setMessage(
        `Operational backup created · ${Math.round(value.sizeBytes / 1024)} KB · checksum ${value.checksumSha256.slice(0, 12)}…`,
      )
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Backup could not be created.')
    }
  }

  return (
    <div className="mx-auto w-full max-w-[78rem]">
      <header className="mb-7">
        <p className="eyebrow mb-2">Application behavior</p>
        <h1 className="page-title">Settings</h1>
        <p className="mt-2 text-sm text-ink/60">
          Sustainable targets, local calendar boundaries, and recoverability.
        </p>
      </header>
      {error ? (
        <p className="mb-5 rounded-xl bg-rose-50 p-3 text-sm text-rose-800" role="alert">
          {error}
        </p>
      ) : null}
      {message ? (
        <p className="mb-5 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800" role="status">
          {message}
        </p>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-2">
        <section className="surface p-5 sm:p-6">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-moss/10 text-moss">
              <Save className="size-5" />
            </span>
            <div>
              <h2 className="font-display text-xl font-semibold">Discipline profile</h2>
              <p className="text-sm text-ink/55">No fixed weekdays or punitive backlog.</p>
            </div>
          </div>
          <div className="mt-6 grid gap-4 sm:grid-cols-2">
            <label className="text-sm font-medium">
              Active days per week
              <input
                className="field mt-2"
                type="number"
                min="1"
                max="7"
                inputMode="numeric"
                value={days}
                onChange={(event) => setDays(event.target.value)}
              />
            </label>
            <label className="text-sm font-medium">
              Minutes per active day
              <input
                className="field mt-2"
                type="number"
                min="1"
                inputMode="numeric"
                value={minutes}
                onChange={(event) => setMinutes(event.target.value)}
              />
            </label>
            <div className="sm:col-span-2">
              <label className="text-sm font-medium" htmlFor="application-timezone">
                Application timezone
              </label>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <input
                  id="application-timezone"
                  className="field"
                  list="iana-timezones"
                  value={timezone}
                  onChange={(event) => {
                    setTimezone(event.target.value)
                    setError('')
                    setMessage('')
                  }}
                  aria-describedby="timezone-help"
                  aria-invalid={!isValidTimezone(timezone)}
                  autoComplete="off"
                  spellCheck={false}
                />
                <button
                  className="button-secondary shrink-0"
                  type="button"
                  onClick={() => setTimezone(deviceTimezone)}
                >
                  <LocateFixed className="size-4" />
                  Use device timezone
                </button>
              </div>
              <datalist id="iana-timezones">
                {timezoneOptions.map((item) => (
                  <option value={item} key={item} />
                ))}
              </datalist>
              <p className="mt-2 text-xs leading-5 text-ink/60" id="timezone-help">
                Start typing a region and city, for example Europe/Istanbul. Calendar boundaries use
                this IANA timezone.
              </p>
            </div>
          </div>
          <p className="mt-4 text-xs text-ink/60">
            Current target: {days} active days ·{' '}
            {formatDuration(minutes ? Number(minutes) * 60_000 : null)} per active day.
          </p>
          <button className="button-primary mt-5" onClick={() => void save()}>
            <Save className="size-4" />
            Save settings
          </button>
        </section>
        <section className="surface p-5 sm:p-6">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-copper/10 text-copper">
              <DatabaseBackup className="size-5" />
            </span>
            <div>
              <h2 className="font-display text-xl font-semibold">Operational backup</h2>
              <p className="text-sm text-ink/55">SQLite-safe deployment recovery artifact.</p>
            </div>
          </div>
          <div className="mt-6 rounded-xl border border-ink/10 bg-white/45 p-4 text-sm leading-6 text-ink/70">
            <p className="flex items-center gap-2 font-semibold text-ink">
              <ShieldCheck className="size-4 text-moss" />
              Protected server artifact
            </p>
            <p className="mt-2">
              Unlike portable backups, operational backups may contain authentication state and must
              remain in restricted server storage.
            </p>
          </div>
          <button className="button-primary mt-5" onClick={() => void backup()}>
            <DatabaseBackup className="size-4" />
            Create operational backup
          </button>
        </section>
      </div>
    </div>
  )
}
