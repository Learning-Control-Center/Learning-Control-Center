import { Save } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api } from '../../api'
import { Button, LiveNotice, SectionError, SectionHeader, Surface, TextAreaField } from '../../shared/components'

function localDateInTimezone(timezone: string) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}`
}

export function DailyReflectionEditor({ localDate }: { localDate?: string }) {
  const [date, setDate] = useState(localDate ?? '')
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError('')
    try {
      let resolvedDate = localDate
      if (!resolvedDate) {
        const settings = await api<{ timezone: string }>('/settings/discipline', { signal })
        resolvedDate = localDateInTimezone(settings.timezone)
      }
      const response = await api<{ reflection: { text: string } | null }>(`/reflections/${resolvedDate}`, { signal })
      if (!signal?.aborted) { setDate(resolvedDate); setText(response.reflection?.text ?? '') }
    } catch (caught) {
      if ((caught as Error).name !== 'AbortError') setError(caught instanceof ApiError ? caught.message : 'The daily reflection could not be loaded.')
    } finally { if (!signal?.aborted) setLoading(false) }
  }, [localDate])

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])

  const save = async () => {
    if (!date || saving) return
    setSaving(true); setError(''); setNotice('')
    try {
      await api(`/reflections/${date}`, { method: 'PUT', body: JSON.stringify({ text }) })
      setNotice(`Reflection saved for ${date}.`)
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'The reflection could not be saved.') }
    finally { setSaving(false) }
  }

  return <Surface id="daily-reflection" className="scroll-mt-20 p-5 sm:p-6"><LiveNotice>{notice}</LiveNotice><SectionHeader title="Daily reflection" description={date ? `${date}. This editable note belongs to a daily context, not an immutable generated report.` : 'This editable note belongs to a daily context, not an immutable generated report.'} />{error ? <div className="mt-4"><SectionError message={error} retry={() => void load()} /></div> : null}<div className="mt-4"><TextAreaField id="daily-reflection-text" label="Reflection" value={text} disabled={loading || saving} onChange={(event) => { setText(event.target.value); setNotice('') }} placeholder="What changed in your understanding? What should tomorrow preserve?" /></div><Button className="mt-3" disabled={loading || saving || !date} aria-busy={saving} onClick={() => void save()}><Save className="size-4" />{saving ? 'Saving…' : 'Save reflection'}</Button></Surface>
}
