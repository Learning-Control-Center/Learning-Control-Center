import '@testing-library/jest-dom/vitest'

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
