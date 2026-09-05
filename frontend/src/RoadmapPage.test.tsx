import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RoadmapPage } from './pages/RoadmapPage'
import type { Competency, Roadmap } from './types'

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function competency(overrides: Partial<Competency> = {}): Competency {
  return {
    definitionId: `definition-${overrides.stableKey ?? 'unknown'}`,
    identityId: `identity-${overrides.stableKey ?? 'unknown'}`,
    stableKey: 'systems.basics',
    parentDefinitionId: null,
    title: 'System Basics',
    description: 'Foundational concepts.',
    goal: 'Understand the basics.',
    priority: 'core',
    weight: 5,
    orderIndex: 0,
    archived: false,
    position: { x: null, y: null },
    status: 'not_started',
    prerequisites: [],
    mustUnderstand: [],
    mustBeAbleTo: [],
    exitCriteria: [],
    ...overrides,
  }
}

function installFakeResizeObserver() {
  class FakeResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = FakeResizeObserver
}

function buildRoadmap(): Roadmap {
  return {
    id: 'roadmap-1',
    stableKey: 'technical-foundations',
    title: 'Technical Foundations',
    description: 'A deterministic demo roadmap.',
    activeVersion: { id: 'version-1', version: '1.0.0', schemaVersion: 1 },
    currentPhaseId: 'phase-1',
    phases: [
      {
        id: 'phase-1',
        stableKey: 'phase-1',
        title: 'Foundations',
        description: 'Core foundations.',
        orderIndex: 0,
        archived: false,
        isCurrent: true,
        tracks: [
          {
            id: 'track-1',
            stableKey: 'systems',
            title: 'Systems',
            description: 'Systems track.',
            orderIndex: 0,
            competencies: [
              competency({
                definitionId: 'definition-a',
                identityId: 'identity-a',
                stableKey: 'systems.basics',
                title: 'System Basics',
              }),
              competency({
                definitionId: 'definition-b',
                identityId: 'identity-b',
                stableKey: 'systems.cli',
                title: 'Command Line',
                orderIndex: 1,
              }),
            ],
          },
        ],
      },
    ],
  }
}

function mockRoadmapResponse(roadmap: Roadmap) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/roadmap/current')) return json({ configured: true, roadmap })
    if (url.endsWith('/roadmap/layout/reset')) {
      return json({ cleared: 2 })
    }
    return json({})
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  cleanup()
})

describe('Roadmap reset layout flow', () => {
  beforeEach(() => installFakeResizeObserver())

  it('cancel closes the dialog and changes nothing', async () => {
    const user = userEvent.setup()
    const roadmap = buildRoadmap()
    const fetchMock = mockRoadmapResponse(roadmap)
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: 'Reset layout' })
    await user.click(screen.getByRole('button', { name: 'Reset layout' }))

    const dialog = await screen.findByRole('dialog', { name: 'Reset layout?' })
    expect(dialog).toHaveTextContent('only manually saved node positions')
    expect(dialog).toHaveTextContent('Progress, sessions, and verification records are not affected.')

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Reset layout?' })).toBeNull())
    expect(screen.queryByRole('status')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/v1/roadmap/layout/reset',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('confirm calls the reset endpoint, reloads, and reports the cleared count', async () => {
    const user = userEvent.setup()
    const roadmap = buildRoadmap()
    const fetchMock = mockRoadmapResponse(roadmap)
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: 'Reset layout' })
    await user.click(screen.getByRole('button', { name: 'Reset layout' }))
    const dialog = await screen.findByRole('dialog', { name: 'Reset layout?' })
    await user.click(within(dialog).getByRole('button', { name: 'Reset layout' }))

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('cleared 2 saved positions'),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/roadmap/layout/reset',
      expect.objectContaining({ method: 'POST' }),
    )
    const roadmapCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith('/roadmap/current'),
    )
    expect(roadmapCalls.length).toBe(2)
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Reset layout?' })).toBeNull())
  })

  it('fit view performs no reset and never opens the dialog', async () => {
    const user = userEvent.setup()
    const roadmap = buildRoadmap()
    const fetchMock = mockRoadmapResponse(roadmap)
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: 'Fit view' })
    await user.click(screen.getByRole('button', { name: 'Fit view' }))
    await waitFor(() =>
      expect(fetchMock).not.toHaveBeenCalledWith(
        '/api/v1/roadmap/layout/reset',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
