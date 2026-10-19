/* Harbour product charts. Prices are IG historical Bid bars, never synthetic candles. */
export const RESOLUTIONS = [
  ['MINUTE','1 分钟',60],['MINUTE_5','5 分钟',300],['MINUTE_15','15 分钟',900],
  ['MINUTE_30','30 分钟',1800],['HOUR','1 小时',3600],['DAY','日',86400],
  ['WEEK','周',604800],['MONTH','月',2592000],
];
export const TV_REFERENCE_SYMBOLS=Object.freeze({'CC.D.CL.BMU.IP':'OANDA:WTICOUSD','IX.D.NIKKEI.BMU.IP':'OANDA:JP225USD'});
export const TV_REFERENCE_INTERVALS=Object.freeze({MINUTE:'1',MINUTE_5:'5',MINUTE_15:'15',MINUTE_30:'30',HOUR:'60',DAY:'D',WEEK:'W',MONTH:'M'});
// Treat the opaque bootstrap's URL as untrusted. Only TradingView's widget
// endpoint with one unambiguous current public configuration may be displayed.
function uniqueTopLevelKeys(json){
  const keys=new Set();let depth=0;
  for(let i=0;i<json.length;i++){
    const c=json[i];if(c==='{'||c==='['){depth++;continue;}if(c==='}'||c===']'){depth--;continue;}if(c!=='"')continue;
    const start=i;for(i++;i<json.length;i++){if(json[i]==='\\'){i++;continue;}if(json[i]==='"')break;}
    let next=i+1;while(/\s/.test(json[next]||'')&&next<json.length)next++;
    if(depth===1&&json[next]===':'){const key=JSON.parse(json.slice(start,i+1));if(keys.has(key))return false;keys.add(key);}
  }
  return true;
}
export function validateTradingViewUrl(raw,expected){
  try{
    if(typeof raw!=='string'||raw.length>20000||/[\u0000-\u0020\\]/.test(raw))return null;
    const u=new URL(raw),hosts=['s.tradingview.com','www.tradingview.com','www.tradingview-widget.com'];
    if(u.protocol!=='https:'||u.username||u.password||(u.port&&u.port!=='443')||!hosts.includes(u.hostname)||u.pathname!=='/widgetembed/')return null;
    const seenQuery=new Set();for(const [key] of u.searchParams){if(seenQuery.has(key))return null;seenQuery.add(key);}
    let config={};if(u.hash){const text=decodeURIComponent(u.hash.slice(1));if(!uniqueTopLevelKeys(text))return null;config=JSON.parse(text);if(!config||Array.isArray(config)||typeof config!=='object')return null;}
    for(const key of ['symbol','interval','style']){const q=u.searchParams.getAll(key),inHash=Object.prototype.hasOwnProperty.call(config,key);if(q.length>1||(q.length&&inHash))return null;const value=q.length?q[0]:config[key];if(typeof value!=='string'||value!==expected[key])return null;}
    for(const key of ['page-uri','page_uri','utm_source','utm_medium','utm_campaign']){u.searchParams.delete(key);delete config[key];}
    if(Object.prototype.hasOwnProperty.call(config,'frameElementId'))config.frameElementId='harbour_tv_reference_display';
    if(u.hash)u.hash=encodeURIComponent(JSON.stringify(config));return u.href;
  }catch{return null;}
}
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-HK',{maximumFractionDigits:6});
const hkt=(seconds,short=false)=>seconds!=null&&Number.isFinite(Number(seconds))?new Date(Number(seconds)*1000).toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong',hour12:false,...(short?{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}:{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}):'—';
const tradeNumber=v=>v==null?'未确认':Number(v).toLocaleString('en-HK',{maximumFractionDigits:20});
const tradeColor=side=>side==='BUY'?'#157d60':'#c14148';
const tradeQuantity=m=>`${m.side==='BUY'?'买':'卖'}${m.action==='OPEN'?'开':'平'} ×${tradeNumber(m.size)}`;
export const TRADE_DOT_RADIUS=2.75;
const tradeHkt=t=>new Date(t*1000).toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
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
// This pinned chart build only accepts integer logical indices. Interpolate
// their pixel coordinates explicitly to retain an exact intra-bar timestamp.
export function logicalCoordinate(scale,logical){if(!finite(logical))return null;const lo=Math.floor(logical),hi=Math.ceil(logical),a=scale.logicalToCoordinate(lo);if(!finite(a))return null;if(lo===hi)return a;const b=scale.logicalToCoordinate(hi);return finite(b)?a+(b-a)*(logical-lo):null;}
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
// Trade coordinates use only verified candle time buckets. Unlike editable
// annotation coordinates, they may never extrapolate through unloaded history.
export function normalizeTradeMarkers(rows){
  const seen=new Set(),markers=[];let invalid=0;
  for(const m of Array.isArray(rows)?rows:[]){
    if(!m||typeof m.id!=='string'||!m.id||m.id.length>200||!finite(m.time)||m.time<=0||m.time>7258118400||!finite(m.price)||!['BUY','SELL'].includes(m.side)||!['OPEN','CLOSE'].includes(m.action)||(m.size!=null&&(!finite(m.size)||m.size<=0))){invalid++;continue;}
    if(seen.has(m.id))continue;seen.add(m.id);markers.push({id:m.id,time:m.time,price:m.price,side:m.side,action:m.action,size:m.size??null,reference:typeof m.reference==='string'?m.reference.slice(0,300):'',source:typeof m.source==='string'?m.source.slice(0,100):''});
  }
  return {markers:markers.sort((a,b)=>a.time-b.time||a.id.localeCompare(b.id)),invalid};
}
function tradeBucketEnd(time,resolution){
  if(resolution==='MONTH'){const d=new Date(time*1000),day=d.getUTCDate(),wasMonthEnd=day===new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()+1);const last=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate();d.setUTCDate(wasMonthEnd?last:Math.min(day,last));return d.getTime()/1000;}
  return time+(RESOLUTIONS.find(r=>r[0]===resolution)?.[2]||60);
}
export function tradeMarkerPlacement(marker,candles,timeline,resolution,gaps=[]){
  if(!candles.length||marker.time<candles[0].time)return {reason:'未加载更早历史'};
  let lo=0,hi=candles.length;while(lo<hi){const mid=(lo+hi)>>1;if(candles[mid].time<=marker.time)lo=mid+1;else hi=mid;}
  const i=lo-1,b=candles[i],next=candles[i+1];let end=tradeBucketEnd(b.time,resolution);
  // Daily and larger bars can shift an hour at DST; the next actual candle
  // remains authoritative when its boundary is within one hour of the calendar.
  if(next&&((resolution==='MONTH'&&next.time-b.time>=20*86400&&next.time-b.time<=35*86400)||(Math.abs(next.time-end)<=3600&&['DAY','WEEK'].includes(resolution))))end=next.time;
  for(const gap of gaps||[])if(finite(gap.from)&&finite(gap.to)&&marker.time>gap.from&&marker.time<gap.to&&!candles.some(c=>c.time>gap.from&&c.time<gap.to))return {reason:'时间区间尚未核对'};
  if(marker.time>=end||(next&&marker.time>=next.time))return {reason:'该时段未加载 K 线'};
  const logical=timeToLogical(marker.time,timeline,Math.max(1,end-b.time));return finite(logical)?{logical}:{reason:'时间坐标无法核对'};
}
export function distanceToSegment(p,a,b){const dx=b.x-a.x,dy=b.y-a.y,l=dx*dx+dy*dy,t=l?Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/l)):0;return Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);}
function stateFor(epic){if(!drafts.has(epic))drafts.set(epic,{drawings:[],revision:0,loaded:false,dirty:false,undo:[],redo:[],conflict:false});return drafts.get(epic);}
function productList(data){const out=new Map();for(const p of data?.positions||[])if(p.epic)out.set(p.epic,{epic:p.epic,name:p.instrumentName||p.epic,position:true});for(const m of data?.markets||[])if(m.epic&&!out.has(m.epic))out.set(m.epic,{epic:m.epic,name:m.name||m.epic,position:false});return [...out.values()];}
const markup=()=>`<section class="panel product-chart-panel" aria-label="产品价格图表">
  <div class="product-chart-heading"><div class="product-chart-picker"><label for="chart-product">持仓与关注产品</label><select id="chart-product" aria-label="选择图表产品"></select><small id="chart-epic"></small></div><div class="chart-quote"><span>当前 IG Bid / Ask</span><strong id="chart-bid">—</strong><small id="chart-ask">—</small></div></div>
  <div class="chart-controls"><div class="chart-periods" role="group" aria-label="K线周期">${RESOLUTIONS.map(([r,l])=>`<button data-resolution="${r}" aria-pressed="false">${l}</button>`).join('')}</div><div class="chart-types segments" role="group" aria-label="图表类型"><button data-chart-type="candles" aria-pressed="true">K 线</button><button data-chart-type="line" aria-pressed="false">趋势线</button></div></div>
  <div class="chart-reading" id="chart-reading" aria-live="off">将十字光标移至图上，查看时间与开高低收。</div>
  <div class="trade-marker-controls"><label class="trade-marker-toggle"><input id="trade-markers-visible" type="checkbox" checked>成交点</label><label class="trade-marker-toggle"><input id="trade-quantity-visible" type="checkbox" checked>成交数量</label><label class="trade-marker-toggle"><input id="trade-price-visible" type="checkbox" checked>成交价虚线</label><div class="trade-marker-legend"><span><i class="trade-dot trade-dot-buy"></i>买入</span><span><i class="trade-dot trade-dot-sell"></i>卖出</span><span><i class="trade-dot trade-dot-open"></i>开仓</span><span><i class="trade-dot trade-dot-close"></i>平仓</span></div><button type="button" data-chart-action="latest-trade" id="trade-marker-latest" disabled>定位最近已加载成交</button><small class="trade-marker-help">× 为每笔成交合约数，不是市场成交量；虚线连接当前视野内的真实成交价，重合成交可点开逐笔查看。</small><small id="trade-marker-status" role="status">正在读取真实成交…</small></div>
  <div class="chart-workspace"><div class="draw-tools" role="toolbar" aria-label="画图工具">
    <button data-draw-tool="pan" aria-label="手形：平移缩放" title="手形：拖动平移，双指或滚轮缩放" aria-pressed="true">↔<span>手形</span></button>
    <button data-draw-tool="select" aria-label="选择和移动标注" title="选择或拖动已有标注" aria-pressed="false">↖<span>选择</span></button>
    <button data-draw-tool="trend" aria-label="画趋势线" title="拖动画线，或依次点击两个端点" aria-pressed="false">╱<span>趋势线</span></button>
    <button data-draw-tool="horizontal" aria-label="画水平线" title="点击价格位置画水平线" aria-pressed="false">━<span>水平线</span></button>
    <button data-draw-tool="rectangle" aria-label="画矩形" title="拖动或依次点击两个角点" aria-pressed="false">▭<span>矩形</span></button>
    <button data-draw-tool="pen" aria-label="自由画笔" title="按住并拖动绘制" aria-pressed="false">✎<span>画笔</span></button>
    <label class="draw-color" title="标注颜色"><span>颜色</span><input id="draw-color" type="color" value="#1b6355" aria-label="标注颜色"></label>
    <label class="draw-width"><span>粗细</span><select id="draw-width" aria-label="标注线条粗细">${[1,2,3,4,5].map(n=>`<option value="${n}" ${n===2?'selected':''}>${n}px</option>`).join('')}</select></label>
  </div><div class="chart-canvas-wrap"><div id="product-chart-canvas" aria-label="IG Bid历史价格交互图；可使用图上方放大缩小按钮" role="img"></div><canvas id="drawing-overlay" aria-label="价格图标注画布"></canvas><div id="trade-marker-tooltip" class="trade-marker-tooltip" role="tooltip" hidden></div><div class="chart-empty-overlay" id="chart-empty">正在读取 IG 历史价格…</div></div></div>
  <div class="chart-navigation"><div class="segments" role="group" aria-label="图表视野"><button data-chart-action="zoom-out" aria-label="缩小图表">− 缩小</button><button data-chart-action="zoom-in" aria-label="放大图表">＋ 放大</button><button data-chart-action="fit">重置视图</button><button data-chart-action="latest">最新位置</button></div><div class="chart-history-actions"><button id="chart-earlier" data-chart-action="earlier">加载更早历史</button><button id="chart-refresh" data-chart-action="refresh">检查历史更新</button></div></div>
  <div class="chart-data-status" role="status"><span id="chart-status">准备读取价格</span><small id="chart-history-range"></small><small id="chart-allowance"></small></div>
  <div class="drawing-actions"><div class="segments"><button data-chart-action="undo" disabled>撤销</button><button data-chart-action="redo" disabled>重做</button><button data-chart-action="delete" disabled>删除选中</button><button data-chart-action="clear" disabled>清除全部</button></div><div class="drawing-persist"><span id="drawing-status" role="status">正在读取共享标注…</span><button data-chart-action="reload-drawings" id="drawing-reload" hidden>放弃本地并读取共享版本</button><button data-chart-action="save" id="drawing-save" disabled>保存标注</button><button data-chart-action="export" id="chart-export">导出 PNG</button></div></div>
  <p class="chart-tool-help" id="chart-tool-help">手形模式：拖动平移，滚轮或双指缩放。选择绘图工具后，可在价格区域画图。</p>
  <div class="chart-footnotes"><p>价格口径：IG Bid · 时间：香港 UTC+8。历史 K 线按需读取并缓存，自动检查更新；不将实时单点报价拼成完整 K 线。休市与缺失区间不补造数据。</p><p>成交点来自可核验的 IG 真实成交时间与价格；数量为各笔真实成交合约数，不等于市场成交量；未确认数量保留为未确认。虚线从当前可见成交点延伸至成交价标签，标签拥挤时用引线连接真实价格。部分平仓逐笔显示，未能核对或未加载历史的成交会提示未展示数量。止损、止盈线来自当前持仓，仅作价格参考。标注按产品保存并在不同周期共享，保存后所有获准访问者均可查看和修改。</p><p>TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">图表技术由 TradingView 提供 ↗</a></p></div>
  <details class="tv-reference-pane" id="tv-reference-pane"><summary><span><strong>TradingView 参考图</strong><small id="tv-reference-symbol">正在匹配公开产品代码</small></span><span class="tv-reference-expand">展开参考图 ＋</span></summary><div class="tv-reference-body"><div class="tv-reference-note">TradingView 参考行情，与 IG 合约价格可能不同；TV 内标注不与 IG 共享标注同步。私有成交点仅显示在上方 IG 图表。</div><p id="tv-reference-status" class="tv-reference-status" role="status">展开后才会加载外部行情。</p><div id="tv-reference-frame"></div><div class="tv-reference-actions"><button type="button" data-chart-action="tv-retry" id="tv-reference-retry">重新加载参考图</button><a id="tv-reference-external" class="link-button" target="_blank" rel="noopener noreferrer">在 TradingView 外部打开 ↗</a></div></div></details>
</section>`;

