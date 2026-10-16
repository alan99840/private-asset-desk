/* Harbour product charts. Prices are IG historical Bid bars, never synthetic candles. */
export const RESOLUTIONS = [
  ['MINUTE','1 分钟',60],['MINUTE_5','5 分钟',300],['MINUTE_15','15 分钟',900],
  ['MINUTE_30','30 分钟',1800],['HOUR','1 小时',3600],['DAY','日',86400],
  ['WEEK','周',604800],['MONTH','月',2592000],
];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-HK',{maximumFractionDigits:6});
const hkt=(seconds,short=false)=>seconds!=null&&Number.isFinite(Number(seconds))?new Date(Number(seconds)*1000).toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong',hour12:false,...(short?{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}:{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}):'—';
const clone=v=>JSON.parse(JSON.stringify(v));
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const drafts=new Map();
let active=null;

export function timestampSeconds(value){
  if(value==null||value==='')return null;
  if(typeof value==='number'||(typeof value==='string'&&/^[+-]?\d+(\.\d+)?$/.test(value.trim()))){const n=Number(value);return Number.isFinite(n)?(Math.abs(n)>=1e11?n/1000:n):null;}
  if(typeof value!=='string')return null;
  const normalized=/^\d{4}-\d{2}-\d{2}T/.test(value)&&!/(Z|[+-]\d{2}:\d{2})$/.test(value)?value+'Z':value;
  const n=Date.parse(normalized);return Number.isFinite(n)?n/1000:null;
}
export function nextHistoryQuery(response,candles=[]){
  if(!response?.has_more)return null;const before=response.next_before??candles[0]?.time;if(!finite(before))return null;
  return {before,...(typeof response.next_cursor==='string'&&response.next_cursor?{cursor:response.next_cursor}:{})};
}

