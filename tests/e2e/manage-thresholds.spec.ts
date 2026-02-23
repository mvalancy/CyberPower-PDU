import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openManageTab, waitForManageLoad } from './helpers';

test.describe('Manage Tab — Thresholds', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
    await openManageTab(page);
  });

  test('thresholds section shows values after loading (not "--")', async ({ page }) => {
    // Wait for each threshold to resolve away from "--"
    await waitForManageLoad(page, '#mgmt-thresh-over');
    await waitForManageLoad(page, '#mgmt-thresh-near');
    await waitForManageLoad(page, '#mgmt-thresh-low');

    // MockPDU returns 80 / 70 / 10
    await expect(page.locator('#mgmt-thresh-over')).toHaveText('80%');
    await expect(page.locator('#mgmt-thresh-near')).toHaveText('70%');
    await expect(page.locator('#mgmt-thresh-low')).toHaveText('10%');
  });

  test('bank thresholds section shows bank data', async ({ page }) => {
    // Wait for device thresholds to load first (bank data loads in the same call)
    await waitForManageLoad(page, '#mgmt-thresh-over');

    const bankDiv = page.locator('#mgmt-bank-thresholds');
    // MockPDU has 2 banks; each renders a line containing "Bank N:"
    await expect(bankDiv).toContainText('Bank 1');
    await expect(bankDiv).toContainText('Bank 2');
    // Each bank line shows threshold values
    await expect(bankDiv).toContainText('overload=');
    await expect(bankDiv).toContainText('near=');
    await expect(bankDiv).toContainText('low=');
  });

  test('edit button shows form with pre-populated values', async ({ page }) => {
    // Wait for values to load
    await waitForManageLoad(page, '#mgmt-thresh-over');

    // Edit form should start hidden
    await expect(page.locator('#mgmt-threshold-edit')).toBeHidden();

    // Click the Edit button near .sec-thresholds
    await page.locator('.sec-thresholds button[onclick="startThresholdEdit()"]').click();

    // Form becomes visible
    await expect(page.locator('#mgmt-threshold-edit')).toBeVisible();
  });

  test('edit form pre-populates with current values', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-thresh-over');

    await page.locator('.sec-thresholds button[onclick="startThresholdEdit()"]').click();
    await expect(page.locator('#mgmt-threshold-edit')).toBeVisible();

    // Inputs should be pre-populated with the stripped numeric values
    await expect(page.locator('#mgmt-thresh-edit-over')).toHaveValue('80');
    await expect(page.locator('#mgmt-thresh-edit-near')).toHaveValue('70');
    await expect(page.locator('#mgmt-thresh-edit-low')).toHaveValue('10');
  });

  test('modify values and save — toast appears', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-thresh-over');

    await page.locator('.sec-thresholds button[onclick="startThresholdEdit()"]').click();
    await expect(page.locator('#mgmt-threshold-edit')).toBeVisible();

    // Change overload to 85
    await page.locator('#mgmt-thresh-edit-over').fill('85');
    await page.locator('#mgmt-thresh-edit-near').fill('75');
    await page.locator('#mgmt-thresh-edit-low').fill('15');

    // Save
    await page.locator('#mgmt-threshold-edit button[onclick="saveThresholds()"]').click();

    // Toast with "Thresholds saved"
    const toast = page.locator('.toast').first();
    await expect(toast).toBeVisible({ timeout: 7000 });
    await expect(toast).toContainText('Thresholds saved');
  });

  test('cancel hides form without saving', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-thresh-over');

    // Record values before edit
    const overBefore = await page.locator('#mgmt-thresh-over').textContent();

    await page.locator('.sec-thresholds button[onclick="startThresholdEdit()"]').click();
    await expect(page.locator('#mgmt-threshold-edit')).toBeVisible();

    // Change a value but don't save
    await page.locator('#mgmt-thresh-edit-over').fill('99');

    await page.locator('#mgmt-threshold-edit button[onclick="cancelThresholdEdit()"]').click();

    // Form hidden
    await expect(page.locator('#mgmt-threshold-edit')).toBeHidden();

    // Displayed value unchanged
    await expect(page.locator('#mgmt-thresh-over')).toHaveText(overBefore!);
  });
});
