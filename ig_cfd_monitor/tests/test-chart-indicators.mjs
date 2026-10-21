/** Synthetic-only math, pane lifecycle and optional local UI preview. No IG calls. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=new URL('../',import.meta.url),code=await fs.readFile(new URL('web/chart-indicators.js',root),'utf8');
const {indicatorData,indicatorSegments,indicatorNotice,ChartIndicators,INDICATORS,interpretationMarkup,positionReading,RSI_BANDS}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const epoch=1800000000;
const bars=Array.from({length:100},(_,i)=>({time:epoch-(100-i)*3600,open:100+i,high:102+i,low:98+i,close:100+i}));
const almost=(a,b)=>assert(Math.abs(a-b)<1e-9,`${a} != ${b}`);
let data=indicatorData(bars,epoch);
assert.equal(data.bars.length,99);almost(data.ema20.at(-1).value,188.5);almost(data.ema50.at(-1).value,173.5);almost(data.atr14.at(-1).value,4);assert.equal(data.rsi14.at(-1).value,100);
assert.equal(data.ema20[0].time,bars[19].time);assert.equal(data.ema50[0].time,bars[49].time);assert.equal(data.atr14[0].time,bars[14].time);
for(const n of [0,1,14,15,19,20,49,50]){const out=indicatorData(bars.slice(0,n),epoch);assert.equal(out.ema20.length,Math.max(0,n-20));assert.equal(out.ema50.length,Math.max(0,n-50));assert.equal(out.atr14.length,Math.max(0,n-15));assert.equal(out.rsi14.length,Math.max(0,n-15));}
const constant=bars.map(b=>({...b,open:42,high:42,low:42,close:42}));
data=indicatorData(constant,epoch);assert.equal(data.rsi14.at(-1).value,50);assert.equal(data.atr14.at(-1).value,0);
const falling=bars.map((b,i)=>({...b,open:100-i,high:102-i,low:98-i,close:100-i}));assert.equal(indicatorData(falling,epoch).rsi14.at(-1).value,0);
const changedTail=structuredClone(bars);changedTail.at(-1).close=1e9;changedTail.at(-1).high=1e9;
for(const key of INDICATORS.map(d=>d.key))assert.deepEqual(indicatorData(changedTail,epoch)[key],indicatorData(bars,epoch)[key]);
assert.deepEqual(indicatorData([...bars,{...bars.at(-1),time:epoch+100}],epoch),indicatorData(bars,epoch));
const mixed=bars.map((b,i)=>{const close=40+Math.sin(i*1.7)*3+i*.03;return {...b,open:close,high:close+1+(i%3),low:close-.7,close};});
const python=execFileSync(fileURLToPath(new URL('.venv/bin/python',root)),['-c',"import json,sys;from quant import indicators;x=json.load(sys.stdin);print(json.dumps([indicators(x['bars'][:n],'HOUR',x['epoch']) for n in (62,75,100)]))"],{cwd:fileURLToPath(root),input:JSON.stringify({bars:mixed,epoch}),encoding:'utf8'});
JSON.parse(python).forEach((expected,i)=>{const result=indicatorData(mixed.slice(0,[62,75,100][i]),epoch);for(const key of INDICATORS.map(d=>d.key))almost(result[key].at(-1).value,expected[key]);});
const rows=[{time:1,value:10},{time:2,value:11},{time:6,value:14},{time:7,value:13}];
assert.deepEqual(indicatorSegments(rows,[{from:2,to:6}]),[rows.slice(0,2),rows.slice(2)]);assert.deepEqual(indicatorSegments(rows,[]),[rows]);
assert(indicatorNotice(indicatorData([]),{}).text.includes('样本不足'));
assert(indicatorNotice(indicatorData(bars,epoch),{status:'error',message:'fixture failure'}).limited);
assert(indicatorNotice(indicatorData(bars,epoch),{status:'ready',indicator_quality:{reasons:[{code:'stale_bars',message:'行情过期'}]}}).text.includes('行情过期'));
assert(!indicatorNotice(indicatorData(bars,epoch),{status:'ready',gaps:[{from:2,to:6}],indicator_quality:{reasons:[]}}).limited,'verified session closure does not become an unexplained gap warning');
// Test pane isolation and visibility against a small chart API contract, not a browser.
const nodes=new Map(),container={querySelector(selector){if(!nodes.has(selector))nodes.set(selector,{textContent:'',hidden:false,style:{},dataset:{},classList:{toggle(){}}});return nodes.get(selector);}};
const main={getHeight:()=>450,setStretchFactor(){},priceScale:()=>({applyOptions(){}})},panes=[main];let series=[];
const chart={timeScale:()=>({getVisibleRange:()=>({from:1,to:100}),setVisibleRange(){}}),panes:()=>panes,addSeries(type,options,index){while(panes.length<=index)panes.push({...main,getHeight:()=>150});const pane=panes[index];const item={pane,options,rows:[],lines:[],setData(rows){this.rows=rows;},createPriceLine(line){this.lines.push(line);}};series.push(item);return item;},removeSeries(item){series=series.filter(s=>s!==item);for(let i=panes.length-1;i>0;i--)if(!series.some(s=>s.pane===panes[i]))panes.splice(i,1);}};
const controller=new ChartIndicators(chart,container,{LineSeries:'line'});
controller.update(bars,{status:'ready',indicator_quality:{calculated_at:epoch,reasons:[]}});
assert.equal(panes.length,3);assert.equal(series.filter(s=>s.pane===main).length,2);
assert.deepEqual(series.find(s=>s.options.title==='RSI 14').lines.map(l=>l.price),[30,50,70]);
controller.toggle('atr14',false);assert.equal(panes.length,2);assert.equal(nodes.get('#indicator-pane-atr14').hidden,true);
controller.toggle('rsi14',false);assert.equal(panes.length,1);controller.toggle('ema20',false);assert.equal(series.length,1);
controller.toggle('atr14',true);controller.toggle('rsi14',true);controller.toggle('ema20',true);assert.equal(panes.length,3);
controller.reading(bars.at(-1).time);assert.equal(nodes.get('#indicator-value-ema20').textContent,'—','unconfirmed tail never inherits the latest confirmed indicator');
controller.update([]);assert(series.every(s=>s.rows.length===0),'switching product clears all old curves');assert.equal(nodes.get('#indicator-value-ema20').textContent,'—');
// Current reading must never survive stale, missing or switched chart data.
const stateAt=t=>({generated_at:new Date(t*1000).toISOString(),positions_at:new Date(t*1000).toISOString(),sync:{positions:{status:'ok'}},positions:[{epic:'A',direction:'SELL',size:1,stopLevel:106},{epic:'OTHER',direction:'BUY'}],markets:[{epic:'A',bid:103,ask:105.5,timestamp:new Date(t*1000).toISOString()}]});
const interpretation={status:'READY',regime:'UP',rsi_band:'overbought',as_of:epoch-600,observations:99,summary:'合成偏多',reasons:[],values:{close:104,atr14:2,rsi14:71},levels:[{key:'high20',label:'前 20 根高点',price:105}],watch:['观察'],risks:['超买'],extension_atr:2,atr_percent:2};
const response={status:'ready',indicator_quality:{status:'READY',calculated_at:epoch},interpretation};
let rendered=interpretationMarkup(response,stateAt(epoch),'A','5 分钟',epoch);
assert(rendered.includes('当前偏多，你的空仓'));assert(rendered.includes('不足 1 ATR'),'short stop uses Ask, not Bid');
for(const timestamp of [epoch*1000,String(epoch*1000),epoch]){const state=stateAt(epoch);state.markets[0].timestamp=timestamp;assert(positionReading(state,'A',interpretation,epoch).some(s=>s.includes('不足 1 ATR')),'IG stream numeric timestamps are supported');}
assert.equal((rendered.match(/aria-current="true"/g)||[]).length,1);assert(rendered.includes('&gt; 70'));
assert.equal(RSI_BANDS.length,4);
for(const key of RSI_BANDS.map(b=>b.key)){const html=interpretationMarkup({...response,interpretation:{...interpretation,rsi_band:key}},stateAt(epoch),'A','日',epoch);assert.equal((html.match(/aria-current="true"/g)||[]).length,1);}
for(const blocked of [null,{...response,status:'error',message:'读取失败'},{...response,indicator_quality:{status:'LIMITED',calculated_at:epoch}},{...response,indicator_quality:{status:'READY',calculated_at:epoch-91}},{...response,indicator_quality:{status:'READY',calculated_at:epoch+1}}]){
 const html=interpretationMarkup(blocked,stateAt(epoch),'A','日',epoch);assert(html.includes('暂停判断'));assert(!html.includes('chart-key-level"'));assert(!html.includes('aria-current="true"'));assert(!html.includes('当前偏多，你的空仓'));
}
for(const state of [{...stateAt(epoch),chart_transport_stale:true},stateAt(epoch-16)])assert(interpretationMarkup(response,state,'A','日',epoch).includes('暂停判断'));
assert(positionReading(stateAt(epoch),'OTHER',interpretation,epoch).some(s=>s.includes('1 笔多仓、0 笔空仓')));
assert(positionReading({...stateAt(epoch),positions_at:new Date((epoch-91)*1000).toISOString()},'A',interpretation,epoch)[0].includes('同步'));
assert(positionReading({...stateAt(epoch),sync:{positions:{status:'retrying'}}},'A',interpretation,epoch)[0].includes('同步'));
assert(positionReading({...stateAt(epoch),markets:[{epic:'A',bid:107,ask:108,timestamp:new Date(epoch*1000).toISOString()}]},'A',interpretation,epoch).some(s=>s.includes('触及或越过')));
assert(positionReading({...stateAt(epoch),markets:[{epic:'A',bid:107,ask:106,timestamp:new Date(epoch*1000).toISOString()}]},'A',interpretation,epoch).some(s=>s.includes('暂停止损距离')));
assert(!interpretationMarkup({...response,interpretation:{...interpretation,summary:'<img src=x onerror=alert(1)>'}},stateAt(epoch),'<script>','日',epoch).includes('<img'));
console.log('PASS: EMA/Wilder math, Python parity, warm-up, unconfirmed/future bars, gap segmentation, limits, pane toggles, product reset, reading gates and position risk');

if(process.argv.includes('--serve')){
 const http=await import('node:http');let now=Math.floor(Date.now()/1000);const steps={MINUTE:60,MINUTE_5:300,MINUTE_15:900,MINUTE_30:1800,HOUR:3600,DAY:86400,WEEK:604800,MONTH:2592000};
 const state={stream:'CONNECTED:WS-STREAMING',sync:{positions:{status:'ok'}},positions_at:new Date().toISOString(),status:'connected',environment:'demo',stale:false,generated_at:new Date().toISOString(),message:'SYNTHETIC INDICATOR PREVIEW',positions:[{epic:'FIXTURE.OIL',instrumentName:'指标测试 · 合成原油',dealId:'FIXTURE-A',direction:'BUY',size:.1,level:99,currency:'USD'}],markets:[{epic:'FIXTURE.OIL',name:'指标测试 · 合成原油',bid:105,ask:105.1,timestamp:new Date().toISOString()},{epic:'FIXTURE.SHORT',name:'样本不足 · 合成数据'},{epic:'FIXTURE.EMPTY',name:'无数据 · 合成测试'}],account:{equity:30000,available:28000,unrealized:0,margin:2000},series:[],risk:{status:'UNKNOWN'},news:{items:[]},history:{},funding:{},margin:{},alerts:[],orders:[]};
 const json=(res,data)=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
 const server=http.createServer(async(req,res)=>{try{now=Math.floor(Date.now()/1000);state.generated_at=state.positions_at=new Date().toISOString();state.markets[0].timestamp=state.generated_at;const url=new URL(req.url,'http://fixture');if(url.pathname==='/api/events'){res.writeHead(200,{'Content-Type':'text/event-stream'});res.write('data: '+JSON.stringify(state)+'\n\n');return;}if(url.pathname==='/api/state')return json(res,state);if(url.pathname==='/api/accounts')return json(res,{profiles:[],totals:[]});if(url.pathname==='/api/chart/trades')return json(res,{epic:url.searchParams.get('epic'),markers:[],omitted:[]});if(url.pathname==='/api/chart/drawings')return json(res,{epic:url.searchParams.get('epic'),drawings:[],revision:0});if(url.pathname==='/api/chart'){const step=steps[url.searchParams.get('resolution')]||300,count=url.searchParams.get('epic')==='FIXTURE.EMPTY'?0:url.searchParams.get('epic')==='FIXTURE.SHORT'?10:url.searchParams.has('before')?400:200;const candles=Array.from({length:count},(_,i)=>{const close=100+Math.sin(i/9)*3+i*.025;return {time:Math.floor(now/step)*step-(count-1-i)*step,open:close-.3*Math.sin(i),close,high:close+.4,low:close-.5};});const computed=JSON.parse(execFileSync(fileURLToPath(new URL('.venv/bin/python',root)),['-c',"import json,sys;from quant import indicators,chart_reading;x=json.load(sys.stdin);q=indicators(x['candles'],x['resolution'],x['now']);print(json.dumps({'indicator_quality':dict(q,calculated_at=x['now']),'interpretation':chart_reading(q,{'status':'ready'})}))"],{cwd:fileURLToPath(root),input:JSON.stringify({candles,resolution:url.searchParams.get('resolution'),now}),encoding:'utf8'}));return json(res,{status:'ready',candles,gaps:[],has_more:true,next_before:candles[0]?.time,...computed});}const name=url.pathname==='/'?'index.html':url.pathname.slice(1);if(name.includes('..'))throw Error('path');const raw=await fs.readFile(new URL('web/'+name,root));res.writeHead(200,{'Content-Type':name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.svg')?'image/svg+xml':'text/html'});res.end(raw);}catch{res.writeHead(404);res.end();}});
 server.listen(8790,'127.0.0.1',()=>console.log('Synthetic indicator preview: http://127.0.0.1:8790/#charts'));
}
