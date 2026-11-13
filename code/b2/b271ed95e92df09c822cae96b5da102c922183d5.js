const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const context = await browser.newContext({viewport:{width:1600,height:1100}});
  const res = await context.request.post('http://127.0.0.1:3010/api/auth/login',{data:{username:'admin',password:'admin123'}});
  console.log('login',res.status());
  const page=await context.newPage();
  await page.goto('http://127.0.0.1:3010/review?job=5e034baf1ef0d23efe3a1a913c21e91e',{waitUntil:'domcontentloaded'});
  await page.waitForTimeout(4000);
  console.log('url',page.url());
  const text=await page.locator('body').innerText();
  console.log('bodylen',text.length);
  console.log(text.slice(0,1200));
  await page.screenshot({path:'/Volumes/workSSD/software development/GovBudgetChecker/scratch/ai_remediation_20261025/acceptance2/debug_S1.png',fullPage:false});
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
