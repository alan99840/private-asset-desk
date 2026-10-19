import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AdvancedRealTimeChart} from 'react-ts-tradingview-widgets';

// This document runs in an opaque-origin sandbox. Only public chart settings enter it.
// The upstream wrapper omits monthly "M" from its type union; runtime forwards the
// string unchanged to TradingView.widget. This adapter adds that one explicit value.
type UpstreamProps=React.ComponentProps<typeof AdvancedRealTimeChart>;
type ReferenceInterval='1'|'5'|'15'|'30'|'60'|'D'|'W'|'M';
type ReferenceProps=Omit<UpstreamProps,'interval'> & {interval:ReferenceInterval};
const ReferenceChart=AdvancedRealTimeChart as React.ComponentType<ReferenceProps>;
const intervals=new Set<ReferenceInterval>(['1','5','15','30','60','D','W','M']);
const query=new URLSearchParams(location.search);
const rawSymbol=query.get('symbol')||'OANDA:WTICOUSD';
const symbol=/^[A-Z0-9_]{1,24}:[A-Z0-9_.!-]{1,50}$/.test(rawSymbol)?rawSymbol:'OANDA:WTICOUSD';
const rawInterval=query.get('interval') as ReferenceInterval|null;
const interval=rawInterval&&intervals.has(rawInterval)?rawInterval:'5';
const style=query.get('style')==='2'?'2':'1';
const nonce=query.get('nonce')||'';
// Keep styles in the public, credential-free bundle: the opaque sandbox cannot
// request authenticated account-page CSS using its parent's session cookie.
const fixedStyle=document.createElement('style');
fixedStyle.textContent=`*{box-sizing:border-box}body{margin:0;background:#fff;color:#1e343b;font:13px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}a{color:#1b6355;text-decoration:none}button,a{font:inherit}button{border:1px solid #cbd9cd;border-radius:6px;background:#f2f6f0;padding:7px 12px;color:#1b6355;cursor:pointer}button:focus-visible,a:focus-visible{outline:3px solid #86b5a6;outline-offset:3px}.reference-app{margin:0;padding:0;min-width:0}.reference-header{padding:14px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid #e2e7e2;background:#fafbf8}.reference-header strong{display:block;font-size:13px}.reference-header span{display:block;color:#708080;font-size:10px;margin-top:2px;overflow-wrap:anywhere}.reference-header a{font-size:11px;white-space:nowrap}.reference-status{padding:9px 16px;margin:0;font-size:10px;color:#946526;background:#faf7ee;min-height:35px}.reference-chart{height:500px;position:relative}.reference-chart [id^="harbour_tv_"]{height:100%;width:100%}.reference-footer{padding:15px 16px;background:#fafbf8;border-top:1px solid #e2e7e2;font-size:10px;color:#708080}.reference-footer p{margin:0 0 6px}.reference-footer button,.reference-footer>a{display:inline-block;margin-top:5px;margin-right:12px;font-size:10px}@media(max-width:500px){.reference-chart{height:420px}.reference-header{padding:12px}.reference-header strong{font-size:12px}.reference-status,.reference-footer{padding-left:12px;padding-right:12px}}`;
document.head.appendChild(fixedStyle);

function Reference(){
  const [attempt,setAttempt]=useState(0);
  const [status,setStatus]=useState('正在加载 TradingView 参考图；图内出现行情前不能视为数据已就绪。');
  useEffect(()=>{
    let sawFrame=false;
    const update=(message:string)=>{setStatus(message);parent.postMessage({type:'harbour-tv-reference-status',nonce,symbol,interval,style,status:message},'*');};
    update('正在加载 TradingView 参考图；图内出现行情前不能视为数据已就绪。');
    const observer=new MutationObserver(()=>{
      const frame=document.querySelector<HTMLIFrameElement>('#reference-chart iframe');
      if(frame&&frame.src.startsWith('https:')&&!sawFrame){
        sawFrame=true;const url=frame.src;
        // Keep tv.js in this opaque sandbox. The parent validates this candidate
        // and alone may display a direct, cross-origin TradingView frame.
        frame.remove();parent.postMessage({type:'harbour-tv-reference-candidate',nonce,symbol,interval,style,url},'*');
        update('参考图配置已生成；行情状态请以主页面中的 TradingView 图表显示为准。');
      }
      for(const script of document.querySelectorAll<HTMLScriptElement>('#reference-chart script[src]')){
        if(script.dataset.harbourObserved)continue;script.dataset.harbourObserved='true';
        script.addEventListener('error',()=>update('TradingView 脚本暂时无法加载。可重试，或使用主页面的外部打开入口。'),{once:true});
      }
    });
    observer.observe(document.getElementById('reference-chart')!,{childList:true,subtree:true,attributes:true,attributeFilter:['src']});
    const timeout=setTimeout(()=>{if(!sawFrame)update('TradingView 尚未建立图表框架。可重试，或使用主页面的外部打开入口。');},20000);
    return ()=>{observer.disconnect();clearTimeout(timeout);};
  },[attempt]);
  return <main className="reference-app">
    <header className="reference-header"><div><strong>TradingView 参考行情</strong><span>{symbol} · {interval==='M'?'月':interval==='W'?'周':interval==='D'?'日':interval+' 分钟'} · 香港时间</span></div></header>
    <p className="reference-status" role="status">{status}</p>
    <div id="reference-chart" className="reference-chart">
      <ReferenceChart key={`${symbol}-${interval}-${attempt}`} container_id={`harbour_tv_${attempt}`} symbol={symbol} interval={interval} style={style} timezone="Asia/Hong_Kong" theme="light" locale="zh_CN" autosize={true} height="100%" width="100%" hide_side_toolbar={false} allow_symbol_change={false} enable_publishing={false} save_image={false} details={false} hotlist={false} calendar={false} toolbar_bg="#fafbf8" backgroundColor="#ffffff" />
    </div>
    <footer className="reference-footer"><p>TradingView 参考行情，与 IG 合约价格可能不同；TV 内标注不与 IG 共享标注同步。</p><button type="button" onClick={()=>setAttempt(n=>n+1)}>重新加载参考图</button><p>仍无法加载时，可使用主页面参考面板中的“外部打开”入口。</p></footer>
  </main>;
}

const root=document.getElementById('reference-root');if(root)createRoot(root).render(<Reference/>);
