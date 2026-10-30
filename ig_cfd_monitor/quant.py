"""Deterministic position risk, completed-bar indicators and conditional stress.

No order endpoints, probability forecasts, fitted trading models or synthetic
prices. Every HKD estimate carries its source/coverage/age constraints.
"""
import math
import re
import statistics
import time
from datetime import datetime, timezone
from cashflows import currency
from chart_history import RESOLUTIONS, ChartError
from market_sessions import describe, gap_details, open_seconds


def num(value):
    if isinstance(value, bool):return None
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError,OverflowError):return None


def ema(values, period):
    if len(values)<period:return None
    result=sum(values[:period])/period
    alpha=2/(period+1)
    for value in values[period:]:result=alpha*value+(1-alpha)*result
    return result


def wilder(values, period=14):
    if len(values)<period:return None
    result=sum(values[:period])/period
    for value in values[period:]:result=(result*(period-1)+value)/period
    return result


def indicators(candles, resolution, epoch=None, epic=None):
    epoch=time.time() if epoch is None else epoch
    # A subsequent bar proves the previous bar closed. The unconfirmed tail is
    # always excluded, including daily/weekly/monthly bars and closed markets.
    known=[c for c in candles if c.get('time',epoch+1)<=epoch]
    bars=known[:-1]
    out={'status':'INSUFFICIENT','resolution':resolution,'observations':len(bars),
         'excluded_tail':1 if known else 0,'as_of':bars[-1]['time'] if bars else None,
         'basis':'completed_observed_bid_bars','unexplained_gaps':0,'session_gaps':0,
         'ema20':None,'ema50':None,'atr14':None,'rsi14':None,
         'high20':None,'low20':None,'high60':None,'low60':None,'regime':'WAIT',
         'conditions':[], 'reasons':[], 'session':describe(epic, epoch), 'gaps':[]}
    if len(bars)<61:
        out['reasons'].append({'code':'insufficient_bars','message':f'需要至少 61 根已确认收盘 K 线，当前 {len(bars)} 根；末根未确认，已排除。'})
        return out
    closes=[b['close'] for b in bars]
    tr=[max(b['high']-b['low'],abs(b['high']-bars[i-1]['close']),abs(b['low']-bars[i-1]['close'])) for i,b in enumerate(bars) if i]
    changes=[b-a for a,b in zip(closes,closes[1:])]
    gain=wilder([max(0,c) for c in changes]);loss=wilder([max(0,-c) for c in changes])
    out.update(ema20=ema(closes,20),ema50=ema(closes,50),atr14=wilder(tr),
               rsi14=50 if not gain and not loss else 100 if loss==0 else 100-100/(1+gain/loss),
               close=closes[-1],high20=max(b['high'] for b in bars[-21:-1]),
               low20=min(b['low'] for b in bars[-21:-1]),high60=max(b['high'] for b in bars[-61:-1]),
               low60=min(b['low'] for b in bars[-61:-1]))
    step=RESOLUTIONS[resolution]
    out['gaps']=gap_details(epic,bars,resolution,step)
    for gap in out['gaps']:
        out['session_gaps' if gap['kind']=='session' else 'unexplained_gaps']+=1
    if out['unexplained_gaps']:
        out['reasons'].append({'code':'unexplained_gaps','message':f"最近 61 根内有 {out['unexplained_gaps']} 段缺口未被常规休市规则解释；可能缺行情或有特殊休市，暂停条件结论。"})
    out['bar_age_seconds']=epoch-bars[-1]['time']
    trading_age=open_seconds(epic,bars[-1]['time'],epoch) if step<86400 else None
    out['open_age_seconds']=trading_age
    age=out['bar_age_seconds'] if trading_age is None else trading_age
    out['stale_bars']=age>({'DAY':5*86400,'WEEK':16*86400,'MONTH':70*86400}.get(resolution,step*3))
    if out['stale_bars']:
        out['reasons'].append({'code':'stale_bars','message':'已收盘 K 线过旧；扣除已匹配常规休市后仍超出更新容限，等待历史行情刷新。' if trading_age is not None else '已收盘 K 线过旧，且没有适用的日内休市校正；等待历史行情刷新。'})
    # A bar fully inside a reference closure invalidates that calendar for this
    # sample. A changed broker schedule must not hide genuine missing data.
    conflicts=[b['time'] for b in bars[-61:] if step<86400 and open_seconds(epic,b['time'],b['time']+step)==0]
    if conflicts:
        out['reasons'].append({'code':'session_conflict','message':'实际 K 线出现在参考休市时段，时段规则需要重新核验，暂停条件结论。'})
    out['session_conflicts']=conflicts
    out['status']='LIMITED' if out['reasons'] else 'READY'
    close=out['close'];fast=out['ema20'];slow=out['ema50']
    out['regime']='UP' if close>fast>slow else 'DOWN' if close<fast<slow else 'RANGE'
    out['conditions']=[
        {'case':'上行延续','trigger':out['high20'],'rule':'后续已收盘 K 线突破前 20 根高点且 EMA20 > EMA50',
         'invalidation':fast,'invalid_rule':'已收盘价格回到 EMA20 下方'},
        {'case':'区间震荡','lower':out['low20'],'upper':out['high20'],
         'rule':'价格维持前 20 根区间内，均线未同向排列','invalid_rule':'已收盘价格突破区间'},
        {'case':'下行延续','trigger':out['low20'],'rule':'后续已收盘 K 线跌破前 20 根低点且 EMA20 < EMA50',
         'invalidation':fast,'invalid_rule':'已收盘价格回到 EMA20 上方'}]
    if out['status']!='READY':out.update(regime='WAIT',conditions=[])
    return out


