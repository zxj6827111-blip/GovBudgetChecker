const { chromium } = require('playwright');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1600,height:1100}});
  await context.request.post('http://127.0.0.1:3010/api/auth/login',{data:{username:'admin',password:'admin123'}});
  const page=await context.newPage();
  await page.goto('http://127.0.0.1:3010/review?job=8c8cde3f186982a6d4c1114ef8eef5a6',{waitUntil:'domcontentloaded'});
  await page.waitForSelector('[data-testid="gbc-review-issues-tab"]',{timeout:30000});
  await page.waitForTimeout(2000);
  const toggle=page.getByTestId('gbc-review-toggle-source-details'); if(await toggle.count()) await toggle.click();
  await page.locator('[data-review-problem-id="ai_semantic_8c8cde3f186982a6d4c1114ef8eef5a6_5_1792937194"]').click();
  await page.waitForTimeout(1200);
  await page.screenshot({path:'/Volumes/workSSD/software development/GovBudgetChecker/scratch/ai_remediation_20261025/acceptance2/S2_target_page.png',fullPage:false});
  console.log('ok');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
