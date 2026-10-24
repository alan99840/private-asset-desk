// Quote state is independent of the selected historical candle / chart period.
// IG PRICE DLG_FLAG values: https://labs.ig.com/streaming-api-reference.html
const finite=v=>typeof v==='number'&&Number.isFinite(v);
function epoch(value){
  if(value==null||value==='')return null;
  if(typeof value==='number'||(typeof value==='string'&&/^[+-]?\d+(\.\d+)?$/.test(value.trim()))){const n=Number(value);return Number.isFinite(n)?(Math.abs(n)>=1e11?n/1000:n):null;}
  if(typeof value!=='string')return null;
  const text=/^\d{4}-\d{2}-\d{2}T/.test(value)&&!/(Z|[+-]\d{2}:\d{2})$/.test(value)?value+'Z':value;
  const n=Date.parse(text);return Number.isFinite(n)?n/1000:null;
}
const fresh=(time,now,seconds)=>time!=null&&time<=now&&now-time<=seconds;
export function quoteState(state,epic,now=Date.now()/1000){
  const quote=(state?.markets||[]).find(m=>m.epic===epic);
  const bid=finite(quote?.bid)?quote.bid:null,ask=finite(quote?.ask)?quote.ask:null;
  const timestamp=epoch(quote?.timestamp),age=timestamp==null?null:now-timestamp;
  const market=String(quote?.market_status||'').trim().toUpperCase();
  const out={bid,ask,timestamp,age,market,spread:bid!=null&&ask!=null&&ask>=bid?ask-bid:null,
    live:false,kind:'waiting',label:'等待报价',detail:'当前产品尚未收到 IG 报价。',lineTitle:'IG Bid 待核验',color:'#8a622b'};
  const update=(kind,label,detail,lineTitle=label+' Bid')=>Object.assign(out,{kind,label,detail,lineTitle});
  if(!quote||bid==null)return out;
  if(state?.chart_transport_stale||!fresh(epoch(state?.generated_at),now,15))return update('stale','连接中断 · 保留报价','账户快照已过期；以下价格不能视为当前报价。','旧报价 Bid');
  if(!String(state?.stream||'').startsWith('CONNECTED'))return update('stale','行情重连 · 保留报价','IG 行情连接尚未恢复；以下价格不能视为当前报价。','旧报价 Bid');
  if(market==='CLOSED')return update('closed','休市 · 保留报价','IG 标记此产品休市；显示最后一次收到的报价。','休市 Bid');
  if(['SUSPEND','SUSPENDED','OFFLINE'].includes(market))return update('closed','暂停报价 / 交易','IG 标记此产品暂停或离线；显示最后一次收到的报价。','暂停 Bid');
  if(ask==null||ask<bid)return update('stale','买卖价待核验','Ask 缺失或低于 Bid，暂停将此价格标记为实时。','Bid 待核验');
  if(timestamp==null)return update('stale','来源时间待核验','IG 未提供有效报价时间；不将快照标记为实时。','Bid 待核验');
  if(age<0)return update('stale','报价时间异常','报价时间在未来，等待来源时间重新核验。','Bid 待核验');
  if(age>30||quote.stale)return update('stale','报价已过期','超过 30 秒未收到有效新报价，或后台标记报价待核验。','旧报价 Bid');
  if(quote.delayed)return update('delayed','延迟报价','IG 标记此报价存在延迟；图中水平线为延迟 Bid。','延迟 Bid');
  if(market&&!['DEAL','DEALNOEDIT','TRADEABLE'].includes(market))return update('restricted','交易状态受限',`IG 当前状态：${market}；报价仅供监控参考。`,'受限 Bid');
  return Object.assign(out,{live:true,kind:'live',label:'实时推送',detail:'蓝色水平线与价格轴标签显示最新 IG Bid；Ask 为买入报价。',lineTitle:'最新 Bid',color:'#195f9a'});
}
export function quoteLineOptions(quote){
  const visible=quote?.bid!=null;
  return {price:visible?quote.bid:0,color:quote?.color||'#8a622b',lineWidth:2,lineStyle:quote?.live?0:2,
    axisLabelVisible:visible,lineVisible:visible,title:quote?.lineTitle||'IG Bid',
    axisLabelColor:quote?.color||'#8a622b',axisLabelTextColor:'#ffffff'};
}
