/** Synthetic fixtures only; never connects to IG or a real monitor. */
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);let chromium;try{({chromium}=require('playwright'));}catch{({chromium}=require('/Users/zexiayang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url))),at=new Date().toISOString();
const data={status:'connected',environment:'demo',generated_at:at,stale:false,stream:'CONNECTED:WS-STREAMING',account:{equity:27000,available:25000,unrealized:-3000,margin:2000},positions:[],orders:[],series:[{day:'2026-10-16',at:'2026-10-16T15:55:00Z',equity:28000},{day:'2026-10-19',at:'2026-10-19T03:00:00Z',equity:27000,provisional:true}],funding:{total_deposits:30000},risk:{status:'BELOW_THRESHOLD',equity_ratio:.9,loss_fraction:.1,threshold_equity:12000,distance_to_threshold:15000},markets:[{epic:'SYNTHETIC',name:'合成品种'}],history:{status:'api_range_loaded'},news:{items:[]},position_groups:[{epic:'SYNTHETIC',name:'合成品种',currency:'USD',expiry:'-',count:3,BUY:{count:2,size:4,average_entry:103},SELL:{count:1,size:2,average_entry:110}}]};
const base={generated_at:at,portfolio:{status:'READY',groups:[],positions:[],reconciliation:{status:'WITHIN_TOLERANCE',difference_hkd:0,tolerance_hkd:50},gross_notional_hkd:100000,effective_gross_leverage:3.7,margin_usage:.074,missing_stop_count:0},account_risk:{},costs:{},signals:{SYNTHETIC:{status:'INSUFFICIENT',observations:10,conditions:[]}},correlations:[],scenario:{status:'READY',assumptions:['合成测试，不是真实账户'],delta_hkd:-100,equity_hkd:26900,available_if_margin_unchanged:24900,distance_to_loss_alert:14900}};
let queries=[];const streams=new Set();
const server=http.createServer(async(req,res)=>{const url=new URL(req.url,'http://fixture.test');const json=x=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(x));};try{
 if(url.pathname==='/api/state')return json(data);
 if(url.pathname==='/api/events'){res.writeHead(200,{'Content-Type':'text/event-stream'});streams.add(res);res.write('data: '+JSON.stringify(data)+'\n\n');req.on('close',()=>streams.delete(res));return;}
 if(url.pathname==='/api/analytics'){queries.push(Object.fromEntries(url.searchParams));return json({...base,scenario:{...base.scenario,delta_hkd:Number(url.searchParams.get('shock'))*100}});}
 const file=url.pathname==='/'?'index.html':url.pathname.slice(1);const body=await fs.readFile(path.join(root,'web',file));res.writeHead(200,{'Content-Type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});res.end(body);
 }catch{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;const pump=setInterval(()=>{for(const res of streams)res.write('data: '+JSON.stringify(data)+'\n\n');},300);
try{browser=await chromium.launch({headless:true,executablePath:process.env.CHART_TEST_BROWSER||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});const page=await browser.newPage({viewport:{width:1365,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/#quant`);await page.getByRole('heading',{name:'价格与费用情景',exact:true}).waitFor();
 await page.getByLabel('价格情景',{exact:true}).selectOption('points');await page.getByLabel('变化幅度',{exact:true}).fill('-12');await page.getByRole('button',{name:'计算情景',exact:true}).click();await page.getByText('-1,200.00',{exact:true}).waitFor();assert.equal(queries.at(-1).shock,'-12');assert.equal(queries.at(-1).mode,'points');
 await page.getByRole('button',{name:'+2 ATR',exact:true}).click();await page.getByText('200.00',{exact:true}).waitFor();assert.equal(queries.at(-1).mode,'atr');
 await page.getByLabel('指标周期',{exact:true}).selectOption('DAY');await page.waitForTimeout(350);assert.equal(queries.at(-1).resolution,'DAY');
 await page.getByLabel('变化幅度',{exact:true}).fill('3.7');await page.waitForTimeout(800);assert.equal(await page.getByLabel('变化幅度',{exact:true}).inputValue(),'3.7');
 await page.getByRole('link',{name:/当前持仓/}).click();await page.getByRole('heading',{name:'按品种汇总 · 开仓均价',exact:true}).waitFor();assert((await page.locator('#view').innerText()).includes('103.0000')||(await page.locator('#view').innerText()).includes('103.0000'));
 await page.getByRole('link',{name:'∿ 仓位风险与量化',exact:true}).click();await page.getByRole('heading',{name:'价格与费用情景',exact:true}).waitFor();await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
 console.log(JSON.stringify({result:'PASS',fixture:'synthetic only',checks:['weighted averages visible','scenario submit while focused','preset updates','period query','SSE preserves typed controls','route remount','390px overflow','no JS errors']}));
}finally{clearInterval(pump);for(const r of streams)r.end();await browser?.close();await new Promise(r=>server.close(r));}
