import { test, expect } from '@playwright/test';
import { waitForBridgeReady } from './helpers';

/**
 * Dashboard security banner tests.
 *
 * The bridge runs in mock mode. MockPDU.check_default_credentials() returns
 * True (admin password is "cyber"). The PDUPoller calls _check_default_creds()
 * on its first successful poll, which:
 *   1. Sets _default_creds_active = True
 *   2. Adds a "security_warning" system event via web.add_system_event()
 *
 * The /api/status response then includes { default_credentials_active: true }
 * and the UI shows #security-banner.
 *
 * After POST /api/pdu/security/password changes the admin password to
 * something other than "cyber", a fresh credential check returns False
 * and the banner is hidden.
 *
 * UI selectors:
 *   #security-banner          — <div class="status-banner warning">
 *   #security-banner-text     — <li> inside the banner
 *   #events-list              — rendered events list
 *   .ev-tag.security_warning  — event tag span for security_warning type
 */

// ---------------------------------------------------------------------------
// Helper: wait for the security banner to become visible (style="display:block")
// ---------------------------------------------------------------------------
async function waitForSecurityBanner(page: import('@playwright/test').Page, timeout = 20000) {
  // The banner is hidden initially (display:none) and set to block when the
  // status response contains default_credentials_active === true.
  await expect(page.locator('#security-banner')).toBeVisible({ timeout });
}

// ---------------------------------------------------------------------------
// Helper: reset admin password back to "cyber" so each test is independent
// ---------------------------------------------------------------------------
async function resetPassword(page: import('@playwright/test').Page) {
  await page.request.post('/api/pdu/security/password', {
    data: { account: 'admin', password: 'cyber' },
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Dashboard — Security Banner', () => {
  test.beforeEach(async ({ page }) => {
    // Make sure we start with the factory default password so the banner shows
    await page.goto('/');
    await resetPassword(page);
    await waitForBridgeReady(page);
  });

  // -------------------------------------------------------------------------
  // 1. Security banner appears when default credentials are active
  // -------------------------------------------------------------------------
  test('security banner becomes visible after first poll with default credentials', async ({ page }) => {
    // The security banner is populated once the status response includes
    // default_credentials_active: true, which happens after the first poll.
    // Allow up to 20s for the first poll + credential check to complete.
    await waitForSecurityBanner(page);

    const banner = page.locator('#security-banner');
    await expect(banner).toBeVisible();
    // The banner must be the warning variant
    await expect(banner).toHaveClass(/warning/);
  });

  // -------------------------------------------------------------------------
  // 2. Banner text mentions factory default credentials
  // -------------------------------------------------------------------------
  test('security banner text mentions "cyber/cyber" or "default credentials"', async ({ page }) => {
    await waitForSecurityBanner(page);

    const bannerText = page.locator('#security-banner-text');
    await expect(bannerText).toBeVisible();

    const text = (await bannerText.textContent()) ?? '';
    const mentionsDefaults =
      text.toLowerCase().includes('default credentials') ||
      text.toLowerCase().includes('cyber/cyber') ||
      text.toLowerCase().includes('factory default');
    expect(mentionsDefaults).toBe(true);
  });

  // -------------------------------------------------------------------------
  // 3. Banner contains a clickable element that navigates toward Security settings
  // -------------------------------------------------------------------------
  test('security banner is clickable and opens Settings panel', async ({ page }) => {
    await waitForSecurityBanner(page);

    // The banner has onclick="openSettings();switchSettingsTab(...,'tab-manage')"
    // Clicking it should open the settings panel
    await page.locator('#security-banner').click();

    const settingsPanel = page.locator('#settings-panel');
    await expect(settingsPanel).toHaveClass(/open/, { timeout: 5000 });
  });

  // -------------------------------------------------------------------------
  // 4. After changing password via API, security banner is hidden
  // -------------------------------------------------------------------------
  test('security banner disappears after password is changed from defaults', async ({ page }) => {
    // Wait for the banner to appear first (confirms default creds are detected)
    await waitForSecurityBanner(page);

    // Change the password via API (simulates completing the security task)
    const resp = await page.request.post('/api/pdu/security/password', {
      data: { account: 'admin', password: 'Secur3dN0wABC!' },
    });
    expect(resp.ok()).toBeTruthy();

    // The UI polls /api/status periodically (BRIDGE_POLL_INTERVAL=1 in test
    // mode). After the next poll, _check_default_creds runs again and the
    // status response includes default_credentials_active: false.
    // The renderStatus() function hides the banner when this is false.
    //
    // We allow up to 15s for the next poll cycle to fire and update the DOM.
    await expect(page.locator('#security-banner')).toBeHidden({ timeout: 15000 });
  });

  // -------------------------------------------------------------------------
  // 5. System events panel shows a security_warning type event
  // -------------------------------------------------------------------------
  test('events list contains a security_warning event after first poll', async ({ page }) => {
    // Wait for polling to establish — the credential check fires on first poll
    await waitForBridgeReady(page);

    // Allow a couple of seconds for the first poll + credential check to
    // emit the security_warning system event
    await page.waitForTimeout(3000);

    // Verify via the API that a security_warning event was emitted
    const eventsResp = await page.request.get('/api/events');
    expect(eventsResp.ok()).toBeTruthy();

    const events: { type: string; rule: string; details: string }[] = await eventsResp.json();
    const securityEvents = events.filter(e => e.type === 'security_warning');
    expect(securityEvents.length).toBeGreaterThan(0);

    // The event details should mention default credentials
    const details = securityEvents[0].details.toLowerCase();
    const isRelevant =
      details.includes('default credentials') ||
      details.includes('cyber/cyber') ||
      details.includes('security');
    expect(isRelevant).toBe(true);
  });

  // -------------------------------------------------------------------------
  // 6. Events panel in the UI renders the security_warning event tag
  // -------------------------------------------------------------------------
  test('UI events panel renders a SECURITY WARNING event row', async ({ page }) => {
    // Give the poller time to run the first credential check
    await page.waitForTimeout(3000);

    const eventsList = page.locator('#events-list');
    await expect(eventsList).toBeVisible();

    // The security_warning event type renders as a span with class "security_warning"
    // and label "SECURITY WARNING" (fallback: type.toUpperCase().replace(/_/g,' '))
    const securityTag = eventsList.locator('.ev-tag.security_warning');
    await expect(securityTag).toBeVisible({ timeout: 10000 });

    const tagText = (await securityTag.textContent()) ?? '';
    expect(tagText).toContain('SECURITY');
  });
});
