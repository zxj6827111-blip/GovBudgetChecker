const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(root,'app/node_modules/@playwright/test'));
const rt=path.join(__dirname,'../runtime');
const credentials=JSON.parse(fs.readFileSync(path.join(rt,'validator.json'),'utf8'));
const origin='http://127.0.0.1:3013';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1600,height:1100}});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const receipts={};
 try{
  await page.goto(origin+'/login?next=/reports');
  await page.getByPlaceholder('例如：admin').fill(credentials.username);await page.getByPlaceholder('请输入密码').fill(credentials.password);
  await page.getByRole('button',{name:'登录',exact:true}).click();await page.waitForURL(u=>u.pathname==='/reports',{timeout:90000});
  for(const label of ['department','unit']){
   const rec=JSON.parse(fs.readFileSync(path.join(__dirname,label,'receipt.json'),'utf8'));
   const org=label==='department'?'5e26afdec7b9':'8c0ba1fafea0';
   const category=label==='department'?'department':'units';
   const url=`${origin}/api/materials/reports?org=${org}&org_scope=exact&year=2024&kind=final&report_category=${category}&page_size=100`;
   const response=await context.request.get(url);assert.equal(response.status(),200);
   const listing=await response.json();const serialized=JSON.stringify(listing);
   assert.ok(serialized.includes(rec.job_id));
   const other=label==='department'?'a296c928fbd69d0b173a5050667e3b6e':'bdba38fab5eab35c2e2c03539ed7036c';
   assert.ok(!serialized.includes(other),'Current department/unit lists must not include the other job');
   fs.writeFileSync(path.join(__dirname,label,'report_listing.json'),JSON.stringify(listing,null,2));
   await page.goto(`${origin}/reports/${org}?year=2024`);
   const filename=label==='department'?'上海市信访办2024年度部门决算.pdf':'上海市信访办公室2024年决算.pdf';
   await page.getByText(filename,{exact:true}).first().waitFor({timeout:90000});
   await page.screenshot({path:path.join(__dirname,label,'reports.png'),fullPage:true});
   await page.goto(`${origin}/task/${rec.job_id}`);
   await page.locator(`[data-analysis-revision="${rec.analysis_revision}"]`).waitFor({timeout:90000});
   assert.match(await page.getByRole('heading',{level:1}).innerText(),label==='unit'?/单位决算/:/部门决算/);
   const ai=await page.getByTestId('task-ai-scope').innerText();
   const body=await page.locator('body').innerText();assert.ok(!body.includes('trusted_ingest_not_committed'));
   await page.screenshot({path:path.join(__dirname,label,'task.png'),fullPage:true});
   receipts[label]={job_id:rec.job_id,revision:rec.analysis_revision,organization_id:org,current_report_in_correct_category:true,ai_banner:ai};
  }
  await page.goto(`${origin}/reports/5e26afdec7b9?tab=units&year=2024`);
  await page.getByText('上海市信访办公室单位',{exact:true}).first().waitFor({timeout:90000});
  await page.screenshot({path:path.join(__dirname,'department_units.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(__dirname,'result_browser_receipt.json'),JSON.stringify({passed:true,real_api:true,network_mock:false,receipts,page_errors:errors,unit_visible_in_department_units:true},null,2));
  console.log('Both real jobs visible in their own report categories, task pages use current revisions, parent department shows unit');
 }catch(e){await page.screenshot({path:path.join(__dirname,'result_browser_failure.png'),fullPage:true}).catch(()=>{});fs.writeFileSync(path.join(__dirname,'result_browser_failure.json'),JSON.stringify({error:String(e),text:await page.locator('body').innerText(),url:page.url()},null,2));throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(String(e));process.exitCode=1;});
