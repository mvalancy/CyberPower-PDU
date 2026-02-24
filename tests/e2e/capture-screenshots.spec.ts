/**
 * Automated screenshot capture for docs/screenshots/.
 *
 * Run:  cd tests/e2e && npx playwright test capture-screenshots.spec.ts
 *
 * Captures 13 screenshots covering the dashboard, settings panels,
 * login overlay, help modal, and key feature sections.
 *
 * By default uses the webServer from playwright.config.ts (mock mode).
 * To point at a running bridge:
 *   NO_AUTO_SERVER=true BASE_URL=http://localhost:9090 npx playwright test capture-screenshots.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';
import * as path from 'path';
import {
  openSettings,
  switchToTab,
} from './helpers';

const SCREENSHOTS = path.resolve(__dirname, '../../docs/screenshots');

function shot(name: string) {
  return path.join(SCREENSHOTS, name);
}

/** Shrink settings panel to content height for tight screenshots. */
async function shrinkSettings(page: Page) {
  await page.evaluate(() => {
    const panel = document.getElementById('settings-panel');
    if (panel) {
      panel.style.height = 'auto';
      panel.style.position = 'relative';
    }
  });
}

/** Navigate to dashboard and wait for live data to populate. */
async function loadDashboard(page: Page) {
  await page.goto('/');
  await page.waitForLoadState('domcontentloaded');
  // Wait for outlet tiles to render (proves polling data is flowing)
  await page.locator('#outlets-grid .outlet-tile').first().waitFor({ timeout: 20000 });
  // Let animations/transitions settle
  await page.waitForTimeout(800);
}

test.describe('Screenshot Capture', () => {
  test.describe.configure({ mode: 'serial' });

  test.use({
    viewport: { width: 1440, height: 900 },
  });

  test('login overlay', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(500);
    // Force-show the login overlay (may not be enabled in mock mode)
    await page.evaluate(() => {
      const overlay = document.getElementById('login-overlay');
      if (overlay) overlay.style.display = 'flex';
    });
    await page.waitForTimeout(300);
    // Screenshot just the login card (the inner div), not the full-page overlay
    const loginCard = page.locator('#login-overlay > div');
    await loginCard.screenshot({ path: shot('login.png') });
    // Hide it so it doesn't interfere if the page context is reused
    await page.evaluate(() => {
      const overlay = document.getElementById('login-overlay');
      if (overlay) overlay.style.display = 'none';
    });
  });

  test('dashboard', async ({ page }) => {
    await loadDashboard(page);
    await page.screenshot({ path: shot('dashboard.png'), fullPage: false });
  });

  test('dashboard full page', async ({ page }) => {
    await loadDashboard(page);
    await page.screenshot({ path: shot('dashboard-full.png'), fullPage: true });
  });

  test('ATS panel', async ({ page }) => {
    await loadDashboard(page);
    const atsPanel = page.locator('#ats-panel');
    await expect(atsPanel).toBeVisible();
    await atsPanel.screenshot({ path: shot('ats-panel.png') });
  });

  test('outlets grid', async ({ page }) => {
    await loadDashboard(page);
    const section = page.locator('.section:has(#outlets-grid)');
    await section.screenshot({ path: shot('outlets.png') });
  });

  test('history charts', async ({ page }) => {
    await loadDashboard(page);
    const section = page.locator('.section:has(.charts-wrap)');
    await section.screenshot({ path: shot('charts.png') });
  });

  test('automation rules', async ({ page }) => {
    await loadDashboard(page);
    const section = page.locator('.section:has(#rule-form-wrap)');
    await section.screenshot({ path: shot('automation.png') });
  });

  test('settings — PDUs tab (edit open)', async ({ page }) => {
    await loadDashboard(page);
    await openSettings(page);
    await page.waitForTimeout(300);
    // Click Edit on the first PDU card to show the config form
    const editBtn = page.locator('.pdu-card-btn', { hasText: 'Edit' }).first();
    if (await editBtn.isVisible()) {
      await editBtn.click();
      await page.locator('.pdu-card-edit').first().waitFor({ state: 'visible', timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(300);
    }
    await shrinkSettings(page);
    await page.locator('#settings-panel').screenshot({ path: shot('settings-pdus.png') });
  });

  test('settings — General tab', async ({ page }) => {
    await loadDashboard(page);
    await openSettings(page);
    await switchToTab(page, 'tab-general');
    await page.waitForTimeout(300);
    await shrinkSettings(page);
    await page.locator('#settings-panel').screenshot({ path: shot('settings-general.png') });
  });

  test('settings — Manage tab', async ({ page }) => {
    await loadDashboard(page);
    await openSettings(page);
    await switchToTab(page, 'tab-manage');
    await page.locator('#mgmt-header').waitFor({ timeout: 10000 });
    await page.waitForTimeout(1000);
    await shrinkSettings(page);
    await page.locator('#settings-panel').screenshot({ path: shot('settings-manage.png') });
  });

  test('settings — Rename tab', async ({ page }) => {
    await loadDashboard(page);
    await openSettings(page);
    await switchToTab(page, 'tab-rename');
    await page.waitForTimeout(300);
    await shrinkSettings(page);
    await page.locator('#settings-panel').screenshot({ path: shot('settings-rename.png') });
  });

  test('settings — Logs tab', async ({ page }) => {
    await loadDashboard(page);
    await openSettings(page);
    await switchToTab(page, 'tab-logs');
    await page.locator('#log-viewer').waitFor({ timeout: 5000 });
    await page.waitForTimeout(1000);
    await shrinkSettings(page);
    await page.locator('#settings-panel').screenshot({ path: shot('settings-logs.png') });
  });

  test('help modal', async ({ page }) => {
    await loadDashboard(page);
    await page.locator('button[onclick="openHelp()"]').click();
    const helpOverlay = page.locator('#help-overlay');
    await expect(helpOverlay).toBeVisible();
    await page.waitForTimeout(300);
    await helpOverlay.screenshot({ path: shot('help.png') });
  });
});
