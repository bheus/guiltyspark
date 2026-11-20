import { test, expect } from '@playwright/test'

test('site serves', async ({ request }) => {
  expect((await request.get('http://127.0.0.1:8096/')).status()).toBe(200)
})
