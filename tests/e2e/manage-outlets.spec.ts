import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openManageTab, waitForManageLoad } from './helpers';

test.describe('Manage Tab — Outlet Config', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
    await openManageTab(page);
  });

  test('outlet config table shows all 10 outlets', async ({ page }) => {
    const container = page.locator('#mgmt-outlet-config');

    // Wait for "Loading..." to resolve
    await expect(container).not.toHaveText('Loading...', { timeout: 15000 });
    await expect(container).not.toContainText('Requires serial', { timeout: 5000 });

    // MockPDU has 10 outlets — one row per outlet
    for (let n = 1; n <= 10; n++) {
      await expect(page.locator(`#mgmt-outlet-row-${n}`)).toBeVisible();
    }
  });

  test('table has correct column headers', async ({ page }) => {
    const container = page.locator('#mgmt-outlet-config');
    await expect(container).not.toHaveText('Loading...', { timeout: 15000 });

    const table = container.locator('table');
    await expect(table).toBeVisible();

    // Check header cells
    await expect(table.locator('th').nth(0)).toContainText('#');
    await expect(table.locator('th').nth(1)).toContainText('Name');
    await expect(table.locator('th').nth(2)).toContainText('On Delay');
    await expect(table.locator('th').nth(3)).toContainText('Off Delay');
    await expect(table.locator('th').nth(4)).toContainText('Reboot');
  });

  test('clicking Edit on outlet 1 transforms row to inputs', async ({ page }) => {
    const container = page.locator('#mgmt-outlet-config');
    await expect(container).not.toHaveText('Loading...', { timeout: 15000 });

    // Click Edit button in outlet row 1
    const row1 = page.locator('#mgmt-outlet-row-1');
    await row1.locator('button', { hasText: 'Edit' }).click();

    // Edit inputs should now exist in the row
    await expect(page.locator('#mgmt-olt-edit-name-1')).toBeVisible();
    await expect(page.locator('#mgmt-olt-edit-on-1')).toBeVisible();
    await expect(page.locator('#mgmt-olt-edit-off-1')).toBeVisible();
    await expect(page.locator('#mgmt-olt-edit-reboot-1')).toBeVisible();
  });

  test('edit inputs are pre-populated with current outlet 1 values', async ({ page }) => {
    const container = page.locator('#mgmt-outlet-config');
    await expect(container).not.toHaveText('Loading...', { timeout: 15000 });

    await page.locator('#mgmt-outlet-row-1').locator('button', { hasText: 'Edit' }).click();

    // MockPDU outlet 1: name="Outlet 1", on_delay=0, off_delay=0, reboot_duration=10
    await expect(page.locator('#mgmt-olt-edit-name-1')).toHaveValue('Outlet 1');
    await expect(page.locator('#mgmt-olt-edit-on-1')).toHaveValue('0');
    await expect(page.locator('#mgmt-olt-edit-off-1')).toHaveValue('0');
    await expect(page.locator('#mgmt-olt-edit-reboot-1')).toHaveValue('10');
  });

  test('change name and delay on outlet 1 then save — toast appears', async ({ page }) => {
    const container = page.locator('#mgmt-outlet-config');
    await expect(container).not.toHaveText('Loading...', { timeout: 15000 });

    await page.locator('#mgmt-outlet-row-1').locator('button', { hasText: 'Edit' }).click();

    // Update name and on_delay
    await page.locator('#mgmt-olt-edit-name-1').fill('Server 1');
    await page.locator('#mgmt-olt-edit-on-1').fill('5');

    // Click Save in the row
    await page.locator('#mgmt-outlet-row-1').locator('button', { hasText: 'Save' }).click();

    // Toast with outlet saved message
    const toast = page.locator('.toast').first();
    await expect(toast).toBeVisible({ timeout: 7000 });
    await expect(toast).toContainText('config saved');
  });

  test('outlet config table refreshes with new values after save', async ({ page }) => {
    const container = page.locator('#mgmt-outlet-config');
    await expect(container).not.toHaveText('Loading...', { timeout: 15000 });

    await page.locator('#mgmt-outlet-row-1').locator('button', { hasText: 'Edit' }).click();

    await page.locator('#mgmt-olt-edit-name-1').fill('Renamed Outlet');
    await page.locator('#mgmt-olt-edit-on-1').fill('3');

    await page.locator('#mgmt-outlet-row-1').locator('button', { hasText: 'Save' }).click();

    // Wait for toast
    await expect(page.locator('.toast').first()).toBeVisible({ timeout: 7000 });

    // After save, loadOutletConfig() is called — table reloads
    // Row 1 should now show the updated name (not input anymore)
    await expect(page.locator('#mgmt-outlet-row-1')).toContainText('Renamed Outlet', { timeout: 8000 });
  });
});
