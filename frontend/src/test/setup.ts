import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)

if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => undefined
}

// jsdom constructs synthetic events with a null view, which makes d3-zoom's
// drag-disable helper (d3-drag) throw when React Flow canvas controls are
// clicked in tests. Fall back to the test window for the test environment only.
const viewDescriptor = Object.getOwnPropertyDescriptor(window.UIEvent.prototype, 'view')
if (viewDescriptor?.get) {
  Object.defineProperty(window.UIEvent.prototype, 'view', {
    configurable: true,
    get(this: UIEvent) {
      return viewDescriptor.get!.call(this) ?? window
    },
  })
}
