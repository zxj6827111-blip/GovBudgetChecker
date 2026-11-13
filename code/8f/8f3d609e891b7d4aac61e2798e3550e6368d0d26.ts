import fs from 'node:fs';
import { expect, test } from '../../app/node_modules/playwright/test';

test('all six department batch warnings must remain accessible', async ({ page }) => {
  test.setTimeout(60_000);
  await page.context().addCookies([{ name: 'gbc_session', value: 'isolated-review', url: 'http://127.0.0.1:18746', sameSite: 'Lax' }]);
  const warnings = Array.from({ length: 6 }, (_, i) => `REVIEW-WARNING-${i + 1}：原模型配置已变化`);
  const jobs = warnings.map((_, i) => ({
    job_id: `review-limit-${i + 1}`, filename: `材料${i + 1}预算.pdf`, status: 'done',
    report_kind: 'budget', report_year: 2025, organization_id: 'dept-review', organization_name: '独立复验部门', merged_issue_total: 0,
  }));
  const messages: string[] = [];
  page.on('dialog', async dialog => { messages.push(dialog.message()); await dialog.accept(); });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let payload: unknown = {};
    if (path === '/api/auth/me') payload = { user: { username: 'reviewer', is_admin: false } };
    if (path === '/api/organizations/list') payload = { organizations: [{ id: 'dept-review', name: '独立复验部门', level: 'department' }] };
    if (path === '/api/organizations/dept-review/jobs') payload = { jobs, total: jobs.length, limit: 80, offset: 0 };
    const match = path.match(/^\/api\/jobs\/review-limit-(\d+)\/reanalyze$/);
    if (match && request.method() === 'POST') payload = { job_id: `review-limit-${match[1]}`, status: 'queued', inheritance_warnings: [warnings[Number(match[1]) - 1]] };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
  });
  await page.goto('/department/dept-review');
  for (const job of jobs) await page.getByTestId(`job-select-${job.job_id}`).check();
  await page.getByTestId('batch-reanalyze-button').click();
  await expect(page.getByTestId('reanalyze-method-dialog')).toBeVisible();
  await page.getByTestId('reanalyze-method-confirm').click();
  await expect.poll(() => messages.length).toBeGreaterThan(0);
  const bodyText = await page.locator('body').innerText();
  const result = { messages, visibleText: bodyText, sixthWarningVisible: (messages.join('\n') + bodyText).includes(warnings[5]) };
  fs.writeFileSync('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_reanalyze_inherit_20261109_r3/batch-six-warning-results.json', JSON.stringify(result, null, 2));
  await page.screenshot({ path: '/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_reanalyze_inherit_20261109_r3/batch-six-warning.png', fullPage: true });
  expect(result.sixthWarningVisible, '第6条告警需要可见，或提供可展开的完整列表；不能只提示去没有该告警的队列查看').toBe(true);
});