def rsi_band(value):
    """Disjoint user-selected ranges; 50 is assigned to upper, but is neutral."""
    value=num(value)
    if value is None or not 0<=value<=100:return None
    return 'overbought' if value>70 else 'strong' if value>=50 else 'weak' if value>=30 else 'oversold'


def chart_reading(quality, response):
    """Explain this chart's completed bars, not a probability or dealing signal.

    EMA supplies structure, RSI confirms direction, ATR only describes movement.
    Keep blocked outputs empty so stale levels cannot look like current guidance.
    """
    reasons=[r['message'] for r in quality.get('reasons',[]) if r.get('message')]
    if response.get('status')!='ready':
        reasons.insert(0,response.get('message') or {
            'loading':'历史行情正在更新，等待本次读取完成。',
            'limited':'IG 历史行情额度受限，等待重新核验。',
            'error':'历史行情读取失败，等待连接恢复。'
        }.get(response.get('status'),'历史行情状态尚未核验。'))
    if response.get('skipped_rows'):reasons.append('本次历史包含无效 K 线，等待行情完整性核验。')
    keys=('close','ema20','ema50','atr14','rsi14','high20','low20','high60','low60')
    values={k:num(quality.get(k)) for k in keys}
    out={'status':'WAIT','regime':'WAIT','resolution':quality.get('resolution'),
         'as_of':quality.get('as_of'),'observations':quality.get('observations',0),
         'reasons':list(dict.fromkeys(reasons)), 'values':{},'levels':[],
         'rsi_band':None,'summary':'暂停趋势判断','watch':[],'risks':[]}
    if quality.get('status')!='READY' or reasons:return out
    if any(v is None for v in values.values()) or rsi_band(values['rsi14']) is None or values['atr14']<0:
        out['reasons']=['指标数值不完整或无效，等待重新核验。'];return out
    c,f,s,a,r=(values[k] for k in ('close','ema20','ema50','atr14','rsi14'))
    structure='UP' if c>f>s else 'DOWN' if c<f<s else 'RANGE'
    regime='UP' if structure=='UP' and r>50 else 'DOWN' if structure=='DOWN' and r<50 else 'RANGE'
    summary={
        'UP':'收盘价高于 EMA20，EMA20 高于 EMA50，RSI 高于 50；价格结构与动量同向，当前偏多。',
        'DOWN':'收盘价低于 EMA20，EMA20 低于 EMA50，RSI 低于 50；价格结构与动量同向，当前偏空。',
        'RANGE':'价格、EMA20/50 与 RSI 未形成同向确认，暂按震荡 / 分歧观察；不代表已确认存在稳定区间。'
    }[regime]
    levels=[{'key':k,'label':label,'price':values[k]} for k,label in (
        ('ema20','EMA20 · 短期分界'),('ema50','EMA50 · 趋势分界'),
        ('low20','前 20 根低点'),('high20','前 20 根高点'),
        ('low60','前 60 根低点'),('high60','前 60 根高点'))]
    if a>0:levels.extend([{'key':'atr_lower','label':'收盘价 − 1 ATR','price':c-a},
                          {'key':'atr_upper','label':'收盘价 + 1 ATR','price':c+a}])
    watch=[
        '若后续收盘价突破当时的前 20 根高点，且 EMA20 > EMA50、RSI > 50，可继续观察上行延续。',
        '若后续收盘价跌破当时的前 20 根低点，且 EMA20 < EMA50、RSI < 50，可继续观察下行延续。',
        '偏多时收盘价回落至 EMA20 下方，或偏空时回升至 EMA20 上方，原有结构开始减弱；RSI 回到 50 附近需重新确认。'
    ]
    risks=[]
    if r>70:risks.append('RSI > 70：动量偏强且进入超买区，关注回撤；强趋势可持续超买，不能单凭此项判断见顶。')
    elif r<30:risks.append('RSI < 30：动量偏弱且进入超卖区，关注反弹；弱趋势可持续超卖，不能单凭此项判断见底。')
    elif r==50:risks.append('RSI = 50：多空动量中性；四档展示归入 50–70，但不作为偏多确认。')
    if a==0:risks.append('ATR 为 0：样本没有有效波动，不能计算 ATR 距离或波动区间。')
    extension=abs(c-f)/a if a>0 else None
    if extension is not None and extension>=2:
        risks.append('收盘价偏离 EMA20 已达到 2 ATR，注意延伸后的回摆风险；2 ATR 是本平台观察阈值，未作胜率回测。')
    risks.append('±1 ATR 仅是以收盘价为中心的历史波动尺，不是未来价格预测区间，也不是建议止损价。')
    out.update(status='READY',regime=regime,ema_structure=structure,values=values,
               levels=levels,rsi_band=rsi_band(r),summary=summary,watch=watch,risks=risks,
               extension_atr=extension,atr_percent=a/abs(c)*100 if c else None)
    return out


