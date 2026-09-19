import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function luminance(hex: string) {
  const channels = hex.match(/[\da-f]{2}/gi)?.map((value) => Number.parseInt(value, 16) / 255) ?? []
  return channels.reduce(
    (total, channel, index) => total + (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][index],
    0,
  )
}

function contrast(first: string, second: string) {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

describe('accessibility color tokens', () => {
  it('uses a 3:1-or-better focus indicator against the dark navigation surface', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')
    expect(css).toContain('.bg-ink :focus-visible')
    expect(css).toContain('outline-color: #ffffff')
    expect(contrast('#ffffff', '#14211c')).toBeGreaterThanOrEqual(3)
  })
})
