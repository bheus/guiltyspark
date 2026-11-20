import { test, expect, gotoDashboard } from '../fixtures.js'
import { panel, row, tile } from '../helpers.js'

test.beforeEach(async ({ page }) => { await gotoDashboard(page) })

test('every panel renders the seeded state', async ({ page }) => {
  for (const [label, value] of [['Anomalies · window', '62'], ['Outside containment', '8'], ['Findings catalogued', '25'], ['Corrective measures', '3']]) {
    await expect(tile(page, label).locator('.tile-value')).toHaveText(value)
  }
  await expect(page.getByText('62 error-severity events among 4812 observed in the last 1h')).toBeVisible()
  const contained = panel(page, 'Contained anomalies')
  await expect(row(contained, 'checkout-api')).toHaveCount(2)
  await expect(row(contained, 'ledger-worker')).toHaveCount(1)
  const silenced = panel(page, 'Silenced anomalies')
  await expect(silenced).toContainText('Legacy export')
  await expect(row(silenced, 'edge-proxy')).toHaveCount(1)
  const protocols = panel(page, 'Containment protocols')
  await expect(protocols).toContainText('example/checkout')
  await expect(protocols).toContainText('example/ledger')
  const measures = panel(page, 'Corrective measures')
  await expect(measures).toContainText('pr-opened')
  await expect(measures.getByRole('link', { name: 'review the proposal' })).toHaveAttribute('href', 'https://github.com/example/checkout/pull/42')
})

test('Codex groups related unassigned anomalies', async ({ page }) => {
  const unassigned = panel(page, 'Unassigned anomalies')
  const group = row(unassigned, 'Media pipeline storage pressure')
  await expect(group).toContainText('×16')
  await expect(group.getByRole('button', { name: 'Silence all 3' })).toBeVisible()
  for (const service of ['noisy-exporter', 'mailer', 'cache-warmer', 'cron-alpha', 'cron-beta']) {
    await expect(row(unassigned, service)).toHaveCount(1)
  }
})

test('pager moves through findings', async ({ page }) => {
  const records = panel(page, 'Containment records')
  await expect(records).toContainText('1–10 of 23 · page 1 / 3')
  await expect(records).toContainText('Finding 23:')
  await records.getByRole('button', { name: /Earlier/ }).click()
  await expect(records).toContainText('11–20 of 23 · page 2 / 3')
  await expect(records).toContainText('Finding 13:')
  await expect(records).not.toContainText('Finding 23:')
})
