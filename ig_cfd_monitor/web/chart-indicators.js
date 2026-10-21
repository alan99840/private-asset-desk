/* Descriptive indicators of observed IG Bid bars; no synthetic prices or orders. */
export const INDICATORS = Object.freeze([
  {key:'ema20',label:'EMA 20',color:'#bc7b19',pane:0,minimum:20,description:'20 根指数均线'},
  {key:'ema50',label:'EMA 50',color:'#4775b9',pane:0,minimum:50,description:'50 根指数均线'},
  {key:'atr14',label:'ATR 14',color:'#157e83',pane:1,minimum:15,description:'平均真实波幅 · 点'},
  {key:'rsi14',label:'RSI 14',color:'#8063b1',pane:2,minimum:15,description:'相对强弱 · 0–100'},
]);
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const number=v=>finite(v)?v.toLocaleString('en-HK',{minimumFractionDigits:2,maximumFractionDigits:2}):'—';

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