def point_spec(position, details, epoch=None):
    epoch=time.time() if epoch is None else epoch
    instrument=details.get('instrument',{});snap=details.get('snapshot',{})
    code=currency(position.get('currency'));unit=instrument.get('unit')
    pip=num(instrument.get('valueOfOnePip'));scale=num(snap.get('scalingFactor'))
    prefix=re.match(r'^\s*(\d+(?:\.\d+)?)\b',str(instrument.get('onePipMeans') or ''))
    step=num(prefix.group(1)) if prefix else None
    result={'status':'UNVERIFIED','currency':code,'as_of':details.get('as_of'),
            'one_pip_means':instrument.get('onePipMeans'),'pip_value':pip,
            'contract_size':instrument.get('contractSize'),'margin_factor':instrument.get('marginFactor'),
            'margin_unit':instrument.get('marginFactorUnit'),'margin_bands':instrument.get('marginDepositBands',[]),
            'expiry':instrument.get('expiry'),'rollover':instrument.get('rolloverDetails'),
            'point_value_native':None,'fx_to_hkd':None,'hkd_per_point_per_contract':None,
            'source':'IG /markets v3; valueOfOnePip / (onePipMeans numeric step × scalingFactor)'}
    if (unit!='CONTRACTS' or instrument.get('type') not in ('INDICES','COMMODITIES','CURRENCIES')
        or not code or pip is None or pip<=0 or not scale or scale<=0 or not step or step<=0):return result
    currencies=instrument.get('currencies',[])
    # valueOfOnePip has no separate currency field; require a single matching
    # contract currency instead of silently converting a multi-currency quote.
    if len(currencies)!=1 or currency(currencies[0].get('code'))!=code:return result
    base=num(currencies[0].get('baseExchangeRate'))
    fx=1.0 if code=='HKD' else 1/base if base and base>0 else None
    if fx is None:return result
    point=pip/(step*scale)
    result.update(status='ESTIMATE',point_value_native=point,fx_to_hkd=fx,
                  hkd_per_point_per_contract=point*fx,fx_basis='1 / IG baseExchangeRate (account HKD)',
                  stale=not 0<=epoch-(num(details.get('fetched_epoch')) or 0)<=7200)
    if result['stale']:result['status']='STALE_SPEC'
    return result


