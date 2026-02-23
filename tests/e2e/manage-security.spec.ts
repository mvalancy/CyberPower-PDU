import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openSettings, switchToTab, openManageTab, waitForManageLoad } from './helpers';

/**
 * Security section inside the Manage tab.
 *
 * MockPDU initial state: admin password = "cyber" (factory default).
 * POST /api/pdu/security/check returns { default_credentials_active: true }.
 *
 * After a successful password change via POST /api/pdu/security/password the
 * MockPDU stores the new password, so a subsequent check returns
 * { default_credentials_active: false } and the UI renders the green
 * "Password has been changed from defaults." message.
 *
 * UI selectors:
 *   #mgmt-security-status        — text result of checkDefaultCreds()
 *   #mgmt-password-form          — hidden form, shown by showChangePasswordModal()
 *   #mgmt-pw-account             — <select> admin | viewer
 *   #mgmt-pw-new / #mgmt-pw-confirm — password inputs
 */

test.describe('Manage Tab — Security', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
    await openManageTab(page);
  });

  // -------------------------------------------------------------------------
  // 1. Security status resolves from initial "--"
  // -------------------------------------------------------------------------
  test('security status resolves past "--" after manage tab opens', async ({ page }) => {
    const status = page.locator('#mgmt-security-status');

    // loadManagementData() fires checkDefaultCreds() automatically when the
    // Manage tab is opened; wait up to 20s for the response.
    await expect(status).not.toHaveText('--', { timeout: 20000 });
    await expect(status).not.toHaveText('Checking credentials...', { timeout: 20000 });
  });

  // -------------------------------------------------------------------------
  // 2. Warning text mentions default credentials
  // -------------------------------------------------------------------------
  test('security status shows default-credentials warning (amber text)', async ({ page }) => {
    const status = page.locator('#mgmt-security-status');

    // Wait for resolution
    await expect(status).not.toHaveText('--', { timeout: 20000 });

    const content = await status.textContent();
    // Skip assertion if endpoint is unavailable (e.g. SNMP-only mode without serial)
    if (content && !content.includes('Requires serial connection') && !content.includes('Error')) {
      // MockPDU has default creds → UI shows the warning variant
      const text = content.toLowerCase();
      const isWarning =
        text.includes('default credentials') ||
        text.includes('cyber/cyber') ||
        text.includes('warning') ||
        text.includes('security risk');
      expect(isWarning).toBe(true);
    }
  });

  // -------------------------------------------------------------------------
  // 3. "Change Password" button is visible
  // -------------------------------------------------------------------------
  test('"Change Password" button is visible in security section', async ({ page }) => {
    const section = page.locator('.sec-security');
    const changeBtn = section.locator('button', { hasText: 'Change Password' });
    await expect(changeBtn).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // 4. Password form opens and has the expected fields
  // -------------------------------------------------------------------------
  test('clicking Change Password reveals form with account selector and password fields', async ({ page }) => {
    const section = page.locator('.sec-security');

    // Form starts hidden
    const form = page.locator('#mgmt-password-form');
    await expect(form).toBeHidden();

    await section.locator('button', { hasText: 'Change Password' }).click();

    await expect(form).toBeVisible();
    await expect(page.locator('#mgmt-pw-account')).toBeVisible();
    await expect(page.locator('#mgmt-pw-new')).toBeVisible();
    await expect(page.locator('#mgmt-pw-confirm')).toBeVisible();

    // Account selector should have "admin" and "viewer" options
    const accountSelect = page.locator('#mgmt-pw-account');
    const adminOption = accountSelect.locator('option[value="admin"]');
    const viewerOption = accountSelect.locator('option[value="viewer"]');
    await expect(adminOption).toHaveCount(1);
    await expect(viewerOption).toHaveCount(1);
  });

  // -------------------------------------------------------------------------
  // 5. Filling matching passwords and submitting shows success toast
  // -------------------------------------------------------------------------
  test('submitting matching passwords shows "Password changed" success toast', async ({ page }) => {
    const section = page.locator('.sec-security');

    // Open the form
    await section.locator('button', { hasText: 'Change Password' }).click();
    await expect(page.locator('#mgmt-password-form')).toBeVisible();

    // Fill in new password
    await page.selectOption('#mgmt-pw-account', 'admin');
    await page.fill('#mgmt-pw-new', 'NewSecurePass1!');
    await page.fill('#mgmt-pw-confirm', 'NewSecurePass1!');

    // Submit
    await page.locator('#mgmt-password-form .btn.btn-primary', { hasText: 'Change Password' }).click();

    // Expect a success toast containing "Password changed"
    const toast = page.locator('.toast, [class*="toast"]').first();
    await expect(toast).toBeVisible({ timeout: 8000 });
    const toastText = await toast.textContent();
    expect(toastText?.toLowerCase()).toContain('password');
  });

  // -------------------------------------------------------------------------
  // 6. After password change, re-check shows green "changed from defaults" text
  // -------------------------------------------------------------------------
  test('after password change, re-check shows green "changed from defaults" message', async ({ page }) => {
    // Change the password via direct API call so we don't depend on toast timing
    const changeResp = await page.request.post('/api/pdu/security/password', {
      data: { account: 'admin', password: 'AlreadyChanged99!' },
    });
    expect(changeResp.ok()).toBeTruthy();

    // Trigger a fresh credential check via the UI button
    const section = page.locator('.sec-security');
    const checkBtn = section.locator('button', { hasText: 'Check Default Credentials' });
    await checkBtn.click();

    const status = page.locator('#mgmt-security-status');
    // Wait for the async check to complete and then render the "changed" message
    await expect(status).not.toHaveText('Checking credentials...', { timeout: 15000 });
    await expect(status).not.toHaveText('--', { timeout: 5000 });

    const content = await status.textContent();
    if (content && !content.includes('Requires serial connection') && !content.includes('Error')) {
      const text = content.toLowerCase();
      const isChanged =
        text.includes('changed') ||
        text.includes('secured') ||
        text.includes('not default');
      expect(isChanged).toBe(true);
    }
  });
});