export function mountCharts(container,data,requestedEpic){
  if(active?.container===container&&container.querySelector('#product-chart-canvas')){active.update(data,requestedEpic);return;}
  unmountCharts();container.innerHTML=markup();active=new ProductCharts(container,data,requestedEpic);
}
export function unmountCharts(){if(active){active.destroy();active=null;}}

class ProductCharts{
  constructor(container,data,requestedEpic){
    this.container=container;this.data=data;this.epic='';this.resolution='MINUTE_5';this.type='candles';this.tool='pan';this.candles=[];this.timeline=[];this.priceLines=[];this.selected=null;this.gesture=null;this.pending=null;this.destroyed=false;this.busy=false;this.queryToken=0;this.response=null;this.pollTimer=null;this.pendingQuery=null;this.abort=null;this.lastDrawFetch=0;this.productKey='';this.linesKey='';this.drawMessage='';this.saving=false;this.drawingLoadBusy=false;this.tradeCache=new Map();this.tradeMarkers=[];this.tradeOmitted=[];this.tradeInvalid=0;this.tradeMessage='';this.tradeVisible=true;this.tradePixels=[];this.tradeAbort=null;this.tradeFetchToken=0;this.tradeLoading=false;
    this.tradeQuantityVisible=true;this.tradePriceVisible=true;this.tradeHighlighted=new Set();this.tradePriceLabels=[];this.tradeQuantityLabels=[];
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
    const autoscaleInfoProvider=original=>this.tradeAutoscale(original);
    this.candleSeries=this.chart.addSeries(L.CandlestickSeries,{autoscaleInfoProvider,upColor:'#287f68',downColor:'#be5a56',wickUpColor:'#287f68',wickDownColor:'#be5a56',borderVisible:false,priceLineVisible:true,lastValueVisible:true});
    this.lineSeries=this.chart.addSeries(L.LineSeries,{autoscaleInfoProvider,color:'#1b6355',lineWidth:2,visible:false,priceLineVisible:true,lastValueVisible:true,crosshairMarkerVisible:true});
    this.gapSeries=[];this.bidLines=[];this.canvas=this.$('#drawing-overlay');this.ctx=this.canvas.getContext('2d');
    this.onClick=e=>this.click(e);this.onChange=e=>this.change(e);this.onPointerDown=e=>this.pointerDown(e);this.onPointerMove=e=>this.pointerMove(e);this.onPointerUp=e=>this.pointerUp(e);this.onPointerCancel=()=>this.cancelGesture();this.onKey=e=>this.key(e);
    container.addEventListener('click',this.onClick);container.addEventListener('change',this.onChange);container.addEventListener('keydown',this.onKey);
    this.onTradeMove=e=>this.tradePointerMove(e);this.onTradeDown=e=>{if(this.tool==='pan'&&!e.target.closest('.trade-marker-tooltip')){this.tradeTap={x:e.clientX,y:e.clientY};this.hideTradeTooltip();}};this.onTradeUp=e=>{if(this.tool==='pan'&&this.tradeTap&&Math.hypot(e.clientX-this.tradeTap.x,e.clientY-this.tradeTap.y)<7)this.showTradeAt(e);this.tradeTap=null;};this.onTradeLeave=()=>{if(!this.tradePinned)this.hideTradeTooltip();};this.tradeWrap=this.$('.chart-canvas-wrap');this.tradeWrap.addEventListener('pointermove',this.onTradeMove,{passive:true});this.tradeWrap.addEventListener('pointerdown',this.onTradeDown,{passive:true});this.tradeWrap.addEventListener('pointerup',this.onTradeUp,{passive:true});this.tradeWrap.addEventListener('pointerleave',this.onTradeLeave);this.onTradeWheel=()=>this.hideTradeTooltip();this.tradeWrap.addEventListener('wheel',this.onTradeWheel,{passive:true});
    this.canvas.addEventListener('pointerdown',this.onPointerDown);this.canvas.addEventListener('pointermove',this.onPointerMove);this.canvas.addEventListener('pointerup',this.onPointerUp);this.canvas.addEventListener('pointercancel',this.onPointerCancel);
    this.onReferenceToggle=()=>{this.$('.tv-reference-expand').textContent=this.$('#tv-reference-pane').open?'收起参考图 −':'展开参考图 ＋';if(this.$('#tv-reference-pane').open)this.reference();else this.closeReference();};this.$('#tv-reference-pane').addEventListener('toggle',this.onReferenceToggle);
    this.onReferenceMessage=e=>{
      const frame=this.$('#tv-reference-bootstrap'),state=this.referenceState,m=e.data;
      if(!frame||!state||state.accepted||e.source!==frame.contentWindow||!m||m.nonce!==state.nonce||m.symbol!==state.symbol||m.interval!==state.interval||m.style!==state.style)return;
      if(m.type==='harbour-tv-reference-status'&&typeof m.status==='string'){this.$('#tv-reference-pane').dataset.referencePhase='bootstrap-status';this.$('#tv-reference-status').textContent=m.status.slice(0,400);return;}
      if(m.type!=='harbour-tv-reference-candidate')return;this.$('#tv-reference-pane').dataset.referenceCandidate=typeof m.url==='string'?m.url.slice(0,20000):'';const url=validateTradingViewUrl(m.url,state);
      if(!url){this.$('#tv-reference-pane').dataset.referencePhase='candidate-rejected';this.$('#tv-reference-status').textContent='参考图地址未通过当前产品与安全校验。可重试或使用外部打开入口。';return;}
      state.accepted=true;this.$('#tv-reference-pane').dataset.referencePhase='display-frame-created';clearTimeout(this.referenceTimer);
      const remote=document.createElement('iframe');remote.id='harbour-tv-reference-display';remote.title=`TradingView 参考行情 ${state.symbol}`;remote.setAttribute('sandbox','allow-scripts allow-same-origin');remote.referrerPolicy='no-referrer';remote.src=url;this.$('#tv-reference-frame').appendChild(remote);
      this.$('#tv-reference-status').textContent='TradingView 参考图已打开；行情是否可用、是否延迟请以图内显示为准。';
    };window.addEventListener('message',this.onReferenceMessage);
    this.chart.subscribeCrosshairMove(p=>this.reading(p));
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{if(!this.tradePinned)this.hideTradeTooltip();this.paint();});
    this.observer=new ResizeObserver(()=>{this.resize();});this.observer.observe(this.$('.chart-canvas-wrap'));
    this.frame=()=>{if(this.destroyed)return;this.paint();this.animation=requestAnimationFrame(this.frame);};this.animation=requestAnimationFrame(this.frame);
    this.setTool('pan');this.update(data,requestedEpic);this.resize();
    this.interval=setInterval(()=>{if(this.destroyed||!this.epic)return;if(!this.busy&&!this.gesture&&!this.pending)this.load();if(!this.tradeLoading)this.loadTrades();if(Date.now()-this.lastDrawFetch>60000&&!this.drawState?.dirty&&!this.saving)this.loadDrawings();},30000);
  }
  axisTime(t,type){const d=new Date(Number(t)*1000);if(!finite(Number(t)))return '';const options=type===0?{year:'numeric'}:type===1?{year:'numeric',month:'short'}:type===2?{month:'short',day:'numeric'}:{hour:'2-digit',minute:'2-digit',hour12:false};return d.toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong',...options});}
  get step(){return RESOLUTIONS.find(r=>r[0]===this.resolution)?.[2]||60;}
  get series(){return this.type==='line'?this.lineSeries:this.candleSeries;}
  update(data,requestedEpic){
    const tradeSignature=JSON.stringify([(data?.positions||[]).map(p=>[p.epic,p.dealId,p.createdDateUTC,p.level,p.size,p.direction]),data?.ledger_count??data?.history?.ledger_count??data?.history?.total_count??null]);if(this.tradeSignature&&tradeSignature!==this.tradeSignature){this.tradeCache.clear();clearTimeout(this.tradeUpdateTimer);this.tradeUpdateTimer=setTimeout(()=>{if(!this.destroyed){this.tradeAbort?.abort();this.tradeFetchToken++;this.tradeLoading=false;this.loadTrades();}},500);}this.tradeSignature=tradeSignature;this.data=data;if(!this.chart)return;const products=productList(data),key=JSON.stringify(products);
    if(this.productKey!==key){this.productKey=key;this.$('#chart-product').innerHTML=products.map(p=>`<option value="${esc(p.epic)}">${p.position?'持仓 · ':'关注 · '}${esc(p.name)}</option>`).join('');}
    const epic=requestedEpic&&products.some(p=>p.epic===requestedEpic)?requestedEpic:products.some(p=>p.epic===this.epic)?this.epic:products[0]?.epic;
    if(epic&&epic!==this.epic)this.chooseProduct(epic);else if(!epic){this.$('#chart-empty').textContent='当前没有已同步的持仓或关注产品。';this.$('#chart-status').textContent='等待产品同步';}
    this.$('#chart-product').value=this.epic;this.quote();this.references();this.reference();
  }
  reference({reload=false}={}){
    const symbol=TV_REFERENCE_SYMBOLS[this.epic],interval=TV_REFERENCE_INTERVALS[this.resolution],style=this.type==='line'?'2':'1',pane=this.$('#tv-reference-pane');
    this.$('#tv-reference-symbol').textContent=symbol?`${symbol} · ${RESOLUTIONS.find(r=>r[0]===this.resolution)?.[1]}`:'此产品尚未配置参考图映射';
    const external=this.$('#tv-reference-external');external.hidden=!symbol;external.href=symbol?`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`:'#';this.$('#tv-reference-retry').disabled=!symbol;
    if(!pane.open)return;if(!symbol){this.closeReference();this.$('#tv-reference-status').textContent='此 IG 产品尚未配置对应的公开 TradingView 产品代码。请使用上方 IG K 线；不会按名称猜测其他合约。';return;}
    const key=`${symbol}/${interval}/${style}`;if(!reload&&this.referenceKey===key&&this.$('#tv-reference-bootstrap'))return;this.closeReference();this.referenceKey=key;
    this.$('#tv-reference-status').textContent='正在加载隔离的 TradingView 参考图；图内出现行情前不视为就绪。';
    const nonce=crypto.randomUUID();this.referenceState={nonce,symbol,interval,style,accepted:false};pane.dataset.referencePhase='bootstrap-loading';pane.dataset.referenceExpected=JSON.stringify({symbol,interval,style});delete pane.dataset.referenceCandidate;const frame=document.createElement('iframe');frame.id='tv-reference-bootstrap';frame.className='tv-reference-bootstrap';frame.title='TradingView 参考图隔离配置';frame.tabIndex=-1;frame.setAttribute('aria-hidden','true');frame.setAttribute('sandbox','allow-scripts');frame.referrerPolicy='no-referrer';frame.src='/tv-reference.html?'+new URLSearchParams({symbol,interval,style,nonce});this.$('#tv-reference-frame').appendChild(frame);
    this.referenceTimer=setTimeout(()=>{if(!this.destroyed&&pane.open&&!this.referenceState?.accepted)this.$('#tv-reference-status').textContent='参考图尚未确认可用。可重试或在 TradingView 外部打开；IG 图表和标注仍可继续使用。';},25000);
  }
  closeReference(){clearTimeout(this.referenceTimer);this.referenceKey='';this.referenceState=null;this.$('#tv-reference-pane').dataset.referencePhase='closed';this.$('#tv-reference-frame').replaceChildren();}
  chooseProduct(epic){
    this.cancelGesture();this.hideTradeTooltip();this.tradeAbort?.abort();this.tradeFetchToken++;this.tradeLoading=false;this.epic=epic;this.tradeMarkers=[];this.tradeOmitted=[];this.tradeInvalid=0;this.tradeMessage='';this.tradePixels=[];this.queryToken++;this.abort?.abort();this.busy=false;clearTimeout(this.pollTimer);this.resKey='';this.candles=[];this.timeline=[];this.response=null;this.pendingQuery=null;this.drawState=stateFor(epic);this.selected=null;this.drawMessage='';this.linesKey='';this.$('#chart-product').value=epic;this.$('#chart-epic').textContent=epic;this.candleSeries.setData([]);this.lineSeries.setData([]);for(const s of this.gapSeries)this.chart.removeSeries(s);this.gapSeries=[];this.$('#chart-empty').hidden=false;this.$('#chart-empty').textContent='正在读取 IG 历史价格…';this.reading();this.drawingStatus();if(!this.drawState.loaded)this.loadDrawings();this.load();this.loadTrades();this.references();this.reference();
  }
  quote(){const m=(this.data.markets||[]).find(m=>m.epic===this.epic),timestamp=timestampSeconds(m?.timestamp),verified=m&&!m.stale&&timestamp!=null;this.$('#chart-bid').textContent=number(m?.bid);this.$('#chart-ask').textContent=`Ask ${number(m?.ask)} · ${m?.stale?'报价待核验':m?.delayed?'延迟报价':timestamp!=null?hkt(timestamp,true):'等待来源时间'}`;
    if(finite(m?.bid)){const options={price:m.bid,color:verified?'#456b7a':'#9d896b',lineWidth:1,lineStyle:3,axisLabelVisible:true,lineVisible:true,title:verified?(m.delayed?'延迟 Bid':'实时 Bid'):'Bid 待核验'};if(!this.bidLines.length)this.bidLines=[this.candleSeries.createPriceLine(options),this.lineSeries.createPriceLine(options)];else for(const line of this.bidLines)line.applyOptions(options);}else for(const line of this.bidLines)line.applyOptions({lineVisible:false,axisLabelVisible:false});
  }
  references(){
    if(!this.chart)return;const positions=(this.data.positions||[]).filter(p=>p.epic===this.epic),key=JSON.stringify([this.epic,this.type,positions.map(p=>[p.dealId,p.level,p.stopLevel,p.limitLevel])]);if(key===this.linesKey)return;this.linesKey=key;
    for(const [s,l] of this.priceLines)s.removePriceLine(l);this.priceLines=[];
    for(const [i,p] of positions.entries())for(const [field,title,color] of [['stopLevel','止损','#b44b4c'],['limitLevel','止盈','#946526']])if(finite(p[field]))this.priceLines.push([this.series,this.series.createPriceLine({price:p[field],color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title:`${title}${positions.length>1?` ${i+1}`:''}`})]);
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
      this.$('#chart-earlier').textContent=result.has_more?'加载更早历史':'已到当前可用历史边界';this.needsPaint=true;this.reading();
    }catch(e){if(e.name!=='AbortError'&&token===this.queryToken&&!this.destroyed){this.$('#chart-status').textContent=e.message||'历史请求失败，请稍后重试。';this.$('#chart-status').classList.add('chart-status-warning');if(!this.candles.length)this.$('#chart-empty').textContent=e.message;this.pendingQuery=null;}}
    finally{if(token===this.queryToken&&!this.destroyed){this.busy=false;this.$('#chart-refresh').disabled=false;this.$('#chart-earlier').disabled=!nextHistoryQuery(this.response,this.candles)||this.response?.status==='loading';}}
  }
  setLineData(rows,priceFormat){
    for(const s of this.gapSeries)this.chart.removeSeries(s);this.gapSeries=[];
    const segments=lineSegments(rows),last=segments.pop()||[];
    this.lineSeries.setData([...last,...rows.filter(b=>!finite(b.value))].sort((a,b)=>a.time-b.time));
    for(const segment of segments){const s=this.chart.addSeries(window.LightweightCharts.LineSeries,{autoscaleInfoProvider:original=>this.tradeAutoscale(original),color:'#1b6355',lineWidth:2,visible:this.type==='line',priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:{type:'price',...priceFormat}});s.setData(segment);this.gapSeries.push(s);}
  }
  inferPriceStep(bars){let precision=2;for(const b of bars.slice(-100))for(const v of [b.open,b.high,b.low,b.close]){const text=String(v);if(text.includes('.'))precision=Math.max(precision,Math.min(6,text.split('.')[1].length));}return {precision,minMove:10**(-precision)};}
  reading(param){if(this.gesture||this.pending)return;let b=param?.seriesData?.get(this.candleSeries);if(!b&&param?.time)b=this.candles.find(b=>b.time===param.time);if(!b)b=this.candles.at(-1);this.$('#chart-reading').textContent=b?`${hkt(b.time)} HKT   开 ${number(b.open)}   高 ${number(b.high)}   低 ${number(b.low)}   收 ${number(b.close)}${param?.time?'':' · 最新已读取 K 线'}`:'将十字光标移至图上，查看时间与开高低收。';}
  setResolution(resolution){if(this.resolution===resolution||!RESOLUTIONS.some(r=>r[0]===resolution))return;this.cancelGesture();this.hideTradeTooltip();this.resolution=resolution;this.queryToken++;this.abort?.abort();this.busy=false;clearTimeout(this.pollTimer);this.candles=[];this.timeline=[];this.response=null;this.pendingQuery=null;this.resKey='';this.candleSeries.setData([]);this.lineSeries.setData([]);for(const s of this.gapSeries)this.chart.removeSeries(s);this.gapSeries=[];this.$('#chart-empty').hidden=false;this.$('#chart-empty').textContent='正在读取此周期…';this.$$('.chart-periods button').forEach(b=>{const on=b.dataset.resolution===resolution;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});this.load();this.reference();}
  $$(s){return [...this.container.querySelectorAll(s)];}
  setType(type){if(!['candles','line'].includes(type)||this.type===type)return;this.cancelGesture();this.type=type;this.candleSeries.applyOptions({visible:type==='candles'});this.lineSeries.applyOptions({visible:type==='line'});for(const s of this.gapSeries)s.applyOptions({visible:type==='line'});this.$$('.chart-types button').forEach(b=>{const on=b.dataset.chartType===type;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});this.references();this.reference();}
  setTool(tool){this.cancelGesture();this.hideTradeTooltip();this.tool=tool;this.canvas.style.pointerEvents=tool==='pan'?'none':'auto';this.canvas.style.touchAction=tool==='pan'?'auto':'none';this.canvas.style.cursor=tool==='select'?'default':'crosshair';this.$$('button[data-draw-tool]').forEach(b=>{const on=b.dataset.drawTool===tool;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});this.$('#chart-tool-help').textContent={pan:'手形模式：拖动平移，滚轮或双指缩放。选择绘图工具后，可在价格区域画图。',select:'选择模式：点击已有标注，再拖动移动；可更换颜色或粗细，并删除选中标注。',trend:'趋势线：按住并拖动，或依次点击两个端点。完成后可切换“选择”移动标注。',horizontal:'水平线：点击价格位置。可在“选择”模式中上下拖动。',rectangle:'矩形：拖动两个对角，或依次点击两个角点。',pen:'画笔：按住并拖动；手机可用单指绘制。切回“手形”后恢复图表平移缩放。'}[tool];this.drawingStatus();}
  tradePlacement(marker){return tradeMarkerPlacement(marker,this.candles,this.timeline,this.resolution,this.response?.gaps);}
  tradeAutoscale(original){
    const base=original();if(!base?.priceRange||!this.tradeVisible||!this.tradeMarkers.length)return base;
    const range=this.chart?.timeScale().getVisibleLogicalRange();if(!range)return base;let min=base.priceRange.minValue,max=base.priceRange.maxValue;
    for(const m of this.tradeMarkers){const p=this.tradePlacement(m);if(p.logical!=null&&p.logical>=range.from&&p.logical<=range.to){min=Math.min(min,m.price);max=Math.max(max,m.price);}}
    return {...base,priceRange:{minValue:min,maxValue:max}};
  }
  refreshTradeScale(){const provider=original=>this.tradeAutoscale(original);this.candleSeries?.applyOptions({autoscaleInfoProvider:provider});this.lineSeries?.applyOptions({autoscaleInfoProvider:provider});for(const s of this.gapSeries||[])s.applyOptions({autoscaleInfoProvider:provider});this.needsPaint=true;}
  async loadTrades(){
    if(!this.epic||this.destroyed||this.tradeLoading)return;const epic=this.epic,cached=this.tradeCache.get(epic);
    if(cached&&Date.now()-cached.at<60000){this.applyTrades(cached);return;}
    this.tradeLoading=true;const token=++this.tradeFetchToken;this.tradeAbort=new AbortController();this.tradeMessage='正在读取真实成交…';this.needsPaint=true;
    try{const r=await fetch('/api/chart/trades?'+new URLSearchParams({epic}),{credentials:'same-origin',cache:'no-store',signal:this.tradeAbort.signal}),result=await r.json();if(this.destroyed||this.epic!==epic||token!==this.tradeFetchToken)return;
      if(!r.ok)throw Error(r.status===401?'登录已过期，请刷新页面重新登录。':result.message||result.error||'成交读取失败');if(result.epic!==epic)throw Error('成交产品与当前图表不一致');
      const normalized=normalizeTradeMarkers(result.markers),entry={...normalized,omitted:Array.isArray(result.omitted)?result.omitted.filter(o=>o&&Number.isInteger(o.count)&&o.count>0).map(o=>({count:o.count,message:typeof o.message==='string'?o.message.slice(0,200):'成交资料未能核对'})):[],message:typeof result.message==='string'?result.message.slice(0,400):'',at:Date.now()};this.tradeCache.set(epic,entry);this.applyTrades(entry);
    }catch(e){if(e.name!=='AbortError'&&!this.destroyed&&epic===this.epic&&token===this.tradeFetchToken){this.tradeMessage='成交点读取失败：'+(e.message||'请稍后重试');this.needsPaint=true;}}
    finally{if(token===this.tradeFetchToken)this.tradeLoading=false;}
  }
  applyTrades(entry){this.tradeMarkers=entry.markers;this.tradeOmitted=entry.omitted;this.tradeInvalid=entry.invalid;this.tradeMessage=entry.message;this.refreshTradeScale();}
  tradeStatus(loaded,visible,reasons){
    let text=this.tradeMarkers.length?`可核验 ${this.tradeMarkers.length} 笔 · 已加载区间 ${loaded} 笔 · 当前视野 ${visible} 笔${this.tradeVisible?'':' · 成交点已隐藏'}`:'暂无可核验成交点';
    const omitted=this.tradeOmitted.map(o=>`${o.count} 笔${o.message}`);if(this.tradeInvalid)omitted.push(`${this.tradeInvalid} 笔成交字段无效`);for(const [reason,count] of reasons)omitted.push(`${count} 笔${reason}`);if(omitted.length)text+='；未展示：'+omitted.join('；');if(this.tradeMessage)text+=' · '+this.tradeMessage;
    const status=this.$('#trade-marker-status');if(status.textContent!==text)status.textContent=text;this.$('#trade-marker-latest').disabled=!loaded;this.canvas.dataset.tradeVisibleCount=String(visible);this.canvas.dataset.tradeLoadedCount=String(loaded);
  }
  paintTrades(ctx){
    this.tradePixels=[];const reasons=new Map();let loaded=0;for(const marker of this.tradeMarkers){const place=this.tradePlacement(marker);if(place.logical==null){reasons.set(place.reason,(reasons.get(place.reason)||0)+1);continue;}loaded++;if(!this.tradeVisible)continue;
      const x=logicalCoordinate(this.chart.timeScale(),place.logical),y=this.series.priceToCoordinate(marker.price);if(!finite(x)||!finite(y)||x<0||x>this.chart.timeScale().width()||y<0||y>this.plotHeight())continue;
      this.tradePixels.push({marker,x,y,logical:place.logical});
    }
    this.tradePriceLabels=[];this.tradeQuantityLabels=[];const width=this.chart.timeScale().width(),height=this.plotHeight(),points=this.tradePixels,prices=new Map(),coincident=new Map();
    for(const p of points){const key=String(p.marker.price);if(!prices.has(key))prices.set(key,[]);prices.get(key).push(p);const pointKey=`${p.marker.time}/${p.marker.price}`;if(!coincident.has(pointKey))coincident.set(pointKey,[]);coincident.get(pointKey).push(p);}
    ctx.save();ctx.font='9px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif';ctx.textBaseline='middle';
    // The horizontal segment always uses the true execution price coordinate.
    // Only its text box may move to avoid another label; a leader retains the link.
    if(this.tradePriceVisible){
      const labels=[...prices.values()].map(group=>{const sides=new Set(group.map(p=>p.marker.side));return {group,y:group[0].y,text:`${tradeNumber(group[0].marker.price)}${group.length>1?` · ${group.length}笔`:''}`,color:sides.size===1?tradeColor(group[0].marker.side):'#526e65',active:group.some(p=>this.tradeHighlighted.has(p.marker.id))};}).sort((a,b)=>a.y-b.y);
      const labelHeight=15,gap=2;let previous=-Infinity;for(const label of labels){label.top=Math.max(1,label.y-labelHeight/2,previous+gap);previous=label.top+labelHeight;}
      if(previous>height-1){let next=height-1;for(const label of [...labels].reverse()){label.top=Math.min(label.top,next-labelHeight);next=label.top-gap;}}
      for(const label of [...labels].sort((a,b)=>Number(a.active)-Number(b.active))){const {group,y,text,color,active}=label,start=Math.min(...group.map(p=>p.x)),boxWidth=Math.min(this.canvas.clientWidth-4,ctx.measureText(text).width+9),left=this.canvas.clientWidth-boxWidth-2,top=Math.max(1,label.top),end=Math.min(width,left);
        ctx.save();ctx.beginPath();ctx.rect(0,0,width,height);ctx.clip();ctx.strokeStyle=color;ctx.globalAlpha=active?.9:.38;ctx.lineWidth=active?1.1:.8;ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(start,y);ctx.lineTo(width,y);ctx.stroke();ctx.restore();
        ctx.save();ctx.strokeStyle=color;ctx.lineWidth=.7;ctx.globalAlpha=1;ctx.setLineDash([]);if(Math.abs(top+labelHeight/2-y)>1){ctx.beginPath();ctx.moveTo(Math.max(start,end-4),y);ctx.lineTo(left-4,top+labelHeight/2);ctx.lineTo(left,top+labelHeight/2);ctx.stroke();}ctx.fillStyle=active?'#f0f6f0':'#ffffff';ctx.fillRect(left,top,boxWidth,labelHeight);ctx.strokeRect(left,top,boxWidth,labelHeight);ctx.fillStyle=color;ctx.fillText(text,left+4,top+labelHeight/2);ctx.restore();this.tradePriceLabels.push({price:group[0].marker.price,x:start,y,left,top,width:boxWidth,height:labelHeight,text,count:group.length});
      }
    }
    // Quantity labels are for the owner's executions, never exchange volume.
    // Exact-time/price overlaps retain one row per execution rather than averaging.
    ctx.save();ctx.beginPath();ctx.rect(0,0,width,height);ctx.clip();const occupied=[];
    if(this.tradeQuantityVisible)for(const group of coincident.values()){const anchor=group[0],rows=group.map(p=>tradeQuantity(p.marker)),boxWidth=Math.min(Math.max(...rows.map(text=>ctx.measureText(text).width))+8,width-4),boxHeight=rows.length*12+3,clampX=x=>Math.max(2,Math.min(width-boxWidth-2,x)),clampY=y=>Math.max(2,Math.min(height-boxHeight-2,y)),collides=(x,y)=>occupied.some(r=>x<r.x+r.width+3&&x+boxWidth+3>r.x&&y<r.y+r.height+2&&y+boxHeight+2>r.y);
      const candidates=[[anchor.x+7,anchor.y-boxHeight-5],[anchor.x-boxWidth-7,anchor.y-boxHeight-5],[anchor.x+7,anchor.y+6],[anchor.x-boxWidth-7,anchor.y+6]];let place=candidates.map(([x,y])=>({x:clampX(x),y:clampY(y)})).find(p=>!collides(p.x,p.y));
      if(!place)for(let offset=boxHeight+5;offset<height&&!place;offset+=boxHeight+5)for(const y of [anchor.y-offset,anchor.y+offset]){const p={x:clampX(anchor.x+7),y:clampY(y)};if(!collides(p.x,p.y)){place=p;break;}}
      place??={x:clampX(anchor.x+7),y:clampY(anchor.y-boxHeight-5)};occupied.push({...place,width:boxWidth,height:boxHeight});const active=group.some(p=>this.tradeHighlighted.has(p.marker.id));ctx.save();ctx.fillStyle=active?'#f0f6f0ee':'#ffffffda';ctx.fillRect(place.x,place.y,boxWidth,boxHeight);if(Math.hypot(place.x-anchor.x,place.y+boxHeight/2-anchor.y)>boxWidth+15){ctx.strokeStyle='#9bad9e';ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(anchor.x,anchor.y);ctx.lineTo(place.x,place.y+boxHeight/2);ctx.stroke();}rows.forEach((text,i)=>{ctx.fillStyle=tradeColor(group[i].marker.side);ctx.fillText(text,place.x+4,place.y+8+i*12);this.tradeQuantityLabels.push({id:group[i].marker.id,size:group[i].marker.size,text,x:place.x+4,y:place.y+8+i*12});});ctx.restore();
    }
    for(const {marker,x,y} of points){ctx.save();const color=tradeColor(marker.side);if(this.tradeHighlighted.has(marker.id)){ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fillStyle=color+'15';ctx.fill();}ctx.beginPath();ctx.arc(x,y,TRADE_DOT_RADIUS,0,Math.PI*2);ctx.fillStyle=marker.action==='OPEN'?color:'#fff';ctx.strokeStyle=color;ctx.lineWidth=1;ctx.fill();ctx.stroke();ctx.restore();}
    ctx.restore();ctx.restore();this.canvas.dataset.tradePriceCount=String(this.tradePriceLabels.length);this.canvas.dataset.tradeQuantityCount=String(this.tradeQuantityLabels.length);this.tradeStatus(loaded,points.length,reasons);
  }
  tradeHits(e){const r=this.canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;return this.tradePixels.filter(p=>Math.hypot(p.x-x,p.y-y)<=12);}
  tradePointerMove(e){if(e.target.closest('.trade-marker-tooltip'))return;if(this.tool!=='pan'||e.buttons){this.hideTradeTooltip();return;}if(e.pointerType==='touch'||this.tradePinned)return;const hits=this.tradeHits(e);if(hits.length)this.showTradeTooltip(hits,false);else this.hideTradeTooltip();}
  showTradeAt(e){const hits=this.tradeHits(e);if(hits.length)this.showTradeTooltip(hits,true);else this.hideTradeTooltip();}
  hideTradeTooltip(){this.tradePinned=false;if(this.tradeHighlighted?.size){this.tradeHighlighted.clear();this.needsPaint=true;}const tip=this.$('#trade-marker-tooltip');if(tip)tip.hidden=true;}
  showTradeTooltip(hits,pinned){
    if(!hits.length||!this.tradeVisible)return;const tip=this.$('#trade-marker-tooltip');tip.replaceChildren();this.tradePinned=pinned;const nextIds=hits.map(p=>p.marker.id).sort();if(JSON.stringify([...this.tradeHighlighted].sort())!==JSON.stringify(nextIds)){this.tradeHighlighted=new Set(nextIds);this.needsPaint=true;}
    const header=document.createElement('div');header.className='trade-marker-tooltip-header';const title=document.createElement('strong');title.textContent=hits.length>1?`此处 ${hits.length} 笔成交`:'真实成交';header.appendChild(title);const close=document.createElement('button');close.type='button';close.dataset.chartAction='trade-tooltip-close';close.setAttribute('aria-label','关闭成交详情');close.textContent='×';header.appendChild(close);tip.appendChild(header);
    for(const {marker:m} of hits){const item=document.createElement('div');item.className='trade-marker-tooltip-item';const action=document.createElement('strong');action.className=m.side==='BUY'?'trade-buy-text':'trade-sell-text';action.textContent=`${m.side==='BUY'?'买入':'卖出'} · ${m.action==='OPEN'?'开仓':'平仓'}`;item.appendChild(action);for(const text of [`${tradeHkt(m.time)} HKT`,`成交价 ${tradeNumber(m.price)} · 数量 ${tradeNumber(m.size)}`,`成交编号 ${m.reference||m.id}`]){const line=document.createElement('div');line.textContent=text;item.appendChild(line);}tip.appendChild(item);}
    tip.hidden=false;const w=this.canvas.clientWidth,h=this.plotHeight(),anchor=hits[0];tip.style.left=`${Math.max(6,Math.min(w-tip.offsetWidth-6,anchor.x+14))}px`;tip.style.top=`${Math.max(6,Math.min(h-tip.offsetHeight-6,anchor.y+14))}px`;
  }
  async latestTrade(){const places=this.tradeMarkers.map(marker=>({marker,...this.tradePlacement(marker)})).filter(p=>p.logical!=null);if(!places.length)return;this.hideTradeTooltip();this.tradeVisible=true;this.$('#trade-markers-visible').checked=true;const last=places.at(-1),epic=this.epic,resolution=this.resolution,token=this.tradeLocateToken=(this.tradeLocateToken||0)+1,range=this.chart.timeScale().getVisibleLogicalRange(),width=Math.max(12,range?range.to-range.from:80);this.chart.timeScale().setVisibleLogicalRange({from:last.logical-width*.6,to:last.logical+width*.4});this.chart.priceScale('right').applyOptions({autoScale:true});this.refreshTradeScale();await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));if(this.destroyed||this.epic!==epic||this.resolution!==resolution||this.tradeLocateToken!==token)return;this.needsPaint=true;this.paint();const hits=this.tradePixels.filter(p=>p.marker.time===last.marker.time&&p.marker.price===last.marker.price);this.showTradeTooltip(hits,true);}
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
  pixel(p){const logical=timeToLogical(p.time,this.timeline,this.step),x=logical==null?null:logicalCoordinate(this.chart.timeScale(),logical),y=this.series.priceToCoordinate(p.price);return finite(x)&&finite(y)?{x,y}:null;}
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
  paint(){if(!this.ctx||!this.chart)return;const range=this.chart.timeScale().getVisibleLogicalRange(),bar=this.candles.at(-1),key=JSON.stringify([range,this.canvas.width,this.canvas.height,bar?this.series.priceToCoordinate(bar.close):null,bar?this.series.priceToCoordinate(bar.close+1):null,this.type,this.resKey]);if(!this.needsPaint&&key===this.paintKey)return;this.paintKey=key;this.needsPaint=false;const ctx=this.ctx;ctx.clearRect(0,0,this.canvas.clientWidth,this.canvas.clientHeight);ctx.save();ctx.beginPath();ctx.rect(0,0,this.chart.timeScale().width(),this.plotHeight());ctx.clip();for(const d of [...(this.drawState?.drawings||[]),...(this.pending?[this.pending]:[])])this.paintDrawing(ctx,d);ctx.restore();this.paintTrades(ctx);}
  paintDrawing(ctx,d){const ps=d.points.map(p=>this.pixel(p));if(ps.some(p=>!p)||!ps.length)return;ctx.save();ctx.strokeStyle=d.color;ctx.lineWidth=d.width;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();if(d.type==='horizontal'){ctx.moveTo(0,ps[0].y);ctx.lineTo(this.chart.timeScale().width(),ps[0].y);}else if(d.type==='rectangle'&&ps.length>=2){ctx.fillStyle=d.color+'12';ctx.fillRect(ps[0].x,ps[0].y,ps[1].x-ps[0].x,ps[1].y-ps[0].y);ctx.rect(ps[0].x,ps[0].y,ps[1].x-ps[0].x,ps[1].y-ps[0].y);}else{ctx.moveTo(ps[0].x,ps[0].y);for(const p of ps.slice(1))ctx.lineTo(p.x,p.y);}ctx.stroke();if(d.id===this.selected){const ends=d.type==='horizontal'?[{x:this.chart.timeScale().width()/2,y:ps[0].y}]:d.type==='pen'?[ps[0],ps.at(-1)]:ps;for(const p of ends){ctx.beginPath();ctx.fillStyle='#ffffff';ctx.lineWidth=1.5;ctx.arc(p.x,p.y,4,0,Math.PI*2);ctx.fill();ctx.stroke();}}ctx.restore();}
  async export(){if(!this.candles.length){this.drawMessage='历史价格尚未就绪，暂不能导出图表。';this.drawingStatus();return;}try{this.paint();const snapshot=this.chart.takeScreenshot(),out=document.createElement('canvas'),dpr=snapshot.width/this.canvas.clientWidth;out.width=snapshot.width;out.height=snapshot.height+Math.round(85*dpr);const ctx=out.getContext('2d');ctx.fillStyle='#ffffff';ctx.fillRect(0,0,out.width,out.height);ctx.save();ctx.scale(dpr,dpr);ctx.fillStyle='#1e343b';ctx.font='600 15px sans-serif';const p=productList(this.data).find(p=>p.epic===this.epic);ctx.fillText(`${p?.name||this.epic} · ${RESOLUTIONS.find(r=>r[0]===this.resolution)[1]} · IG Bid`,12,24);ctx.fillStyle='#708080';ctx.font='11px sans-serif';ctx.fillText(`香港时间 UTC+8 · 最新 K 线 ${hkt(this.candles.at(-1).time)} · TradingView Lightweight Charts™`,12,45);ctx.fillText(`绿买 / 红卖 · 实心开仓 / 空心平仓 · 当前视野 ${this.tradePixels.length} 笔${this.tradeVisible?'':'（已隐藏）'} · ×合约数${this.tradeQuantityVisible?'':'（已隐藏）'} · 成交价虚线${this.tradePriceVisible?'':'（已隐藏）'}`,12,62);ctx.restore();ctx.drawImage(snapshot,0,Math.round(75*dpr));ctx.drawImage(this.canvas,0,Math.round(75*dpr),snapshot.width,snapshot.height);const blob=await new Promise(r=>out.toBlob(r,'image/png'));if(!blob)throw Error('浏览器未能生成 PNG');const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`Harbour-${this.epic.replace(/[^\w.-]/g,'_')}-${this.resolution}.png`;a.click();setTimeout(()=>URL.revokeObjectURL(url),3000);this.drawMessage='PNG 已生成。';this.drawingStatus();}catch(e){this.drawMessage=`导出失败：${e.message}`;this.drawingStatus();}}
  zoom(factor){const r=this.chart.timeScale().getVisibleLogicalRange();if(!r)return;const center=(r.from+r.to)/2,width=Math.max(8,(r.to-r.from)*factor);this.chart.timeScale().setVisibleLogicalRange({from:center-width/2,to:center+width/2});}
  click(e){const b=e.target.closest('button');if(!b||!this.chart)return;if(b.dataset.resolution)this.setResolution(b.dataset.resolution);if(b.dataset.chartType)this.setType(b.dataset.chartType);if(b.dataset.drawTool)this.setTool(b.dataset.drawTool);const action=b.dataset.chartAction;if(action&&action!=='latest-trade')this.hideTradeTooltip();if(action==='latest-trade')this.latestTrade();if(action==='trade-tooltip-close')this.hideTradeTooltip();if(action==='tv-retry')this.reference({reload:true});if(['undo','redo','delete','clear'].includes(action))this.edit(action);if(action==='save')this.saveDrawings();if(action==='reload-drawings')this.loadDrawings({discard:true});if(action==='export')this.export();if(action==='earlier')this.load({earlier:true});if(action==='refresh'){this.pendingQuery=null;this.load();}if(action==='zoom-out')this.zoom(1.5);if(action==='zoom-in')this.zoom(1/1.5);if(action==='fit'){this.chart.priceScale('right').applyOptions({autoScale:true});this.chart.timeScale().fitContent();}if(action==='latest')this.chart.timeScale().scrollToRealTime();}
  change(e){if(e.target.id==='trade-quantity-visible'){this.tradeQuantityVisible=e.target.checked;this.needsPaint=true;}if(e.target.id==='trade-price-visible'){this.tradePriceVisible=e.target.checked;this.needsPaint=true;}if(e.target.id==='trade-markers-visible'){this.tradeVisible=e.target.checked;this.hideTradeTooltip();this.needsPaint=true;this.refreshTradeScale();}if(e.target.id==='chart-product'){this.chooseProduct(e.target.value);history.replaceState(null,'',`#charts?epic=${encodeURIComponent(e.target.value)}`);}if(['draw-color','draw-width'].includes(e.target.id)&&this.selected&&this.drawState?.loaded){const d=this.drawState.drawings.find(d=>d.id===this.selected);if(d){this.checkpoint();d.color=this.$('#draw-color').value;d.width=Number(this.$('#draw-width').value);this.changed();}}}
  key(e){if(e.target.matches('input,select,textarea'))return;if(e.key==='Escape'){this.cancelGesture();this.setTool('pan');}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();this.edit(e.shiftKey?'redo':'undo');}if((e.key==='Delete'||e.key==='Backspace')&&this.selected){e.preventDefault();this.edit('delete');}}
  destroy(){this.destroyed=true;clearTimeout(this.tradeUpdateTimer);this.tradeAbort?.abort();this.tradeWrap?.removeEventListener('pointermove',this.onTradeMove);this.tradeWrap?.removeEventListener('pointerdown',this.onTradeDown);this.tradeWrap?.removeEventListener('pointerup',this.onTradeUp);this.tradeWrap?.removeEventListener('pointerleave',this.onTradeLeave);this.tradeWrap?.removeEventListener('wheel',this.onTradeWheel);this.closeReference();this.$('#tv-reference-pane')?.removeEventListener('toggle',this.onReferenceToggle);window.removeEventListener('message',this.onReferenceMessage);clearTimeout(this.pollTimer);clearInterval(this.interval);cancelAnimationFrame(this.animation);this.abort?.abort();this.observer?.disconnect();this.container.removeEventListener('click',this.onClick);this.container.removeEventListener('change',this.onChange);this.container.removeEventListener('keydown',this.onKey);this.canvas?.removeEventListener('pointerdown',this.onPointerDown);this.canvas?.removeEventListener('pointermove',this.onPointerMove);this.canvas?.removeEventListener('pointerup',this.onPointerUp);this.canvas?.removeEventListener('pointercancel',this.onPointerCancel);this.chart?.remove();}
}

if(typeof window!=='undefined')window.addEventListener('beforeunload',e=>{if([...drafts.values()].some(s=>s.dirty)){e.preventDefault();e.returnValue='';}});
