const fs = require('fs');
const path = require('path');
const {chromium} = require('/Volumes/workSSD/software development/GovBudgetChecker/artifacts/daily-main/app/node_modules/@playwright/test');
(async()=>{
 const root=path.resolve(__dirname,'..');const state=JSON.parse(fs.readFileSync(path.join(root,'run_state.json')));const front=JSON.parse(fs.readFileSync(path.join(root,'frontend_state.json')));const base=`http://127.0.0.1:${front.port}`;
 const browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(String(e)));const failures=[];page.on('response',r=>{if(r.status()>=500)failures.push({url:new URL(r.url()).pathname,status:r.status()})});
 await page.goto(base+'/login',{waitUntil:'networkidle'});await page.getByPlaceholder('例如：admin').fill('admin');await page.getByPlaceholder('请输入密码').fill(state.admin_password);await page.getByRole('button',{name:'登录',exact:true}).click();await page.waitForURL('**/workbench',{timeout:30000});
 const job=state.items['7'].job_id;await page.goto(base+'/review?job='+job,{waitUntil:'networkidle'});await page.getByTestId('gbc-review-workbench-page').waitFor({timeout:45000});
 const text=await page.locator('body').innerText();if(!text.includes('离退休干部服务中心'))throw new Error('wrong subject in review');
 await page.screenshot({path:path.join(root,'evidence/user_flow/review_desktop.png'),fullPage:true});
 const complete=page.getByTestId('gbc-review-complete');const blocked=await complete.isDisabled();if(!blocked)throw new Error('unresolved review completion enabled');
 const list=page.getByTestId('gbc-review-toggle-list');if(await list.isVisible())await list.click();
 const cards=page.locator('[data-testid^="gbc-review-issue-card-"]');const count=await cards.count();if(count)await cards.first().click();
 await page.screenshot({path:path.join(root,'evidence/user_flow/review_selected.png'),fullPage:true});
 const result={overall:errors.length||failures.length?'FAIL':'PASS',subject_visible:true,unresolved_completion_blocked:blocked,issue_cards:count,page_errors:errors,server_failures:failures,scope:'real isolated backend, no mocks; existing unchanged frontend build'};fs.writeFileSync(path.join(root,'evidence/user_flow/browser_verification.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));await browser.close();if(result.overall!=='PASS')process.exitCode=1;
})().catch(e=>{console.error(String(e));process.exitCode=1});
