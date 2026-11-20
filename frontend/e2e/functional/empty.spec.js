import { test, expect, gotoDashboard, DASHBOARDS } from '../fixtures.js'

test('a quiet installation says so in every panel', async ({ page }) => {
  await gotoDashboard(page, DASHBOARDS.empty)
  for (const copy of [
    '0 error-severity events among 0 observed in the last 1h',
    'None. Every observed anomaly falls within an existing containment protocol.',
    'Nothing has been silenced.',
    'No contained anomalies in this observation window.',
    'No containment protocols are established.',
    'The archive holds no catalogued findings yet, Reclaimer.',
    'No corrective measures have been required.',
  ]) {
    await expect(page.getByText(copy)).toBeVisible()
  }
})
