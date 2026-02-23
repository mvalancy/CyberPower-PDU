import { test, expect } from '@playwright/test';
import { waitForBridgeReady } from './helpers';

/**
 * Automation Rules section on the main dashboard.
 *
 * UI selectors:
 *   #rules-body           — <tbody> of the rules table
 *   .btn-add[onclick]     — "Add Rule" button (calls toggleForm())
 *   #rule-form-wrap       — wrapper div; class "open" = visible
 *   #form-title           — "New Rule" | "Edit Rule"
 *   #form-submit          — submit button ("Create Rule" | "Update Rule")
 *   #f-name               — rule name input (readOnly when editing)
 *   #f-input              — input source select (1 = A, 2 = B, 0 = N/A)
 *   #f-condition          — condition select
 *   #f-threshold          — threshold value
 *   #f-outlet             — outlet(s) — comma-sep or range
 *   #f-action             — action select (off | on)
 *   #f-delay              — delay in seconds
 *   #f-restore            — checkbox: restore on recovery
 *   #f-oneshot            — checkbox: one-shot (auto-disable)
 *   input[name="dow"]     — days-of-week checkboxes (values 0-6, Mon-Sun)
 *
 * API:
 *   GET    /api/rules              — list
 *   POST   /api/rules              — create
 *   PUT    /api/rules/{name}       — update
 *   DELETE /api/rules/{name}       — delete
 *   PUT    /api/rules/{name}/toggle — enable / disable
 *
 * The bridge is started in BRIDGE_MOCK_MODE=true by playwright.config.ts so
 * a real automation engine is attached to the mock device.
 */

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openRuleForm(page: import('@playwright/test').Page) {
  const addBtn = page.locator('.btn-add[onclick*="toggleForm"]');
  await addBtn.click();
  await expect(page.locator('#rule-form-wrap')).toHaveClass(/open/);
}

async function fillBasicRule(
  page: import('@playwright/test').Page,
  opts: {
    name?: string;
    condition?: string;
    threshold?: string;
    outlet?: string;
    action?: string;
    delay?: string;
  } = {},
) {
  const {
    name = 'test-rule',
    condition = 'voltage_below',
    threshold = '100',
    outlet = '1',
    action = 'off',
    delay = '5',
  } = opts;

  await page.fill('#f-name', name);
  await page.selectOption('#f-condition', condition);
  await page.fill('#f-threshold', threshold);
  await page.fill('#f-outlet', outlet);
  await page.selectOption('#f-action', action);
  await page.fill('#f-delay', delay);
}

async function submitForm(page: import('@playwright/test').Page) {
  await page.locator('#form-submit').click();
  // Wait for form to close (successful submit closes it)
  await expect(page.locator('#rule-form-wrap')).not.toHaveClass(/open/, { timeout: 8000 });
}

