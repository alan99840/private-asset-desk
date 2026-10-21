/* Descriptive indicators of observed IG Bid bars; no synthetic prices or orders. */
export const INDICATORS = Object.freeze([
  {key:'ema20',label:'EMA 20',color:'#bc7b19',pane:0,minimum:20,description:'20 根指数均线'},
  {key:'ema50',label:'EMA 50',color:'#4775b9',pane:0,minimum:50,description:'50 根指数均线'},
  {key:'atr14',label:'ATR 14',color:'#157e83',pane:1,minimum:15,description:'平均真实波幅 · 点'},
  {key:'rsi14',label:'RSI 14',color:'#8063b1',pane:2,minimum:15,description:'相对强弱 · 0–100'},
]);
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const number=v=>finite(v)?v.toLocaleString('en-HK',{minimumFractionDigits:2,maximumFractionDigits:2}):'—';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const price=v=>finite(v)?v.toLocaleString('en-HK',{maximumFractionDigits:6}):'—';
const epochOf=v=>{
  if(typeof v==='number'||(typeof v==='string'&&/^[+-]?\d+(\.\d+)?$/.test(v.trim()))){const n=Number(v);return finite(n)?(Math.abs(n)>=1e11?n/1000:n):NaN;}
  return typeof v==='string'?Date.parse(v)/1000:NaN;
};
const fresh=(time,now,seconds)=>finite(time)&&now-time>=0&&now-time<=seconds;
export const RSI_BANDS=Object.freeze([
  {key:'overbought',range:'> 70',title:'偏强 · 超买',note:'关注回撤，强势可持续'},
  {key:'strong',range:'50–70',title:'动量偏强',note:'50 恰为中性分界'},
  {key:'weak',range:'30–50',title:'动量偏弱',note:'包含 30，不包含 50'},
  {key:'oversold',range:'< 30',title:'偏弱 · 超卖',note:'关注反弹，弱势可持续'},
]);

export function positionReading(state,epic,reading,now=Date.now()/1000){
  if(state.chart_transport_stale||!fresh(epochOf(state.generated_at),now,15)||
      !fresh(epochOf(state.positions_at),now,90)||!Array.isArray(state.positions)||
      (state.sync?.positions&&state.sync.positions.status!=='ok'))
    return ['持仓同步尚未完成或已过期；暂停针对当前持仓的风险判断。'];
  const positions=state.positions.filter(p=>p.epic===epic),notes=[];
  if(!positions.length)return ['此产品当前没有已同步持仓，以上仅为行情观察。'];
  const longs=positions.filter(p=>p.direction==='BUY'),shorts=positions.filter(p=>p.direction==='SELL');
  notes.push(`当前持仓：${longs.length} 笔多仓、${shorts.length} 笔空仓。`);
  if(longs.length+shorts.length!==positions.length)notes.push('有持仓方向待核验，未将其归入多空风险。');
  if(longs.length&&shorts.length)notes.push('同一产品同时持有多仓与空仓；分别承担价格风险，多空抵消不代表没有保证金、点差或融资成本。');
  if(reading?.status==='READY'){
    if(reading.regime==='UP'&&shorts.length)notes.push('当前偏多，你的空仓面对逆势上行风险；重点观察前 20 根高点及上方延伸。');
    if(reading.regime==='DOWN'&&longs.length)notes.push('当前偏空，你的多仓面对逆势下行风险；重点观察前 20 根低点及下方延伸。');
    if(reading.regime==='RANGE')notes.push('当前按震荡 / 分歧观察，持仓需关注区间两端和假突破；上下方向均未形成确认。');
    if(reading.rsi_band==='overbought')notes.push(`${longs.length?'多仓注意回撤；':''}${shorts.length?'空仓注意强势延续，不能将超买等同下跌；':''}RSI 超买不自动触发交易。`);
    if(reading.rsi_band==='oversold')notes.push(`${shorts.length?'空仓注意反弹；':''}${longs.length?'多仓注意弱势延续，不能将超卖等同上涨；':''}RSI 超卖不自动触发交易。`);
  }
  const missing=positions.filter(p=>!finite(p.stopLevel));
  if(missing.length)notes.push(`${missing.length} / ${positions.length} 笔持仓未返回有效止损价；价格跳空、滑点与隔夜费用可能扩大损失。`);
  const quote=(state.markets||[]).find(m=>m.epic===epic);
  if(!quote||quote.stale||quote.delayed||!fresh(epochOf(quote.timestamp),now,30)||
      !finite(quote.bid)||!finite(quote.ask)||quote.ask<quote.bid){
    notes.push('当前 Bid / Ask 不足、延迟或过期，暂停止损距离测算。');return notes;
  }
  const distances=positions.filter(p=>['BUY','SELL'].includes(p.direction)&&finite(p.stopLevel))
    .map(p=>p.direction==='BUY'?quote.bid-p.stopLevel:p.stopLevel-quote.ask);
  if(distances.some(d=>d<=0))notes.push('至少一笔报价已触及或越过止损参考价；这不是成交确认，请核对 IG 最新持仓与成交记录。');
  const atr=reading?.status==='READY'?reading.values?.atr14:null;
  if(atr>0&&distances.some(d=>d>0&&d<atr))notes.push('至少一笔持仓距止损不足 1 ATR，普通波动也可能触及；此处仅比较距离，不调整止损。');
  return notes;
}