def correlation(a,b):
    def returns(bars):
        result={}
        for prev,cur in zip(bars[:-2],bars[1:-1]):
            if prev['close']>0 and cur['close']>0:
                result[(prev['time'],cur['time'])]=cur['close']/prev['close']-1
        return result
    ar,br=returns(a),returns(b);times=sorted(set(ar)&set(br))[-60:]
    if len(times)<30:return {'status':'INSUFFICIENT','samples':len(times),'value':None}
    x=[ar[t] for t in times];y=[br[t] for t in times]
    if not statistics.pstdev(x) or not statistics.pstdev(y):return {'status':'CONSTANT','samples':len(times),'value':None}
    return {'status':'READY','samples':len(times),'value':statistics.correlation(x,y),
            'basis':'matching UTC start/end intervals; last 60 completed positive-price returns'}


def portfolio(state, signals, epoch=None):
    epoch=time.time() if epoch is None else epoch
    quotes={q['epic']:q for q in state.get('markets',[])};details=state.get('market_specs',{})
    account=state.get('account') or {};eq=num(account.get('equity'))
    latest=state.get('positions_at')
    try:position_age=epoch-datetime.fromisoformat(latest).timestamp()
    except (ValueError,TypeError):position_age=None
    sync=state.get('sync') or {};position_sync=sync.get('positions')
    position_sync_ok='positions' not in sync or (isinstance(position_sync,dict) and position_sync.get('status')=='ok')
    positions_stale=bool(state.get('positions_stale') or state.get('positions') is None or
        position_age is None or not 0<=position_age<=90 or
        not position_sync_ok)
    positions=[];groups={};unverified=0
    for p in state.get('positions') or []:
        epic=p['epic'];q=quotes.get(epic,{});sign=1 if p.get('direction')=='BUY' else -1
        size=num(p.get('size'));level=num(p.get('level'));bid=num(q.get('bid'));ask=num(q.get('ask'))
        close=bid if sign>0 else ask;spec=point_spec(p,details.get(epic,{}),epoch)
        per=spec.get('hkd_per_point_per_contract');atr=signals.get(epic,{}).get('atr14');stop=num(p.get('stopLevel'))
        ready=bool(p.get('direction') in ('BUY','SELL') and per is not None and size is not None and size>0 and level is not None and close is not None and bid is not None and ask is not None and ask>=bid)
        stale=positions_stale or q.get('stale',True) or q.get('delayed',False) or spec.get('stale',False)
        row={**p,'spec':spec,'reference_price':close,'quote_at':q.get('timestamp'),
             'status':'UNVERIFIED' if not ready else 'STALE' if stale else 'ESTIMATE',
             'hkd_per_point':sign*size*per if ready else None,
             'estimated_pnl_hkd':sign*size*per*(close-level) if ready else None,
             'gross_notional_hkd':abs(close)*size*per if ready else None,
             'stop_distance_points':sign*(close-stop) if ready and stop is not None else None,
             'stop_distance_atr':sign*(close-stop)/atr if ready and stop is not None and atr and atr>0 else None,
             'loss_to_stop_hkd':max(0,sign*(close-stop)*size*per) if ready and stop is not None else None,
             'entry_to_stop_pnl_hkd':sign*(stop-level)*size*per if ready and stop is not None else None,
             'spread_points':ask-bid if ask is not None and bid is not None and ask>=bid else None}
        positions.append(row)
        if not ready:unverified+=1
        group=groups.setdefault(epic,{'epic':epic,'name':p.get('instrumentName'), 'count':0,
             'long_size':0,'short_size':0,'net_hkd_per_point':0,'gross_hkd_per_point':0,
             'gross_notional_hkd':0,'net_notional_hkd':0,'estimated_pnl_hkd':0,'unverified':0,'stale':positions_stale})
        group['count']+=1;group['long_size' if sign>0 else 'short_size']+=size or 0
        if ready:
            group['net_hkd_per_point']+=row['hkd_per_point'];group['gross_hkd_per_point']+=size*per
            group['gross_notional_hkd']+=row['gross_notional_hkd'];group['net_notional_hkd']+=sign*row['gross_notional_hkd']
            group['estimated_pnl_hkd']+=row['estimated_pnl_hkd'];group['stale']|=stale
        else:group['unverified']+=1
    gross=sum(g['gross_notional_hkd'] for g in groups.values());estimated=sum(g['estimated_pnl_hkd'] for g in groups.values())
    for g in groups.values():g['concentration']=g['gross_notional_hkd']/gross if gross and not unverified else None
    broker=num(account.get('unrealized'));difference=estimated-broker if broker is not None and not unverified else None
    tolerance=max(50,abs(broker or 0)*.05)
    reconcile='INCOMPLETE' if state.get('positions') is None or unverified or broker is None else 'STALE' if positions_stale or state.get('stale') or any(g['stale'] for g in groups.values()) else 'WITHIN_TOLERANCE' if abs(difference)<=tolerance else 'CHECK_REQUIRED'
    missing_stop=sum(1 for p in positions if p.get('stopLevel') is None)
    known_stop=sum(p['loss_to_stop_hkd'] or 0 for p in positions)
    complete=not positions_stale and not unverified and not state.get('stale') and not any(g['stale'] for g in groups.values())
    return {'positions':positions,'groups':list(groups.values()),'unverified_positions':unverified,
        'status':'READY' if complete else 'DATA_LIMITED','positions_age_seconds':position_age,
        'gross_notional_hkd':gross if not unverified else None,
        'effective_gross_leverage':gross/eq if eq and eq>0 and not unverified else None,
        'estimated_pnl_hkd':estimated if not unverified else None,'broker_pnl_hkd':broker,
        'reconciliation':{'status':reconcile,'difference_hkd':difference,'tolerance_hkd':tolerance,
                          'note':'HKD估算与IG汇总核对；汇率、换汇费及采样时间可形成差异。'},
        'missing_stop_count':missing_stop,'known_loss_to_stop_hkd':known_stop,
        'stop_risk_complete':complete and not missing_stop,
        'margin_usage':account.get('margin')/eq if eq and eq>0 and num(account.get('margin')) is not None else None}