async function deleteAllRules(page: import('@playwright/test').Page) {
  // Clean up via API so each test starts with an empty rules list
  const resp = await page.request.get('/api/rules');
  const rules: { name: string }[] = await resp.json();
  for (const r of rules) {
    await page.request.delete(`/api/rules/${encodeURIComponent(r.name)}`);
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Automation Rules', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForBridgeReady(page);
    // Clean up any rules left by a previous test
    await deleteAllRules(page);
    // Trigger a UI poll so the table reflects the empty state
    await page.waitForTimeout(500);
  });

  // -------------------------------------------------------------------------
  // 1. Empty state
  // -------------------------------------------------------------------------
  test('rules table shows "No automation rules" when no rules exist', async ({ page }) => {
    const tbody = page.locator('#rules-body');
    await expect(tbody).toContainText('No automation rules', { timeout: 8000 });
  });

  // -------------------------------------------------------------------------
  // 2. New Rule button opens the form
  // -------------------------------------------------------------------------
  test('"Add Rule" button opens the rule form', async ({ page }) => {
    await expect(page.locator('#rule-form-wrap')).not.toHaveClass(/open/);
    await openRuleForm(page);
    await expect(page.locator('#rule-form-wrap')).toHaveClass(/open/);
  });

  // -------------------------------------------------------------------------
  // 3. Form has all expected fields
  // -------------------------------------------------------------------------
  test('rule form contains all expected input fields', async ({ page }) => {
    await openRuleForm(page);

    await expect(page.locator('#f-name')).toBeVisible();
    await expect(page.locator('#f-input')).toBeVisible();
    await expect(page.locator('#f-condition')).toBeVisible();
    await expect(page.locator('#f-threshold')).toBeVisible();
    await expect(page.locator('#f-outlet')).toBeVisible();
    await expect(page.locator('#f-action')).toBeVisible();
    await expect(page.locator('#f-delay')).toBeVisible();
    await expect(page.locator('#f-restore')).toBeVisible();
    await expect(page.locator('#f-oneshot')).toBeVisible();

    // Days-of-week checkboxes (7 total: Mon=0 … Sun=6)
    const dowBoxes = page.locator('input[name="dow"]');
    await expect(dowBoxes).toHaveCount(7);
  });

  // -------------------------------------------------------------------------
  // 4. Creating a rule adds a row to the table
  // -------------------------------------------------------------------------
  test('created rule appears in the rules table', async ({ page }) => {
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'my-voltage-rule' });
    await submitForm(page);

    // After form closes, the poll cycle updates the table
    await expect(page.locator('#rules-body')).toContainText('my-voltage-rule', { timeout: 8000 });
  });

  // -------------------------------------------------------------------------
  // 5. Table row shows correct name, condition, action
  // -------------------------------------------------------------------------
  test('rule row shows correct name, condition text, and action', async ({ page }) => {
    await openRuleForm(page);
    await fillBasicRule(page, {
      name: 'voltage-check',
      condition: 'voltage_below',
      threshold: '108',
      outlet: '2',
      action: 'off',
    });
    await submitForm(page);

    const tbody = page.locator('#rules-body');
    await expect(tbody).toContainText('voltage-check', { timeout: 8000 });
    // Condition cell: "Input 1 voltage < 108V"
    await expect(tbody).toContainText('108');
    // Action cell: "OFF"
    await expect(tbody).toContainText('OFF');
  });

  // -------------------------------------------------------------------------
  // 6. Days-of-week checkboxes → day abbreviations appear in the row
  // -------------------------------------------------------------------------
  test('selected days-of-week appear as [Mon,Wed] pills in the rule row', async ({ page }) => {
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'dow-rule' });

    // Check Mon (0) and Wed (2)
    await page.locator('input[name="dow"][value="0"]').check();
    await page.locator('input[name="dow"][value="2"]').check();
    await submitForm(page);

    const tbody = page.locator('#rules-body');
    await expect(tbody).toContainText('dow-rule', { timeout: 8000 });
    // Days-of-week are rendered as "[Mon,Wed]" inside the name cell
    await expect(tbody).toContainText('Mon');
    await expect(tbody).toContainText('Wed');
  });

  // -------------------------------------------------------------------------
  // 7. One-shot checkbox → "ONE-SHOT" badge in the row
  // -------------------------------------------------------------------------
  test('oneshot checkbox produces "ONE-SHOT" badge in the rule row', async ({ page }) => {
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'oneshot-rule' });
    await page.locator('#f-oneshot').check();
    await submitForm(page);

    const tbody = page.locator('#rules-body');
    await expect(tbody).toContainText('oneshot-rule', { timeout: 8000 });
    await expect(tbody).toContainText('ONE-SHOT');
  });

  // -------------------------------------------------------------------------
  // 8. Multi-outlet "1,3,5" → shown in action column
  // -------------------------------------------------------------------------
  test('multi-outlet "1,3,5" is stored and shown in the table', async ({ page }) => {
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'multi-outlet', outlet: '1,3,5' });
    await submitForm(page);

    const tbody = page.locator('#rules-body');
    await expect(tbody).toContainText('multi-outlet', { timeout: 8000 });
    // The outlet column renders arrays as comma-joined strings
    await expect(tbody).toContainText('1,3,5');
  });

  // -------------------------------------------------------------------------
  // 9. Outlet range "1-4" → stored and displayed
  // -------------------------------------------------------------------------
  test('outlet range "1-4" is accepted and displayed in the table', async ({ page }) => {
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'range-rule', outlet: '1-4' });
    await submitForm(page);

    const tbody = page.locator('#rules-body');
    await expect(tbody).toContainText('range-rule', { timeout: 8000 });
    // Range is parsed to an array [1,2,3,4] → rendered "1,2,3,4"
    // OR the raw string "1-4" might pass through — either is acceptable
    const rowText = await tbody.textContent();
    const hasRange = rowText?.includes('1,2,3,4') || rowText?.includes('1-4');
    expect(hasRange).toBe(true);
  });

  // -------------------------------------------------------------------------
  // 10. Edit: form pre-populates with existing rule values; name is read-only
  // -------------------------------------------------------------------------
  test('editing a rule pre-populates the form with correct values', async ({ page }) => {
    // Create a rule first
    await openRuleForm(page);
    await fillBasicRule(page, {
      name: 'edit-me',
      condition: 'voltage_above',
      threshold: '130',
      outlet: '3',
      action: 'on',
      delay: '10',
    });
    await submitForm(page);
    await expect(page.locator('#rules-body')).toContainText('edit-me', { timeout: 8000 });

    // Click Edit button for this rule
    const editBtn = page.locator('#rules-body tr', { hasText: 'edit-me' }).locator('button', { hasText: 'Edit' });
    await editBtn.click();

    // Form should open in edit mode
    await expect(page.locator('#rule-form-wrap')).toHaveClass(/open/);
    await expect(page.locator('#form-title')).toHaveText('Edit Rule');

    // Name field should be read-only and contain the rule name
    const nameInput = page.locator('#f-name');
    await expect(nameInput).toHaveValue('edit-me');
    await expect(nameInput).toHaveAttribute('readonly', '');

    // Other fields should be populated
    await expect(page.locator('#f-condition')).toHaveValue('voltage_above');
    await expect(page.locator('#f-threshold')).toHaveValue('130');
    await expect(page.locator('#f-action')).toHaveValue('on');
    await expect(page.locator('#f-delay')).toHaveValue('10');
  });

  // -------------------------------------------------------------------------
  // 11. Updating a rule shows toast and reflects new values
  // -------------------------------------------------------------------------
  test('updating rule threshold shows success toast and updates table', async ({ page }) => {
    // Create rule
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'update-me', threshold: '110' });
    await submitForm(page);
    await expect(page.locator('#rules-body')).toContainText('update-me', { timeout: 8000 });

    // Edit it
    const editBtn = page.locator('#rules-body tr', { hasText: 'update-me' }).locator('button', { hasText: 'Edit' });
    await editBtn.click();
    await expect(page.locator('#rule-form-wrap')).toHaveClass(/open/);

    // Change threshold
    await page.fill('#f-threshold', '95');
    await page.locator('#form-submit').click();

    // Toast should appear
    const toast = page.locator('.toast, [class*="toast"]').first();
    await expect(toast).toBeVisible({ timeout: 8000 });

    // Table should now show the new threshold
    await expect(page.locator('#rules-body')).toContainText('95', { timeout: 8000 });
  });

  // -------------------------------------------------------------------------
  // 12. Delete rule: confirm dialog → rule removed
  // -------------------------------------------------------------------------
  test('deleting a rule (after confirm) removes it from the table', async ({ page }) => {
    // Create rule
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'delete-me' });
    await submitForm(page);
    await expect(page.locator('#rules-body')).toContainText('delete-me', { timeout: 8000 });

    // Accept the browser confirm dialog
    page.on('dialog', dialog => dialog.accept());

    const deleteBtn = page.locator('#rules-body tr', { hasText: 'delete-me' }).locator('button', { hasText: 'Delete' });
    await deleteBtn.click();

    // Rule should be gone
    await expect(page.locator('#rules-body')).not.toContainText('delete-me', { timeout: 8000 });
  });

  // -------------------------------------------------------------------------
  // 13. Toggle enable/disable → row opacity changes
  // -------------------------------------------------------------------------
  test('disabling a rule applies opacity:0.5 to the row', async ({ page }) => {
    // Create rule
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'toggle-me' });
    await submitForm(page);

    const row = page.locator('#rules-body tr', { hasText: 'toggle-me' });
    await expect(row).toBeVisible({ timeout: 8000 });

    // Initially enabled — no opacity:0.5
    const styleBefore = await row.getAttribute('style');
    expect(styleBefore).not.toContain('opacity:0.5');

    // Uncheck the enable checkbox to disable
    const enableCheckbox = row.locator('input[type="checkbox"]');
    await enableCheckbox.uncheck();

    // After toggle, row should have opacity:0.5
    await expect(row).toHaveAttribute('style', /opacity:0\.5/, { timeout: 8000 });
  });

  // -------------------------------------------------------------------------
  // 14. Cancel button closes the form without creating a rule
  // -------------------------------------------------------------------------
  test('Cancel button closes the form and no rule is created', async ({ page }) => {
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'cancelled-rule' });

    // Click Cancel (the "ghost" button inside the form)
    await page.locator('#rule-form-wrap .btn.btn-ghost', { hasText: 'Cancel' }).click();

    // Form should close
    await expect(page.locator('#rule-form-wrap')).not.toHaveClass(/open/);

    // No row for the rule that was never submitted
    await expect(page.locator('#rules-body')).not.toContainText('cancelled-rule');
  });

  // -------------------------------------------------------------------------
  // 15. Form title switches between "New Rule" and "Edit Rule"
  // -------------------------------------------------------------------------
  test('form title changes between "New Rule" when creating and "Edit Rule" when editing', async ({ page }) => {
    // Open for creation
    await openRuleForm(page);
    await expect(page.locator('#form-title')).toHaveText('New Rule');
    await expect(page.locator('#form-submit')).toHaveText('Create Rule');

    // Close form
    await page.locator('#rule-form-wrap .btn.btn-ghost', { hasText: 'Cancel' }).click();

    // Create a rule so we can edit it
    await openRuleForm(page);
    await fillBasicRule(page, { name: 'title-test' });
    await submitForm(page);
    await expect(page.locator('#rules-body')).toContainText('title-test', { timeout: 8000 });

    // Edit that rule
    const editBtn = page.locator('#rules-body tr', { hasText: 'title-test' }).locator('button', { hasText: 'Edit' });
    await editBtn.click();

    await expect(page.locator('#form-title')).toHaveText('Edit Rule');
    await expect(page.locator('#form-submit')).toHaveText('Update Rule');
  });
});
