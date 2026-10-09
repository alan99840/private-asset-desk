"""V2.3 quote-only decisions. No account, order, amendment or execution interface."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
RULES = json.loads((HERE / 'rules.json').read_text())
ET, HK = ZoneInfo('America/New_York'), ZoneInfo('Asia/Hong_Kong')
CENT = Decimal('0.01')
D = lambda x: Decimal(str(x))


def dt(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        result = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    else:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return result


def up(value, tick=CENT):
    return (D(value) / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def down(value, tick=CENT):
    return (D(value) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def clock(now=None):
    now = dt(now) if now else datetime.now(timezone.utc)
    et = now.astimezone(ET)
    opening = et.replace(hour=9, minute=30, second=0, microsecond=0)
    hkday = opening.astimezone(HK).date()
    def deadline(name):
        return datetime.fromisoformat(hkday.isoformat()+'T'+RULES['schedule'][name]).replace(tzinfo=HK)
    last = min(deadline('last_initial_HK_exclusive'), opening+timedelta(minutes=60))
    exit_at, confirm, end = (deadline(k) for k in ['exit_reminder_HK','exit_confirm_HK','end_HK'])
    prep = et.replace(hour=8, minute=55, second=0, microsecond=0)
    available = opening+timedelta(minutes=7) < last
    phase = ('IDLE' if now < prep or now > end else
             'EXIT_REVIEW' if now >= exit_at else
             'PREMARKET' if now < opening else
             'MONITOR_ONLY' if now >= last else 'INTRADAY')
    return {'ET':et.isoformat(),'HK':now.astimezone(HK).isoformat(),'trade_date':et.date().isoformat(),
            'phase':phase,'entry_window_available':available,
            'regular_open_HK':opening.astimezone(HK).isoformat(),
            'weekday_candidate_only_not_exchange_calendar':et.weekday()<5,
            'deadlines_ET':{k:t.astimezone(ET).isoformat() for k,t in
                            [('last_initial',last),('last_add',last),('exit',exit_at),('exit_confirm',confirm),('end',end)]}}


def fee(q, side, price, extra='0'):
    f = RULES['fees']
    clearing = up(D(q)*D(f['clearing_usd_share_side']))
    sec = max(CENT,up(D(q)*D(price)*D(f['SEC_sell_rate']))) if side=='SELL' else D(0)
    taf = min(D(f['TAF_maximum_usd']),max(CENT,up(D(q)*D(f['TAF_share'])))) if side=='SELL' else D(0)
    return clearing+sec+taf+D(extra)


def size_plan(entry, structural_exit, resistance, cash, mean_volume, tick=CENT, budget='15', extra='0'):
    entry, structural_exit, resistance = D(entry),D(structural_exit),D(resistance)
    if not 0 < structural_exit < entry or (entry-structural_exit)/entry > D('.10'):
        return None
    cash = min(D(cash),D(RULES['capital']['trading_plan_usd']))
    hard = up(entry*D('.90'))
    expected_hard = hard-CENT
    maximum = min(int(D('250')/entry),int(cash/entry),int(D(mean_volume)*D('.01')))
    for q in range(maximum,0,-1):
        bf = fee(q,'BUY',entry,extra)
        hard_fee = fee(q,'SELL',expected_hard,extra)
        hard_risk = q*(entry-expected_hard)+bf+hard_fee+D(1)
        if hard_risk>D(budget) or q*entry+bf+hard_fee>cash:
            continue
        expected_structure = structural_exit-tick
        if expected_structure<=0:
            continue
        structure_r = q*(entry-expected_structure)+bf+fee(q,'SELL',expected_structure,extra)+D(1)
        reference = up(entry+(2*structure_r+bf+fee(q,'SELL',entry,extra)+D(1))/q+tick,tick)
        for _ in range(20):
            reference_gain = q*(reference-tick-entry)-bf-fee(q,'SELL',reference-tick,extra)-D(1)
            if reference_gain>=2*structure_r:
                break
            reference += tick
        else:
            continue
        if reference > down(resistance,tick)-tick:
            continue
        return {'quantity':q,'entry_limit':entry,'planned_hard_stop':hard,
                'structural_exit_level':structural_exit,'estimated_hard_stop_loss':hard_risk,
                'structural_R_reference':structure_r,'reference_2R_price':reference,
                'reference_net_gain':reference_gain,'nearest_known_resistance':resistance,
                'gross_purchase':q*entry,'known_buy_fee':bf,'fixed_profit_target':None,
                'profit_order_instruction':None,'unknown_extra_fee_assumed_zero':extra=='0'}
    return None


def bars_asof(rows, now):
    result=[]
    for r in rows:
        end=dt(r.get('available_at_et') or r.get('end') or r.get('time_key'))
        if end>now:
            continue
        start=dt(r['time_et']) if r.get('time_et') else end-timedelta(minutes=1)
        b={'start':start.astimezone(ET),'end':end.astimezone(ET),'volume':int(r['volume'])}
        for k in ['open','high','low','close']:
            b[k]=D(r[k] if k in r else r[k+'_price'])
        if b['end']-b['start']!=timedelta(minutes=1) or b['start'].second or b['end'].second:
            raise ValueError('Minute timestamps must describe complete aligned one-minute bars')
        if b['volume']<0 or not 0<b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']:
            raise ValueError('Invalid OHLC or volume')
        result.append(b)
    return sorted(result,key=lambda b:b['start'])


def regular_bars(rows,now,day):
    opening=datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ET)
    bars=[b for b in bars_asof(rows,now) if b['start']>=opening and b['start'].date()==opening.date()]
    if (not bars or bars[0]['start']!=opening or any(b['volume']<=0 for b in bars)
            or any(bars[i]['end']!=bars[i+1]['start'] for i in range(len(bars)-1))):
        return []
    return bars


def vwap(bars):
    volume=sum(b['volume'] for b in bars)
    return sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in bars)/volume if volume else None


def five_bars(bars):
    result=[]
    for i in range(0,len(bars)-4,5):
        group=bars[i:i+5]
        result.append({'start':group[0]['start'],'end':group[-1]['end'],
                       'high':max(b['high'] for b in group),'low':min(b['low'] for b in group),
                       'close':group[-1]['close']})
    return result


def setup_asof(rows,now,day):
    bars=regular_bars(rows,now,day)
    if len(bars)<7:
        return None
    high=max(b['high'] for b in bars[:5]); low=min(b['low'] for b in bars[:5])
    breakout=None; confirmations=[]
    for i,b in enumerate(bars[5:],5):
        if breakout is None:
            if b['close']>high:
                breakout=b
            continue
        if b['close']<=high:
            breakout=None
            continue
        if b['low']<=high:
            previous=bars[i-5:i]
            mean=D(sum(x['volume'] for x in previous))/5
            current_vwap=vwap(bars[:i+1])
            if b['close']>b['open'] and b['volume']>mean and b['close']>current_vwap:
                confirmations.append({'known_at':b['end'],'pattern_started_at':breakout['start'],
                    'confirmation_high':b['high'],'retest_low':b['low'],
                    'opening_range_high':high,'opening_range_low':low,
                    'mean_prior_5_volume':mean,'vwap':current_vwap})
            # First retest consumes this breakout even when confirmation fails.
            breakout=None
    if not confirmations:
        return None
    setup=confirmations[-1]
    setup['completed_5m_highs']=[x['high'] for x in five_bars(bars)]
    return setup


def macro_pause(macro,now):
    if macro.get('verified') is not True:
        return '宏观日历待核'
    for e in macro.get('events',[]):
        if e.get('high_importance') and dt(e['event_at'])-timedelta(minutes=5)<=now<=dt(e['event_at'])+timedelta(minutes=10):
            return '宏观暂停新增风险：'+e['name']
    return None


def setup_after_macro(setup,macro):
    return all(not e.get('high_importance') or
        setup['known_at']<dt(e['event_at'])-timedelta(minutes=5) or
        (setup['known_at']>dt(e['event_at'])+timedelta(minutes=10) and
         setup['pattern_started_at']>=dt(e['event_at'])+timedelta(minutes=10)) for e in macro.get('events',[]))


def fresh_quote(q,now,day):
    stamp=dt(q['update_time']) if q.get('update_time') else None
    return bool(stamp and 0<=(now-stamp).total_seconds()<=30 and q.get('data_date')==day)


def gates_pass(candidate,now):
    return (all(candidate.get('gates',{}).get(k)=='pass' for k in ['price_gap','float','RVOL','news','security_type'])
        and not candidate.get('news_adverse_now') and candidate.get('news_published_at') is not None
        and dt(candidate['news_published_at'])<=now)


def position_review(packet,now,c):
    cards=[]; candidates={x['symbol']:x for x in packet.get('candidates',[])}
    for symbol,p in packet['state']['actual_positions'].items():
        if p.get('is_ETF') or symbol in RULES['capital']['ETF_symbols']:
            continue
        qty=p.get('quantity'); average=p.get('initial_average')
        if qty is None or average is None or int(qty)<=0:
            cards.append({'symbol':symbol,'status':'DATA_BLOCKED','reason':['实际剩余股数或首仓均价待核']})
            continue
        candidate=candidates.get(symbol,{})
        quote=candidate.get('quote',{}); fresh=fresh_quote(quote,now,c['trade_date'])
        bid=D(quote.get('bid_price') or 0)
        confirmed=p.get('confirmed_stop')
        proposed=up(D(average)*D('.90'))
        protected=bool(p.get('protection_acknowledged') and confirmed is not None)
        status='PROTECT'; reasons=[]
        if (now>=dt(c['deadlines_ET']['exit']) or
                (p.get('entry_at') and dt(p['entry_at']).astimezone(ET).date().isoformat()!=c['trade_date'])):
            status='EXIT_REVIEW';reasons.append('北京时间22:20起退出；22:25核对，22:30仍持有则报告真实敞口')
        elif candidate.get('news_adverse_now'):
            status='EXIT_REVIEW';reasons.append('已核实的重要不利消息，人工处理退出')
        elif not protected:
            status='PROTECTION_REQUIRED';reasons.append('未确认券商保护生效，优先处理保护或人工退出')
        elif fresh and bid<=D(confirmed):
            status='EXIT_REVIEW';reasons.append('盘口已到用户确认的硬止损，核对成交和剩余股数，不撤销保护等待反弹')
        try:
            bars=regular_bars(candidate.get('bars',[]),now,c['trade_date'])
        except (ValueError,KeyError):
            bars=[]
        # No stale candles may create a new exit/hold or tightening decision.
        recent=bool(bars and 0<=(now-bars[-1]['end']).total_seconds()<=60)
        structure=p.get('structural_exit_level')
        weakness=False
        if recent and len(bars)>=7:
            b,a=bars[-1],bars[-2]
            mean=D(sum(x['volume'] for x in bars[-6:-1]))/5
            weakness=(a['close']<vwap(bars[:-1]) and b['close']<vwap(bars)
                      and b['close']<a['low'] and b['volume']>=mean)
        if status=='PROTECT' and recent and structure is not None and bars[-1]['close']<D(structure):
            status='EXIT_REVIEW';reasons.append('完成1分钟收盘跌破原回踩结构，形态已失效；不等待亏满10%')
        elif status=='PROTECT' and recent and weakness:
            status='EXIT_REVIEW';reasons.append('连续两根收盘低于VWAP且放量跌破前一分钟低点，动量转弱')
        if not fresh:
            reasons.append('盘口待更新；不据旧报价判断现在可成交或继续持有')
        if not recent:
            reasons.append('已完成分钟线待更新，不能据旧形态证明当前仍可持有')
        cards.append({'symbol':symbol,'status':status,'actual_quantity':int(qty),
            'initial_average':D(average),'hard_stop_proposed':proposed,'confirmed_stop':confirmed,
            'protection_acknowledged':protected,'structural_exit_level':structure,
            'fixed_profit_target':None,'quote_fresh':fresh,'bars_fresh':recent,
            'reason':reasons or ['结构尚未失效；保留已生效保护，不因普通波动强制止盈']})
        if status!='PROTECT' or not fresh or not recent:
            continue
        required=['buy_gross','buy_fees','structural_R_reference']
        if any(p.get(k) is None for k in required):
            continue
        extra=str(packet['state'].get('extra_fee_per_order_usd') or '0')
        profit=D(p.get('net_sales') or 0)+int(qty)*bid-fee(int(qty),'SELL',bid,extra)-D(p['buy_gross'])-D(p['buy_fees'])
        if profit<D(p['structural_R_reference']):
            continue
        five=five_bars(bars)
        pivots=[x for i,x in enumerate(five) if i>=1 and i+2<len(five)
                and x['low']<five[i-1]['low'] and five[i+1]['low']>x['low'] and five[i+2]['low']>x['low']
                and now>=five[i+2]['end']+timedelta(minutes=1)]
        if not pivots:
            continue
        if candidate.get('tick_verified') is not True or not candidate.get('tick_size'):
            continue
        tick=D(candidate['tick_size'])
        if tick<=0:
            continue
        stop=down(pivots[-1]['low'],tick)-tick
        if D(confirmed)<stop<bid:
            cards.append({'symbol':symbol,'status':'CONDITIONAL_PLAN','kind':'STOP_RAISE',
                'actual_quantity':int(qty),'proposed_stop':stop,'actual_stop_changed':False,
                'effective_after_user_confirmation':True,
                'reason':['+1结构R及更高5分钟摆动低点已确认；用户核对改单生效后才提高记录中的止损']})
    return cards


def evaluate(packet):
    now=dt(packet['asof']);c=clock(now)
    out={'version':RULES['version'],'asof_ET':c['ET'],'asof_HK':c['HK'],'trade_date':c['trade_date'],
         'status':'WAIT','reason':[],'cards':[],'actual_orders_submitted':False}
    if packet.get('calendar_verified') is not True:
        out.update(status='DATA_BLOCKED',reason=['美国交易日历未核验']);return out
    if c['trade_date'] not in packet.get('trading_dates',[]):
        out.update(status='REST',reason=['富途日历核验为美国休市日']);return out
    state=packet['state'];positions=state.get('actual_positions')
    if positions:
        out['cards']=position_review(packet,now,c)
        if out['cards']:
            out['status']='EXIT_REVIEW' if any(x['status']=='EXIT_REVIEW' for x in out['cards']) else 'PROTECT'
            if any(x['status']=='PROTECTION_REQUIRED' for x in out['cards']) and out['status']!='EXIT_REVIEW':
                out['status']='PROTECTION_REQUIRED'
            if all(x['status']=='DATA_BLOCKED' for x in out['cards']):
                out['status']='DATA_BLOCKED'
            return out
    if not c['entry_window_available']:
        out['reason']=['22:30截止与冬令时开盘重合，仅做盘前研究，不开日内仓'];return out
    if c['phase']!='INTRADAY':
        out['reason']=['当前仅做准备、保护或退出，不在首仓窗口'];return out
    frozen=packet.get('watchlist_frozen_at')
    cutoff=now.astimezone(ET).replace(hour=9,minute=25,second=0,microsecond=0)
    if not frozen or not dt(frozen).astimezone(ET).date()==now.astimezone(ET).date() or dt(frozen)>cutoff or dt(frozen)>now:
        out.update(status='DATA_BLOCKED',reason=['候选未在当日09:25 ET前冻结']);return out
    if len(packet.get('candidates',[]))>3:
        out.update(status='DATA_BLOCKED',reason=['冻结候选超过3只']);return out
    paused=macro_pause(packet.get('macro',{}),now)
    if paused:
        out.update(status='DATA_BLOCKED',reason=[paused]);return out
    if any(dt(f['time']).astimezone(ET).date().isoformat()==c['trade_date'] and f['side']=='BUY'
           and not f.get('is_ETF') for f in state.get('fills',[])):
        out['reason']=['当日首仓已成交，退出后不重开'];return out
    week=D(state.get('weekly_realized_strategy_pnl_usd') or 0)
    total=D(state.get('experiment_realized_strategy_pnl_usd') or 0)
    allowance=max(D(0),min(D(15),D(45)+week,D(150)+total))
    if allowance<=0 or state.get('weekly_stop_latched') or state.get('experiment_stop_latched'):
        out.update(status='DATA_BLOCKED',reason=['周或本轮风险停线触发']);return out
    for candidate in packet.get('candidates',[]):
        symbol=candidate['symbol'];q=candidate.get('quote',{});rejected=[]
        if not gates_pass(candidate,now):
            rejected.append('选股或已发布新闻未核实通过')
        if candidate.get('gates_known_at') and dt(candidate['gates_known_at'])>dt(frozen):
            rejected.append('选股证据在冻结后才取得，不能回填')
        if not fresh_quote(q,now,c['trade_date']):
            rejected.append('报价超过30秒、来自未来或日期无效')
        if q.get('suspension') or q.get('sec_status') not in [None,'NORMAL']:
            rejected.append('停牌或交易状态异常')
        bid,ask=D(q.get('bid_price') or 0),D(q.get('ask_price') or 0)
        if not 0<bid<=ask or ask-bid>D('.03'):
            rejected.append('盘口价格无效或价差超过3美分')
        if candidate.get('tick_verified') is not True or not candidate.get('tick_size'):
            rejected.append('最小报价单位未核实')
        try:
            setup=setup_asof(candidate.get('bars',[]),now,c['trade_date'])
        except (ValueError,KeyError) as e:
            setup=None;rejected.append('分钟线格式或覆盖无效')
        if not setup:
            rejected.append('完整5分钟区间突破回踩及动量确认尚未成立')
        else:
            expiry=min(setup['known_at']+timedelta(seconds=60),dt(c['deadlines_ET']['last_initial']))
            if now>=expiry:
                rejected.append('一分钟信号已过期')
            if not setup_after_macro(setup,packet.get('macro',{})):
                rejected.append('宏观暂停后须形成新的突破回踩')
        if rejected:
            out['cards'].append({'symbol':symbol,'status':'WAIT','reason':rejected});continue
        tick=D(candidate['tick_size'])
        if tick<=0:
            out['cards'].append({'symbol':symbol,'status':'WAIT','reason':['最小报价单位无效']});continue
        trigger=up(setup['confirmation_high'],tick)+tick;limit=trigger+D('.02')
        structure=down(setup['retest_low'],tick)-tick
        if not D(5)<=limit<=D(20) or ask+tick>limit:
            out['cards'].append({'symbol':symbol,'status':'WAIT','reason':['买入价范围或最高限价不允许追价']});continue
        resistance=[D(v) for v in [candidate.get('previous_high'),candidate.get('premarket_high'),
                    *setup['completed_5m_highs']] if v is not None and D(v)>limit]
        if not resistance:
            out['cards'].append({'symbol':symbol,'status':'WAIT','reason':['上方阻力未知，不假设有足够盈利空间']});continue
        cash=state.get('actual_settled_cash_usd')
        planning=cash is None or positions is None or not state.get('fees_complete') or not state.get('stop_order_support_verified')
        plan=size_plan(limit,structure,min(resistance),cash if cash is not None else '1500',
                       setup['mean_prior_5_volume'],tick,budget=allowance,
                       extra=str(state.get('extra_fee_per_order_usd') or '0'))
        if not plan:
            out['cards'].append({'symbol':symbol,'status':'WAIT',
               'reason':['10%硬止损风险、现金、流动性、结构距离或已知阻力前净2结构R空间不足']});continue
        card={'symbol':symbol,'status':'CONDITIONAL_PLAN' if planning else 'ENTRY_CONDITIONAL',
             'kind':'INITIAL','trigger':trigger,**plan,
             'opening_range_high':setup['opening_range_high'],'opening_range_low':setup['opening_range_low'],
             'signal_known_at_ET':setup['known_at'].isoformat(),'valid_until_ET':expiry.isoformat(),
             'valid_until_HK':expiry.astimezone(HK).isoformat(),
             'reason':['用户提交前核对真实盘口、已交收现金、费用与保护单支持',
                       '须真实价格触发且卖一价加一个tick不超过最高限价；不提前买、不追价',
                       '按实际均价重算10%硬保护；结构失效可提前退出；没有固定止盈单',
                       '2R仅为结构风险空间参考，不是硬止损风险的两倍，也不是强制卖出价']}
        out['cards'].append(card);out['status']=card['status'];break
    if not out['cards']:
        out['reason']=['没有当日冻结候选可评估']
    return out


def default(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,datetime):return value.isoformat()
    raise TypeError(type(value).__name__)


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['clock','evaluate'])
    p.add_argument('--at');p.add_argument('--input');p.add_argument('--output');args=p.parse_args()
    result=clock(args.at) if args.command=='clock' else evaluate(json.loads(Path(args.input).read_text()))
    text=json.dumps(result,ensure_ascii=False,indent=2,default=default)
    if args.output:Path(args.output).write_text(text+'\n')
    print(text)


if __name__=='__main__':main()
