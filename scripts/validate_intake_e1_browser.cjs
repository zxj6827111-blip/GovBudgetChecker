/* E1 使用真实页面和代理，不拦截网络或构造前端响应。 */
const { chromium } = require('../app/node_modules/@playwright/test');
const fs = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  let activePage;
  try {
    const context = await browser.newContext({ viewport: { width: 1500, height: 1100 } });
    const page = await context.newPage();
    activePage = page;
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(`${config.origin}/login?next=/materials`);
    await page.getByPlaceholder('例如：admin').fill('admin');
    await page.getByPlaceholder('请输入密码').fill(config.password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    // 查询串也含 /materials；必须按 pathname 等待，防止在登录响应前误判导航完成。
    await page.waitForURL(url => url.pathname === '/materials', { timeout: 90000 });
    assert.ok((await context.cookies()).some(cookie => cookie.name === 'gbc_session'));
    const results = [];
    for (const sample of config.samples) {
      await page.goto(`${config.origin}/task/${sample.job_id}`);
      await page.locator(`[data-analysis-revision="${sample.analysis_revision}"]`).waitFor({ timeout: 90000 });
      await page.getByText(/汇总口径待确认/).first().waitFor({ timeout: 30000 });
      let body = await page.locator('body').innerText();
      assert.match(body, /汇总口径待确认/);
      if (sample.id === 'E1-S') {
        await page.getByText('V33-240', { exact: true }).first().click();
        body = await page.locator('body').innerText();
        assert.match(body, /10\.00|10万元|10\.0/);
        assert.match(body, /208|收入决算表/);
      }
      const path = `${config.output}/${sample.id}-task.png`;
      await page.screenshot({ path, fullPage: true });
      results.push({ id: sample.id, job_id: sample.job_id, revision: sample.analysis_revision,
        scope_pending_visible: true, screenshot: path });
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(`${config.output}/browser-receipt.json`, JSON.stringify({ passed: true,
      real_api: true, network_interception: false, samples: results, page_errors: errors }, null, 2));
    await context.close();
  } catch (error) {
    if (activePage) {
      await activePage.screenshot({ path: `${config.output}/browser-failure.png`, fullPage: true }).catch(() => {});
      fs.writeFileSync(`${config.output}/browser-failure.json`, JSON.stringify({ error: String(error),
        url: activePage.url(), body: await activePage.locator('body').innerText().catch(() => '') }, null, 2));
    }
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(String(error)); process.exitCode = 1; });