export function interpretationMarkup(response,state,epic,period,now=Date.now()/1000){
  const raw=response?.interpretation,quality=response?.indicator_quality,reasons=[...(raw?.reasons||[])];
  if(!raw)reasons.push('等待当前产品与周期的指标核验。');
  if(state.chart_transport_stale||!fresh(epochOf(state.generated_at),now,15))reasons.push('后台连接中断或账户快照已过期。');
  if(raw&&!fresh(quality?.calculated_at,now,90))reasons.push('图表核验结果已过期，等待刷新。');
  if(response&&response.status!=='ready')reasons.push(response.message||'历史行情读取尚未就绪。');
  const ready=raw?.status==='READY'&&quality?.status==='READY'&&!reasons.length;
  const r=ready?raw:null,v=r?.values||{},bias=r?({UP:'偏多',DOWN:'偏空',RANGE:'震荡 / 分歧'}[r.regime]||'待核验'):'暂停判断';
  const band=RSI_BANDS.find(b=>b.key===r?.rsi_band);
  const time=r?.as_of?new Date(r.as_of*1000).toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong',hour12:false}):'—';
  const levels=(r?.levels||[]).map(l=>`<div class="chart-key-level"><span>${esc(l.label)}</span><strong>${price(l.price)}</strong><small>${l.key.startsWith('atr_')?'波动尺 · 非目标价':l.price>v.close?'位于已收盘价上方':l.price<v.close?'位于已收盘价下方':'与已收盘价相同'}</small></div>`).join('');
  const list=rows=>`<ul>${rows.map(s=>`<li>${esc(s)}</li>`).join('')}</ul>`;
  return `<div class="chart-insight-heading"><div><h2>趋势解读与风险提示</h2><p>${esc(period)} · ${esc(epic)} · 仅针对当前图表周期</p></div><strong class="chart-bias chart-bias-${r?.regime||'WAIT'}" role="status">${bias}</strong></div>
    ${r?`<p class="chart-insight-summary">${esc(r.summary)}</p><div class="chart-insight-metrics"><span>已确认收盘价 <b>${price(v.close)}</b></span><span>ATR14 <b>${price(v.atr14)} 点</b>${finite(r.atr_percent)?` · ${number(r.atr_percent)}%`:''}</span><span>距 EMA20 <b>${finite(r.extension_atr)?number(r.extension_atr)+' ATR':'—'}</b></span></div><p class="chart-insight-asof">K 线起始时间 ${esc(time)} HKT · ${esc(r.observations)} 根已确认收盘样本${quality?.session?.reference_open===false?' · 常规参考时段休市，按最后有效收盘解读':''}</p>`:`<div class="chart-insight-blocked">${list([...new Set(reasons.length?reasons:['行情或指标数据受限，等待有效数据。'])])}<p>保留上方历史曲线；当前趋势、RSI 档位和关键价位暂停发布。</p></div>`}
    <div class="chart-rsi-heading"><h3>RSI14 四档</h3><strong>${r?number(v.rsi14)+' · '+esc(band?.title):'等待有效数据'}</strong></div>
    <div class="chart-rsi-bands" aria-label="RSI 四档区间">${RSI_BANDS.map(b=>`<div class="chart-rsi-band ${b.key===r?.rsi_band?'is-current':''}" ${b.key===r?.rsi_band?'aria-current="true"':''}><strong>${esc(b.range)}</strong><span>${esc(b.title)}</span><small>${esc(b.note)}</small>${b.key===r?.rsi_band?'<b>当前所在档</b>':''}</div>`).join('')}</div>
    ${r?`<h3>需要关注的关键价位</h3><div class="chart-key-levels">${levels}</div><p class="chart-insight-asof">20 / 60 根高低点不包含用于判断突破的最新已收盘 K 线。高低点是观察参考，突破后不固定充当支撑或阻力。</p><div class="chart-insight-columns"><div><h3>接下来观察什么</h3>${list(r.watch)}</div><div><h3>指标风险提示</h3>${list(r.risks)}</div></div>`:''}
    <div class="chart-position-notes"><h3>与你的持仓有关</h3>${list(positionReading(state,epic,r,now))}</div>
    <details class="chart-insight-method"><summary>判断规则与价格口径</summary><p>本平台规则：收盘价 > EMA20 > EMA50 且 RSI > 50 为偏多；反向排列且 RSI < 50 为偏空；其余按震荡 / 分歧观察。70 归入 50–70，30 归入 30–50；50 归入上档但动量中性。ATR 只描述波动，不决定多空。不同周期可得到不同结论。</p><p>关键价位基于 IG Bid，空仓平仓和止损距离参考 Ask；点差、融资费用和跳空会影响 CFD 实际结果。此处是规则化状态解读，未提供胜率或价格预测，不执行交易。<a href="https://www.ig.com/sg/trading-strategies/relative-strength-index--rsi---the-complete-guide-for-traders-250717" target="_blank" rel="noopener noreferrer">IG RSI 说明 ↗</a> · <a href="https://www.ig.com/en/trading-opportunities/how-to-trade-average-true-range-41931-180201" target="_blank" rel="noopener noreferrer">IG ATR 说明 ↗</a></p></details>`;
}

