const fs = require('node:fs');
const { chromium } = require('/private/tmp/gbc-reanalyze-review-euwrvxvq/app/node_modules/playwright');
const OUT = __dirname;
const capabilities = { enabled: true, default_use_ai: false, active_profile_id: 'model-b', profiles: [{ id: 'model-b', name: '当前模型B', model: 'model-b' }] };
const base = {
  job_id: 'review-job',
  inherit: { available: true, unknown_fields: [], message: null, mode: 'dual', use_local_rules: true, use_ai_assist: false, ai_profile_id: null, ai_configuration: null },
  last_execution: { status: 'done', analysis_conclusion: null, ai_requested: false, local_state: 'completed', ai_state: 'skipped' },
  model: { state: 'not_applicable', message: null, profile: null },
  capabilities,
};
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const results = [];
  try {
    for (const kind of ['local-control', 'history-unknown', 'model-deleted']) {
      const data = JSON.parse(JSON.stringify(base));
      if (kind === 'history-unknown') {
        Object.assign(data.inherit, { available: false, unknown_fields: ['mode', 'use_local_rules', 'use_ai_assist'], message: '该材料没有已记录的上次检查方式，请显式选择本次检查方式', mode: null, use_local_rules: null, use_ai_assist: null });
      }
      if (kind === 'model-deleted') {
        Object.assign(data.inherit, { use_ai_assist: true, ai_profile_id: 'model-a', ai_configuration: { id: 'model-a', name: '原模型A', model: 'model-a' } });
        data.model = { state: 'deleted', message: '上次使用的模型「原模型A」已删除，请指定本次模型', profile: null };
      }
      const page = await browser.newPage({ viewport: { width: 1200, height: 850 } });
      const pageErrors = [];
      page.on('pageerror', error => pageErrors.push(String(error)));
      await page.route('**/api/**', async route => {
        const path = new URL(route.request().url()).pathname;
        const body = path.endsWith('/reanalyze-options') ? data : path.endsWith('/capabilities') ? capabilities : {};
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      });
      await page.goto('http://127.0.0.1:18746/e2e/reanalyze-independent', { waitUntil: 'networkidle', timeout: 90000 });
      const confirm = page.getByTestId('reanalyze-method-confirm');
      await confirm.waitFor();
      if (kind === 'local-control') {
        await page.waitForFunction(() => !document.querySelector('[data-testid="reanalyze-method-confirm"]').disabled);
      } else {
        await page.waitForFunction(() => document.querySelector('[data-testid="reanalyze-method-picker-ai"]')?.checked === true);
      }
      const beforeInteraction = {
        inheritChecked: await page.getByTestId('reanalyze-method-picker-inherit').isChecked(),
        aiChecked: await page.getByTestId('reanalyze-method-picker-ai').isChecked(),
        confirmEnabled: await confirm.isEnabled(),
      };
      await confirm.click();
      const submitted = await page.getByTestId('submitted').textContent();
      await page.screenshot({ path: `${OUT}/${kind}.png`, fullPage: true });
      results.push({ kind, beforeInteraction, submitted: JSON.parse(submitted), pageErrors });
      await page.close();
    }
  } finally { await browser.close(); }
  fs.writeFileSync(`${OUT}/browser-probe-results.json`, JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
