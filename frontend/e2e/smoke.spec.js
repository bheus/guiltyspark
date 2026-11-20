import { test, expect, gotoDashboard, DASHBOARDS } from './fixtures.js'

for (const [name, url] of Object.entries(DASHBOARDS)) {
  test(`${name} dashboard loads`, async ({ page }) => {
    await gotoDashboard(page, url)
    await expect(page.getByRole('heading', { name: '343 Guilty Spark' })).toBeVisible()
  })
}
