import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const code=await fs.readFile(new URL('../web/spot-price.js',import.meta.url),'utf8');
const {quoteState,quoteLineOptions}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const now=1800000000;
const state=()=>({generated_at:new Date(now*1000).toISOString(),stream:'CONNECTED:WS-STREAMING',markets:[{epic:'OIL',bid:70,ask:70.03,timestamp:now*1000,market_status:'DEAL ',stale:false}]});
const q=s=>quoteState(s,'OIL',now);
let out=q(state());assert.equal(out.kind,'live');assert.equal(out.live,true);assert(Math.abs(out.spread-.03)<1e-8);
assert.equal(quoteLineOptions(out).lineWidth,2);assert.equal(quoteLineOptions(out).lineStyle,0);assert.equal(quoteLineOptions(out).title,'最新 Bid');
for(const timestamp of [now,String(now*1000),new Date(now*1000).toISOString()]){const s=state();s.markets[0].timestamp=timestamp;assert.equal(q(s).live,true);}
for(const delta of [-31,1]){const s=state();s.markets[0].timestamp=(now+delta)*1000;assert.equal(q(s).live,false);assert.equal(quoteLineOptions(q(s)).lineStyle,2);}
for(const key of ['stale','delayed']){const s=state();s.markets[0][key]=true;assert.equal(q(s).live,false);}
for(const market of ['CLOSED ','SUSPEND','SUSPENDED','OFFLINE','CLOSINGSONLY','AUCTIONNOEDIT','EDIT']){const s=state();s.markets[0].market_status=market;assert.equal(q(s).live,false);assert(!q(s).lineTitle.includes('最新'));}
for(const partial of [{chart_transport_stale:true},{stream:'DISCONNECTED'},{generated_at:new Date((now-16)*1000).toISOString()},{generated_at:new Date((now+1)*1000).toISOString()}])assert.equal(q({...state(),...partial}).live,false);
for(const partial of [{ask:69},{ask:null},{timestamp:null},{bid:NaN}]){const s=state();Object.assign(s.markets[0],partial);assert.equal(q(s).live,false);}
out=quoteState(state(),'NIKKEI',now);assert.equal(out.bid,null);assert.equal(out.ask,null);assert.equal(out.kind,'waiting');assert.equal(quoteLineOptions(out).lineVisible,false);
out=quoteState({markets:[]},'OIL',now);assert.equal(out.bid,null);assert.equal(quoteLineOptions(out).axisLabelVisible,false);
// Clock-only degradation works even if the stream sends no further state.
assert.equal(quoteState(state(),'OIL',now+16).kind,'stale');
console.log('PASS: latest-price state, IG dealing states, quote/snapshot age, delays, invalid pairs, future times, product isolation and line labels');
