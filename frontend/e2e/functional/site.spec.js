import { test, expect, gotoSite, SITE } from '../fixtures.js'

test('marketing page renders with its links', async ({ page }) => {
  await gotoSite(page)
  await expect(page.getByRole('heading', { level: 1 })).toContainText('It finds the bug. Then it writes the fix.')
  for (const href of ['https://github.com/bheus/guiltyspark', 'https://github.com/bheus/guiltyspark#readme']) {
    await expect(page.locator(`a[href="${href}"]`).first()).toBeVisible()
  }
})

for (const path of ['/robots.txt', '/llms.txt', '/sitemap.xml', '/favicon.ico', '/favicon.svg', '/apple-touch-icon.png']) {
  test(`serves ${path}`, async ({ request }) => {
    expect((await request.get(`${SITE}${path}`)).status()).toBe(200)
  })
}
