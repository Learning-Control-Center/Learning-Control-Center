import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

function installReactFlowMocks() {
  // Standard xyflow testing mocks: nodes are measured through offsetWidth /
  // offsetHeight, and jsdom reports zeros for geometry by default. The fake
  // ResizeObserver fires once per observed element so React Flow populates
  // node internals (handle bounds), which edge rendering depends on.
  class FakeResizeObserver {
    private readonly callback: ResizeObserverCallback
    constructor(callback: ResizeObserverCallback) {
      this.callback = callback
    }
    observe(target: Element) {
      const entry = {
        target,
        contentRect: { x: 0, y: 0, width: 1024, height: 768, top: 0, left: 0, bottom: 768, right: 1024 },
      } as ResizeObserverEntry
      queueMicrotask(() => {
        this.callback([entry], this as unknown as ResizeObserver)
      })
    }
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = FakeResizeObserver
  class FakeDOMMatrixReadOnly {
    m22 = 1
    transform() {
      return this
    }
    scale() {
      return this
    }
    translate() {
      return this
    }
  }
  ;(globalThis as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly = FakeDOMMatrixReadOnly
  Object.defineProperties(globalThis.HTMLElement.prototype, {
    offsetHeight: {
      configurable: true,
      get(this: HTMLElement) {
        return parseFloat(this.style.height) || 1
      },
    },
    offsetWidth: {
      configurable: true,
      get(this: HTMLElement) {
        return parseFloat(this.style.width) || 1
      },
    },
  })
  ;(globalThis.SVGElement.prototype as unknown as { getBBox: () => unknown }).getBBox = () => ({
    x: 0,
    y: 0,
    width: 0,
    height: 0,
  })
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
    if (url.includes('/roadmap/current-phase/')) return json({})
    if (url.includes('/verification?')) return json({ items: [] })
    return json({})
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  cleanup()
})

describe('Roadmap reset layout flow', () => {
  beforeEach(() => installReactFlowMocks())

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

function buildPhasedRoadmap(): Roadmap {
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
                prerequisites: [{ identityId: 'identity-a', stableKey: 'systems.basics', kind: 'required' }],
              }),
            ],
          },
        ],
      },
      {
        id: 'phase-2',
        stableKey: 'phase-2',
        title: 'Build',
        description: 'Applied work.',
        orderIndex: 1,
        archived: false,
        isCurrent: false,
        tracks: [
          {
            id: 'track-2',
            stableKey: 'delivery',
            title: 'Delivery',
            description: 'Delivery track.',
            orderIndex: 0,
            competencies: [
              competency({
                definitionId: 'definition-c',
                identityId: 'identity-c',
                stableKey: 'delivery.project',
                title: 'Project Work',
                orderIndex: 2,
                prerequisites: [{ identityId: 'identity-a', stableKey: 'systems.basics', kind: 'recommended' }],
              }),
            ],
          },
        ],
      },
    ],
  }
}

function edgePaths(container: HTMLElement) {
  return container.querySelectorAll('path.react-flow__edge-path')
}

