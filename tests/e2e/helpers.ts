/**
 * Shared Playwright helpers for CyberPDU E2E tests.
 */
import { type Page, expect } from '@playwright/test';

/** Wait for the bridge to be connected (green dot). */
export async function waitForBridgeReady(page: Page, timeout = 15000) {
  await page.locator('#conn-dot.ok').waitFor({ timeout });
}

/** Open the settings panel. */
export async function openSettings(page: Page) {
  await page.locator('button[onclick="openSettings()"]').click();
  await expect(page.locator('#settings-panel')).toHaveClass(/open/);
}

/** Switch to a settings tab by data-tab attribute. */
export async function switchToTab(page: Page, tab: string) {
  await page.locator(`.settings-tab[data-tab="${tab}"]`).click();
  await expect(page.locator(`#${tab}`)).toHaveClass(/active/);
}

/** Open the Manage tab inside settings. */
export async function openManageTab(page: Page) {
  await openSettings(page);
  await switchToTab(page, 'tab-manage');
}

/** Wait for a toast notification to appear with optional text match. */
export async function waitForToast(page: Page, textMatch?: string | RegExp, timeout = 5000) {
  const toast = page.locator('.toast, .notification, [class*="toast"]').first();
  await toast.waitFor({ state: 'visible', timeout });
  if (textMatch) {
    if (typeof textMatch === 'string') {
      await expect(toast).toContainText(textMatch);
    } else {
      await expect(toast).toHaveText(textMatch);
    }
  }
}

/** Wait for management data to finish loading (not show "Loading..." or "--"). */
export async function waitForManageLoad(page: Page, selector: string, timeout = 15000) {
  const el = page.locator(selector);
  await expect(el).not.toHaveText('--', { timeout });
  await expect(el).not.toHaveText('Loading...', { timeout });
}