// Interpolate only annotation coordinates. These functions never create price bars.
export function timeToLogical(time,bars,step=60){
  if(!bars.length||!finite(time))return null;
  if(time<=bars[0].time)return (time-bars[0].time)/step;
  const last=bars.length-1;if(time>=bars[last].time)return last+(time-bars[last].time)/step;
  let lo=0,hi=last;while(hi-lo>1){const mid=(lo+hi)>>1;if(bars[mid].time<=time)lo=mid;else hi=mid;}
  return lo+(time-bars[lo].time)/(bars[hi].time-bars[lo].time);
}
export function logicalToTime(logical,bars,step=60){
  if(!bars.length||!finite(logical))return null;
  const last=bars.length-1;if(logical<=0)return bars[0].time+logical*step;
  if(logical>=last)return bars[last].time+(logical-last)*step;
  const i=Math.floor(logical);return bars[i].time+(logical-i)*(bars[i+1].time-bars[i].time);
}
export function normalizeCandles(rows){
  const result=new Map();for(const b of rows||[]){if(![b.time,b.open,b.high,b.low,b.close].every(finite)||b.high<Math.max(b.open,b.close,b.low)||b.low>Math.min(b.open,b.close,b.high))continue;result.set(b.time,{time:b.time,open:b.open,high:b.high,low:b.low,close:b.close});}
  return [...result.values()].sort((a,b)=>a.time-b.time);
}
export function lineDataWithGaps(candles,gaps=[]){
  const rows=candles.map(b=>({time:b.time,value:b.close}));
  for(const gap of gaps){if(!finite(gap.from)||!finite(gap.to)||gap.to-gap.from<=1)continue;
    // The separator has no price. Rendering splits into distinct series below;
    // Lightweight Charts otherwise joins line values across whitespace.
    if(candles.some(b=>b.time>gap.from&&b.time<gap.to))continue;
    const time=Math.floor((gap.from+gap.to)/2);if(!rows.some(b=>b.time===time))rows.push({time});
  }
  return rows.sort((a,b)=>a.time-b.time);
}
export function lineSegments(rows){const segments=[];let current=[];for(const row of rows){if(finite(row.value))current.push(row);else if(current.length){segments.push(current);current=[];}}if(current.length)segments.push(current);return segments;}
export function distanceToSegment(p,a,b){const dx=b.x-a.x,dy=b.y-a.y,l=dx*dx+dy*dy,t=l?Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/l)):0;return Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);}
function stateFor(epic){if(!drafts.has(epic))drafts.set(epic,{drawings:[],revision:0,loaded:false,dirty:false,undo:[],redo:[],conflict:false});return drafts.get(epic);}
function productList(data){const out=new Map();for(const p of data?.positions||[])if(p.epic)out.set(p.epic,{epic:p.epic,name:p.instrumentName||p.epic,position:true});for(const m of data?.markets||[])if(m.epic&&!out.has(m.epic))out.set(m.epic,{epic:m.epic,name:m.name||m.epic,position:false});return [...out.values()];}
const markup=()=>`<section class="panel product-chart-panel" aria-label="产品价格图表">
  <div class="product-chart-heading"><div class="product-chart-picker"><label for="chart-product">持仓与关注产品</label><select id="chart-product" aria-label="选择图表产品"></select><small id="chart-epic"></small></div><div class="chart-quote"><span>当前 IG Bid / Ask</span><strong id="chart-bid">—</strong><small id="chart-ask">—</small></div></div>
  <div class="chart-controls"><div class="chart-periods" role="group" aria-label="K线周期">${RESOLUTIONS.map(([r,l])=>`<button data-resolution="${r}" aria-pressed="false">${l}</button>`).join('')}</div><div class="chart-types segments" role="group" aria-label="图表类型"><button data-chart-type="candles" aria-pressed="true">K 线</button><button data-chart-type="line" aria-pressed="false">趋势线</button></div></div>
  <div class="chart-reading" id="chart-reading" aria-live="off">将十字光标移至图上，查看时间与开高低收。</div>
  <div class="chart-workspace"><div class="draw-tools" role="toolbar" aria-label="画图工具">
    <button data-draw-tool="pan" aria-label="手形：平移缩放" title="手形：拖动平移，双指或滚轮缩放" aria-pressed="true">↔<span>手形</span></button>
    <button data-draw-tool="select" aria-label="选择和移动标注" title="选择或拖动已有标注" aria-pressed="false">↖<span>选择</span></button>
    <button data-draw-tool="trend" aria-label="画趋势线" title="拖动画线，或依次点击两个端点" aria-pressed="false">╱<span>趋势线</span></button>
    <button data-draw-tool="horizontal" aria-label="画水平线" title="点击价格位置画水平线" aria-pressed="false">━<span>水平线</span></button>
    <button data-draw-tool="rectangle" aria-label="画矩形" title="拖动或依次点击两个角点" aria-pressed="false">▭<span>矩形</span></button>
    <button data-draw-tool="pen" aria-label="自由画笔" title="按住并拖动绘制" aria-pressed="false">✎<span>画笔</span></button>
    <label class="draw-color" title="标注颜色"><span>颜色</span><input id="draw-color" type="color" value="#1b6355" aria-label="标注颜色"></label>
    <label class="draw-width"><span>粗细</span><select id="draw-width" aria-label="标注线条粗细">${[1,2,3,4,5].map(n=>`<option value="${n}" ${n===2?'selected':''}>${n}px</option>`).join('')}</select></label>
  </div><div class="chart-canvas-wrap"><div id="product-chart-canvas" aria-label="IG Bid历史价格交互图；可使用图上方放大缩小按钮" role="img"></div><canvas id="drawing-overlay" aria-label="价格图标注画布"></canvas><div class="chart-empty-overlay" id="chart-empty">正在读取 IG 历史价格…</div></div></div>
  <div class="chart-navigation"><div class="segments" role="group" aria-label="图表视野"><button data-chart-action="zoom-out" aria-label="缩小图表">− 缩小</button><button data-chart-action="zoom-in" aria-label="放大图表">＋ 放大</button><button data-chart-action="fit">重置视图</button><button data-chart-action="latest">最新位置</button></div><div class="chart-history-actions"><button id="chart-earlier" data-chart-action="earlier">加载更早历史</button><button id="chart-refresh" data-chart-action="refresh">检查历史更新</button></div></div>
  <div class="chart-data-status" role="status"><span id="chart-status">准备读取价格</span><small id="chart-history-range"></small><small id="chart-allowance"></small></div>
  <div class="drawing-actions"><div class="segments"><button data-chart-action="undo" disabled>撤销</button><button data-chart-action="redo" disabled>重做</button><button data-chart-action="delete" disabled>删除选中</button><button data-chart-action="clear" disabled>清除全部</button></div><div class="drawing-persist"><span id="drawing-status" role="status">正在读取共享标注…</span><button data-chart-action="reload-drawings" id="drawing-reload" hidden>放弃本地并读取共享版本</button><button data-chart-action="save" id="drawing-save" disabled>保存标注</button><button data-chart-action="export" id="chart-export">导出 PNG</button></div></div>
  <p class="chart-tool-help" id="chart-tool-help">手形模式：拖动平移，滚轮或双指缩放。选择绘图工具后，可在价格区域画图。</p>
  <div class="chart-footnotes"><p>价格口径：IG Bid · 时间：香港 UTC+8。历史 K 线按需读取并缓存，自动检查更新；不将实时单点报价拼成完整 K 线。休市与缺失区间不补造数据。</p><p>开仓、止损、止盈线来自当前持仓，仅作价格参考。标注按产品保存并在不同周期共享，保存后所有获准访问者均可查看和修改。</p><p>TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">图表技术由 TradingView 提供 ↗</a></p></div>
</section>`;

export function mountCharts(container,data,requestedEpic){
  if(active?.container===container&&container.querySelector('#product-chart-canvas')){active.update(data,requestedEpic);return;}
  unmountCharts();container.innerHTML=markup();active=new ProductCharts(container,data,requestedEpic);
}
export function unmountCharts(){if(active){active.destroy();active=null;}}

