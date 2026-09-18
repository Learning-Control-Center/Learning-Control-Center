export type ApiErrorShape = {
  error?: { code?: string; message?: string; details?: unknown }
}

let csrfToken = ''

export function setCsrfToken(value: string) {
  csrfToken = value
}

export function getCsrfToken() {
  return csrfToken
}

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  return apiVersioned<T>('/api/v1', path, init)
}

export async function apiV2<T>(path: string, init: RequestInit = {}): Promise<T> {
  return apiVersioned<T>('/api/v2', path, init)
}

async function apiVersioned<T>(prefix: string, path: string, init: RequestInit): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(`${prefix}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ApiErrorShape
    throw new ApiError(
      response.status,
      payload.error?.code ?? 'REQUEST_FAILED',
      payload.error?.message ?? 'The request could not be completed.',
    )
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

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

export function downloadText(content: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
