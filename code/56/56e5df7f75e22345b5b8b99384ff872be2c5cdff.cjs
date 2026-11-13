const {chromium}=require('/Volumes/workSSD/software development/GovBudgetChecker/app/node_modules/@playwright/test');
const fs=require('fs');const path=require('path');const crypto=require('crypto');const assert=require('assert/strict');
(async()=>{
 const cases=JSON.parse(fs.readFileSync(process.env.UAT_ALL_CASES,'utf8'));
 const out=path.join(process.env.UAT_OUTPUT,'all_material_pages');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true});const records=[];const errors=[];
 try{
  const ctx=await browser.newContext({viewport:{width:1440,height:1000}});const page=await ctx.newPage();page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(`${process.env.UAT_ORIGIN}/login?next=/materials`);await page.getByPlaceholder('例如：admin').fill('admin');await page.getByPlaceholder('请输入密码').fill(process.env.UAT_PASSWORD);await page.getByRole('button',{name:'登录',exact:true}).click();await page.waitForURL('**/materials',{timeout:60000});await page.getByTestId('gbc-expected-import').waitFor();
  async function get(url){for(let i=0;i<3;i++){let r=await ctx.request.get(url);if(r.status()!=429)return r;await new Promise(x=>setTimeout(x,Math.min(60000,Math.max(2000,Number(r.headers()['retry-after']||2)*1000))));}throw new Error('rate limit exhausted');}
  for(const c of cases){const record={...c,passed:false};try{
   const job=encodeURIComponent(c.job_id);const vers=await get(`${process.env.UAT_ORIGIN}/api/jobs/${job}/analysis-versions`);record.stage='version_list';record.version_response_status=vers.status();const versionText=await vers.text();if(vers.status()!==200)record.response_body=versionText;assert.equal(vers.status(),200);const vv=JSON.parse(versionText);assert(vv.items.some(x=>x.revision_id===c.revision_id));
   record.stage='visible_revision';await page.goto(`${process.env.UAT_ORIGIN}/task/${job}`,{waitUntil:'domcontentloaded'});
   const isCurrent=vv.items.some(x=>x.revision_id===c.revision_id&&x.is_current);
   if(!isCurrent){await page.getByTestId('analysis-version-selector').waitFor({timeout:20000});await page.getByTestId('analysis-version-selector').selectOption(c.revision_id);}
   await page.locator(`[data-analysis-revision="${c.revision_id}"]`).waitFor({timeout:20000});
   for(const view of ['raw','effective']){record.stage='snapshot_'+view;const r=await get(`${process.env.UAT_ORIGIN}/api/jobs/${job}/fact-snapshot?${new URLSearchParams({revision_id:c.revision_id,view,limit:'1'})}`);const bodyText=await r.text();if(r.status()!==200)record.response_body=bodyText;assert.equal(r.status(),200);const body=JSON.parse(bodyText);assert.equal(body.snapshot_hash,c.snapshot_hash);assert.equal(body.revision_id,c.revision_id);assert.equal(body.total,c[view+'_count']);}
   record.stage='export';const exp=await get(`${process.env.UAT_ORIGIN}/api/reports/download?${new URLSearchParams({job_id:c.job_id,format:'json',analysis_revision:c.revision_id})}`);assert.equal(exp.status(),200);assert.equal(crypto.createHash('sha256').update(await exp.body()).digest('hex'),c.json_sha256);
   await page.screenshot({path:path.join(out,c.doc_id+'.png'),fullPage:true});record.passed=true;record.stage='completed';record.visible_revision=c.revision_id;record.raw_effective_proxy_same_snapshot=true;record.json_export_identical_to_first_execution=true;
  }catch(e){record.error=String(e);await page.screenshot({path:path.join(out,c.doc_id+'-failure.png'),fullPage:true}).catch(()=>{});}records.push(record);fs.writeFileSync(path.join(out,'receipt.json'),JSON.stringify({scope:'47_actual_material_task_pages_first_revision_and_authenticated_proxies',records,runtime_errors:errors,passed:records.length===cases.length&&records.every(x=>x.passed)&&!errors.length},null,2));await new Promise(x=>setTimeout(x,1600));}
  await ctx.close();
 }finally{await browser.close();}
 if(records.some(x=>!x.passed)||errors.length)process.exitCode=2;
})().catch(e=>{console.error(e);process.exitCode=1;});
