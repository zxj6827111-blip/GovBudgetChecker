const { chromium } = require('playwright');
const fs = require('fs');
const [job, slot, out, frontend, apiBase, apiKey] = process.argv.slice(2);

const headers = { 'Content-Type': 'application/json', 'X-API-Key': apiKey };
let token = '';
const H = () => ({ ...headers, 'X-Session-Token': token });

async function api(path, options = {}) {
  const resp = await fetch(apiBase + path, { ...options, headers: { ...H(), ...(options.headers || {}) } });
  let body = null;
  try { body = await resp.json(); } catch { body = null; }
  return { status: resp.status, body };
}

async function basis() {
  const r = await api(`/api/reviews/${slot}?job_uuid=${job}`);
  return r.body?.data?.current_analysis?.analysis_basis_token || '';
}

async function navToObligation(page) {
  for (let i = 0; i < 6; i += 1) {
    const text = await page.innerText('body');
    if (text.includes('一般公共预算支出决算表 与 功能分类说明一致') && text.includes('核对无误')) return true;
    const next = page.getByRole('button', { name: '下一条' });
    if (!(await next.count())) return false;
    await next.first().click({ timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(1000);
  }
  return false;
}

(async () => {
  const login = await api('/api/auth/login', {
    method: 'POST', body: JSON.stringify({ username: 'admin', password: 'admin' }),
  });
  token = login.body.token;
  const basis1 = await basis();
  const result = { job, slot, basis1, captures: [], notes: [] };

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  const puts = [];
  page.on('request', (req) => {
    if (req.method() === 'PUT' && req.url().includes('/obligations/')) {
      puts.push({ phase: 'request', url: req.url(), body: req.postData() });
    }
  });
  page.on('response', async (resp) => {
    if (resp.request().method() === 'PUT' && resp.url().includes('/obligations/')) {
      let body = '';
      try { body = await resp.text(); } catch (e) { body = String(e); }
      puts.push({ phase: 'response', url: resp.url(), status: resp.status(), body });
    }
  });

  await page.goto(frontend + '/login', { waitUntil: 'networkidle' });
  await page.fill('input[placeholder*="admin"]', 'admin');
  await page.fill('input[type="password"]', 'admin');
  await page.click('button[type="submit"]');
  await page.waitForTimeout(3500);
  await page.goto(frontend + '/review?job=' + job, { waitUntil: 'networkidle' });
  await page.waitForTimeout(7000);
  const reached = await navToObligation(page);
  result.notes.push({ reached_obligation: reached });
  if (!reached) throw new Error('obligation card not found');

  // First click: capture the page's real request body, abort so the obligation stays pending.
  let abortedBody = null;
  await page.route('**/api/reviews/**/obligations/**', async (route) => {
    if (route.request().method() === 'PUT') {
      abortedBody = route.request().postData();
      await route.abort();
    } else {
      await route.continue();
    }
  });
  await page.getByRole('button', { name: '核对无误', exact: true }).first().click({ timeout: 5000 });
  await page.waitForTimeout(2000);
  result.notes.push({ first_body: abortedBody });
  const firstJson = abortedBody ? JSON.parse(abortedBody) : {};
  result.notes.push({ first_body_has_basis: Boolean(firstJson.analysis_basis_token), first_body_basis: firstJson.analysis_basis_token });
  if (firstJson.analysis_basis_token !== basis1) {
    throw new Error(`page basis ${firstJson.analysis_basis_token} != ${basis1}`);
  }
  await page.unroute('**/api/reviews/**/obligations/**');

  // Reanalyze -> new analysis generation.
  const st0 = await api(`/api/jobs/${job}/status`);
  const oldRevision = st0.body?.analysis_revision || '';
  const re = await api(`/api/jobs/${job}/reanalyze`, { method: 'POST', body: '{}' });
  result.notes.push({ reanalyze: re.status, old_revision: oldRevision });
  let newRevision = '';
  for (let i = 0; i < 120; i += 1) {
    const st = await api(`/api/jobs/${job}/status`);
    if (st.body?.analysis_revision && st.body.analysis_revision !== oldRevision
        && ['review_required', 'completed'].includes(st.body.status)) {
      newRevision = st.body.analysis_revision;
      break;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  result.notes.push({ new_revision: newRevision });
  const start = await api(`/api/reviews/${slot}/start`, { method: 'POST', body: JSON.stringify({ job_uuid: job }) });
  result.notes.push({ start_new_session: start.status });
  const basis2 = await basis();
  result.notes.push({ basis2 });
  if (!basis2 || basis2 === basis1) throw new Error('new basis not changed');

  // Stale page (still basis1) submits again: must be rejected, not written.
  await page.getByRole('button', { name: '核对无误', exact: true }).first().click({ timeout: 5000 }).catch((e) => {
    result.notes.push({ stale_click_error: String(e) });
  });
  await page.waitForTimeout(2500);
  result.captures = puts.slice();
  const staleResp = puts.filter((x) => x.phase === 'response').slice(-1)[0];
  result.notes.push({ stale_response: staleResp });

  // Refresh page -> basis2 -> submit succeeds.
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(6000);
  const reached2 = await navToObligation(page);
  result.notes.push({ reached_after_reload: reached2 });
  if (reached2) {
    await page.getByRole('button', { name: '核对无误', exact: true }).first().click({ timeout: 5000 }).catch((e) => {
      result.notes.push({ fresh_click_error: String(e) });
    });
    await page.waitForTimeout(2500);
  }
  result.captures = puts.slice();
  result.notes.push({ fresh_response: puts.filter((x) => x.phase === 'response').slice(-1)[0] });

  fs.writeFileSync(out + '/ui_stale_basis_result.json', JSON.stringify(result, null, 1), 'utf8');
  await page.screenshot({ path: out + '/ui_stale_basis_last.png', fullPage: true });
  await browser.close();
  console.log(JSON.stringify(result.notes, null, 1));
})().catch((e) => { console.error(e); process.exit(1); });
