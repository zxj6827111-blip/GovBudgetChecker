const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/Volumes/workSSD/software development/GovBudgetChecker/app/node_modules/playwright');

const OUT = '/Volumes/workSSD/software development/GovBudgetChecker/scratch/ai_remediation_20261025/acceptance3';
const BASE = 'http://127.0.0.1:3011';
const summary = JSON.parse(fs.readFileSync(path.join(OUT, 'acceptance3_summary.json'), 'utf8'));

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
  const login = await context.request.post(`${BASE}/api/auth/login`, { data: { username: 'admin', password: 'admin123' } });
  console.log('login', login.status());
  const evidence = {};

  for (const sid of ['S1', 'S2', 'S3']) {
    const job = summary[sid]?.job_id;
    if (!job) { console.log(sid, 'no job'); continue; }
    const page = await context.newPage();
    const record = { job_id: job };
    try {
      await page.goto(`${BASE}/review?job=${job}`, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('[data-testid="gbc-review-issues-tab"]', { timeout: 30000 });
      await page.waitForTimeout(1500);
      const scope = page.getByTestId('gbc-review-ai-scope');
      record.scope_visible = await scope.count() > 0;
      if (record.scope_visible) {
        record.scope_text = (await scope.innerText()).slice(0, 1500);
      }
      await page.screenshot({ path: path.join(OUT, `${sid}_review.png`) });
      record.review_text = (await page.locator('body').innerText()).slice(0, 4000);

      if (sid === 'S1') {
        const card = page.getByText('机关事业单位基本养老保险缴费支出表文金额不一致').first();
        if (await card.count()) {
          await card.click();
          await page.waitForTimeout(800);
          const explanation = page.locator('[data-testid^="gbc-review-issue-explanation-"]');
          record.explanation_count = await explanation.count();
          record.explanation_text = record.explanation_count ? (await explanation.first().innerText()).slice(0, 1000) : '';
          await page.screenshot({ path: path.join(OUT, 'S1_explanation.png') });
        }
      }
      if (sid === 'S2') {
        record.s2_fragment_reported = /残句/.test(record.review_text);
        record.s2_target_quote_present = record.review_text.includes('管委会行政运行费用按实际情况支出');
      }
      if (sid === 'S3') {
        const target = page.getByText(/决算市全口径|广播影电视/).first();
        if (await target.count()) {
          await target.click();
          await page.waitForTimeout(800);
          await page.screenshot({ path: path.join(OUT, 'S3_target.png') });
        }
      }
    } catch (error) {
      record.error = `${error.name}: ${error.message}`;
    }

    const taskPage = await context.newPage();
    try {
      await taskPage.goto(`${BASE}/task/${job}`, { waitUntil: 'domcontentloaded' });
      await taskPage.waitForTimeout(2500);
      const taskScope = taskPage.getByTestId('task-ai-scope');
      record.task_scope_visible = await taskScope.count() > 0;
      if (record.task_scope_visible) record.task_scope_text = (await taskScope.innerText()).slice(0, 1200);
      await taskPage.screenshot({ path: path.join(OUT, `${sid}_task.png`), fullPage: false });
    } catch (error) {
      record.task_error = `${error.name}: ${error.message}`;
    }
    await taskPage.close();
    await page.close();
    evidence[sid] = record;
    console.log(sid, JSON.stringify(record).slice(0, 1200));
  }

  fs.writeFileSync(path.join(OUT, 'page_evidence.json'), JSON.stringify(evidence, null, 2), 'utf8');
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