export function indicatorData(candles,epoch=Date.now()/1000){
  const known=candles.filter(b=>b.time<=epoch),bars=known.slice(0,-1);
  const result={bars,excludedTail:known.length?1:0,ema20:[],ema50:[],atr14:[],rsi14:[]};
  for(const period of [20,50]){
    let sum=0,value;
    bars.forEach((b,i)=>{if(i<period)sum+=b.close;if(i===period-1)value=sum/period;else if(i>=period)value+=2/(period+1)*(b.close-value);if(i>=period-1)result['ema'+period].push({time:b.time,value});});
  }
  let atr=0,gain=0,loss=0;
  for(let i=1;i<bars.length;i++){
    const b=bars[i],previous=bars[i-1],change=b.close-previous.close;
    const tr=Math.max(b.high-b.low,Math.abs(b.high-previous.close),Math.abs(b.low-previous.close));
    if(i<=14){atr+=tr/14;gain+=Math.max(0,change)/14;loss+=Math.max(0,-change)/14;}
    else {atr=(atr*13+tr)/14;gain=(gain*13+Math.max(0,change))/14;loss=(loss*13+Math.max(0,-change))/14;}
    if(i>=14){result.atr14.push({time:b.time,value:atr});result.rsi14.push({time:b.time,value:!gain&&!loss?50:loss===0?100:100-100/(1+gain/loss)});}
  }
  return result;
}

// Split visual lines at unfilled intervals without changing the recursive
// calculation over observed bars. Never interpolate a missing candle/value.
export function indicatorSegments(rows,gaps=[]){
  const segments=[];let current=[];
  for(const row of rows){const previous=current.at(-1);if(previous&&gaps.some(g=>finite(g.from)&&finite(g.to)&&previous.time<=g.from&&row.time>=g.to)){segments.push(current);current=[];}current.push(row);}
  if(current.length)segments.push(current);return segments;
}

export function indicatorNotice(data,response={}){
  const q=response.indicator_quality,reasons=[];
  if(response.status&&response.status!=='ready')reasons.push(response.message||({loading:'历史行情正在更新',limited:'IG 历史行情额度受限',error:'历史行情读取失败'}[response.status]||'历史行情待核验'));
  if(response.skipped_rows)reasons.push(`${response.skipped_rows} 根原始 K 线无效，已跳过`);
  if(q)reasons.push(...(q.reasons||[]).filter(r=>r.code!=='insufficient_bars').map(r=>r.message));
  else if(response.gaps?.length)reasons.push('历史区间存在未核验缺口');
  const warm=INDICATORS.filter(d=>!data[d.key].length).map(d=>`${d.label} 需 ${d.minimum} 根`);
  return {limited:reasons.length>0,text:[`${data.bars.length} 根已确认收盘 K 线；末根未确认，指标不纳入`,warm.length?'样本不足：'+warm.join('、'):'',reasons.length?'数据受限：'+[...new Set(reasons)].join('；')+'。曲线仅作历史观察。':'',!q?'时段与新鲜度尚待核验。':''].filter(Boolean).join(' · ')};
}

