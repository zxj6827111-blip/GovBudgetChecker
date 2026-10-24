const { chromium } = require('../app/node_modules/@playwright/test');
const fs = require('fs');
const { createHash } = require('node:crypto');
const assert = require('node:assert/strict');
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
    fs.writeFileSync(`${process.env.UAT_OUTPUT}/ledger_proxy_response.json`,JSON.stringify({status:ledger.status(),body:await ledger.text()},null,2));
    if (!ledger.ok()) throw new Error(`real ledger proxy failed ${ledger.status()}`);
    await page.screenshot({path:`${process.env.UAT_OUTPUT}/live_materials.png`,fullPage:true});
    const factCase=JSON.parse(process.env.UAT_FACT_CASE || '{}');
    let versionBinding=null;
    if (factCase.job_id) {
      const job=encodeURIComponent(factCase.job_id);
      for (const [revision,expected] of [[factCase.old_revision,factCase.old_json_sha256],[factCase.new_revision,factCase.new_json_sha256]]) {
        const params=new URLSearchParams({job_id:factCase.job_id,format:'json',analysis_revision:revision});
        const report=await context.request.get(`${process.env.UAT_ORIGIN}/api/reports/download?${params}`);
        if (!report.ok() || createHash('sha256').update(await report.body()).digest('hex')!==expected) throw new Error('frontend export selected a different analysis revision');
        for (const view of ['raw','effective']) {
          const snapshot=await context.request.get(`${process.env.UAT_ORIGIN}/api/jobs/${job}/fact-snapshot?${new URLSearchParams({revision_id:revision,view,limit:'1'})}`);
          if (!snapshot.ok()) throw new Error(`frontend snapshot proxy failed ${snapshot.status()}`);
          const body=await snapshot.json();
          if (body.revision_id!==revision || body.view!==view || body.total<1 || body.facts.length!==1) throw new Error('frontend snapshot identity mismatch');
        }
      }
      await page.goto(`${process.env.UAT_ORIGIN}/task/${job}`);
      await page.locator(`[data-analysis-revision="${factCase.new_revision}"]`).waitFor({timeout:60000});
      const versions=await context.request.get(`${process.env.UAT_ORIGIN}/api/jobs/${job}/analysis-versions`);
      assert.equal(versions.status(),200);
      assert.ok((await versions.json()).items.some(item=>item.revision_id===factCase.old_revision));
      const selector=page.getByTestId('analysis-version-selector');
      await selector.selectOption(factCase.old_revision);
      await page.locator(`[data-analysis-revision="${factCase.old_revision}"]`).waitFor({timeout:60000});
      assert.equal(await page.getByTestId('task-reanalyze-button').isDisabled(),true);
      assert.equal(await page.getByTestId('task-associate-button').isDisabled(),true);
      await page.screenshot({path:`${process.env.UAT_OUTPUT}/live_historical_task.png`,fullPage:true});
      await page.getByTestId('analysis-version-selector').selectOption('');
      await page.locator(`[data-analysis-revision="${factCase.new_revision}"]`).waitFor({timeout:60000});
      await page.screenshot({path:`${process.env.UAT_OUTPUT}/live_version_bound_task.png`,fullPage:true});
      const anonymous=await browser.newContext();
      const denied=await anonymous.request.get(`${process.env.UAT_ORIGIN}/api/jobs/${job}/fact-snapshot`);
      if (denied.status()!==401) throw new Error('anonymous frontend snapshot request was not rejected');
      await anonymous.close();
      versionBinding={passed:true,current_revision:factCase.new_revision,historical_revision:factCase.old_revision,
        frontend_current_historical_json_identical_to_backend:true,raw_effective_snapshot_proxy:true,
        visible_task_revision_bound:true,anonymous_denied:true};
    }
    if (failures.length) throw new Error(`browser runtime errors: ${failures.join(';')}`);
    fs.writeFileSync(`${process.env.UAT_OUTPUT}/browser_receipt.json`,JSON.stringify({passed:true,scope:'production_next_authenticated_real_api',network_interception:false,login:true,csv_preview:true,csv_atomic_replay:true,template_download:true,ledger_proxy:true,version_binding:versionBinding,browser:'Google Chrome',runtime_errors:failures},null,2));
    await context.close();
  } finally { await browser.close(); }
})().catch(error=>{console.error(String(error));process.exitCode=1;});
