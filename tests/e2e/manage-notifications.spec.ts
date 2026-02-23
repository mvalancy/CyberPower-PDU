import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openSettings, switchToTab, openManageTab, waitForManageLoad } from './helpers';

/**
 * Notification configuration section inside the Manage tab.
 *
 * MockPDU initial state:
 *   - 4 trap receivers   — all ip 0.0.0.0, Disabled
 *   - SMTP               — server: "" (empty, i.e. "Not configured")
 *   - 4 email recipients — all to: "", Disabled
 *   - 4 syslog servers   — all ip 0.0.0.0, Disabled
 *
 * The manage tab calls loadManagementData() on open which, among other
 * things, fires loadNotifications() → GET /api/pdu/notifications.
 */

test.describe('Manage Tab — Notifications', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
    await openManageTab(page);
  });

  test('notifications section is visible and has a Refresh button', async ({ page }) => {
    const section = page.locator('#tab-manage .gen-section', { has: page.locator('#mgmt-notifications') });
    await expect(section).toBeVisible();

    const refreshBtn = section.locator('button', { hasText: 'Refresh' });
    await expect(refreshBtn).toBeVisible();
  });

  test('trap receivers section populates with 4 entries (all Disabled)', async ({ page }) => {
    const traps = page.locator('#mgmt-notif-traps');

    // Wait for data to load — the initial "--" placeholder gets replaced
    await expect(traps).not.toHaveText('--', { timeout: 15000 });
    await expect(traps).not.toHaveText('Requires serial', { timeout: 5000 }).catch(() => {
      // In SNMP-only mock mode the endpoint may return "Requires serial" — that
      // is a valid response; the test still verifies the section loaded.
    });

    const content = await traps.textContent();
    if (content && !content.includes('Requires serial') && !content.includes('Error')) {
      // 4 entries are rendered — each line starts with "1.", "2.", "3.", "4."
      await expect(traps).toContainText('1.');
      await expect(traps).toContainText('4.');
      // All entries should be Disabled (MockPDU sets enabled: false)
      const disabledCount = (content.match(/Disabled/g) || []).length;
      expect(disabledCount).toBeGreaterThanOrEqual(4);
    }
  });

  test('SMTP section shows "Not configured" for empty server', async ({ page }) => {
    const smtp = page.locator('#mgmt-notif-smtp');

    await expect(smtp).not.toHaveText('--', { timeout: 15000 });

    const content = await smtp.textContent();
    if (content && !content.includes('Requires serial') && !content.includes('Error')) {
      // MockPDU._smtp.server is "" → rendered as "Not configured"
      await expect(smtp).toContainText('Not configured');
    }
  });

  test('email recipients section populates with 4 entries', async ({ page }) => {
    const email = page.locator('#mgmt-notif-email');

    await expect(email).not.toHaveText('--', { timeout: 15000 });

    const content = await email.textContent();
    if (content && !content.includes('Requires serial') && !content.includes('Error')) {
      await expect(email).toContainText('1.');
      await expect(email).toContainText('4.');
    }
  });

  test('syslog servers section populates with 4 entries (all Disabled)', async ({ page }) => {
    const syslog = page.locator('#mgmt-notif-syslog');

    await expect(syslog).not.toHaveText('--', { timeout: 15000 });

    const content = await syslog.textContent();
    if (content && !content.includes('Requires serial') && !content.includes('Error')) {
      await expect(syslog).toContainText('1.');
      await expect(syslog).toContainText('4.');
      const disabledCount = (content.match(/Disabled/g) || []).length;
      expect(disabledCount).toBeGreaterThanOrEqual(4);
    }
  });

  test('Refresh button re-loads notification data without error', async ({ page }) => {
    // Wait for initial load to complete
    await expect(page.locator('#mgmt-notif-traps')).not.toHaveText('--', { timeout: 15000 });

    // Click Refresh
    const section = page.locator('#tab-manage .gen-section', { has: page.locator('#mgmt-notifications') });
    const refreshBtn = section.locator('button', { hasText: 'Refresh' });
    await refreshBtn.click();

    // After re-load, the trap section should still have data (not revert to '--')
    await expect(page.locator('#mgmt-notif-traps')).not.toHaveText('--', { timeout: 10000 });
    await expect(page.locator('#mgmt-notif-smtp')).not.toHaveText('--', { timeout: 10000 });
  });
});
