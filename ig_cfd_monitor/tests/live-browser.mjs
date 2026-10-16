const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
import assert from 'node:assert/strict';
import fs from 'node:fs';
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
const base=process.env.MONITOR_TEST_URL||'http://127.0.0.1:8786';
await page.goto(base);await page.waitForFunction(()=>document.querySelector('#preview-banner').textContent.includes('IG 尚未连接'));
assert(!(await page.locator('body').innerText()).includes('28,363'));
for(const view of ['overview','markets','positions','ledger','news','risk']){await page.locator(`[data-nav="${view}"]`).click();await page.waitForTimeout(300);assert(await page.locator('#page-title').innerText());assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
await page.locator('[data-nav="overview"]').click();await page.screenshot({path:'qa/live-desktop.png',fullPage:true});
await page.setViewportSize({width:390,height:844});
for(const view of ['overview','markets','positions','ledger','news','risk']){await page.locator(`[data-nav="${view}"]`).click();await page.waitForTimeout(200);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`mobile overflow ${view}`);}
await page.locator('[data-nav="overview"]').click();await page.screenshot({path:'qa/live-mobile.png',fullPage:true});
for(const path of ['/state/credentials.json','/credentials.py','/setup','/api/configure','/.env','/fixtures.js','/app.js'])assert.equal((await page.request.get(base+path)).status(),404,path);
assert.equal((await page.request.post(base+'/api/state',{data:{}})).status(),405);
const s=await (await page.request.get(base+'/api/state')).json();assert.equal(s.account,null);assert.equal(s.positions,null);assert.equal(s.risk.status,'UNKNOWN');assert(!JSON.stringify(s).includes('api_key'));
assert.equal((await page.request.post('http://127.0.0.1:8787/api/configure',{data:{}})).status(),403);
assert.equal(errors.length,0,errors.join('\n'));
fs.writeFileSync('qa/live-verification.json',JSON.stringify({passed:true,views:6,desktop:true,mobile:true,secretRoutesDenied:7,accountConnected:false,errors},null,2));
console.log('Live UI: 6 routes x desktop/mobile; 7 private routes blocked; no fake account; writes/CSRF denied; no JS errors.');await browser.close();
