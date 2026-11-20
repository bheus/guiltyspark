import { appendFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { test as base, expect } from '@playwright/test'

export const FAKES = 'http://127.0.0.1:8097'
export const DASHBOARDS = { busy: 'http://127.0.0.1:8099', empty: 'http://127.0.0.1:8098', actions: 'http://127.0.0.1:8095' }
export const SITE = 'http://127.0.0.1:8096'
// Matches tests/e2e/seeds.py FROZEN_NOW.
const FROZEN_AT = new Date('2026-06-15T12:00:00Z')

const allowances = new WeakMap()

/** Declare API failures or console errors this test expects to see. */
export function allowFailures(page, matchers) {
  allowances.get(page)?.push(...matchers)
}

const matches = (pattern, value) => (pattern instanceof RegExp ? pattern.test(value) : pattern === value)

function isAllowed(allowed, { path, status, console: text }) {
  return allowed.some(rule =>
    text !== undefined
      ? rule.console && matches(rule.console, text)
      : rule.path && matches(rule.path, path) && (rule.status === undefined || rule.status === status),
  )
}

/** toHaveScreenshot that records its baseline, so a full run can find unused ones. */
export async function expectBaseline(target, name, options) {
  const info = base.info()
  // Workers are separate processes; the teardown guard reads these files to find unused baselines.
  const dir = join(info.project.outputDir, 'baselines')
  mkdirSync(dir, { recursive: true })
  appendFileSync(join(dir, `${info.testId}.txt`), `${info.snapshotPath(name, { kind: 'screenshot' })}\n`)
  await expect(target).toHaveScreenshot(name, options)
}

/** Load a dashboard once its server has finished clustering, and wait for the first poll. */
export async function gotoDashboard(page, url = DASHBOARDS.busy) {
  // Clustering runs off-thread and is cached by the server, so wait for it there
  // rather than racing the page's own polls for a response body.
  await expect.poll(async () => {
    const body = await (await page.request.get(`${url}/api/anomalies?minutes=60`)).json()
    return Boolean(body.groups_pending)
  }, { timeout: 20_000 }).toBe(false)
  const ready = Promise.all([
    page.waitForResponse(r => r.url().includes('/api/overview') && r.ok()),
    page.waitForResponse(r => r.url().includes('/api/anomalies?') && r.ok()),
  ])
  await page.goto(url)
  await ready
  await page.waitForLoadState('networkidle')
  await page.evaluate(() => document.fonts.ready)
}

export async function gotoSite(page) {
  await page.goto(SITE)
  await page.waitForLoadState('networkidle')
  await page.evaluate(() => document.fonts.ready)
}

export const test = base.extend({
  page: async ({ page, request }, use) => {
    const allowed = []
    allowances.set(page, allowed)
    const problems = []

    page.on('console', message => {
      if (message.type() !== 'error') return
      const text = message.text()
      // A failed fetch also logs this line; the response check below judges it.
      if (text.startsWith('Failed to load resource')) return
      if (!isAllowed(allowed, { console: text })) problems.push(`console error: ${text}`)
    })
    page.on('pageerror', error => problems.push(`page error: ${error.message}`))
    page.on('response', response => {
      if (response.status() < 400) return
      const url = new URL(response.url())
      if (!isAllowed(allowed, { path: url.pathname, status: response.status() })) {
        problems.push(`${response.status()} from ${url.pathname}`)
      }
    })

    // Tests never leave the machine: the marketing site's Google Fonts fall back
    // to local faces, which also keeps its screenshots independent of a CDN.
    await page.route(url => !['127.0.0.1', 'localhost'].includes(url.hostname), route => route.abort())
    await page.clock.setFixedTime(FROZEN_AT)
    await use(page)
    const unexpected = await (await request.get(`${FAKES}/__unexpected`)).json()
    expect(unexpected, 'requests the fakes did not expect').toEqual([])
    expect(problems, 'unexpected errors while the test ran').toEqual([])
  },
})

export { expect }
