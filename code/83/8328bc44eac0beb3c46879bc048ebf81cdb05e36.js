const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const context = await browser.newContext({viewport:{width:1600,height:1100}});
  const res = await context.request.post('http://127.0.0.1:3010/api/auth/login',{data:{username:'admin',password:'admin123'}});
  const out='/Volumes/workSSD/software development/GovBudgetChecker/scratch/ai_remediation_20261025/acceptance2';
  const jobs={S1:['5e034baf1ef0d23efe3a1a913c21e91e',/机关事业单位基本养老保险缴费支出表文金额不一致/,true],
              S2:['8c8cde3f186982a6d4c1114ef8eef5a6',/说明缺少实质性原因/,false],
              S3:['02f5f886affdda4fe2470f7440af0899',/文字表述疑似错误（错别字）/,false]};
  for(const [sid,[job,re,allFirst]] of Object.entries(jobs)){
    const page=await context.newPage();
    await page.goto(`http://127.0.0.1:3010/review?job=${job}`,{waitUntil:'domcontentloaded'});
    await page.waitForSelector('[data-testid="gbc-review-issues-tab"]',{timeout:30000});
    await page.waitForTimeout(2000);
    const toggle=page.getByTestId('gbc-review-toggle-source-details');
    if(await toggle.count()){ await toggle.click(); await page.waitForTimeout(400); }
    if(allFirst){ const all=page.getByTestId('gbc-review-scope-all'); if(await all.count()){ await all.click(); await page.waitForTimeout(600); } }
    const btn=page.getByRole('button',{name:re}).first();
    await btn.click({timeout:15000});
    await page.waitForTimeout(1500);
    await page.screenshot({path:`${out}/${sid}_page.png`,fullPage:false});
    console.log(sid,'shot ok',await page.title());
    await page.close();
  }
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
