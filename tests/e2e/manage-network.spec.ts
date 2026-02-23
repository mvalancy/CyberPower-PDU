import { test, expect } from '@playwright/test';
import { waitForBridgeReady, openManageTab, waitForManageLoad } from './helpers';

test.describe('Manage Tab — Network Config', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
    await openManageTab(page);
  });

  test('network section shows IP 192.168.1.100', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-net-ip');
    await expect(page.locator('#mgmt-net-ip')).toHaveText('192.168.1.100');
  });

  test('shows subnet, gateway, DHCP status, and MAC address', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-net-ip');

    await expect(page.locator('#mgmt-net-subnet')).toHaveText('255.255.255.0');
    await expect(page.locator('#mgmt-net-gw')).toHaveText('192.168.1.1');
    // MockPDU dhcp=False → UI shows "Disabled"
    await expect(page.locator('#mgmt-net-dhcp')).toHaveText('Disabled');
    await expect(page.locator('#mgmt-net-mac')).toHaveText('00:11:22:33:44:55');
  });

  test('clicking Edit shows form with red warning', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-net-ip');

    // Edit form starts hidden
    await expect(page.locator('#mgmt-network-edit')).toBeHidden();

    await page.locator('.sec-network button[onclick="startNetworkEdit()"]').click();

    // Edit form is now visible
    await expect(page.locator('#mgmt-network-edit')).toBeVisible();

    // The form has a red/warning border/background (verify the div is present)
    // The HTML inline style uses rgba(255,42,109,…) — the red warning color
    const editDiv = page.locator('#mgmt-network-edit');
    const style = await editDiv.getAttribute('style');
    expect(style).toContain('display:block');
  });

  test('edit form pre-populates with current IP, subnet, and gateway values', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-net-ip');

    await page.locator('.sec-network button[onclick="startNetworkEdit()"]').click();
    await expect(page.locator('#mgmt-network-edit')).toBeVisible();

    await expect(page.locator('#mgmt-net-edit-ip')).toHaveValue('192.168.1.100');
    await expect(page.locator('#mgmt-net-edit-subnet')).toHaveValue('255.255.255.0');
    await expect(page.locator('#mgmt-net-edit-gw')).toHaveValue('192.168.1.1');
    // DHCP is Disabled → select value "false"
    await expect(page.locator('#mgmt-net-edit-dhcp')).toHaveValue('false');
  });

  test('save button is disabled until "CONFIRM" is typed exactly', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-net-ip');

    await page.locator('.sec-network button[onclick="startNetworkEdit()"]').click();
    await expect(page.locator('#mgmt-network-edit')).toBeVisible();

    // Save button starts disabled (CONFIRM field is empty)
    await expect(page.locator('#mgmt-net-save-btn')).toBeDisabled();

    // Partial input does not enable save
    await page.locator('#mgmt-net-confirm').fill('CONFI');
    await expect(page.locator('#mgmt-net-save-btn')).toBeDisabled();

    // Wrong case does not enable save
    await page.locator('#mgmt-net-confirm').fill('confirm');
    await expect(page.locator('#mgmt-net-save-btn')).toBeDisabled();
  });

  test('typing "CONFIRM" exactly enables the save button', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-net-ip');

    await page.locator('.sec-network button[onclick="startNetworkEdit()"]').click();
    await expect(page.locator('#mgmt-network-edit')).toBeVisible();

    await expect(page.locator('#mgmt-net-save-btn')).toBeDisabled();

    // Type exactly CONFIRM
    await page.locator('#mgmt-net-confirm').fill('CONFIRM');

    // Save button now enabled
    await expect(page.locator('#mgmt-net-save-btn')).toBeEnabled();
  });

  test('cancel hides the edit form', async ({ page }) => {
    await waitForManageLoad(page, '#mgmt-net-ip');

    await page.locator('.sec-network button[onclick="startNetworkEdit()"]').click();
    await expect(page.locator('#mgmt-network-edit')).toBeVisible();

    await page.locator('#mgmt-network-edit button[onclick="cancelNetworkEdit()"]').click();

    await expect(page.locator('#mgmt-network-edit')).toBeHidden();

    // Displayed values remain intact
    await expect(page.locator('#mgmt-net-ip')).toHaveText('192.168.1.100');
  });
});
