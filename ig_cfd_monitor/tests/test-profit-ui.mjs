/** Pure render tests with synthetic data; no browser, credentials or IG calls. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const root=new URL('../',import.meta.url);
const live=await fs.readFile(new URL('web/live.js',root),'utf8');
const summary=live.slice(live.indexOf('function riskSummary()'),live.indexOf('function positionTable()'));
const context=vm.createContext({
 data:{funding:{total_deposits:30000},risk:{}},isOffline:false,
 num:v=>v==null?'—':Number(v).toLocaleString('en-HK',{minimumFractionDigits:2,maximumFractionDigits:2}),
 pill:s=>String(s),
});
vm.runInContext('function offline(){return isOffline;}\n'+summary,context);
const render=risk=>{context.data.risk=risk;return vm.runInContext('riskSummary()',context);};
const positive={status:'BELOW_THRESHOLD',equity_ratio:1.1,loss_fraction:0,profit_hkd:3000,profit_fraction:.1};
let html=render(positive);
assert(html.includes('累计 Profit · HKD')&&html.includes('+3,000.00')&&html.includes('+10.00%'));
assert(html.includes('/ 60% 提醒线'));
html=render({...positive,profit_hkd:0,profit_fraction:0});
assert(html.includes('0.00%')&&!html.includes('+0.00'));
html=render({...positive,status:'TRIGGERED',profit_hkd:-18000,profit_fraction:-.6,loss_fraction:.6});
assert(html.includes('-18,000.00')&&html.includes('-60.00%')&&html.includes('已达到提醒线'));
for(const status of ['UNKNOWN','STALE']){
 html=render({...positive,status});
 assert(!html.includes('+3,000.00')&&!html.includes('+10.00%')&&html.includes('待核验'));
}
context.isOffline=true;
assert(!render(positive).includes('+3,000.00'));

const risk=await fs.readFile(new URL('web/risk-ui.js',root),'utf8');
const pure=risk.slice(risk.indexOf('const esc='),risk.indexOf('function render()')).replace('export function','function');
const qc=vm.createContext({});
vm.runInContext(pure,qc);
const quality=v=>{qc.fixture=v;return vm.runInContext('qualityDetails(fixture)',qc);};
html=quality({reasons:[{message:'交易时段缺行情',detail:'<script>bad()</script>'}],gaps:[{start:1792443600,end:1792447200,kind:'unexplained',missing_bars:1}],session:{status:'UNVERIFIED'}});
assert(html.includes('受限原因')&&html.includes('交易时段缺行情')&&html.includes('HKT')&&html.includes('合约时段待核验'));
assert(!html.includes('<script>'));
html=quality({session:{status:'REGULAR_REFERENCE',reference_open:false,dst_active:true,timezone:'America/New_York',timezone_abbr:'EDT',week_hkt:'周一 06:00 至周六 05:00',daily_break_hkt:'周二至周五 05:00–06:00'},gaps:[{kind:'session',missing_bars:1}]});
assert(html.includes('休市，保留上次收盘状态')&&html.includes('夏令时')&&html.includes('常规休市'));
assert(!html.includes('受限原因'));
console.log('PASS: profit signs, 60% threshold, invalid/offline gating, session reasons and HTML escaping');