export class ChartIndicators {
  constructor(chart,container,L){
    this.chart=chart;this.container=container;this.L=L;this.series=[];this.visible=new Set(INDICATORS.map(d=>d.key));this.data=indicatorData([]);this.response={};
  }
  update(candles,response={}){
    this.response=response;const epoch=response.indicator_quality?.calculated_at??Date.now()/1000;
    const key=JSON.stringify([candles,response.gaps,candles.filter(b=>b.time<=epoch).length]);
    if(key!==this.key){this.key=key;this.data=indicatorData(candles,epoch);this.lookup=new Map(this.data.bars.map(b=>[b.time,{}]));for(const d of INDICATORS)for(const row of this.data[d.key])this.lookup.get(row.time)[d.key]=row.value;this.render();}
    const notice=indicatorNotice(this.data,response),el=this.container.querySelector('#chart-indicator-status');el.textContent=notice.text;el.classList.toggle('chart-status-warning',notice.limited);this.reading();
  }
  toggle(key,on){if(!INDICATORS.some(d=>d.key===key))return;on?this.visible.add(key):this.visible.delete(key);this.render();this.reading();}
  render(){
    const range=this.chart.timeScale().getVisibleRange();for(const s of this.series)this.chart.removeSeries(s);this.series=[];this.panes=new Map();let nextPane=1;
    for(const d of INDICATORS){
      if(!this.visible.has(d.key))continue;const pane=d.pane?nextPane++:0;if(pane)this.panes.set(d.key,pane);
      const segments=indicatorSegments(this.data[d.key],this.response.gaps);if(!segments.length)segments.push([]);
      segments.forEach((rows,i)=>{
        const last=i===segments.length-1,options={title:last?d.label:'',color:d.color,lineWidth:2,priceLineVisible:false,lastValueVisible:last,crosshairMarkerVisible:true,priceFormat:{type:'price',precision:2,minMove:.01}};
        if(d.key==='rsi14')options.autoscaleInfoProvider=()=>({priceRange:{minValue:0,maxValue:100}});
        const s=this.chart.addSeries(this.L.LineSeries,options,pane);s.setData(rows);this.series.push(s);
        if(last&&d.key==='rsi14')for(const level of [30,50,70])s.createPriceLine({price:level,color:level===50?'#c8c6d0':'#b7a6cb',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:level===50?'中线':''});
      });
    }
    const count=this.panes.size,wrap=this.container.querySelector('.chart-canvas-wrap');wrap.dataset.indicatorPanes=String(count);
    // Keep enough price space for existing drawings and trade annotations.
    this.chart.panes().forEach((pane,i)=>{pane.setStretchFactor(i===0?3:1);if(i)pane.priceScale('right').applyOptions({autoScale:true,scaleMargins:i===this.panes.get('rsi14')?{top:0,bottom:0}:{top:.22,bottom:.14}});});
    if(range&&this.data.bars.length)this.chart.timeScale().setVisibleRange(range);
    for(const d of INDICATORS){const label=this.container.querySelector('#indicator-pane-'+d.key);if(label)label.hidden=!this.visible.has(d.key);}
  }
  reading(time){
    const point=this.lookup?.get(time??this.data.bars.at(-1)?.time)||{};
    for(const d of INDICATORS){const text=this.visible.has(d.key)?number(point[d.key]):'已隐藏';this.container.querySelector('#indicator-value-'+d.key).textContent=text;const label=this.container.querySelector('#indicator-pane-value-'+d.key);if(label)label.textContent=text;}
    this.container.querySelector('#chart-indicator-reading').textContent=time?'光标所在 K 线 · 无值表示未收盘或仍在预热':'最新已确认收盘值';
  }
  layout(){
    const panes=this.chart.panes();for(const [key,index] of this.panes||[]){const label=this.container.querySelector('#indicator-pane-'+key);label.style.top=`${panes.slice(0,index).reduce((sum,p)=>sum+p.getHeight()+1,0)+6}px`;}
  }
  exportLabels(ctx,width){
    for(const [key,index] of this.panes||[]){const d=INDICATORS.find(d=>d.key===key),y=this.chart.panes().slice(0,index).reduce((sum,p)=>sum+p.getHeight()+1,0)+17;ctx.fillStyle=d.color;ctx.font='600 11px sans-serif';ctx.fillText(`${d.label} · ${d.description}`,10,y,Math.max(20,width-85));}
  }
}
