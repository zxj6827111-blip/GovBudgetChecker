const { chromium } = require('playwright');
const fs = require('fs');
const [job, out, frontend] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  await page.goto(frontend + '/login', { waitUntil: 'networkidle' });
  await page.fill('input[placeholder*="admin"]', 'admin');
  await page.fill('input[type="password"]', 'admin');
  await page.click('button[type="submit"]');
  await page.waitForTimeout(4000);
  await page.goto(frontend + '/review?job=' + job, { waitUntil: 'networkidle' });
  await page.waitForTimeout(7000);
  const steps = [];
  for (let i = 0; i < 8; i += 1) {
    const text = await page.innerText('body');
    steps.push({ i, text: text.slice(0, 500), hasObligation: text.includes('一般公共预算支出决算表 与 功能分类说明一致'), hasOk: text.includes('核对无误') });
    const next = page.getByRole('button', { name: '下一条' });
    if (await next.count()) {
      await next.first().click({ timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(1200);
    } else {
      break;
    }
  }
  fs.writeFileSync(out + '/nav_steps.json', JSON.stringify(steps, null, 1), 'utf8');
  await page.screenshot({ path: out + '/nav_last.png', fullPage: true });
  console.log(JSON.stringify(steps.map((s) => ({ i: s.i, hasObligation: s.hasObligation, hasOk: s.hasOk, head: s.text.replace(/\s+/g, ' ').slice(0, 160) })), null, 1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
