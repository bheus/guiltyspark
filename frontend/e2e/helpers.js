import { expect } from '@playwright/test'

export const panel = (page, title) =>
  page.locator('section.panel').filter({ has: page.getByRole('heading', { name: title, exact: true }) })

// Groups nest their members' cards, so only outermost rows count.
const TOP_LEVEL_ROW = 'xpath=.//details[contains(concat(" ", @class, " "), " incident ")][not(ancestor::details[contains(concat(" ", @class, " "), " incident ")])]'

/** A top-level row in a panel whose title (service or group name) matches exactly. */
export const row = (scope, title) =>
  scope.locator(TOP_LEVEL_ROW).filter({ has: scope.page().locator('xpath=./summary//*[contains(@class, "inc-service")]', { hasText: new RegExp(`^${title}$`) }) })

export const tile = (page, label) =>
  page.locator('.tile').filter({ has: page.locator('.tile-label', { hasText: new RegExp(`^${label}$`) }) })

/** Click and wait for the given API call to succeed. */
export async function clickAndAwait(page, locator, pathPart, status = 200) {
  const response = page.waitForResponse(r => r.url().includes(pathPart) && r.request().method() !== 'GET')
  await locator.click()
  expect((await response).status()).toBe(status)
}
