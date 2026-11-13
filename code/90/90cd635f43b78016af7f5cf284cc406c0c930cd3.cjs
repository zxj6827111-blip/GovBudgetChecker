const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const { chromium } = require(path.join(root, 'app/node_modules/@playwright/test'));
const rt = path.join(__dirname, 'runtime');
const credentials = JSON.parse(fs.readFileSync(path.join(rt, 'validator.json'), 'utf8'));
const joint = JSON.parse(fs.readFileSync(path.join(__dirname, 'joint_chain/receipt.json'), 'utf8'));
const native = JSON.parse(fs.readFileSync(path.join(__dirname, 'native_c7/receipt.json'), 'utf8'));
const origin = 'http://127.0.0.1:3013';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  const responses = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('response', r => { if (r.status() >= 400) responses.push({ status: r.status(), path: new URL(r.url()).pathname }); });
  try {
    await context.addCookies([{ name: 'gbc_session', value: 'original-cookie-sentinel', url: origin }]);
    await page.goto(`${origin}/login?next=/materials`);
    await page.getByPlaceholder('例如：admin').fill(credentials.username);
    await page.getByPlaceholder('请输入密码').fill(credentials.password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.waitForURL(u => u.pathname === '/materials', { timeout: 90000 });
    const cookies = await context.cookies();
    assert.ok(cookies.some(c => c.name === 'gbc_acceptance_20261026'));
    assert.equal(cookies.find(c => c.name === 'gbc_session').value, 'original-cookie-sentinel');
    await page.goto(`${origin}/task/${joint.job_id}`);
    await page.locator(`[data-analysis-revision="${joint.analysis_revision}"]`).waitFor({ timeout: 90000 });
    const aiText = await page.getByTestId('task-ai-scope').innerText();
    assert.match(aiText, /2\s*\/\s*2/);
    await page.getByText(/汇总口径待确认/).first().waitFor({ timeout: 30000 });
    await page.getByText('V33-240', { exact: true }).first().click();
    const taskText = await page.locator('body').innerText();
    assert.match(taskText, /10\.00|10万元|10\.0/);
    await page.screenshot({ path: path.join(__dirname, 'joint_chain/task.png'), fullPage: true });
    await page.getByTestId('task-open-report-modal').click();
    await page.getByTestId('task-report-modal').waitFor();
    const downloadEvent = page.waitForEvent('download', { timeout: 90000 });
    await page.getByTestId('task-report-download').click();
    const download = await downloadEvent;
    assert.equal(await download.failure(), null);
    await download.saveAs(path.join(__dirname, 'joint_chain/browser_report.pdf'));
    const pdf = fs.readFileSync(path.join(__dirname, 'joint_chain/browser_report.pdf'));
    assert.equal(pdf.subarray(0, 5).toString(), '%PDF-');
    assert.ok(pdf.length > 1000);
    await page.getByTestId('task-report-close').click();
    for (const format of ['json', 'csv']) {
      const response = await context.request.get(`${origin}/api/reports/download?job_id=${joint.job_id}&format=${format}`);
      assert.equal(response.status(), 200);
      const buffer = await response.body();
      fs.writeFileSync(path.join(__dirname, `joint_chain/browser_report.${format}`), buffer);
      assert.ok(buffer.toString().includes(joint.analysis_revision));
    }
    await page.goto(`${origin}/review?job=${joint.job_id}`);
    await page.getByRole('button', { name: '读取校正', exact: true }).waitFor({ timeout: 90000 });
    await page.screenshot({ path: path.join(__dirname, 'joint_chain/review.png'), fullPage: true });
    await page.getByRole('button', { name: '读取校正', exact: true }).click();
    await page.getByText(/当前没有需要人工补录或校正的表格|整行补录|表种/).first().waitFor({ timeout: 30000 });
    await page.screenshot({ path: path.join(__dirname, 'joint_chain/corrections.png'), fullPage: true });
    await page.goto(`${origin}/task/${native.job_id}`);
    await page.locator(`[data-analysis-revision="${native.analysis_revision}"]`).waitFor({ timeout: 90000 });
    const nativeAiText = await page.getByTestId('task-ai-scope').innerText();
    assert.match(nativeAiText, /未执行|未请求/);
    await page.screenshot({ path: path.join(__dirname, 'native_c7/task.png'), fullPage: true });
    assert.deepEqual(errors, []);
    assert.deepEqual(responses, []);
    fs.writeFileSync(path.join(__dirname, 'browser_receipt.json'), JSON.stringify({ passed: true,
      real_api: true, network_interception: false, login: true, original_cookie_preserved: true,
      ai_scope: aiText, native_ai_scope: nativeAiText, known_numeric_error_visible: true,
      corrections_tab_visible: true, pdf_download_from_ui: true, json_csv_same_revision: true,
      page_errors: errors, http_errors: responses }, null, 2));
    console.log('Real browser login, joint task, correction tab, native task and PDF/JSON/CSV exports passed');
  } catch(e) {
    await page.screenshot({ path: path.join(__dirname, 'browser_failure.png'), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(__dirname, 'browser_failure.json'), JSON.stringify({ error: String(e), url: page.url(), text: await page.locator('body').innerText(), errors, responses }, null, 2));
    throw e;
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(String(e)); process.exitCode = 1; });
