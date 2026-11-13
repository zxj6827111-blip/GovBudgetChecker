const fs = require('fs');
const path = require('path');
const { chromium } = require('/Volumes/workSSD/software development/GovBudgetChecker/artifacts/daily-main/app/node_modules/@playwright/test');
(async () => {
  const root=path.resolve(__dirname,'..');
  const privateRoot=path.join(root,'runtime_private_2');
  const access=JSON.parse(fs.readFileSync(path.join(privateRoot,'browser_access.json')));
  const front=JSON.parse(fs.readFileSync(path.join(privateRoot,'frontend_access.json')));
  const errors=[];const failures=[];const blocked=[];
  const browser=await chromium.launch({headless:true});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      if (!['127.0.0.1','localhost'].includes(url.hostname)&&!['data:','blob:'].includes(url.protocol)) {
        blocked.push(url.hostname);return route.abort();
      }
      return route.continue();
    });
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.name));
    page.on('response',r=>{if(r.status()>=500)failures.push({path:new URL(r.url()).pathname,http:r.status()});});
    const base=`http://127.0.0.1:${front.port}`;
    await page.goto(base+'/login',{waitUntil:'networkidle'});
    await page.getByPlaceholder('例如：admin').fill('admin');
    await page.getByPlaceholder('请输入密码').fill(access.admin_password);
    await page.getByRole('button',{name:'登录',exact:true}).click();
    await page.waitForURL('**/workbench',{timeout:30000});
    await page.goto(base+'/review?job='+access.job_id,{waitUntil:'networkidle'});
    await page.getByTestId('gbc-review-workbench-page').waitFor({timeout:45000});
    const text=await page.locator('body').innerText();
    if(!text.includes('离退休干部服务中心'))throw new Error('subject assertion failed');
    const disabled=await page.getByTestId('gbc-review-complete').isDisabled();
    if(!disabled)throw new Error('unfinished completion was enabled');
    const list=page.getByTestId('gbc-review-toggle-list');if(await list.isVisible())await list.click();
    const cards=page.locator('[data-testid^="gbc-review-issue-card-"]');const count=await cards.count();
    if(count)await cards.first().click();
    await page.screenshot({path:path.join(root,'live_receipts_2/independent_review_selected.png'),fullPage:true});
    const result={overall:errors.length||failures.length?'FAIL':'PASS',independent_browser_session:true,
      subject_visible:true,unfinished_completion_disabled:disabled,issue_cards:count,page_errors:errors,
      server_failures:failures,blocked_external_hosts:[...new Set(blocked)],
      scope:'new isolated API/worker/PostgreSQL and copied unchanged frontend build; no daily entry switch'};
    fs.writeFileSync(path.join(root,'live_receipts_2/independent_browser_report.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  } finally {await browser.close();}
})().catch(e=>{console.log(JSON.stringify({overall:'FAIL',error_type:e.name}));process.exitCode=1;});