describe('Roadmap phase chrome', () => {
  beforeEach(() => installReactFlowMocks())

  it('renders phase headers with progress, controls, and track sections but no PHASE N prefix', async () => {
    const roadmap = buildPhasedRoadmap()
    vi.stubGlobal('fetch', mockRoadmapResponse(roadmap))
    render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )
    await screen.findByText('System Basics')

    expect(screen.queryByText(/^PHASE \d+/)).toBeNull()
    expect(screen.getAllByText('Foundations').length).toBeGreaterThan(0)
    expect(screen.getByText('0 of 2 verified')).toBeInTheDocument()
    expect(screen.getByText('Current')).toBeInTheDocument()
    expect(screen.getByText('Systems')).toBeInTheDocument()
    expect(screen.getByText('Delivery')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Collapse phase Foundations' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Focus phase Foundations' })).toBeInTheDocument()
    // The current phase shows a badge instead of the make-current control.
    expect(screen.queryByRole('button', { name: 'Make Foundations the current phase' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Make Build the current phase' })).toBeInTheDocument()
    // The navigator mirrors the phases with progress.
    expect(screen.getByRole('button', { name: 'Go to phase Foundations' })).toHaveTextContent('0/2')
    expect(screen.getByRole('button', { name: 'Go to phase Build' })).toHaveTextContent('0/1')
  })

  it('collapsing a phase hides its competencies semantically and expanding restores them', async () => {
    const user = userEvent.setup()
    const roadmap = buildPhasedRoadmap()
    vi.stubGlobal('fetch', mockRoadmapResponse(roadmap))
    render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )
    await screen.findByText('System Basics')
    expect(screen.getByText('Command Line')).toBeInTheDocument()

    // fireEvent dispatches click only: in-pane elements sit under d3-zoom's
    // mousedown listener, which cannot handle jsdom's null event view.
    fireEvent.click(screen.getByRole('button', { name: 'Collapse phase Foundations' }))
    await waitFor(() => expect(screen.queryByText('System Basics')).toBeNull())
    expect(screen.queryByText('Command Line')).toBeNull()
    // Nodes in the open phase stay visible; the collapsed phase keeps its place.
    expect(screen.getByText('Project Work')).toBeInTheDocument()
    expect(screen.getByText('2 competencies hidden')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Phases' })).toHaveTextContent('Foundations')

    await user.click(screen.getByRole('button', { name: 'Expand Foundations' }))
    await screen.findByText('System Basics')
    expect(screen.getByText('Command Line')).toBeInTheDocument()
  })

  it('make current calls the current-phase endpoint and reloads the roadmap', async () => {
    const roadmap = buildPhasedRoadmap()
    const fetchMock = mockRoadmapResponse(roadmap)
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: 'Make Build the current phase' })
    fireEvent.click(screen.getByRole('button', { name: 'Make Build the current phase' }))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/roadmap/current-phase/phase-2',
        expect.objectContaining({ method: 'PUT' }),
      ),
    )
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Current phase updated'))
    const roadmapCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/roadmap/current'))
    expect(roadmapCalls.length).toBe(2)
  })
})

describe('Roadmap prerequisite disclosure', () => {
  beforeEach(() => installReactFlowMocks())

  it('keeps prerequisites off the default canvas and reveals them on selection', async () => {
    const user = userEvent.setup()
    const roadmap = buildPhasedRoadmap()
    vi.stubGlobal('fetch', mockRoadmapResponse(roadmap))
    const { container } = render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )
    await screen.findByText('System Basics')

    // Default: no prerequisite edge paths, but cards hint at hidden links.
    expect(edgePaths(container)).toHaveLength(0)
    expect(screen.getAllByTitle(/1 prerequisite link in the current view/)).toHaveLength(2)

    fireEvent.click(screen.getByText('Command Line'))
    await waitFor(() => expect(edgePaths(container)).toHaveLength(1))

    // Closing the detail panel clears the selection and hides the edge again.
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(edgePaths(container)).toHaveLength(0))
  })

  it('the prerequisites toggle reveals and hides every link without touching nodes', async () => {
    const user = userEvent.setup()
    const roadmap = buildPhasedRoadmap()
    vi.stubGlobal('fetch', mockRoadmapResponse(roadmap))
    const { container } = render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )
    await screen.findByText('System Basics')

    const toggle = screen.getByRole('button', { name: 'Prerequisites' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(edgePaths(container)).toHaveLength(2))
    // Both kinds are distinguishable by their stroke treatment.
    const strokes = Array.from(edgePaths(container)).map((path) => (path as SVGPathElement).style.stroke)
    expect(strokes).toContain('#326653')
    expect(strokes).toContain('#93a69b')

    await user.click(toggle)
    await waitFor(() => expect(edgePaths(container)).toHaveLength(0))
    expect(screen.getByText('Project Work')).toBeInTheDocument()
  })
})
