import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openManageTab, waitForManageLoad } from './helpers';

test.describe('Manage Tab — EnergyWise', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
    await openManageTab(page);
  });

  test('EnergyWise section shows domain placeholder, port 43440, and enabled No', async ({ page }) => {
    // MockPDU energywise: domain="", port=43440, enabled=False
    // Empty domain renders as "--" in the UI
    const domainEl = page.locator('#mgmt-ew-domain');
    const portEl = page.locator('#mgmt-ew-port');
    const enabledEl = page.locator('#mgmt-ew-enabled');

    // Wait for the port to resolve (confirms load completed)
    await expect(portEl).not.toHaveText('--', { timeout: 15000 });
    await expect(portEl).not.toHaveText('Loading...', { timeout: 5000 });

    await expect(portEl).toHaveText('43440');
    await expect(enabledEl).toHaveText('No');

    // Domain is empty string from mock → renders as "--"
    const domainText = await domainEl.textContent();
    expect(['--', '']).toContain(domainText?.trim());
  });

  test('clicking Edit shows form with current values pre-populated', async ({ page }) => {
    // Wait for load
    const portEl = page.locator('#mgmt-ew-port');
    await expect(portEl).not.toHaveText('--', { timeout: 15000 });

    // Form starts hidden
    await expect(page.locator('#mgmt-ew-edit')).toBeHidden();

    await page.locator('button[onclick="startEnergyWiseEdit()"]').click();

    await expect(page.locator('#mgmt-ew-edit')).toBeVisible();

    // Port pre-populated from displayed value (43440)
    await expect(page.locator('#mgmt-ew-edit-port')).toHaveValue('43440');

    // Enabled is No → select value "false"
    await expect(page.locator('#mgmt-ew-edit-enabled')).toHaveValue('false');
  });

  test('fill domain, set enabled to Yes, and save — toast appears', async ({ page }) => {
    const portEl = page.locator('#mgmt-ew-port');
    await expect(portEl).not.toHaveText('--', { timeout: 15000 });

    await page.locator('button[onclick="startEnergyWiseEdit()"]').click();
    await expect(page.locator('#mgmt-ew-edit')).toBeVisible();

    // Fill in domain and enable
    await page.locator('#mgmt-ew-edit-domain').fill('energywise.local');
    await page.locator('#mgmt-ew-edit-port').fill('43440');
    await page.locator('#mgmt-ew-edit-enabled').selectOption('true');

    await page.locator('#mgmt-ew-edit button[onclick="saveEnergyWise()"]').click();

    // Toast with "EnergyWise saved"
    const toast = page.locator('.toast').first();
    await expect(toast).toBeVisible({ timeout: 7000 });
    await expect(toast).toContainText('EnergyWise saved');
  });

  test('values refresh after saving EnergyWise config', async ({ page }) => {
    const portEl = page.locator('#mgmt-ew-port');
    await expect(portEl).not.toHaveText('--', { timeout: 15000 });

    await page.locator('button[onclick="startEnergyWiseEdit()"]').click();
    await expect(page.locator('#mgmt-ew-edit')).toBeVisible();

    await page.locator('#mgmt-ew-edit-domain').fill('test-domain');
    await page.locator('#mgmt-ew-edit-enabled').selectOption('true');

    await page.locator('#mgmt-ew-edit button[onclick="saveEnergyWise()"]').click();

    // Wait for toast
    await expect(page.locator('.toast').first()).toBeVisible({ timeout: 7000 });

    // After save, loadEnergyWise() is called — values refresh
    // enabled=Yes should now appear
    await expect(page.locator('#mgmt-ew-enabled')).toHaveText('Yes', { timeout: 8000 });

    // Domain should show the newly saved value
    await expect(page.locator('#mgmt-ew-domain')).toHaveText('test-domain', { timeout: 8000 });

    // Edit form should close
    await expect(page.locator('#mgmt-ew-edit')).toBeHidden({ timeout: 5000 });
  });

  test('cancel hides the edit form without saving', async ({ page }) => {
    const portEl = page.locator('#mgmt-ew-port');
    await expect(portEl).not.toHaveText('--', { timeout: 15000 });

    // Record original enabled value
    const originalEnabled = await page.locator('#mgmt-ew-enabled').textContent();

    await page.locator('button[onclick="startEnergyWiseEdit()"]').click();
    await expect(page.locator('#mgmt-ew-edit')).toBeVisible();

    // Make a change but cancel
    await page.locator('#mgmt-ew-edit-domain').fill('will-not-save');
    await page.locator('#mgmt-ew-edit-enabled').selectOption('true');

    await page.locator('#mgmt-ew-edit button[onclick="cancelEnergyWiseEdit()"]').click();

    // Form hidden
    await expect(page.locator('#mgmt-ew-edit')).toBeHidden();

    // Enabled value unchanged
    await expect(page.locator('#mgmt-ew-enabled')).toHaveText(originalEnabled!);
  });
});
