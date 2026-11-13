const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(root,'app/node_modules/@playwright/test'));
const rt=path.join(__dirname,'../runtime');
const credentials=JSON.parse(fs.readFileSync(path.join(rt,'validator.json'),'utf8'));
const origin='http://127.0.0.1:3013';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1600,height:1100}});
 const page=await context.newPage();const errors=[];
 page.on('pageerror',e=>errors.push(String(e)));
 try{
  await page.goto(origin+'/login?next=/upload');
  await page.getByPlaceholder('例如：admin').fill(credentials.username);
  await page.getByPlaceholder('请输入密码').fill(credentials.password);
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.waitForURL(u=>u.pathname==='/upload',{timeout:90000});
  await page.getByTestId('gbc-upload-preset-org').selectOption('5e26afdec7b9');
  const file=path.join(rt,'uploads/a296c928fbd69d0b173a5050667e3b6e/上海市信访办公室2024年决算.pdf');
  await page.locator('input[type="file"]').first().setInputFiles(file);
  await page.getByText('需要确认',{exact:true}).waitFor({timeout:90000});
  assert.equal(await page.getByTestId('gbc-upload-submit').isDisabled(),true);
  await page.getByRole('button',{name:'补齐',exact:true}).click();
  await page.getByText('部门/单位归属待逐份确认',{exact:true}).waitFor();
  const select=page.getByTestId('gbc-upload-confirm-org');
  const unitText=await select.locator('option[value="8c0ba1fafea0"]').innerText();
  const deptText=await select.locator('option[value="5e26afdec7b9"]').innerText();
  assert.match(unitText,/上海市信访办公室单位.*单位/);assert.match(deptText,/部门/);
  await page.screenshot({path:path.join(__dirname,'upload_scope_confirmation.png'),fullPage:true});
  await select.selectOption('8c0ba1fafea0');
  await page.getByTestId('gbc-upload-confirm-save').click();
  await page.getByText('校验通过',{exact:true}).waitFor();
  assert.equal(await page.getByTestId('gbc-upload-submit').isEnabled(),true);
  await page.screenshot({path:path.join(__dirname,'upload_unit_selected.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(__dirname,'upload_browser_receipt.json'),JSON.stringify({passed:true,real_api:true,network_mock:false,
    batch_department_did_not_confirm_ambiguous_unit:true,unit_option:unitText,department_option:deptText,
    explicit_unit_selection_unblocks_upload:true,submitted_duplicate_upload:false,page_errors:errors},null,2));
  console.log('Real upload UI: unit selectable, batch department cannot auto-confirm ambiguous unit, explicit selection passes');
 }catch(e){await page.screenshot({path:path.join(__dirname,'upload_browser_failure.png'),fullPage:true}).catch(()=>{});fs.writeFileSync(path.join(__dirname,'upload_browser_failure.json'),JSON.stringify({error:String(e),text:await page.locator('body').innerText(),url:page.url()},null,2));throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(String(e));process.exitCode=1;});
