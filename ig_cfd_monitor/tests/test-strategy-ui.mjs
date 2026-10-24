/** Pure rendering checks; no browser or IG requests. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const code=(await fs.readFile(new URL('../web/strategy-ui.js',import.meta.url),'utf8')).replace("import {accountURL} from '/charts.js';","const accountURL=x=>x;");
const {resultsMarkup,equitySVG}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const blocked=resultsMarkup({status:'BLOCKED',epic:'A',resolution:'HOUR',coverage:{bars:10},reasons:[{message:'<img src=x onerror=alert(1)>'}]},'<script>bad</script>');
assert(blocked.includes('暂停检验'));assert(blocked.includes('&lt;img'));assert(!blocked.includes('<img'));assert(!blocked.includes('<script>'));assert(!blocked.includes('模拟路径回撤 ≥ 60%'));
const sample={metrics:{trades:0,win_rate:null,profit_factor:null,mean_r:null,return_pct:0,max_drawdown_pct:0},curve:[{time:1,equity:100},{time:2,equity:100}],trades:[]};
const ready={status:'READY',epic:'A',resolution:'HOUR',options:{risk_pct:.5},coverage:{bars:200,start:1,end:2,split_at:1.7},in_sample:sample,out_of_sample:sample,monte_carlo:{status:'INSUFFICIENT'},recommendations:[]};
let html=resultsMarkup(ready);assert(html.includes('Monte Carlo 暂不显示概率'));assert(html.includes('不是完整净收益')===false);assert(html.includes('部分成本'));assert(html.includes('后段一旦参与选规则'));assert(!html.includes('NaN'));assert(!html.includes('Infinity'));assert(html.includes('不能据此判断策略有效或推断未来胜率'));assert(html.includes('初步结果 · 交易样本不足'));
ready.monte_carlo={status:'READY',paths:500,horizon:50,block_length:3,seed:7,terminal:{p05:98,p50:100,p95:104},drawdown:{p50:1,p95:5,probability_ge_60_pct:0},fan:[{trade:0,p05:100,p50:100,p95:100},{trade:50,p05:98,p50:100,p95:104}]};
html=resultsMarkup(ready);assert(html.includes('不是未来价格'));assert(html.includes('累计入金'));assert(html.includes('60% 是模拟曲线从高点回撤'));
for(const points of [[],[{equity:NaN}],[{equity:100},{equity:100}],[{equity:-40},{equity:100}],Array.from({length:20000},(_,i)=>({equity:100+i/20000}))]){const svg=equitySVG(points);assert(!svg.includes('NaN'));assert(!svg.includes('Infinity'));assert(svg.length<30000);}
console.log('Strategy UI: data gates, unverified claims, escaping and bounded curves passed');
