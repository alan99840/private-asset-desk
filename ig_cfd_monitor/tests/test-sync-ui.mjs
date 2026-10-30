/** Render regression checks with synthetic data; no network or account credentials. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const source=await fs.readFile(new URL('../web/live.js',import.meta.url),'utf8');
const elements=new Map();
const element=()=>({textContent:'',innerHTML:'',className:'',children:[],replaceChildren(){this.children=[];},append(...children){this.children.push(...children);}});
const context=vm.createContext({
 document:{querySelector(selector){if(!elements.has(selector))elements.set(selector,element());return elements.get(selector);},querySelectorAll:()=>[],createElement:element},
 location:{hash:'#positions'},URLSearchParams,
 unmountCharts(){},unmountRisk(){},unmountStrategy(){},accountOverview:()=>'',accountURL:x=>x,
});
vm.runInContext(source.replace(/^import .*;\n/gm,'').split('const stream=new EventSource')[0],context);
const now=new Date().toISOString();
const healthy=()=>({status:'connected',environment:'live',stream:'CONNECTED:WS-STREAMING',stale:false,
 generated_at:now,account_at:now,account_rest_at:now,positions_at:now,orders_at:now,positions_stale:false,orders_stale:false,
 sync:{account:{status:'ok'},positions:{status:'ok'},orders:{status:'ok'}},
 account:{equity:30000,available:29000,unrealized:100,margin:1000},positions:[],position_groups:[],orders:[],markets:[],funding:{},alerts:[]});
function render(state,isOffline=false){context.fixture=state;context.isOffline=isOffline;vm.runInContext("data=fixture;transport=!isOffline;lastMessage=Date.now();view='positions';render();",context);return {banner:elements.get('#preview-banner'),body:elements.get('#view').innerHTML,count:elements.get('#position-count').textContent,button:elements.get('#connection-button').textContent};}
let result=render(healthy());
assert.equal(result.button,'IG 已连接');assert.equal(result.count,0);assert(!result.banner.className.includes('stale'));
assert(result.body.includes('当前已同步持仓为 0')&&result.body.includes('当前已同步挂单为 0'));
assert(!result.body.includes('持仓待核验'));

let state=healthy();state.status='partial';state.positions_at='2026-10-26T05:24:04Z';state.positions_stale=true;
state.sync.positions={status:'retrying',error:'ig.network.or.tls.error'};
result=render(state);
assert(result.banner.className.includes('stale'));assert(result.banner.children[0].textContent.includes('持仓同步待核验'));
assert(result.banner.children[1].textContent.includes('ig.network.or.tls.error'));
assert.equal(result.count,'待核验');assert(result.body.includes('最后记录为 0 笔 · 当前待核验'));
assert(result.body.includes('最后成功同步：')&&result.body.includes('ig.network.or.tls.error'));
assert(result.body.includes('IG 账户现金 + 浮动盈亏')); // Fresh equity remains independently usable.
assert(!result.body.includes('当前已同步持仓为 0'));

// Even a stale status label cannot hide the independent component freshness flags.
state.status='connected';state.sync.positions.status='ok';result=render(state);
assert(result.banner.className.includes('stale'));assert.equal(result.button,'同步待核验');
state=healthy();state.sync.orders={status:'retrying',error:'<script>unsafe</script>'};state.stream='DISCONNECTED';
result=render(state);
assert(result.banner.children[0].textContent.includes('挂单同步待核验'));
assert(!result.banner.children[1].textContent.includes('已通过 REST 对账'));
assert(result.body.includes('最后记录为 0 笔挂单 · 当前待核验'));
assert(result.body.includes('&lt;script&gt;unsafe&lt;/script&gt;')&&!result.body.includes('<script>unsafe</script>'));
assert(result.body.includes('笔数待核验'));

state=healthy();state.positions=[{instrumentName:'Synthetic',epic:'SYNTH',direction:'BUY',size:1,level:100}];state.positions_at=null;
result=render(state);assert(result.body.includes('Synthetic')&&result.body.includes('尚未成功同步'));assert.equal(result.count,'待核验');
state=healthy();state.positions_at=new Date(Date.now()+60000).toISOString();assert(render(state).banner.className.includes('stale'));
state=healthy();state.positions_at=new Date(Date.now()-91000).toISOString();assert(render(state).banner.className.includes('stale'));
result=render(healthy(),true);assert.equal(result.button,'后台连接中断');assert(result.body.includes('后台连接中断。'));assert.equal(result.count,'待核验');
state=healthy();state.status='error';state.stream='DISCONNECTED';state.message='error.security.invalid-details';result=render(state);assert.equal(result.button,'IG 待连接');assert(result.banner.children[1].textContent.includes('invalid-details'));
console.log('PASS: partial sync, stale/empty positions and orders, independent equity, safe errors, offline and healthy UI');

// Fleet counts do not inherit the account stream freshness.
const accountSource=await fs.readFile(new URL('../web/account-ui.js',import.meta.url),'utf8');
const fleetContext=vm.createContext({location:{search:''},URLSearchParams});
vm.runInContext(accountSource.replaceAll('export function','function'),fleetContext);
fleetContext.fixture={generated_at:now,totals:[],profiles:[{id:'synthetic',name:'Synthetic',enabled:true,status:'connected',stale:false,positions_stale:true,positions_at:now,positions_count:21,account:{equity:30000},funding:{},risk:{}}]};
let fleetHtml=vm.runInContext('state=fixture;lastSuccess=Date.now();accountOverview()',fleetContext);
assert(fleetHtml.includes('上次记录 21 笔')&&fleetHtml.includes('持仓笔数</span><strong>待核验'));
fleetContext.fixture.profiles[0].positions_stale=false;
fleetHtml=vm.runInContext('accountOverview()',fleetContext);
assert(fleetHtml.includes('持仓笔数</span><strong>21'));

// Chart reference lines refresh their label when holdings become unverified.
const chartSource=await fs.readFile(new URL('../web/charts.js',import.meta.url),'utf8');
const references=chartSource.slice(chartSource.indexOf('  references(){'),chartSource.indexOf('  async load('));
const lineOptions=[];
const chartContext=vm.createContext({finite:Number.isFinite,lineOptions});
vm.runInContext('var widget={'+references+'};',chartContext);
chartContext.fixture={positions_at:now,positions_stale:false,sync:{positions:{status:'ok'}},positions:[{epic:'SYNTH',dealId:'one',stopLevel:90}]};
vm.runInContext("widget.chart={};widget.epic='SYNTH';widget.type='candle';widget.data=fixture;widget.priceLines=[];widget.series={createPriceLine:o=>(lineOptions.push(o),o),removePriceLine(){}};widget.references();",chartContext);
assert.equal(lineOptions.at(-1).title,'止损');
chartContext.fixture.positions_stale=true;
vm.runInContext('widget.references()',chartContext);
assert.equal(lineOptions.at(-1).title,'止损 · 待核验');
console.log('PASS: fleet position freshness and chart stop/limit stale labels');
