export function formatDuration(milliseconds: number | null | undefined, includeSeconds = false) {
  if (milliseconds == null) return 'N/A'
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (includeSeconds) {
    return hours > 0
      ? `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
      : `${minutes}:${seconds.toString().padStart(2, '0')}`
  }
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

export function formatRatio(value: number | null | undefined) {
  return value == null ? 'N/A' : `${Math.round(value * 100)}%`
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return 'Unknown'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? 'Unknown' : date.toLocaleString()
}

export function formatIdentifier(value: string | null | undefined) {
  if (!value) return 'Unknown'
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value
}

export function formatEnumLabel(value: string) {
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(' ')
}

export function pluralize(count: number, singular: string, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`
}
