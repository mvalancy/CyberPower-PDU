import { test, expect } from '@playwright/test';

test.describe('Settings Panel', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Wait for initial load
    await page.locator('.header-brand').waitFor();
  });

  test('settings modal opens on gear icon click', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    const panel = page.locator('#settings-panel');
    await expect(panel).toHaveClass(/open/);
  });

  test('settings modal closes properly', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await expect(page.locator('#settings-panel')).toHaveClass(/open/);
    await page.locator('.settings-close').click();
    await expect(page.locator('#settings-panel')).not.toHaveClass(/open/);
  });

  test('PDUs tab is default and shows content', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    const pdusTab = page.locator('.settings-tab[data-tab="tab-pdus"]');
    await expect(pdusTab).toHaveClass(/active/);
    const pdusContent = page.locator('#tab-pdus');
    await expect(pdusContent).toHaveClass(/active/);
  });

  test('PDUs tab shows Add PDU button', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    const addBtn = page.locator('#tab-pdus button', { hasText: 'Add PDU' });
    await expect(addBtn).toBeVisible();
  });

  test('General tab shows all settings sections', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-general"]').click();

    const generalTab = page.locator('#tab-general');
    await expect(generalTab).toHaveClass(/active/);

    // Check for key sections
    await expect(generalTab.locator('.sec-polling')).toBeVisible();
    await expect(generalTab.locator('.sec-mqtt')).toBeVisible();
    await expect(generalTab.locator('.sec-auth')).toBeVisible();
    await expect(generalTab.locator('.sec-info')).toBeVisible();
  });

  test('MQTT section has test connection button', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-general"]').click();

    const testBtn = page.locator('.sec-mqtt button', { hasText: 'Test Connection' });
    await expect(testBtn).toBeVisible();
  });

  test('Rename tab shows device name and location fields', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-rename"]').click();

    await expect(page.locator('#rename-device-name')).toBeVisible();
    await expect(page.locator('#rename-device-location')).toBeVisible();
    await expect(page.locator('#rename-source-a')).toBeVisible();
    await expect(page.locator('#rename-source-b')).toBeVisible();
  });

  test('Manage tab opens and shows sections', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const manageTab = page.locator('#tab-manage');
    await expect(manageTab).toHaveClass(/active/);

    // Manage header should be visible
    await expect(page.locator('#mgmt-header')).toBeVisible();

    // All management sections should exist
    await expect(manageTab.locator('.sec-security')).toBeVisible();
    await expect(manageTab.locator('.sec-network')).toBeVisible();
    await expect(manageTab.locator('.sec-thresholds')).toBeVisible();
    await expect(manageTab.locator('.sec-outlets')).toBeVisible();
    await expect(manageTab.locator('.sec-eventlog')).toBeVisible();
  });

  test('Manage tab security section resolves (not stuck on initial state)', async ({ page }) => {
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const status = page.locator('#mgmt-security-status');
    // Should transition from "--" to something else (either "Checking credentials..." then a result, or error)
    // Wait up to 20s for it to resolve past the initial checking state
    await expect(status).not.toHaveText('--', { timeout: 20000 });
    // Should not be permanently stuck on "Checking credentials..."
    await expect(status).not.toHaveText('Checking credentials...', { timeout: 20000 });
  });

  test('Manage tab shows transport badge', async ({ page }) => {
    // Wait for PDU list to load
    await page.locator('#conn-dot.ok').waitFor({ timeout: 10000 }).catch(() => {});
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const badge = page.locator('#mgmt-transport-badge');
    await expect(badge).toBeVisible();
    // Badge should show a transport type
    const text = await badge.textContent();
    expect(['SNMP Only', 'Serial', 'SNMP+Serial', '--']).toContain(text?.trim());
  });

  test('Manage tab shows serial-required banner for SNMP-only PDUs', async ({ page }) => {
    await page.locator('#conn-dot.ok').waitFor({ timeout: 10000 }).catch(() => {});
    await page.locator('button[onclick="openSettings()"]').click();
    await page.locator('.settings-tab[data-tab="tab-manage"]').click();

    const badge = page.locator('#mgmt-transport-badge');
    const badgeText = await badge.textContent();

    if (badgeText?.trim() === 'SNMP Only') {
      // Banner should be visible for SNMP-only PDUs
      await expect(page.locator('#mgmt-no-serial-banner')).toBeVisible();
    } else {
      // Banner should be hidden for serial-capable PDUs
      await expect(page.locator('#mgmt-no-serial-banner')).not.toBeVisible();
    }
  });

  test('Help modal opens on help button click', async ({ page }) => {
    await page.locator('button[onclick="openHelp()"]').click();
    const helpOverlay = page.locator('#help-overlay');
    await expect(helpOverlay).toBeVisible();
  });
});