def stress(state, book, signals, mode='atr', shock=1, fx_percent=0, spread_multiple=1, cost_hkd=0):
    # Point-value model is linear and excludes stop execution (gap-through-stop
    # losses can be larger). Margin is held fixed for the displayed what-if only.
    pmap={p['epic']:p for p in book['groups']};rows=[];total=0;missing=[]
    for p in book['positions']:
        epic=p['epic'];per=p.get('hkd_per_point');reference=p.get('reference_price')
        atr=signals.get(epic,{}).get('atr14');distance=None
        if mode=='percent' and reference is not None:distance=abs(reference)*shock/100
        elif mode=='points':distance=shock
        elif mode in ('atr','adverse') and atr and atr>0 and signals.get(epic,{}).get('status')=='READY':
            direction=-1 if pmap[epic]['net_hkd_per_point']>=0 else 1
            distance=atr*(abs(shock)*direction if mode=='adverse' else shock)
        if per is None or distance is None or p.get('status')!='ESTIMATE' or (spread_multiple>1 and p.get('spread_points') is None):missing.append(p['dealId']);continue
        extra=(p.get('spread_points') or 0)*max(0,spread_multiple-1)/2
        delta=per*distance-abs(per)*extra
        foreign=p.get('currency')!='HKD'
        fx=fx_percent/100 if foreign else 0
        delta=delta*(1+fx)+(p.get('estimated_pnl_hkd') or 0)*fx
        total+=delta;rows.append({'epic':epic,'dealId':p['dealId'],'price_move':distance,'delta_hkd':delta})
    valid=not missing and book['status']=='READY' and book['reconciliation']['status']=='WITHIN_TOLERANCE'
    eq=(state.get('account') or {}).get('equity');available=(state.get('account') or {}).get('available')
    delta=total-cost_hkd
    threshold=(state.get('risk') or {}).get('threshold_equity')
    return {'status':'READY' if valid else 'DATA_LIMITED','mode':mode,'shock':shock,
            'fx_percent':fx_percent,'spread_multiple':spread_multiple,'cost_hkd':cost_hkd,
            'delta_hkd':delta if valid else None,'equity_hkd':eq+delta if valid and eq is not None else None,
            'available_if_margin_unchanged':available+delta if valid and available is not None else None,
            'distance_to_loss_alert':eq+delta-threshold if valid and eq is not None and threshold is not None else None,
            'excluded_positions':len(missing),'rows':rows,
            'assumptions':['点值与汇率维持当前估计，所有产品同时变化。','保证金保持当前金额；券商可能调整保证金。',
                           '不模拟止损成交；跳空和滑点可能造成更大损失。','额外成本为用户输入假设，0表示未纳入未知费用。',
                           '汇率冲击只作用于外币持仓损益，不推断账户外币现金。']}


