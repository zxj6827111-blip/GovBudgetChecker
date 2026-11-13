const { chromium } = require('playwright');
const fs = require('fs');
const [job, out, frontend] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  const captured = [];
  page.on('request', (req) => {
    if (req.method() === 'PUT' && req.url().includes('/obligations/')) {
      captured.push({ phase: 'request', url: req.url(), body: req.postData() });
    }
  });
  page.on('response', async (resp) => {
    if (resp.request().method() === 'PUT' && resp.url().includes('/obligations/')) {
      let body = '';
      try { body = await resp.text(); } catch (e) { body = String(e); }
      captured.push({ phase: 'response', url: resp.url(), status: resp.status(), body });
    }
  });
  await page.goto(frontend + '/login', { waitUntil: 'networkidle' });
  await page.fill('input[placeholder*="admin"]', 'admin');
  await page.fill('input[type="password"]', 'admin');
  await page.click('button[type="submit"]');
  await page.waitForTimeout(4000);
  await page.goto(frontend + '/review?job=' + job, { waitUntil: 'networkidle' });
  await page.waitForTimeout(8000);
  const before = await page.innerText('body');
  fs.writeFileSync(out + '/ui_before.txt', before, 'utf8');
  await page.screenshot({ path: out + '/ui_before.png', fullPage: true });
  const buttons = page.getByRole('button', { name: '确认问题', exact: true });
  const count = await buttons.count();
  let clickError = null;
  if (count > 0) {
    await buttons.first().click({ timeout: 5000 }).catch((e) => { clickError = String(e); });
    await page.waitForTimeout(2500);
  } else {
    clickError = 'no 确认问题 button';
  }
  const after = await page.innerText('body');
  fs.writeFileSync(out + '/ui_after.txt', after, 'utf8');
  await page.screenshot({ path: out + '/ui_after.png', fullPage: true });
  const dialogs = await page.getByRole('dialog').count();
  const inputs = await page.locator('textarea, input[type="text"]').count();
  fs.writeFileSync(out + '/ui_probe.json', JSON.stringify({
    button_count: count, click_error: clickError, dialogs, inputs,
    captured, before_head: before.slice(0, 1200), after_head: after.slice(0, 1600),
  }, null, 1), 'utf8');
  await browser.close();
  console.log(JSON.stringify({ count, clickError, dialogs, inputs, captured: captured.length }));
})().catch((e) => { console.error(e); process.exit(1); });