class ProductCharts{
  constructor(container,data,requestedEpic){
    this.container=container;this.data=data;this.epic='';this.resolution='MINUTE_5';this.type='candles';this.tool='pan';this.candles=[];this.timeline=[];this.priceLines=[];this.selected=null;this.gesture=null;this.pending=null;this.destroyed=false;this.busy=false;this.queryToken=0;this.response=null;this.pollTimer=null;this.pendingQuery=null;this.abort=null;this.lastDrawFetch=0;this.productKey='';this.linesKey='';this.drawMessage='';this.saving=false;this.drawingLoadBusy=false;
    this.$=s=>container.querySelector(s);const L=window.LightweightCharts;
    if(!L){this.$('#chart-empty').textContent='图表组件加载失败，请刷新页面。';return;}
    this.chart=L.createChart(this.$('#product-chart-canvas'),{
      autoSize:true,height:480,layout:{background:{type:'solid',color:'#ffffff'},textColor:'#708080',fontFamily:'-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',fontSize:11,attributionLogo:true},
      grid:{vertLines:{color:'#f1f3ef'},horzLines:{color:'#edf0eb'}},
      crosshair:{mode:L.CrosshairMode.Normal,vertLine:{color:'#8b9f99',style:2,labelBackgroundColor:'#1b6355'},horzLine:{color:'#8b9f99',style:2,labelBackgroundColor:'#1b6355'}},
      rightPriceScale:{borderColor:'#e2e7e2',minimumWidth:68,scaleMargins:{top:.12,bottom:.10}},
      timeScale:{borderColor:'#e2e7e2',timeVisible:true,secondsVisible:false,rightOffset:6,barSpacing:7,minBarSpacing:2,tickMarkFormatter:(t,type)=>this.axisTime(t,type)},
      localization:{locale:'zh-HK',timeFormatter:t=>hkt(t)},handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:true},handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:true},
    });
    this.candleSeries=this.chart.addSeries(L.CandlestickSeries,{upColor:'#287f68',downColor:'#be5a56',wickUpColor:'#287f68',wickDownColor:'#be5a56',borderVisible:false,priceLineVisible:true,lastValueVisible:true});
    this.lineSeries=this.chart.addSeries(L.LineSeries,{color:'#1b6355',lineWidth:2,visible:false,priceLineVisible:true,lastValueVisible:true,crosshairMarkerVisible:true});
    this.gapSeries=[];this.bidLines=[];this.canvas=this.$('#drawing-overlay');this.ctx=this.canvas.getContext('2d');
    this.onClick=e=>this.click(e);this.onChange=e=>this.change(e);this.onPointerDown=e=>this.pointerDown(e);this.onPointerMove=e=>this.pointerMove(e);this.onPointerUp=e=>this.pointerUp(e);this.onPointerCancel=()=>this.cancelGesture();this.onKey=e=>this.key(e);
    container.addEventListener('click',this.onClick);container.addEventListener('change',this.onChange);container.addEventListener('keydown',this.onKey);
    this.canvas.addEventListener('pointerdown',this.onPointerDown);this.canvas.addEventListener('pointermove',this.onPointerMove);this.canvas.addEventListener('pointerup',this.onPointerUp);this.canvas.addEventListener('pointercancel',this.onPointerCancel);
    this.chart.subscribeCrosshairMove(p=>this.reading(p));
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(()=>this.paint());
    this.observer=new ResizeObserver(()=>{this.resize();});this.observer.observe(this.$('.chart-canvas-wrap'));
    this.frame=()=>{if(this.destroyed)return;this.paint();this.animation=requestAnimationFrame(this.frame);};this.animation=requestAnimationFrame(this.frame);
    this.setTool('pan');this.update(data,requestedEpic);this.resize();
    this.interval=setInterval(()=>{if(this.destroyed||!this.epic)return;if(!this.busy&&!this.gesture&&!this.pending)this.load();if(Date.now()-this.lastDrawFetch>60000&&!this.drawState?.dirty&&!this.saving)this.loadDrawings();},30000);
  }
  axisTime(t,type){const d=new Date(Number(t)*1000);if(!finite(Number(t)))return '';const options=type===0?{year:'numeric'}:type===1?{year:'numeric',month:'short'}:type===2?{month:'short',day:'numeric'}:{hour:'2-digit',minute:'2-digit',hour12:false};return d.toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong',...options});}
  get step(){return RESOLUTIONS.find(r=>r[0]===this.resolution)?.[2]||60;}
  get series(){return this.type==='line'?this.lineSeries:this.candleSeries;}
  update(data,requestedEpic){
    this.data=data;if(!this.chart)return;const products=productList(data),key=JSON.stringify(products);
    if(this.productKey!==key){this.productKey=key;this.$('#chart-product').innerHTML=products.map(p=>`<option value="${esc(p.epic)}">${p.position?'持仓 · ':'关注 · '}${esc(p.name)}</option>`).join('');}
    const epic=requestedEpic&&products.some(p=>p.epic===requestedEpic)?requestedEpic:products.some(p=>p.epic===this.epic)?this.epic:products[0]?.epic;
    if(epic&&epic!==this.epic)this.chooseProduct(epic);else if(!epic){this.$('#chart-empty').textContent='当前没有已同步的持仓或关注产品。';this.$('#chart-status').textContent='等待产品同步';}
    this.$('#chart-product').value=this.epic;this.quote();this.references();
  }
  chooseProduct(epic){
    this.cancelGesture();this.epic=epic;this.queryToken++;this.abort?.abort();this.busy=false;clearTimeout(this.pollTimer);this.resKey='';this.candles=[];this.timeline=[];this.response=null;this.pendingQuery=null;this.drawState=stateFor(epic);this.selected=null;this.drawMessage='';this.linesKey='';this.$('#chart-product').value=epic;this.$('#chart-epic').textContent=epic;this.candleSeries.setData([]);this.lineSeries.setData([]);for(const s of this.gapSeries)this.chart.removeSeries(s);this.gapSeries=[];this.$('#chart-empty').hidden=false;this.$('#chart-empty').textContent='正在读取 IG 历史价格…';this.reading();this.drawingStatus();if(!this.drawState.loaded)this.loadDrawings();this.load();this.references();
  }
  quote(){const m=(this.data.markets||[]).find(m=>m.epic===this.epic),timestamp=timestampSeconds(m?.timestamp),verified=m&&!m.stale&&timestamp!=null;this.$('#chart-bid').textContent=number(m?.bid);this.$('#chart-ask').textContent=`Ask ${number(m?.ask)} · ${m?.stale?'报价待核验':m?.delayed?'延迟报价':timestamp!=null?hkt(timestamp,true):'等待来源时间'}`;
    if(finite(m?.bid)){const options={price:m.bid,color:verified?'#456b7a':'#9d896b',lineWidth:1,lineStyle:3,axisLabelVisible:true,lineVisible:true,title:verified?(m.delayed?'延迟 Bid':'实时 Bid'):'Bid 待核验'};if(!this.bidLines.length)this.bidLines=[this.candleSeries.createPriceLine(options),this.lineSeries.createPriceLine(options)];else for(const line of this.bidLines)line.applyOptions(options);}else for(const line of this.bidLines)line.applyOptions({lineVisible:false,axisLabelVisible:false});
  }
  references(){
    if(!this.chart)return;const positions=(this.data.positions||[]).filter(p=>p.epic===this.epic),key=JSON.stringify([this.epic,this.type,positions.map(p=>[p.dealId,p.level,p.stopLevel,p.limitLevel])]);if(key===this.linesKey)return;this.linesKey=key;
    for(const [s,l] of this.priceLines)s.removePriceLine(l);this.priceLines=[];
    for(const [i,p] of positions.entries())for(const [field,title,color] of [['level','开仓','#607d87'],['stopLevel','止损','#b44b4c'],['limitLevel','止盈','#946526']])if(finite(p[field]))this.priceLines.push([this.series,this.series.createPriceLine({price:p[field],color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title:`${title}${positions.length>1?` ${i+1}`:''}`})]);
  }
  async load({earlier=false}={}){
    if(!this.epic||this.busy||this.destroyed)return;const requested=earlier?nextHistoryQuery(this.response,this.candles):this.pendingQuery||{};if(!requested)return;clearTimeout(this.pollTimer);
    this.busy=true;this.$('#chart-refresh').disabled=true;this.$('#chart-earlier').disabled=true;const token=this.queryToken,epic=this.epic,resolution=this.resolution;
    this.pendingQuery={...requested};
    const query=new URLSearchParams({epic,resolution});if(requested.before!=null)query.set('before',String(requested.before));if(requested.cursor)query.set('cursor',requested.cursor);this.abort=new AbortController();
    this.$('#chart-status').textContent=earlier?'正在读取更早历史…':this.candles.length?'正在核验历史缓存…':'正在向 IG 请求历史价格…';
    try{
      const r=await fetch(`/api/chart?${query}`,{credentials:'same-origin',cache:'no-store',signal:this.abort.signal});const result=await r.json();if(token!==this.queryToken||this.destroyed)return;
      if(!r.ok){if(r.status===401)throw Error('登录已过期，请刷新页面重新输入访问密码。');throw Error(result.message||result.error||`历史请求失败 (${r.status})`);}
      this.response=result;const candles=normalizeCandles(result.candles),lineRows=lineDataWithGaps(candles,result.gaps),old=this.candles,changed=JSON.stringify(candles)!==JSON.stringify(old)||JSON.stringify(lineRows.map(b=>b.time))!==JSON.stringify(this.timeline.map(b=>b.time));
      if(changed){const visible=this.chart.timeScale().getVisibleRange(),same=this.resKey===`${epic}/${resolution}`;this.candles=candles;this.timeline=lineRows;
        const step=this.inferPriceStep(candles);this.candleSeries.applyOptions({priceFormat:{type:'price',precision:step.precision,minMove:step.minMove}});this.lineSeries.applyOptions({priceFormat:{type:'price',precision:step.precision,minMove:step.minMove}});
        this.candleSeries.setData(candles);this.setLineData(lineRows,step);
        if(candles.length){if(same&&visible)this.chart.timeScale().setVisibleRange(visible);else this.chart.timeScale().setVisibleLogicalRange({from:Math.max(-3,lineRows.length-100),to:lineRows.length+5});}this.resKey=`${epic}/${resolution}`;
      }
      this.$('#chart-empty').hidden=!!candles.length;this.$('#chart-empty').textContent=result.status==='loading'?'正在读取 IG 历史价格…':result.message||'IG 暂未返回该产品在此周期的历史价格。';
      this.$('#chart-status').textContent=({loading:'历史正在后台读取',ready:'历史缓存可用',limited:'IG 历史价格配额受限',error:'历史价格读取失败'}[result.status]||result.status)+(result.gaps?.length?` · ${result.gaps.length} 段时间区间尚未核对，趋势线已断开`:'')+(result.message?' · '+result.message:'');
      this.$('#chart-status').classList.toggle('chart-status-warning',['limited','error'].includes(result.status)||!!result.gaps?.length);
      this.$('#chart-history-range').textContent=candles.length?`${candles.length.toLocaleString()} 根 · ${hkt(candles[0].time)} — ${hkt(candles.at(-1).time)} · 缓存更新 ${result.updated_at?hkt(Date.parse(result.updated_at)/1000):'—'}`:'暂无已确认的历史 K 线';
      const a=result.allowance;this.$('#chart-allowance').textContent=a?`历史价格额度：${number(a.remainingAllowance)} / ${number(a.totalAllowance)}${a.allowanceExpiry!=null?` · 重置倒计时 ${number(a.allowanceExpiry)} 秒（请求时）`:''}`:'历史价格额度：等待 IG 返回';
      if(result.status==='loading'){this.pollTimer=setTimeout(()=>this.load(),1500);}else this.pendingQuery=null;
      this.$('#chart-earlier').textContent=result.has_more?'加载更早历史':'已到当前可用历史边界';this.reading();
    }catch(e){if(e.name!=='AbortError'&&token===this.queryToken&&!this.destroyed){this.$('#chart-status').textContent=e.message||'历史请求失败，请稍后重试。';this.$('#chart-status').classList.add('chart-status-warning');if(!this.candles.length)this.$('#chart-empty').textContent=e.message;this.pendingQuery=null;}}
    finally{if(token===this.queryToken&&!this.destroyed){this.busy=false;this.$('#chart-refresh').disabled=false;this.$('#chart-earlier').disabled=!nextHistoryQuery(this.response,this.candles)||this.response?.status==='loading';}}
  }
  setLineData(rows,priceFormat){
    for(const s of this.gapSeries)this.chart.removeSeries(s);this.gapSeries=[];
    const segments=lineSegments(rows),last=segments.pop()||[];
    this.lineSeries.setData([...last,...rows.filter(b=>!finite(b.value))].sort((a,b)=>a.time-b.time));
    for(const segment of segments){const s=this.chart.addSeries(window.LightweightCharts.LineSeries,{color:'#1b6355',lineWidth:2,visible:this.type==='line',priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:{type:'price',...priceFormat}});s.setData(segment);this.gapSeries.push(s);}
  }
  inferPriceStep(bars){let precision=2;for(const b of bars.slice(-100))for(const v of [b.open,b.high,b.low,b.close]){const text=String(v);if(text.includes('.'))precision=Math.max(precision,Math.min(6,text.split('.')[1].length));}return {precision,minMove:10**(-precision)};}
  reading(param){if(this.gesture||this.pending)return;let b=param?.seriesData?.get(this.candleSeries);if(!b&&param?.time)b=this.candles.find(b=>b.time===param.time);if(!b)b=this.candles.at(-1);this.$('#chart-reading').textContent=b?`${hkt(b.time)} HKT   开 ${number(b.open)}   高 ${number(b.high)}   低 ${number(b.low)}   收 ${number(b.close)}${param?.time?'':' · 最新已读取 K 线'}`:'将十字光标移至图上，查看时间与开高低收。';}
  setResolution(resolution){if(this.resolution===resolution||!RESOLUTIONS.some(r=>r[0]===resolution))return;this.cancelGesture();this.resolution=resolution;this.queryToken++;this.abort?.abort();this.busy=false;clearTimeout(this.pollTimer);this.candles=[];this.timeline=[];this.response=null;this.pendingQuery=null;this.resKey='';this.candleSeries.setData([]);this.lineSeries.setData([]);for(const s of this.gapSeries)this.chart.removeSeries(s);this.gapSeries=[];this.$('#chart-empty').hidden=false;this.$('#chart-empty').textContent='正在读取此周期…';this.$$('.chart-periods button').forEach(b=>{const on=b.dataset.resolution===resolution;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});this.load();}
  $$(s){return [...this.container.querySelectorAll(s)];}
  setType(type){if(!['candles','line'].includes(type)||this.type===type)return;this.cancelGesture();this.type=type;this.candleSeries.applyOptions({visible:type==='candles'});this.lineSeries.applyOptions({visible:type==='line'});for(const s of this.gapSeries)s.applyOptions({visible:type==='line'});this.$$('.chart-types button').forEach(b=>{const on=b.dataset.chartType===type;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});this.references();}
  setTool(tool){this.cancelGesture();this.tool=tool;this.canvas.style.pointerEvents=tool==='pan'?'none':'auto';this.canvas.style.touchAction=tool==='pan'?'auto':'none';this.canvas.style.cursor=tool==='select'?'default':'crosshair';this.$$('button[data-draw-tool]').forEach(b=>{const on=b.dataset.drawTool===tool;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});this.$('#chart-tool-help').textContent={pan:'手形模式：拖动平移，滚轮或双指缩放。选择绘图工具后，可在价格区域画图。',select:'选择模式：点击已有标注，再拖动移动；可更换颜色或粗细，并删除选中标注。',trend:'趋势线：按住并拖动，或依次点击两个端点。完成后可切换“选择”移动标注。',horizontal:'水平线：点击价格位置。可在“选择”模式中上下拖动。',rectangle:'矩形：拖动两个对角，或依次点击两个角点。',pen:'画笔：按住并拖动；手机可用单指绘制。切回“手形”后恢复图表平移缩放。'}[tool];this.drawingStatus();}
  async loadDrawings({discard=false}={}){
    if(!this.epic||this.destroyed||this.drawingLoadBusy)return;const epic=this.epic,state=stateFor(epic);if(state.dirty&&!discard)return;this.drawingLoadBusy=true;
    try{const r=await fetch('/api/chart/drawings?'+new URLSearchParams({epic}),{credentials:'same-origin',cache:'no-store'});const result=await r.json();if(!r.ok)throw Error(result.message||result.error||'共享标注读取失败');if(state.dirty&&!discard)return;state.drawings=result.drawings||[];state.revision=result.revision;state.loaded=true;state.dirty=false;state.conflict=false;state.undo=[];state.redo=[];if(this.epic===epic){this.selected=null;this.drawMessage='';this.lastDrawFetch=Date.now();}}
    catch(e){if(this.epic===epic)this.drawMessage=`共享标注读取失败：${e.message}。请点击重读后再编辑。`;}
    finally{this.drawingLoadBusy=false;if(!this.destroyed){this.drawingStatus();if(this.epic!==epic&&!this.drawState.loaded)this.loadDrawings();}}
  }
  async saveDrawings(){
    const state=this.drawState;if(!state?.loaded||!state.dirty||state.conflict||this.saving)return;const epic=this.epic,drawings=clone(state.drawings),revision=state.revision;this.saving=true;this.drawMessage='正在保存共享标注…';this.drawingStatus();
    try{const r=await fetch('/api/chart/drawings?'+new URLSearchParams({epic}),{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({epic,revision,drawings})});const result=await r.json();if(r.status===409){state.conflict=true;throw Error('其他查看者已更新共享标注。本地改动仍保留；请先导出 PNG，再读取共享版本重新编辑。');}if(!r.ok)throw Error(result.message||result.error||'保存失败');state.revision=result.revision;state.dirty=JSON.stringify(drawings)!==JSON.stringify(state.drawings);state.loaded=true;if(this.epic===epic)this.drawMessage=state.dirty?'上次标注已保存，仍有新改动待保存。':'共享标注已保存。';}
    catch(e){if(this.epic===epic)this.drawMessage=e.message||'保存失败，本地标注仍保留。';}
    finally{this.saving=false;if(this.epic===epic&&!this.destroyed)this.drawingStatus();}
  }
  drawingStatus(){this.needsPaint=true;const s=this.drawState;if(!s)return;const count=s.drawings.length;this.$('#drawing-status').textContent=this.drawMessage||(s.loaded?(s.dirty?`${count} 条标注 · 有未保存改动`:`${count} 条共享标注 · 已保存`):'正在读取共享标注…');this.$('#drawing-status').classList.toggle('chart-status-warning',s.conflict||!!this.drawMessage.includes('失败'));this.$('#drawing-save').disabled=!s.loaded||!s.dirty||s.conflict||this.saving||!!this.pending||!!this.gesture;this.$('#drawing-reload').hidden=!s.conflict&&s.loaded;this.$('[data-chart-action="undo"]').disabled=!s.undo.length||this.saving;this.$('[data-chart-action="redo"]').disabled=!s.redo.length||this.saving;this.$('[data-chart-action="delete"]').disabled=!this.selected||this.saving;this.$('[data-chart-action="clear"]').disabled=!count||this.saving;this.$$('.chart-periods button').forEach(b=>{const on=b.dataset.resolution===this.resolution;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});}
  checkpoint(){const s=this.drawState;s.undo.push(clone(s.drawings));if(s.undo.length>50)s.undo.shift();s.redo=[];}
  changed(){this.drawState.dirty=true;this.drawMessage='';this.needsPaint=true;this.drawingStatus();}
  edit(action){const s=this.drawState;if(!s?.loaded||this.saving)return;this.cancelGesture();if(action==='undo'&&s.undo.length){s.redo.push(clone(s.drawings));s.drawings=s.undo.pop();}else if(action==='redo'&&s.redo.length){s.undo.push(clone(s.drawings));s.drawings=s.redo.pop();}else if(action==='delete'&&this.selected){this.checkpoint();s.drawings=s.drawings.filter(d=>d.id!==this.selected);}else if(action==='clear'&&s.drawings.length){this.checkpoint();s.drawings=[];}else return;this.selected=null;this.changed();}
  point(e){const r=this.canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;if(x<0||x>this.chart.timeScale().width()||y<0||y>this.plotHeight())return null;const logical=this.chart.timeScale().coordinateToLogical(x),price=this.series.coordinateToPrice(y),time=logicalToTime(logical,this.timeline,this.step);return finite(time)&&finite(price)?{x,y,time,price}:null;}
  pixel(p){const logical=timeToLogical(p.time,this.timeline,this.step),x=logical==null?null:this.chart.timeScale().logicalToCoordinate(logical),y=this.series.priceToCoordinate(p.price);return finite(x)&&finite(y)?{x,y}:null;}
  hit(p){for(const d of [...(this.drawState?.drawings||[])].reverse()){const ps=d.points.map(p=>this.pixel(p)).filter(Boolean);if(!ps.length)continue;const radius=Math.max(9,d.width+4);if(d.type==='horizontal'&&Math.abs(p.y-ps[0].y)<radius)return d;if(d.type==='rectangle'&&ps.length===2){const [a,b]=ps,c={x:b.x,y:a.y},v={x:a.x,y:b.y};if([distanceToSegment(p,a,c),distanceToSegment(p,c,b),distanceToSegment(p,b,v),distanceToSegment(p,v,a)].some(n=>n<radius))return d;}else for(let i=1;i<ps.length;i++)if(distanceToSegment(p,ps[i-1],ps[i])<radius)return d;}return null;}
  pointerDown(e){if(this.tool==='pan'||!this.drawState?.loaded||this.saving||!this.candles.length)return;const p=this.point(e);if(!p)return;e.preventDefault();this.canvas.setPointerCapture(e.pointerId);if(this.tool==='select'){const d=this.hit(p);this.selected=d?.id||null;if(d){this.$('#draw-color').value=d.color;this.$('#draw-width').value=String(d.width);this.gesture={kind:'move',start:p,original:clone(d),moved:false};}this.drawingStatus();return;}
    if(this.drawState.drawings.length>=200){this.drawMessage='最多保存 200 条标注，请先删除不需要的内容。';this.drawingStatus();return;}
    if(this.pending){this.pending.points[1]={time:p.time,price:p.price};this.gesture={kind:'complete',start:p,moved:false};return;}
    const d={id:globalThis.crypto?.randomUUID?.()||`d-${Date.now()}-${Math.random().toString(16).slice(2)}`,type:this.tool,points:[{time:p.time,price:p.price}],color:this.$('#draw-color').value,width:Number(this.$('#draw-width').value)};
    if(this.tool==='horizontal'){this.checkpoint();this.drawState.drawings.push(d);this.selected=d.id;this.changed();return;}
    if(this.tool!=='pen')d.points.push({...d.points[0]});this.pending=d;this.gesture={kind:'new',start:p,moved:false};this.drawingStatus();
  }
  pointerMove(e){if(!this.pending&&!this.gesture)return;this.needsPaint=true;const p=this.point(e);if(!p)return;if(this.gesture){if(Math.hypot(p.x-this.gesture.start.x,p.y-this.gesture.start.y)>4)this.gesture.moved=true;
      if(this.gesture.kind==='move'){const target=this.drawState.drawings.find(d=>d.id===this.selected);if(target){const dt=p.time-this.gesture.start.time,dp=p.price-this.gesture.start.price;target.points=this.gesture.original.points.map(q=>({time:q.time+dt,price:q.price+dp}));}return;}}
    if(this.pending?.type==='pen'){if(this.gesture&&this.pending.points.length<1000){const last=this.pixel(this.pending.points.at(-1));if(!last||Math.hypot(p.x-last.x,p.y-last.y)>2)this.pending.points.push({time:p.time,price:p.price});}}
    else if(this.pending)this.pending.points[1]={time:p.time,price:p.price};
  }
  pointerUp(e){if(this.canvas.hasPointerCapture(e.pointerId))this.canvas.releasePointerCapture(e.pointerId);const g=this.gesture;if(!g)return;this.gesture=null;if(g.kind==='move'){if(g.moved){const updated=clone(this.drawState.drawings);const s=this.drawState,old=clone(s.drawings);const i=old.findIndex(d=>d.id===this.selected);if(i>=0)old[i]=g.original;s.undo.push(old);if(s.undo.length>50)s.undo.shift();s.redo=[];s.drawings=updated;this.changed();}this.drawingStatus();return;}
    if(this.pending&&(g.kind==='complete'||g.moved||this.pending.type==='pen')){if(this.pending.points.length>=2){this.checkpoint();this.drawState.drawings.push(this.pending);this.selected=this.pending.id;this.changed();}this.pending=null;}this.drawingStatus();
  }
  cancelGesture(){if(this.gesture?.kind==='move'){const i=this.drawState?.drawings.findIndex(d=>d.id===this.gesture.original.id);if(i>=0)this.drawState.drawings[i]=this.gesture.original;}this.gesture=null;this.pending=null;if(this.chart&&this.drawState)this.drawingStatus();}
  plotHeight(){return Math.max(0,this.canvas.clientHeight-this.chart.timeScale().height());}
  resize(){if(!this.canvas)return;this.needsPaint=true;const r=this.$('.chart-canvas-wrap').getBoundingClientRect(),dpr=window.devicePixelRatio||1;this.canvas.width=Math.round(r.width*dpr);this.canvas.height=Math.round(r.height*dpr);this.canvas.style.width=`${r.width}px`;this.canvas.style.height=`${r.height}px`;this.ctx.setTransform(dpr,0,0,dpr,0,0);this.paint();}
  paint(){if(!this.ctx||!this.chart)return;const range=this.chart.timeScale().getVisibleLogicalRange(),bar=this.candles.at(-1),key=JSON.stringify([range,this.canvas.width,this.canvas.height,bar?this.series.priceToCoordinate(bar.close):null,bar?this.series.priceToCoordinate(bar.close+1):null,this.type,this.resKey]);if(!this.needsPaint&&key===this.paintKey)return;this.paintKey=key;this.needsPaint=false;const ctx=this.ctx;ctx.clearRect(0,0,this.canvas.clientWidth,this.canvas.clientHeight);ctx.save();ctx.beginPath();ctx.rect(0,0,this.chart.timeScale().width(),this.plotHeight());ctx.clip();for(const d of [...(this.drawState?.drawings||[]),...(this.pending?[this.pending]:[])])this.paintDrawing(ctx,d);ctx.restore();}
  paintDrawing(ctx,d){const ps=d.points.map(p=>this.pixel(p));if(ps.some(p=>!p)||!ps.length)return;ctx.save();ctx.strokeStyle=d.color;ctx.lineWidth=d.width;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();if(d.type==='horizontal'){ctx.moveTo(0,ps[0].y);ctx.lineTo(this.chart.timeScale().width(),ps[0].y);}else if(d.type==='rectangle'&&ps.length>=2){ctx.fillStyle=d.color+'12';ctx.fillRect(ps[0].x,ps[0].y,ps[1].x-ps[0].x,ps[1].y-ps[0].y);ctx.rect(ps[0].x,ps[0].y,ps[1].x-ps[0].x,ps[1].y-ps[0].y);}else{ctx.moveTo(ps[0].x,ps[0].y);for(const p of ps.slice(1))ctx.lineTo(p.x,p.y);}ctx.stroke();if(d.id===this.selected){const ends=d.type==='horizontal'?[{x:this.chart.timeScale().width()/2,y:ps[0].y}]:d.type==='pen'?[ps[0],ps.at(-1)]:ps;for(const p of ends){ctx.beginPath();ctx.fillStyle='#ffffff';ctx.lineWidth=1.5;ctx.arc(p.x,p.y,4,0,Math.PI*2);ctx.fill();ctx.stroke();}}ctx.restore();}
  async export(){if(!this.candles.length){this.drawMessage='历史价格尚未就绪，暂不能导出图表。';this.drawingStatus();return;}try{const snapshot=this.chart.takeScreenshot(),out=document.createElement('canvas'),dpr=snapshot.width/this.canvas.clientWidth;out.width=snapshot.width;out.height=snapshot.height+Math.round(75*dpr);const ctx=out.getContext('2d');ctx.fillStyle='#ffffff';ctx.fillRect(0,0,out.width,out.height);ctx.save();ctx.scale(dpr,dpr);ctx.fillStyle='#1e343b';ctx.font='600 15px sans-serif';const p=productList(this.data).find(p=>p.epic===this.epic);ctx.fillText(`${p?.name||this.epic} · ${RESOLUTIONS.find(r=>r[0]===this.resolution)[1]} · IG Bid`,12,24);ctx.fillStyle='#708080';ctx.font='11px sans-serif';ctx.fillText(`香港时间 UTC+8 · 最新 K 线 ${hkt(this.candles.at(-1).time)} · TradingView Lightweight Charts™`,12,45);ctx.restore();ctx.drawImage(snapshot,0,Math.round(65*dpr));ctx.drawImage(this.canvas,0,Math.round(65*dpr),snapshot.width,snapshot.height);const blob=await new Promise(r=>out.toBlob(r,'image/png'));if(!blob)throw Error('浏览器未能生成 PNG');const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`Harbour-${this.epic.replace(/[^\w.-]/g,'_')}-${this.resolution}.png`;a.click();setTimeout(()=>URL.revokeObjectURL(url),3000);this.drawMessage='PNG 已生成。';this.drawingStatus();}catch(e){this.drawMessage=`导出失败：${e.message}`;this.drawingStatus();}}
  zoom(factor){const r=this.chart.timeScale().getVisibleLogicalRange();if(!r)return;const center=(r.from+r.to)/2,width=Math.max(8,(r.to-r.from)*factor);this.chart.timeScale().setVisibleLogicalRange({from:center-width/2,to:center+width/2});}
  click(e){const b=e.target.closest('button');if(!b||!this.chart)return;if(b.dataset.resolution)this.setResolution(b.dataset.resolution);if(b.dataset.chartType)this.setType(b.dataset.chartType);if(b.dataset.drawTool)this.setTool(b.dataset.drawTool);const action=b.dataset.chartAction;if(['undo','redo','delete','clear'].includes(action))this.edit(action);if(action==='save')this.saveDrawings();if(action==='reload-drawings')this.loadDrawings({discard:true});if(action==='export')this.export();if(action==='earlier')this.load({earlier:true});if(action==='refresh'){this.pendingQuery=null;this.load();}if(action==='zoom-out')this.zoom(1.5);if(action==='zoom-in')this.zoom(1/1.5);if(action==='fit'){this.chart.priceScale('right').applyOptions({autoScale:true});this.chart.timeScale().fitContent();}if(action==='latest')this.chart.timeScale().scrollToRealTime();}
  change(e){if(e.target.id==='chart-product'){this.chooseProduct(e.target.value);history.replaceState(null,'',`#charts?epic=${encodeURIComponent(e.target.value)}`);}if(['draw-color','draw-width'].includes(e.target.id)&&this.selected&&this.drawState?.loaded){const d=this.drawState.drawings.find(d=>d.id===this.selected);if(d){this.checkpoint();d.color=this.$('#draw-color').value;d.width=Number(this.$('#draw-width').value);this.changed();}}}
  key(e){if(e.target.matches('input,select,textarea'))return;if(e.key==='Escape'){this.cancelGesture();this.setTool('pan');}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();this.edit(e.shiftKey?'redo':'undo');}if((e.key==='Delete'||e.key==='Backspace')&&this.selected){e.preventDefault();this.edit('delete');}}
  destroy(){this.destroyed=true;clearTimeout(this.pollTimer);clearInterval(this.interval);cancelAnimationFrame(this.animation);this.abort?.abort();this.observer?.disconnect();this.container.removeEventListener('click',this.onClick);this.container.removeEventListener('change',this.onChange);this.container.removeEventListener('keydown',this.onKey);this.canvas?.removeEventListener('pointerdown',this.onPointerDown);this.canvas?.removeEventListener('pointermove',this.onPointerMove);this.canvas?.removeEventListener('pointerup',this.onPointerUp);this.canvas?.removeEventListener('pointercancel',this.onPointerCancel);this.chart?.remove();}
}

if(typeof window!=='undefined')window.addEventListener('beforeunload',e=>{if([...drafts.values()].some(s=>s.dirty)){e.preventDefault();e.returnValue='';}});
