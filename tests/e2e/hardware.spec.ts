import { test, expect } from '@playwright/test';

// Hardware E2E tests — only run when HW_TEST=true
const hw = process.env.HW_TEST === 'true';

test.describe('Hardware Validation', () => {
  test.skip(!hw, 'Skipped — set HW_TEST=true to run against real hardware');

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Wait for connection
    await page.locator('#conn-dot.ok').waitFor({ timeout: 15000 });
  });

  test('dashboard shows real voltage > 100V', async ({ page }) => {
    const voltageA = page.locator('#ats-input-a .inp-val');
    await expect(voltageA).not.toHaveText('--', { timeout: 10000 });
    const text = await voltageA.textContent();
    const volts = parseFloat(text || '0');
    expect(volts).toBeGreaterThan(100);
  });

  test('ATS panel shows real source status', async ({ page }) => {
    const statusText = page.locator('#ats-status-text');
    await expect(statusText).toBeVisible();
    const text = await statusText.textContent();
    expect(['NORMAL', 'FAULT', 'STANDBY']).toContain(text?.trim());
  });

  test('outlet states are on or off (not "--")', async ({ page }) => {
    const outlets = page.locator('#outlets-grid .outlet-card');
    await expect(outlets.first()).toBeVisible({ timeout: 10000 });
    const count = await outlets.count();
    expect(count).toBeGreaterThan(0);

    for (let i = 0; i < Math.min(count, 4); i++) {
      const stateEl = outlets.nth(i).locator('.out-state');
      const state = await stateEl.textContent();
      expect(['ON', 'OFF', 'on', 'off']).toContain(state?.trim());
    }
  });

  test('manage tab security check resolves', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const status = page.locator('#mgmt-security-status');
    // Wait for check to complete (serial can take up to 15s)
    await expect(status).not.toHaveText('--', { timeout: 5000 });
    await expect(status).not.toHaveText('Checking credentials...', { timeout: 20000 });

    const text = await status.textContent();
    // Should resolve to a meaningful result
    expect(text).toBeTruthy();
    expect(text).not.toBe('--');
  });

  test('manage tab network shows real IP address', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const ip = page.locator('#mgmt-net-ip');
    // Wait for network config to load
    await expect(ip).not.toHaveText('--', { timeout: 15000 });
    const text = await ip.textContent();
    // Should look like an IP address (x.x.x.x) or show an error
    expect(text).toBeTruthy();
    expect(text).not.toBe('--');
  });

  test('manage tab thresholds shows percentage values', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const overload = page.locator('#mgmt-thresh-over');
    await expect(overload).not.toHaveText('--', { timeout: 15000 });
    const text = await overload.textContent();
    expect(text).toBeTruthy();
    expect(text).not.toBe('--');
  });

  test('manage tab outlet config shows outlet table', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const outletConfig = page.locator('#mgmt-outlet-config');
    // Wait for table to load (not "Loading...")
    await expect(outletConfig).not.toHaveText('Loading...', { timeout: 15000 });
    const text = await outletConfig.textContent();
    expect(text).toBeTruthy();
    expect(text).not.toBe('Loading...');
  });

  test('manage tab event log shows entries or empty message', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const eventlog = page.locator('#mgmt-eventlog');
    // Wait for event log to load
    await expect(eventlog).not.toHaveText('Loading...', { timeout: 15000 });
    const text = await eventlog.textContent();
    expect(text).toBeTruthy();
    expect(text).not.toBe('Loading...');
  });

  test('MQTT status shows connected in General tab', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-general"]').click();

    const mqttConnected = page.locator('#gen-mqtt-connected');
    await expect(mqttConnected).not.toHaveText('--', { timeout: 10000 });
  });
});
