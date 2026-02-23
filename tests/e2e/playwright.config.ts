import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  timeout: 30000,
  expect: { timeout: 10000 },
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:8080',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: process.env.NO_AUTO_SERVER ? undefined : {
    command: 'BRIDGE_MOCK_MODE=true BRIDGE_POLL_INTERVAL=1 python3 -m bridge.src.main',
    port: 8080,
    timeout: 15000,
    reuseExistingServer: true,
    cwd: '../../',
  },
});
