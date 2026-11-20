import { test, expect, gotoDashboard, allowFailures, DASHBOARDS } from '../fixtures.js'
import { panel, row, clickAndAwait } from '../helpers.js'

// Every test mutates the shared actions dashboard, each on its own seeded rows.
// Desktop only: two projects would race on the same server state.
test.describe.configure({ mode: 'serial' })
test.beforeEach(async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'mutates shared server state')
  await gotoDashboard(page, DASHBOARDS.actions)
})

const reload = page => gotoDashboard(page, DASHBOARDS.actions)

test('silence one anomaly', async ({ page }) => {
  await clickAndAwait(page, row(panel(page, 'Unassigned anomalies'), 'cache-warmer').getByRole('button', { name: 'Silence', exact: true }), '/api/anomalies/ignore')
  await reload(page)
  await expect(row(panel(page, 'Unassigned anomalies'), 'cache-warmer')).toHaveCount(0)
  await expect(row(panel(page, 'Silenced anomalies'), 'cache-warmer')).toHaveCount(1)
})

test('silence a whole group', async ({ page }) => {
  const group = row(panel(page, 'Unassigned anomalies'), 'Media pipeline storage pressure')
  await clickAndAwait(page, group.getByRole('button', { name: 'Silence all 3' }), '/api/anomalies/ignore-batch')
  await reload(page)
  await expect(row(panel(page, 'Unassigned anomalies'), 'Media pipeline storage pressure')).toHaveCount(0)
  await expect(row(panel(page, 'Silenced anomalies'), 'image-proxy')).toHaveCount(2)
  await expect(row(panel(page, 'Silenced anomalies'), 'search-indexer')).toHaveCount(1)
})

test('save a triage note', async ({ page }) => {
  const item = row(panel(page, 'Silenced anomalies'), 'edge-proxy')
  await item.locator('summary').click()
  await item.getByLabel('Triage note').fill('Probe noise, confirmed with the platform team.')
  await clickAndAwait(page, item.getByRole('button', { name: 'Save note' }), '/api/anomalies/note')
  await reload(page)
  const reopened = row(panel(page, 'Silenced anomalies'), 'edge-proxy')
  await reopened.locator('summary').click()
  await expect(reopened.getByLabel('Triage note')).toHaveValue('Probe noise, confirmed with the platform team.')
})

test('establish a pattern rule from the suggestion', async ({ page }) => {
  const target = row(panel(page, 'Unassigned anomalies'), 'cron-alpha')
  const suggestion = page.waitForResponse(r => r.url().includes('/api/anomalies/suggest-pattern'))
  await target.getByRole('button', { name: 'Silence pattern…' }).click()
  await suggestion
  const editor = page.locator('.pattern-editor, [class*="pattern"]').filter({ has: page.getByRole('button', { name: 'Establish rule' }) })
  await expect(editor.locator('textarea')).toHaveValue('level=error\\ msg="job\\ alpha\\ failed:\\ lock\\ already\\ held"')
  await editor.getByLabel(/Triage note/).fill('Alpha contention is expected during deploys.')
  await clickAndAwait(page, editor.getByRole('button', { name: 'Establish rule' }), '/api/anomalies/rules')
  await reload(page)
  await expect(row(panel(page, 'Unassigned anomalies'), 'cron-alpha')).toHaveCount(0)
  await expect(panel(page, 'Silenced anomalies')).toContainText('SERVICE: CRON-ALPHA', { ignoreCase: true })
})

test('an invalid pattern shows the server error', async ({ page }) => {
  allowFailures(page, [{ path: '/api/anomalies/rules', status: 400 }])
  const target = row(panel(page, 'Unassigned anomalies'), 'noisy-exporter')
  const suggestion = page.waitForResponse(r => r.url().includes('/api/anomalies/suggest-pattern'))
  await target.getByRole('button', { name: 'Silence pattern…' }).click()
  await suggestion
  const editor = page.locator('.pattern-editor, [class*="pattern"]').filter({ has: page.getByRole('button', { name: 'Establish rule' }) })
  await editor.locator('textarea').fill('(unclosed')
  await clickAndAwait(page, editor.getByRole('button', { name: 'Establish rule' }), '/api/anomalies/rules', 400)
  await expect(page.getByText('pattern is not a valid regular expression')).toBeVisible()
  await reload(page)
  await expect(row(panel(page, 'Unassigned anomalies'), 'noisy-exporter')).toHaveCount(1)
})

test('amend a rule record', async ({ page }) => {
  const rule = panel(page, 'Silenced anomalies').locator('.rule-row').filter({ hasText: 'service: legacy-cron' })
  await rule.getByText('Amend record').click()
  await rule.getByLabel('Containment label').fill('Legacy export (retired)')
  await clickAndAwait(page, rule.getByRole('button', { name: 'Save record' }), '/api/anomalies/rules/metadata')
  await reload(page)
  await expect(panel(page, 'Silenced anomalies')).toContainText('Legacy export (retired)')
})

test('establish and amend a protocol', async ({ page }) => {
  const protocols = panel(page, 'Containment protocols')
  await protocols.getByRole('button', { name: 'Establish protocol' }).click()
  await protocols.getByLabel('Designation (id)').fill('mailer')
  await protocols.getByLabel('Archive endpoint (loki_url)').fill('http://127.0.0.1:8097')
  await protocols.getByLabel('GitHub repository (owner/name)').fill('example/mailer')
  await protocols.getByLabel('Log stream selector (loki_query)').fill('{container="mailer"}')
  await clickAndAwait(page, protocols.getByRole('button', { name: 'Commit protocol' }), '/api/targets')
  await reload(page)
  const reloaded = panel(page, 'Containment protocols')
  await expect(reloaded).toContainText('example/mailer')
  await expect(row(panel(page, 'Contained anomalies'), 'mailer')).toHaveCount(1)

  const card = reloaded.locator('*').filter({ hasText: /^mailer/ }).filter({ has: page.getByRole('button', { name: 'Amend' }) }).last()
  await card.getByRole('button', { name: 'Amend' }).click()
  await reloaded.getByLabel('GitHub repository (owner/name)').fill('example/notifications')
  await clickAndAwait(page, reloaded.getByRole('button', { name: 'Commit protocol' }), '/api/targets')
  await reload(page)
  await expect(panel(page, 'Containment protocols')).toContainText('example/notifications')
})
