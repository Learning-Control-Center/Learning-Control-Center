import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RoadmapV2Page } from './pages/RoadmapV2Page'

const projection = {
  configured: true,
  authority: 'v2_projection',
  scopeKey: 'profile:p1:graph:g1',
  projectionPolicyVersion: 'roadmap-projection/v2.0',
  layoutPolicyVersion: 'roadmap-layout/v2.0',
  outputHash: 'hash',
  relationshipVisibility: { prerequisite: false },
  groups: [{ id: 'domain-1', title: 'Engineering', orderIndex: 0 }],
  nodes: [
    {
      id: 'semantic-1',
      nodeKey: 'semantic-1',
      semanticDefinitionId: 'definition-1',
      stableKey: 'python.delivery',
      title: 'Python delivery',
      profileDomain: { id: 'domain-1', title: 'Engineering', orderIndex: 0 },
      profileTarget: { priority: 'core', targetLevelOrdinal: 3 },
      capability: { scopes: [] },
      position: { x: 0, y: 0 },
      positionSource: 'roadmap-layout/v2.0',
      presentationParentId: 'semantic-0',
      isCurrent: true,
      isToday: true,
    },
    {
      id: 'semantic-0',
      nodeKey: 'semantic-0',
      semanticDefinitionId: 'definition-0',
      stableKey: 'python.foundation',
      title: 'Graph prerequisite',
      profileDomain: null,
      profileTarget: null,
      capability: { scopes: [] },
      position: { x: -300, y: 0 },
      positionSource: 'roadmap-layout/v2.0',
      presentationParentId: null,
      isCurrent: false,
      isToday: false,
    },
  ],
  edges: [
    {
      id: 'edge-1',
      edgeType: 'prerequisite',
      source: 'semantic-0',
      target: 'semantic-1',
      visibleByDefault: false,
      eligibilityAuthority: true,
      satisfaction: { aggregate_state: 'unknown', unknown_reasons: ['CAPABILITY_UNKNOWN'] },
    },
  ],
  legacyPhaseAuthority: false,
}

const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })

describe('Roadmap Projection V2 thin workflow', () => {
  beforeEach(() => {
    class FakeResizeObserver {
      private readonly callback: ResizeObserverCallback
      constructor(callback: ResizeObserverCallback) {
        this.callback = callback
      }
      observe(target: Element) {
        const entry = {
          target,
          contentRect: {
            x: 0,
            y: 0,
            width: 1024,
            height: 768,
            top: 0,
            left: 0,
            bottom: 768,
            right: 1024,
          },
        } as ResizeObserverEntry
        queueMicrotask(() => this.callback([entry], this as unknown as ResizeObserver))
      }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
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
    vi.stubGlobal('DOMMatrixReadOnly', FakeDOMMatrixReadOnly)
    Object.defineProperties(globalThis.HTMLElement.prototype, {
      offsetHeight: { configurable: true, get: () => 1 },
      offsetWidth: { configurable: true, get: () => 1 },
    })
    ;(globalThis.SVGElement.prototype as unknown as { getBBox: () => unknown }).getBBox = () => ({
      x: 0,
      y: 0,
      width: 0,
      height: 0,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/api/v2/roadmap-projection/current')) return json(projection)
        if (url.endsWith('/api/v2/roadmap-projection/rebuild') && init?.method === 'POST') {
          return json(projection)
        }
        if (
          url.endsWith('/api/v2/roadmap-projection/profile:p1:graph:g1/positions') &&
          init?.method === 'DELETE'
        ) {
          return json({ clearedCount: 1, projection })
        }
        return json({ error: { code: 'NOT_FOUND', message: url } })
      }),
    )
  })

  it('keeps prerequisites hidden by default and supports projection rebuild/reset', async () => {
    const user = userEvent.setup()
    render(<RoadmapV2Page />)

    expect(await screen.findByRole('heading', { name: 'Roadmap Projection' })).toBeInTheDocument()
    expect(
      await screen.findByLabelText(
        'Python delivery, Engineering, specialization child, recommended for Today',
      ),
    ).toBeInTheDocument()
    expect(await screen.findByText(/Python delivery · Today/)).toBeInTheDocument()
    expect(await screen.findByLabelText('Graph prerequisite, graph foundation')).toBeInTheDocument()
    expect(screen.queryByText('prerequisite')).not.toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'Show prerequisite relationships' }))
    expect((await screen.findAllByText('prerequisite')).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Graph foundations' }))
    expect(screen.queryByLabelText('Graph prerequisite, graph foundation')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Rebuild/ }))
    await user.click(screen.getByRole('button', { name: /Reset layout/ }))

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/v2/roadmap-projection/profile:p1:graph:g1/positions',
        expect.objectContaining({ method: 'DELETE' }),
      )
    })
  })
})
