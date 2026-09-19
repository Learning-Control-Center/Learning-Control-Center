import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RoadmapV2Page } from './pages/RoadmapV2Page'

const projection = {
  configured: true, authority: 'v2_projection', scopeKey: 'profile:p1:graph:g1',
  projectionPolicyVersion: 'roadmap-projection/v3.0', layoutPolicyVersion: 'roadmap-layout/v3.0', outputHash: 'hash',
  relationshipVisibility: { prerequisite: false },
  nodes: [
    {
      id: 'semantic-1', nodeKey: 'semantic-1', semanticDefinitionId: 'definition-1', stableKey: 'python.delivery', title: 'Python delivery', description: 'Deliver reliable Python systems.', profileDomain: null,
      profileTarget: { priority: 'core', targetLevelOrdinal: 3 }, profileTargets: [{ id: 'target-1', priority: 'core', targetLevelOrdinal: 3, targetLevelTitle: 'Independent', profileDomain: { id: 'domain-1', title: 'Engineering', orderIndex: 0 } }],
      layoutLane: { id: 'engineering', title: 'Engineering', orderIndex: 1 }, capability: { scopes: [] }, canonicalPosition: { x: 320, y: 0 }, position: { x: 320, y: 0 }, positionSource: 'roadmap-layout/v3.0', presentationParentId: 'semantic-0', isCurrent: true, isTargeted: true, isToday: true,
    },
    {
      id: 'semantic-0', nodeKey: 'semantic-0', semanticDefinitionId: 'definition-0', stableKey: 'python.foundation', title: 'Graph prerequisite', profileDomain: null, profileTarget: null, profileTargets: [], layoutLane: { id: 'foundations', title: 'Foundations', orderIndex: 0 }, capability: { scopes: [] }, canonicalPosition: { x: 0, y: 0 }, position: { x: 0, y: 0 }, positionSource: 'roadmap-layout/v3.0', presentationParentId: null, isCurrent: false, isTargeted: false, isToday: false,
    },
  ],
  edges: [{ id: 'edge-1', edgeType: 'prerequisite', source: 'semantic-0', target: 'semantic-1', visibleByDefault: false, eligibilityAuthority: true, satisfaction: { aggregate_state: 'unknown', unknown_reasons: ['CAPABILITY_UNKNOWN'] } }],
  legacyPhaseAuthority: false,
}

const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
let positionFailure = false

describe('Roadmap learning journey', () => {
  beforeEach(() => {
    positionFailure = false
    class FakeResizeObserver { constructor(private callback: ResizeObserverCallback) {} observe(target: Element) { queueMicrotask(() => this.callback([{ target, contentRect: { x: 0, y: 0, width: 1024, height: 768, top: 0, left: 0, bottom: 768, right: 1024 } } as ResizeObserverEntry], this as unknown as ResizeObserver)) } unobserve() {} disconnect() {} }
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    class FakeDOMMatrixReadOnly { m22 = 1; transform() { return this }; scale() { return this }; translate() { return this } }
    vi.stubGlobal('DOMMatrixReadOnly', FakeDOMMatrixReadOnly)
    Object.defineProperties(globalThis.HTMLElement.prototype, { offsetHeight: { configurable: true, get: () => 140 }, offsetWidth: { configurable: true, get: () => 240 } })
    ;(globalThis.SVGElement.prototype as unknown as { getBBox: () => unknown }).getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/current')) return json(projection)
      if (url.endsWith('/capability-scales')) return json([])
      if (url.endsWith('/rebuild') && init?.method === 'POST') return json(projection)
      if (url.endsWith('/positions') && init?.method === 'DELETE') return json({ clearedCount: 1, projection })
      if (url.includes('/positions/semantic-1') && init?.method === 'PUT') {
        if (positionFailure) return new Response(JSON.stringify({ error: { code: 'POSITION_SAVE_FAILED', message: 'Position save failed safely.' } }), { status: 503, headers: { 'Content-Type': 'application/json' } })
        return json({ position: { x: 40, y: 50 } })
      }
      return new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: url } }), { status: 404 })
    }))
  })

  function renderPage(path = '/roadmap?view=outline') { return render(<MemoryRouter initialEntries={[path]}><RoadmapV2Page /></MemoryRouter>) }

  it('presents synchronized human-readable journey facts and complete focused prerequisites', async () => {
    const user = userEvent.setup(); renderPage()
    expect(await screen.findByRole('heading', { name: 'Roadmap' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Python delivery/ })).toHaveTextContent('Independent')
    await user.click(screen.getByRole('button', { name: /Python delivery/ }))
    const details = screen.getByRole('complementary', { name: 'Roadmap item details' })
    expect(within(details).getByText('Current capability unknown')).toBeInTheDocument()
    expect(within(details).getByText('Prerequisite status unknown')).toBeInTheDocument()
    expect(within(details).getByText('Recommended for Today')).toBeInTheDocument()
    expect(screen.getAllByText('Graph prerequisite').length).toBeGreaterThan(0)
  })

  it('supports non-drag positioning and confirmed canonical reset', async () => {
    const user = userEvent.setup(); renderPage()
    await screen.findByRole('heading', { name: 'Roadmap' })
    await user.click(screen.getByRole('button', { name: /Python delivery/ }))
    await user.click(screen.getByRole('button', { name: 'Set X/Y position' }))
    const dialog = screen.getByRole('dialog', { name: /Set position/ })
    await user.clear(within(dialog).getByLabelText('X coordinate')); await user.type(within(dialog).getByLabelText('X coordinate'), '40')
    await user.clear(within(dialog).getByLabelText('Y coordinate')); await user.type(within(dialog).getByLabelText('Y coordinate'), '50')
    await user.click(within(dialog).getByRole('button', { name: 'Save position' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/positions/semantic-1'), expect.objectContaining({ method: 'PUT' })))
    await user.click(screen.getByRole('button', { name: 'Reset layout' }))
    const resetDialog = screen.getByRole('dialog', { name: 'Reset Roadmap layout' })
    await user.click(within(resetDialog).getByRole('button', { name: 'Reset layout' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/positions'), expect.objectContaining({ method: 'DELETE' })))
  })

  it('keeps numeric position input recoverable after validation or server failure', async () => {
    const user = userEvent.setup(); renderPage()
    await screen.findByRole('heading', { name: 'Roadmap' })
    await user.click(screen.getByRole('button', { name: /Python delivery/ }))
    await user.click(screen.getByRole('button', { name: 'Set X/Y position' }))
    const dialog = screen.getByRole('dialog', { name: /Set position/ })
    await user.clear(within(dialog).getByLabelText('X coordinate'))
    await user.click(within(dialog).getByRole('button', { name: 'Save position' }))
    expect(within(dialog).getByText('Enter finite numeric X and Y coordinates.')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('X coordinate')).toHaveFocus()
    await user.type(within(dialog).getByLabelText('X coordinate'), '40')
    positionFailure = true
    await user.click(within(dialog).getByRole('button', { name: 'Save position' }))
    expect(await within(dialog).findByText('Position save failed safely.')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('X coordinate')).toHaveValue(40)
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: /Set position/ })).not.toBeInTheDocument()
  })
})
