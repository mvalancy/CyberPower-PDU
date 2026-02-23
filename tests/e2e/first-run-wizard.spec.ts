import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openSettings, switchToTab } from './helpers';

test.describe('Add PDU Wizard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
  });

  // Test 1: Settings opens and PDUs tab shows Add PDU button
  test('settings opens and PDUs tab shows Add PDU button', async ({ page }) => {
    await openSettings(page);

    // PDUs tab is active by default
    const pdusTab = page.locator('.settings-tab[data-tab="tab-pdus"]');
    await expect(pdusTab).toHaveClass(/active/);

    // Add PDU button is visible inside the PDUs tab content
    const addBtn = page.locator('#tab-pdus button', { hasText: 'Add PDU' });
    await expect(addBtn).toBeVisible();
  });

  // Test 2: Add PDU button opens wizard
  test('Add PDU button opens wizard', async ({ page }) => {
    await openSettings(page);

    // Wizard wrap should be hidden before clicking
    await expect(page.locator('#wizard-wrap')).toBeHidden();

    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();

    // Wizard wrap becomes visible
    await expect(page.locator('#wizard-wrap')).toBeVisible();
  });

  // Test 3: Wizard scan step is first visible step
  test('wizard shows step 1 (choice step) first', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();

    // wiz-step-1 should be the active step
    const step1 = page.locator('#wiz-step-1');
    await expect(step1).toHaveClass(/active/);

    // The three choice buttons should be visible
    await expect(page.locator('#wiz-step-1 button', { hasText: 'Scan Network' })).toBeVisible();
    await expect(page.locator('#wiz-step-1 button', { hasText: 'Scan Serial Ports' })).toBeVisible();
    await expect(page.locator('#wiz-step-1 button', { hasText: 'Enter Manually' })).toBeVisible();
  });

  // Test 4: Manual button switches to manual entry step
  test('clicking Enter Manually switches to manual entry step', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();

    await page.locator('#wiz-step-1 button', { hasText: 'Enter Manually' }).click();

    // wiz-step-manual becomes active, wiz-step-1 becomes inactive
    await expect(page.locator('#wiz-step-manual')).toHaveClass(/active/);
    await expect(page.locator('#wiz-step-1')).not.toHaveClass(/active/);
  });

  // Test 5: Manual entry shows SNMP host/port/community fields by default
  test('manual entry shows SNMP host, port, and community fields', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();
    await page.locator('#wiz-step-1 button', { hasText: 'Enter Manually' }).click();

    // SNMP fields are visible by default
    await expect(page.locator('#wiz-snmp-fields')).toBeVisible();
    await expect(page.locator('#wiz-host')).toBeVisible();
    await expect(page.locator('#wiz-port')).toBeVisible();
    await expect(page.locator('#wiz-comm-read')).toBeVisible();

    // Serial fields are hidden
    await expect(page.locator('#wiz-serial-fields')).toBeHidden();
  });

  // Test 6: Serial toggle shows serial port fields
  test('selecting Serial connection type reveals serial port fields', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();
    await page.locator('#wiz-step-1 button', { hasText: 'Enter Manually' }).click();

    // Switch connection type to serial
    await page.locator('#wiz-conn-type').selectOption('serial');

    // Serial fields are now visible
    await expect(page.locator('#wiz-serial-fields')).toBeVisible();
    await expect(page.locator('#wiz-serial-port')).toBeVisible();
    await expect(page.locator('#wiz-serial-baud')).toBeVisible();
    await expect(page.locator('#wiz-serial-user')).toBeVisible();
    await expect(page.locator('#wiz-serial-pass')).toBeVisible();

    // SNMP fields are now hidden
    await expect(page.locator('#wiz-snmp-fields')).toBeHidden();
  });

  // Test 7: Filling host and clicking Next goes to config step
  test('filling host and clicking Next advances to config step', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();
    await page.locator('#wiz-step-1 button', { hasText: 'Enter Manually' }).click();

    // Fill in the host field
    await page.locator('#wiz-host').fill('192.168.1.100');

    // Click Next
    await page.locator('#wiz-step-manual button', { hasText: 'Next' }).click();

    // Config step becomes active
    await expect(page.locator('#wiz-step-config')).toHaveClass(/active/);
  });

  // Test 8: Config step shows device_id and label fields
  test('config step shows device ID and label fields', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();
    await page.locator('#wiz-step-1 button', { hasText: 'Enter Manually' }).click();
    await page.locator('#wiz-host').fill('192.168.1.100');
    await page.locator('#wiz-step-manual button', { hasText: 'Next' }).click();

    // Config fields are visible
    await expect(page.locator('#wiz-device-id')).toBeVisible();
    await expect(page.locator('#wiz-label')).toBeVisible();

    // Community write field also present
    await expect(page.locator('#wiz-comm-write')).toBeVisible();
  });

  // Test 9: Test connection button exists in config step
  test('config step has a Test Connection button', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();
    await page.locator('#wiz-step-1 button', { hasText: 'Enter Manually' }).click();
    await page.locator('#wiz-host').fill('192.168.1.100');
    await page.locator('#wiz-step-manual button', { hasText: 'Next' }).click();

    const testBtn = page.locator('#wiz-step-config button', { hasText: 'Test Connection' });
    await expect(testBtn).toBeVisible();
  });

  // Test 10: Cancel button closes wizard
  test('Cancel button on step 1 closes the wizard', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();

    await expect(page.locator('#wizard-wrap')).toBeVisible();

    await page.locator('#wiz-step-1 button', { hasText: 'Cancel' }).click();

    // Wizard is hidden again
    await expect(page.locator('#wizard-wrap')).toBeHidden();
  });

  // Test 11: Full wizard flow — manual entry → config → confirm (verify summary renders)
  test('full wizard flow: manual → config → confirm shows summary', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();

    // Step 1: choose manual
    await page.locator('#wiz-step-1 button', { hasText: 'Enter Manually' }).click();

    // Step 2: fill SNMP fields
    await page.locator('#wiz-host').fill('192.168.1.200');
    await page.locator('#wiz-port').fill('161');
    await page.locator('#wiz-step-manual button', { hasText: 'Next' }).click();

    // Step 3: fill config fields
    await expect(page.locator('#wiz-step-config')).toHaveClass(/active/);
    await page.locator('#wiz-device-id').fill('rack1-pdu');
    await page.locator('#wiz-label').fill('Test Rack PDU');

    // Advance to confirm step
    await page.locator('#wiz-step-config button', { hasText: 'Next' }).click();

    // Confirm step should be active
    await expect(page.locator('#wiz-step-confirm')).toHaveClass(/active/);

    // Summary dl element should be populated with at least one dt/dd pair
    const summary = page.locator('#wiz-summary');
    await expect(summary).toBeVisible();
    const summaryContent = await summary.innerHTML();
    expect(summaryContent.trim().length).toBeGreaterThan(0);
    // Summary should contain the label we entered
    await expect(summary).toContainText('Test Rack PDU');

    // Save PDU button should be visible
    await expect(page.locator('#wiz-step-confirm button', { hasText: 'Save PDU' })).toBeVisible();
  });

  // Test 12: Wizard closes on cancel from any step and when settings closes
  test('wizard closes when settings panel is closed', async ({ page }) => {
    await openSettings(page);
    await page.locator('#tab-pdus button', { hasText: 'Add PDU' }).click();

    await expect(page.locator('#wizard-wrap')).toBeVisible();

    // Close the settings panel
    await page.locator('.settings-close').click();

    // Settings panel should close
    await expect(page.locator('#settings-panel')).not.toHaveClass(/open/);

    // Reopen settings — wizard should no longer be visible (cancelWizard called on close)
    await openSettings(page);
    await expect(page.locator('#wizard-wrap')).toBeHidden();
  });
});
