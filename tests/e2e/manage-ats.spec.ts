import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openManageTab, waitForManageLoad } from './helpers';

test.describe('Manage Tab — ATS Config', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
    await openManageTab(page);
  });

  test('ATS section loads with preferred source value', async ({ page }) => {
    // MockPDU returns preferred_source="A"
    await waitForManageLoad(page, '#mgmt-ats-preferred');
    await expect(page.locator('#mgmt-ats-preferred')).toHaveText('A');
  });

  test('shows sensitivity, voltage limits, and coldstart values', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-ats-preferred');

    // MockPDU: ats_sensitivity="normal" → capitalize → "Normal"
    await expect(page.locator('#mgmt-ats-sensitivity')).toHaveText('Normal');

    // MockPDU: transfer_voltage upper=138, lower=96 → shown as "138V" / "96V"
    await expect(page.locator('#mgmt-ats-upper')).toHaveText('138V');
    await expect(page.locator('#mgmt-ats-lower')).toHaveText('96V');

    // MockPDU: coldstart_delay=0, coldstart_state="allon"
    await expect(page.locator('#mgmt-ats-colddelay')).toHaveText('0s');
    await expect(page.locator('#mgmt-ats-coldstate')).toHaveText('allon');
  });

  test('Edit shows form with pre-populated values', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-ats-preferred');

    // Edit form starts hidden
    await expect(page.locator('#mgmt-ats-edit')).toBeHidden();

    await page.locator('button[onclick="startATSEdit()"]').click();

    await expect(page.locator('#mgmt-ats-edit')).toBeVisible();

    // Verify pre-populated values
    await expect(page.locator('#mgmt-ats-edit-preferred')).toHaveValue('A');
    await expect(page.locator('#mgmt-ats-edit-sensitivity')).toHaveValue('normal');
    await expect(page.locator('#mgmt-ats-edit-upper')).toHaveValue('138');
    await expect(page.locator('#mgmt-ats-edit-lower')).toHaveValue('96');
    await expect(page.locator('#mgmt-ats-edit-colddelay')).toHaveValue('0');
    await expect(page.locator('#mgmt-ats-edit-coldstate')).toHaveValue('allon');
  });

  test('change preferred source from A to B in edit form', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-ats-preferred');

    await page.locator('button[onclick="startATSEdit()"]').click();
    await expect(page.locator('#mgmt-ats-edit')).toBeVisible();

    // Change preferred source select to B
    await page.locator('#mgmt-ats-edit-preferred').selectOption('B');
    await expect(page.locator('#mgmt-ats-edit-preferred')).toHaveValue('B');
  });

  test('change sensitivity in edit form', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-ats-preferred');

    await page.locator('button[onclick="startATSEdit()"]').click();
    await expect(page.locator('#mgmt-ats-edit')).toBeVisible();

    // Change sensitivity to high
    await page.locator('#mgmt-ats-edit-sensitivity').selectOption('high');
    await expect(page.locator('#mgmt-ats-edit-sensitivity')).toHaveValue('high');
  });

  test('save ATS config — toast appears and values refresh', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-ats-preferred');

    await page.locator('button[onclick="startATSEdit()"]').click();
    await expect(page.locator('#mgmt-ats-edit')).toBeVisible();

    // Make a change: preferred source to B, sensitivity to high
    await page.locator('#mgmt-ats-edit-preferred').selectOption('B');
    await page.locator('#mgmt-ats-edit-sensitivity').selectOption('high');
    await page.locator('#mgmt-ats-edit-colddelay').fill('5');

    await page.locator('#mgmt-ats-edit button[onclick="saveATSConfig()"]').click();

    // Toast with "ATS config saved"
    const toast = page.locator('.toast').first();
    await expect(toast).toBeVisible({ timeout: 7000 });
    await expect(toast).toContainText('ATS config saved');

    // Form should close after save
    await expect(page.locator('#mgmt-ats-edit')).toBeHidden({ timeout: 5000 });

    // Values refresh — preferred source is now B
    await expect(page.locator('#mgmt-ats-preferred')).toHaveText('B', { timeout: 8000 });
  });

  test('cancel hides the edit form without saving', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-ats-preferred');

    // Record original value
    const originalSource = await page.locator('#mgmt-ats-preferred').textContent();

    await page.locator('button[onclick="startATSEdit()"]').click();
    await expect(page.locator('#mgmt-ats-edit')).toBeVisible();

    // Make a change but cancel
    await page.locator('#mgmt-ats-edit-preferred').selectOption('B');

    await page.locator('#mgmt-ats-edit button[onclick="cancelATSEdit()"]').click();

    // Form hidden
    await expect(page.locator('#mgmt-ats-edit')).toBeHidden();

    // Displayed value unchanged
    await expect(page.locator('#mgmt-ats-preferred')).toHaveText(originalSource!);
  });
});
