const { chromium } = require('playwright');
const fs = require('fs');
const [job, out, frontend] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(frontend + '/login', { waitUntil: 'networkidle' });
  await page.fill('input[placeholder*="admin"]', 'admin');
  await page.fill('input[type="password"]', 'admin');
  await page.click('button[type="submit"]');
  await page.waitForTimeout(4000);
  await page.goto(frontend + '/review?job=' + job, { waitUntil: 'networkidle' });
  await page.waitForTimeout(8000);
  const reviewText = await page.innerText('body');
  fs.writeFileSync(out + '/16_review_page_text.txt', reviewText, 'utf8');
  await page.screenshot({ path: out + '/17_review_page.png', fullPage: true });
  await page.goto(frontend + '/history', { waitUntil: 'networkidle' });
  await page.waitForTimeout(4000);
  const historyText = await page.innerText('body');
  fs.writeFileSync(out + '/18_history_page_text.txt', historyText, 'utf8');
  await page.screenshot({ path: out + '/19_history_page.png', fullPage: true });
  const result = {
    review_has_queue: reviewText.includes('必须处理') || reviewText.includes('参考'),
    review_has_sample: reviewText.includes('环境学校') || reviewText.includes('2023'),
    history_has_job: historyText.includes('环境学校') || historyText.includes('2023'),
    review_head: reviewText.slice(0, 600),
    history_head: historyText.slice(0, 600),
  };
  fs.writeFileSync(out + '/15_ui_probe.json', JSON.stringify(result, null, 1), 'utf8');
  await browser.close();
  if (!result.review_has_queue || !result.review_has_sample || !result.history_has_job) {
    console.error(JSON.stringify(result, null, 1));
    process.exit(1);
  }
  console.log('UI OK');
})().catch((error) => { console.error(error); process.exit(1); });
