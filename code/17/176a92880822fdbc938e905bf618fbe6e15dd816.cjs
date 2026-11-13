const { chromium } = require('../app/node_modules/@playwright/test');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    const failures=[];
    page.on('pageerror',error=>failures.push(String(error)));
    await page.goto(`${process.env.UAT_ORIGIN}/login?next=/materials`);
    await page.getByPlaceholder('例如：admin').fill('admin');
    await page.getByPlaceholder('请输入密码').fill(process.env.UAT_PASSWORD);
    await page.getByRole('button',{name:'登录',exact:true}).click();
    await page.waitForURL('**/materials', {timeout:60000});
    await page.getByTestId('gbc-expected-import').locator('summary').click();
    await page.getByTestId('gbc-expected-file').setInputFiles({name:'uat.csv',mimeType:'text/csv',buffer:Buffer.from(process.env.UAT_CSV)});
    await page.getByTestId('gbc-expected-preview').click();
    await page.getByTestId('gbc-expected-result').filter({hasText:'可导入 1 行，错误 0 行。'}).waitFor();
    await page.getByTestId('gbc-expected-confirm').click();
    await page.getByRole('status').filter({hasText:'重复提交，未新增槽位'}).waitFor();
    const template=await context.request.get(`${process.env.UAT_ORIGIN}/api/materials/expected/template`);
    if (!template.ok() || !(await template.text()).includes('subject_org_id')) throw new Error('real template proxy failed');
    const ledger=await context.request.get(`${process.env.UAT_ORIGIN}/api/materials/coverage`);
    if (!ledger.ok()) throw new Error(`real ledger proxy failed ${ledger.status()}`);
    await page.screenshot({path:`${process.env.UAT_OUTPUT}/live_materials.png`,fullPage:true});
    if (failures.length) throw new Error(`browser runtime errors: ${failures.join(';')}`);
    fs.writeFileSync(`${process.env.UAT_OUTPUT}/browser_receipt.json`,JSON.stringify({passed:true,scope:'production_next_authenticated_real_api',network_interception:false,login:true,csv_preview:true,csv_atomic_replay:true,template_download:true,ledger_proxy:true,browser:'Google Chrome',runtime_errors:failures},null,2));
    await context.close();
  } finally { await browser.close(); }
})().catch(error=>{console.error(String(error));process.exitCode=1;});
