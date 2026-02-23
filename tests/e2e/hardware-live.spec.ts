import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openSettings, switchToTab, openManageTab, waitForManageLoad } from './helpers';

/**
 * Extended hardware E2E tests — interact with real PDU management features.
 *
 * These tests are tagged @hardware and only run when HW_TEST=true.
 * They require a real PDU connected via SNMP or serial, with the bridge
 * already running (use NO_AUTO_SERVER=true in playwright.config.ts).
 *
 * IMPORTANT: Some tests WRITE to the PDU (thresholds, outlet names, ATS).
 * They restore original values after each test where possible.
 */

const hw = process.env.HW_TEST === 'true';

test.describe('Hardware Live Management @hardware', () => {
  test.skip(!hw, 'Skipped — set HW_TEST=true to run against real hardware');
  test.describe.configure({ timeout: 60000 });

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page, 20000);
  });

  // -------------------------------------------------------------------------
  // Dashboard: verify real data
  // -------------------------------------------------------------------------

  test('dashboard shows real voltage for both sources', async ({ page }) => {
    const voltageA = page.locator('#ats-input-a .inp-val');
    await expect(voltageA).not.toHaveText('--', { timeout: 15000 });
    const textA = await voltageA.textContent();
    expect(parseFloat(textA || '0')).toBeGreaterThan(100);

    // Source B may or may not be present
    const voltageB = page.locator('#ats-input-b .inp-val');
    if (await voltageB.isVisible()) {
      const textB = await voltageB.textContent();
      if (textB && textB !== '--') {
        expect(parseFloat(textB)).toBeGreaterThanOrEqual(0);
      }
    }
  });

  test('outlet ON/OFF via dashboard produces state change', async ({ page }) => {
    const outlets = page.locator('#outlets-grid .outlet-tile');
    await expect(outlets.first()).toBeVisible({ timeout: 15000 });

    // Find the first outlet and get its current state
    const firstOutlet = outlets.first();
    const stateEl = firstOutlet.locator('.o-state');
    const stateBefore = (await stateEl.textContent())?.trim().toUpperCase();

    // Click the outlet to open the control popup
    await firstOutlet.click();

    // The popup/modal should appear with ON/OFF buttons
    const popup = page.locator('.outlet-popup, .outlet-controls, [class*="outlet-control"]');
    if (await popup.isVisible({ timeout: 3000 }).catch(() => false)) {
      const targetCmd = stateBefore === 'ON' ? 'OFF' : 'ON';
      const cmdBtn = popup.locator(`button`, { hasText: targetCmd });
      if (await cmdBtn.isVisible()) {
        await cmdBtn.click();
        // Wait for state change (SNMP/serial round-trip)
        await page.waitForTimeout(5000);
        const stateAfter = (await stateEl.textContent())?.trim().toUpperCase();
        expect(stateAfter).toBe(targetCmd);

        // Restore original state
        await firstOutlet.click();
        const restoreBtn = popup.locator(`button`, { hasText: stateBefore || 'ON' });
        if (await restoreBtn.isVisible()) {
          await restoreBtn.click();
        }
      }
    }
  });

  // -------------------------------------------------------------------------
  // Manage tab: threshold edit/save on real hardware
  // -------------------------------------------------------------------------

  test('threshold values load from real PDU', async ({ page }) => {
    await openManageTab(page);
    await waitForManageLoad(page, '#mgmt-thresh-over', 20000);

    const text = await page.locator('#mgmt-thresh-over').textContent();
    expect(text).toBeTruthy();
    expect(text).not.toBe('--');
    expect(text).toMatch(/\d+%/);
  });

  test('threshold edit/save round-trip on real PDU', async ({ page }) => {
    await openManageTab(page);
    await waitForManageLoad(page, '#mgmt-thresh-over', 20000);

    // Save original values
    const origOver = (await page.locator('#mgmt-thresh-over').textContent())?.replace('%', '').trim();

    // Edit
    await page.locator('.sec-thresholds button[onclick="startThresholdEdit()"]').click();
    await expect(page.locator('#mgmt-threshold-edit')).toBeVisible();

    // Change to a safe test value (original + 1, then restore)
    const testVal = String(Math.min(parseInt(origOver || '80') + 1, 99));
    await page.locator('#mgmt-thresh-edit-over').fill(testVal);
    await page.locator('#mgmt-threshold-edit button[onclick="saveThresholds()"]').click();

    // Wait for toast
    const toast = page.locator('.toast').first();
    await expect(toast).toBeVisible({ timeout: 15000 });

    // Verify new value loaded
    await waitForManageLoad(page, '#mgmt-thresh-over', 10000);
    await expect(page.locator('#mgmt-thresh-over')).toContainText(testVal);

    // Restore original value
    await page.locator('.sec-thresholds button[onclick="startThresholdEdit()"]').click();
    await page.locator('#mgmt-thresh-edit-over').fill(origOver || '80');
    await page.locator('#mgmt-threshold-edit button[onclick="saveThresholds()"]').click();
    await page.waitForTimeout(2000);
  });

  // -------------------------------------------------------------------------
  // Manage tab: outlet config
  // -------------------------------------------------------------------------

  test('outlet config loads from real PDU', async ({ page }) => {
    await openManageTab(page);
    const outletConfig = page.locator('#mgmt-outlet-config');
    await expect(outletConfig).not.toContainText('Loading...', { timeout: 20000 });

    // Should have at least one outlet row
    const rows = outletConfig.locator('tr[id^="mgmt-outlet-row-"]');
    expect(await rows.count()).toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // Manage tab: ATS config
  // -------------------------------------------------------------------------

  test('ATS config loads from real PDU', async ({ page }) => {
    await openManageTab(page);
    await waitForManageLoad(page, '#mgmt-ats-preferred', 20000);

    const pref = await page.locator('#mgmt-ats-preferred').textContent();
    expect(pref).toBeTruthy();
    expect(['A', 'B']).toContain(pref?.trim());
  });

  test('ATS sensitivity change on real PDU', async ({ page }) => {
    await openManageTab(page);
    await waitForManageLoad(page, '#mgmt-ats-sensitivity', 20000);

    const origSens = (await page.locator('#mgmt-ats-sensitivity').textContent())?.trim().toLowerCase();

    // Edit ATS config
    await page.locator('button[onclick="startATSEdit()"]').click();
    await expect(page.locator('#mgmt-ats-edit')).toBeVisible();

    // Change sensitivity to something different
    const newSens = origSens === 'normal' ? 'high' : 'normal';
    await page.selectOption('#mgmt-ats-edit-sensitivity', newSens);
    await page.locator('#mgmt-ats-edit button[onclick="saveATSConfig()"]').click();

    const toast = page.locator('.toast').first();
    await expect(toast).toBeVisible({ timeout: 15000 });

    // Restore original
    await page.locator('button[onclick="startATSEdit()"]').click();
    await page.selectOption('#mgmt-ats-edit-sensitivity', origSens || 'normal');
    await page.locator('#mgmt-ats-edit button[onclick="saveATSConfig()"]').click();
    await page.waitForTimeout(2000);
  });

  // -------------------------------------------------------------------------
  // Manage tab: network config (READ-ONLY verification)
  // -------------------------------------------------------------------------

  test('network config displays real IP address', async ({ page }) => {
    await openManageTab(page);
    await waitForManageLoad(page, '#mgmt-net-ip', 20000);

    const ip = await page.locator('#mgmt-net-ip').textContent();
    expect(ip).toBeTruthy();
    expect(ip).not.toBe('--');
    // Should match an IP-like pattern
    expect(ip).toMatch(/\d+\.\d+\.\d+\.\d+/);
  });

  test('network config shows MAC address', async ({ page }) => {
    await openManageTab(page);
    await waitForManageLoad(page, '#mgmt-net-mac', 20000);

    const mac = await page.locator('#mgmt-net-mac').textContent();
    expect(mac).toBeTruthy();
    expect(mac).not.toBe('--');
  });

  // -------------------------------------------------------------------------
  // Manage tab: security check
  // -------------------------------------------------------------------------

  test('security check shows real credential status', async ({ page }) => {
    await openManageTab(page);
    const status = page.locator('#mgmt-security-status');
    await expect(status).not.toHaveText('--', { timeout: 25000 });
    await expect(status).not.toHaveText('Checking credentials...', { timeout: 25000 });

    const text = await status.textContent();
    expect(text).toBeTruthy();
    // Should show either "default credentials" warning or "changed" message
    const isResolvedResult =
      text!.toLowerCase().includes('credentials') ||
      text!.toLowerCase().includes('changed') ||
      text!.toLowerCase().includes('password') ||
      text!.toLowerCase().includes('requires serial');
    expect(isResolvedResult).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Manage tab: notifications
  // -------------------------------------------------------------------------

  test('notifications loads real config data', async ({ page }) => {
    await openManageTab(page);

    const traps = page.locator('#mgmt-notif-traps');
    await expect(traps).not.toHaveText('--', { timeout: 20000 });
    const text = await traps.textContent();
    expect(text).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // Manage tab: event log
  // -------------------------------------------------------------------------

  test('event log shows real entries', async ({ page }) => {
    await openManageTab(page);

    const eventlog = page.locator('#mgmt-eventlog');
    await expect(eventlog).not.toContainText('Loading...', { timeout: 20000 });
    const text = await eventlog.textContent();
    expect(text).toBeTruthy();
    expect(text).not.toBe('Loading...');
  });

  // -------------------------------------------------------------------------
  // Automation rules: create/persist
  // -------------------------------------------------------------------------

  test('create automation rule persists across page reload', async ({ page }) => {
    // Clean up any existing test rule
    await page.request.delete('/api/rules/hw-test-rule').catch(() => {});

    // Create a rule
    const resp = await page.request.post('/api/rules', {
      data: {
        name: 'hw-test-rule',
        input: 1,
        condition: 'voltage_below',
        threshold: 80,
        outlet: 1,
        action: 'off',
        delay: 30,
        restore: true,
      },
    });
    expect(resp.ok()).toBeTruthy();

    // Verify via API
    const listResp = await page.request.get('/api/rules');
    const rules = await listResp.json();
    expect(rules.some((r: any) => r.name === 'hw-test-rule')).toBe(true);

    // Reload page and verify rule shows in UI
    await page.reload();
    await waitForBridgeReady(page, 15000);
    await expect(page.locator('#rules-body')).toContainText('hw-test-rule', { timeout: 10000 });

    // Clean up
    await page.request.delete('/api/rules/hw-test-rule');
  });

  // -------------------------------------------------------------------------
  // All manage sections load without errors
  // -------------------------------------------------------------------------

  test('all manage tab sections load without error text', async ({ page }) => {
    await openManageTab(page);

    // Give all sections time to load (serial operations can be slow)
    await page.waitForTimeout(5000);

    // None of the sections should show "Error loading"
    const manageTab = page.locator('#tab-manage');
    const content = await manageTab.textContent();
    expect(content).not.toContain('Error loading');
  });
});