class Analytics:
    def __init__(self,monitor,charts):
        self.monitor=monitor;self.charts=charts

    def get(self,resolution='HOUR',mode='atr',shock=1,fx_percent=0,spread_multiple=1,cost_hkd=0,epic=None):
        state=self.monitor.project();signals={};raw={};charts_status={}
        held=list(dict.fromkeys(p['epic'] for p in state.get('positions') or []))
        if epic and epic not in held:
            if epic not in [m['epic'] for m in state['markets']]:raise ChartError('unknown_product')
            held.append(epic)
        for e in held:
            try:
                history=self.charts.get(e,resolution);candles=history['candles']
                raw[e]=candles;signals[e]=indicators(candles,resolution,epic=e)
                signals[e]['cache_status']=history['status'];signals[e]['updated_at']=history.get('updated_at')
                signals[e]['cache_error']=history.get('error');charts_status[e]=history['status']
                if history.get('skipped_rows'):
                    signals[e]['reasons'].append({'code':'invalid_ohlc','message':f"IG 最近返回 {history['skipped_rows']} 条无有效 UTC 或 Bid OHLC 的记录，已跳过；等待完整数据核对。"})
                    if signals[e]['status']=='READY':signals[e].update(status='LIMITED',regime='WAIT',conditions=[])
                if history['status']!='ready' or history.get('error'):
                    reason=history.get('error') or history['status']
                    message=('IG 历史行情配额受限，保留缓存并等待额度恢复。' if 'allowance' in str(reason) or 'quota' in str(reason) else
                             '历史行情正在读取或刷新，等待本次请求完成。' if history['status'] in ('loading','refreshing','queued') else
                             '历史行情尚未就绪或读取失败，保留缓存，暂停条件结论。')
                    signals[e]['reasons'].append({'code':'history_not_ready','message':history.get('message') or message,'detail':str(reason)})
                    if signals[e]['status']=='READY':signals[e].update(status='LIMITED',regime='WAIT',conditions=[])
            except ChartError as exc:
                signals[e]={'status':'UNAVAILABLE','error':exc.code,'resolution':resolution,'conditions':[],
                            'reasons':[{'code':'history_unavailable','message':'无法读取此合约历史行情。','detail':exc.code}],
                            'session':describe(e,time.time())}
        book=portfolio(state,signals)
        correlations=[]
        for i,a in enumerate(held):
            for b in held[i+1:]:
                if a in raw and b in raw:
                    value=correlation(raw[a],raw[b])
                    if signals[a]['status']!='READY' or signals[b]['status']!='READY':value.update(status='LIMITED',value=None)
                    correlations.append({'a':a,'b':b,**value})
        daily=state.get('series',[]);eq=(state.get('account') or {}).get('equity')
        completed=[d for d in daily if not d.get('provisional') and num(d.get('equity')) is not None]
        last_close=completed[-1]['equity'] if completed else None
        observed=[d['equity'] for d in daily if num(d.get('equity')) is not None]
        peak=max(observed+[eq]) if eq is not None else max(observed) if observed else None
        return {'generated_at':state['generated_at'],'resolution':resolution,'portfolio':book,'signals':signals,
            'correlations':correlations,'scenario':stress(state,book,signals,mode,shock,fx_percent,spread_multiple,cost_hkd),
            'presets':[stress(state,book,signals,'atr',v) for v in (-2,-1,1,2)],
            'account_risk':{'daily_equity_change_hkd':eq-last_close if eq is not None and last_close is not None else None,
                'last_close_at':completed[-1]['at'] if completed else None,
                'observed_peak_hkd':peak,'drawdown_fraction':max(0,(peak-eq)/peak) if peak and peak>0 and eq is not None else None,
                'basis':'观察期权益变化，包含出入金；不等于净交易收益率。'},
            'costs':state.get('costs',{}),'scope':'描述指标与条件压力测试；未实施概率预测或回测模型'}
