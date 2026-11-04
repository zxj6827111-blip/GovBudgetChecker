const { chromium } = require('playwright');
const fs = require('fs');
const [job, out, frontend, targetSnippet] = process.argv.slice(2);
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
  let reviewText = await page.innerText('body');
  // 目标问题可能不在默认队列；依次尝试参考/全部条目入口后再取正文。
  if (targetSnippet && !reviewText.replace(/\s+/g, '').includes(targetSnippet)) {
    for (const label of ['参考', '查看大模型参考条目', '全部', '提示']) {
      const link = page.getByText(label, { exact: false }).first();
      if (await link.count()) {
        await link.click({ timeout: 3000 }).catch(() => {});
        await page.waitForTimeout(2000);
        reviewText = await page.innerText('body');
        if (reviewText.replace(/\s+/g, '').includes(targetSnippet)) break;
      }
    }
  }
  fs.writeFileSync(out + '/16_review_page_text.txt', reviewText, 'utf8');
  await page.screenshot({ path: out + '/17_review_page.png', fullPage: true });
  await page.goto(frontend + '/history', { waitUntil: 'networkidle' });
  await page.waitForTimeout(4000);
  const historyText = await page.innerText('body');
  fs.writeFileSync(out + '/18_history_page_text.txt', historyText, 'utf8');
  await page.screenshot({ path: out + '/19_history_page.png', fullPage: true });
  const compactReview = reviewText.replace(/\s+/g, '');
  const result = {
    review_has_queue: reviewText.includes('必须处理') || reviewText.includes('参考'),
    review_has_sample: reviewText.includes('环境学校') || reviewText.includes('2023'),
    history_has_job: historyText.includes('环境学校') || historyText.includes('2023'),
    review_has_target_issue: targetSnippet ? compactReview.includes(targetSnippet) : true,
    review_head: reviewText.slice(0, 800),
    history_head: historyText.slice(0, 600),
  };
  fs.writeFileSync(out + '/15_ui_probe.json', JSON.stringify(result, null, 1), 'utf8');
  await browser.close();
  if (!result.review_has_queue || !result.review_has_sample || !result.history_has_job
      || !result.review_has_target_issue) {
    console.error(JSON.stringify(result, null, 1));
    process.exit(1);
  }
  console.log('UI OK');
})().catch((error) => { console.error(error); process.exit(1); });
