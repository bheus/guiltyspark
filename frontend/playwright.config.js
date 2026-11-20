import { defineConfig, devices } from '@playwright/test'

// The pixel baselines are exact, and Chromium rasterizes differently on other
// platforms, so the suite only runs inside e2e/run.sh's linux/arm64 container.
if (process.env.E2E_CONTAINER !== '1') {
  throw new Error('Run the suite through `npm run test:e2e`, which uses the pinned container.')
}

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.js',
  snapshotPathTemplate: '{testDir}/__screenshots__/{projectName}/{testFilePath}/{arg}{ext}',
  retries: 0,
  workers: process.env.CI ? '100%' : undefined,
  forbidOnly: true,
  // A missing baseline fails without being written: new screenshots are approved
  // only through `--update-snapshots`, never by a rerun.
  updateSnapshots: 'none',
  reporter: [['list'], ['html', { open: 'never' }]],
  expect: {
    toHaveScreenshot: { maxDiffPixels: 0, threshold: 0, animations: 'disabled', caret: 'hide' },
  },
  use: {
    locale: 'en-US',
    timezoneId: 'UTC',
    colorScheme: 'light',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    // Chromium's GPU and partial rasterization occasionally anti-aliases a curved
    // border differently between identical runs.
    launchOptions: {
      args: ['--disable-gpu', '--disable-partial-raster', '--disable-skia-runtime-opts', '--force-color-profile=srgb', '--font-render-hinting=none'],
    },
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
    },
    {
      name: 'phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 3,
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: [
    {
      command: 'cd .. && uv run --no-sync python -m tests.e2e.fakes --port 8097',
      url: 'http://127.0.0.1:8097/__health',
      reuseExistingServer: false,
    },
    {
      command: 'cd .. && uv run --no-sync python -m tests.e2e.fixture_server --state busy --port 8099',
      url: 'http://127.0.0.1:8099/api/overview',
      reuseExistingServer: false,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    {
      command: 'cd .. && uv run --no-sync python -m tests.e2e.fixture_server --state empty --port 8098',
      url: 'http://127.0.0.1:8098/api/overview',
      reuseExistingServer: false,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    {
      command: 'cd .. && uv run --no-sync python -m tests.e2e.fixture_server --state busy --port 8095',
      url: 'http://127.0.0.1:8095/api/overview',
      reuseExistingServer: false,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    {
      command: 'python3 -m http.server 8096 --bind 127.0.0.1 -d ../site',
      url: 'http://127.0.0.1:8096/robots.txt',
      reuseExistingServer: false,
      stdout: 'ignore',
      stderr: 'ignore',
    },
  ],
})
