import { test, expect } from '@playwright/test';

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('page loads with correct title', async ({ page }) => {
    await expect(page).toHaveTitle('CyberPDU');
  });

  test('header shows CyberPower PDU Bridge branding', async ({ page }) => {
    const brand = page.locator('.header-brand');
    await expect(brand).toContainText('CyberPower PDU Bridge');
  });

  test('connection dot turns green within 10s', async ({ page }) => {
    const dot = page.locator('#conn-dot');
    await expect(dot).toHaveClass(/ok/, { timeout: 10000 });
  });

  test('ATS panel renders with voltage values', async ({ page }) => {
    const atsPanel = page.locator('#ats-panel');
    await expect(atsPanel).toBeVisible();

    // Wait for polling data to populate
    const voltageA = page.locator('#ats-input-a .inp-val');
    await expect(voltageA).not.toHaveText('--', { timeout: 10000 });
  });

  test('outlet grid renders with outlet cards', async ({ page }) => {
    const grid = page.locator('#outlets-grid');
    await expect(grid).toBeVisible();

    // Wait for outlets to populate
    const outlets = page.locator('#outlets-grid .outlet-card');
    await expect(outlets.first()).toBeVisible({ timeout: 10000 });
    expect(await outlets.count()).toBeGreaterThan(0);
  });

  test('history charts section has canvas elements', async ({ page }) => {
    const powerChart = page.locator('#chart-power');
    await expect(powerChart).toBeVisible();

    const voltageChart = page.locator('#chart-voltage');
    await expect(voltageChart).toBeVisible();

    const currentChart = page.locator('#chart-current');
    await expect(currentChart).toBeVisible();
  });

  test('status banner hidden when healthy', async ({ page }) => {
    // Wait for first poll
    await page.locator('#conn-dot.ok').waitFor({ timeout: 10000 });
    const banner = page.locator('#status-banner');
    await expect(banner).not.toHaveClass(/visible/);
  });

  test('device info appears after polling', async ({ page }) => {
    // After successful polling, device info section may show
    await page.locator('#conn-dot.ok').waitFor({ timeout: 10000 });
    // Device name in header should update from "--"
    const deviceName = page.locator('#device-name');
    await expect(deviceName).not.toHaveText('--', { timeout: 10000 });
  });
});
