export type RemoteResource<T> =
  | { status: 'idle'; data?: never; error?: never }
  | { status: 'loading'; data?: never; error?: never }
  | { status: 'success'; data: T; error?: never }
  | { status: 'refreshing'; data: T; error?: never }
  | { status: 'error'; data?: T; error: string }

export type MutationState =
  | { status: 'idle'; error?: never }
  | { status: 'pending'; error?: never }
  | { status: 'error'; error: string }
  | { status: 'success'; error?: never }

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function errorMessage(error: unknown, fallback: string): string {
  if (isAbortError(error)) return ''
  return error instanceof Error && error.message ? error.message : fallback
}

export function createRequestVersionGate() {
  let currentVersion = 0
  return {
    begin() {
      currentVersion += 1
      return currentVersion
    },
    isCurrent(version: number) {
      return version === currentVersion
    },
    invalidate() {
      currentVersion += 1
    },
  }
}

export function mutationIsPending(state: MutationState): boolean {
  return state.status === 'pending'
}
