import { test, expectBaseline, gotoDashboard, gotoSite, DASHBOARDS } from '../fixtures.js'

test('busy dashboard', async ({ page }) => {
  await gotoDashboard(page, DASHBOARDS.busy)
  await expectBaseline(page, 'dashboard-busy.png', { fullPage: true })
})

test('empty dashboard', async ({ page }) => {
  await gotoDashboard(page, DASHBOARDS.empty)
  await expectBaseline(page, 'dashboard-empty.png', { fullPage: true })
})

test('marketing site', async ({ page }) => {
  await gotoSite(page)
  await expectBaseline(page, 'site.png', { fullPage: true })
})
