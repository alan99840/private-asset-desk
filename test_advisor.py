"""Synthetic execution/risk boundaries, not market performance evidence."""
import json
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
from advisor import ET,D,clock,evaluate,setup_after_macro,default

day='2026-10-05'
opening=datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ET)
bars=[]
for i in range(11):
    o,h,l,c,v=('5.90','6.00','5.80','5.95',10000)
    if i==5:o,h,l,c,v='5.98','6.15','5.98','6.10',15000
    if i==6:o,h,l,c,v='6.04','6.13','6.00','6.12',25000
    if i>=7:o,h,l,c,v='6.12','6.20','6.10','6.18',10000
    start=opening+timedelta(minutes=i)
    bars.append(dict(time_et=start.isoformat(),available_at_et=(start+timedelta(minutes=1)).isoformat(),
                     open=o,high=h,low=l,close=c,volume=v))
now=opening+timedelta(minutes=7,seconds=20)
state=json.loads((Path(__file__).parent/'state/portfolio.json').read_text())
state.update(actual_positions={},actual_settled_cash_usd=None,fills=[])
candidate={'symbol':'US.SYNTHETIC_TEST','tick_size':'.01','tick_verified':True,
           'gates':dict.fromkeys(['price_gap','float','RVOL','news','security_type'],'pass'),
           'gates_known_at':(opening-timedelta(minutes=10)).isoformat(),
           'news_published_at':(opening-timedelta(hours=1)).isoformat(),'bars':bars,
           'previous_high':'8','premarket_high':'8',
           'quote':{'bid_price':'6.13','ask_price':'6.15','data_date':day,'sec_status':'NORMAL',
                    'update_time':int((now-timedelta(seconds=2)).timestamp()*1000)}}
packet={'asof':now.isoformat(),'trading_dates':[day],'calendar_verified':True,'state':state,
        'watchlist_frozen_at':(opening-timedelta(minutes=6)).isoformat(),
        'macro':{'verified':True,'events':[]},'candidates':[candidate]}
passed=[]
def check(name,condition):
    assert condition,name
    passed.append(name)

r=evaluate(packet);card=r['cards'][0]
check('conditional_entry_without_actual_cash',r['status']=='CONDITIONAL_PLAN' and card['quantity']>0)
check('hard_10pct_risk_controls_size',card['estimated_hard_stop_loss']<=15 and card['gross_purchase']<=250
      and card['gross_purchase']<150 and card['planned_hard_stop']>=card['entry_limit']*D('.9'))
check('no_fixed_profit_order',card['fixed_profit_target'] is None and card['profit_order_instruction'] is None)
check('structure_2R_is_labelled_separately',card['reference_net_gain']>=2*card['structural_R_reference']
      and card['structural_R_reference']<card['estimated_hard_stop_loss'])
check('no_actual_orders_or_state_mutation',not r['actual_orders_submitted'] and state['fills']==[] and state['actual_positions']=={})
future=deepcopy(packet)
for b in future['candidates'][0]['bars']:
    if datetime.fromisoformat(b['available_at_et'])>now:
        for k in ['open','high','low','close']:b[k]='900'
        b['volume']=90000000
check('future_bars_cannot_change_present_decision',evaluate(future)==r)
gap=deepcopy(packet);gap['candidates'][0]['bars'].pop(0)
check('missing_opening_coverage_blocks_entry',evaluate(gap)['status']=='WAIT')
unclosed=deepcopy(packet);unclosed['asof']=(opening+timedelta(minutes=4,seconds=59)).isoformat()
check('unfinished_opening_range_blocks_entry',evaluate(unclosed)['status']=='WAIT')
wick=deepcopy(packet);wick['candidates'][0]['bars'][5]['close']='5.99'
check('wick_only_breakout_not_accepted',evaluate(wick)['status']=='WAIT')
failed=deepcopy(packet);failed['candidates'][0]['bars'][6]['open']='6.13'
check('first_retest_without_bullish_close_fails',evaluate(failed)['status']=='WAIT')
news=deepcopy(packet);news['candidates'][0]['news_published_at']=(now+timedelta(minutes=1)).isoformat()
check('future_news_blocks_entry',evaluate(news)['status']=='WAIT')
lateproof=deepcopy(packet);lateproof['candidates'][0]['gates_known_at']=now.isoformat()
check('late_filter_evidence_cannot_be_backfilled',evaluate(lateproof)['status']=='WAIT')
expired=deepcopy(packet);expired['asof']=(opening+timedelta(minutes=8)).isoformat()
expired['candidates'][0]['quote']['update_time']=int(datetime.fromisoformat(expired['asof']).timestamp()*1000)
check('one_minute_signal_expires',evaluate(expired)['status']=='WAIT')
for name,stamp in [('stale_quote',now-timedelta(seconds=31)),('future_quote',now+timedelta(seconds=1))]:
    p=deepcopy(packet);p['candidates'][0]['quote']['update_time']=int(stamp.timestamp()*1000)
    check(name+'_blocks_entry',evaluate(p)['status']=='WAIT')
spread=deepcopy(packet);spread['candidates'][0]['quote']['bid_price']='6.10'
check('wide_spread_blocks_entry',evaluate(spread)['status']=='WAIT')
ticks=deepcopy(packet);ticks['candidates'][0]['tick_verified']=False
check('unknown_tick_blocks_entry',evaluate(ticks)['status']=='WAIT')
late=deepcopy(packet);late['watchlist_frozen_at']=(opening-timedelta(minutes=4)).isoformat()
check('late_watchlist_blocks_entry',evaluate(late)['status']=='DATA_BLOCKED')
macro=deepcopy(packet);macro['macro']['events']=[{'event_at':now.isoformat(),'name':'TEST_CPI','high_importance':True}]
check('macro_pause_blocks_new_risk',evaluate(macro)['status']=='DATA_BLOCKED')
check('pre_pause_pattern_cannot_restart',not setup_after_macro({'known_at':now,'pattern_started_at':now-timedelta(minutes=3)},
      {'events':[{'event_at':(now-timedelta(minutes=11)).isoformat(),'high_importance':True}]}))
zero=deepcopy(packet);zero['state']['actual_settled_cash_usd']='0'
check('zero_real_cash_blocks_entry',evaluate(zero)['status']=='WAIT')
nearstop=deepcopy(packet);nearstop['state']['weekly_realized_strategy_pnl_usd']='-44'
check('remaining_weekly_risk_budget_is_enforced',evaluate(nearstop)['status']=='WAIT')
latched=deepcopy(packet);latched['state']['weekly_stop_latched']=True
check('latched_loss_stop_blocks_entry',evaluate(latched)['status']=='DATA_BLOCKED')
unknown=deepcopy(packet);unknown['calendar_verified']=False
check('unverified_calendar_blocks_entry',evaluate(unknown)['status']=='DATA_BLOCKED')
reentry=deepcopy(packet);reentry['state']['fills']=[{'time':now.isoformat(),'side':'BUY','is_ETF':False}]
check('no_reentry_after_daily_fill',evaluate(reentry)['status']=='WAIT')
check('summer_time_entry_available',clock('2026-10-05T13:37:20Z')['entry_window_available'])
check('winter_has_no_entry_window',not clock('2026-11-05T14:37:20Z')['entry_window_available'])
cutoff=deepcopy(packet);cutoff['asof']='2026-10-05T10:10:00-04:00'
check('HK_2210_stops_new_entries',evaluate(cutoff)['status']=='WAIT')
held=deepcopy(packet)
held['state']['actual_positions']={'US.SYNTHETIC_TEST':{'quantity':20,'initial_average':'6.16','confirmed_stop':'5.55',
            'protection_acknowledged':True,'structural_exit_level':'5.99','entry_at':now.isoformat()}}
held['asof']='2026-10-05T10:20:00-04:00'
check('HK_2220_requires_exit_review',evaluate(held)['status']=='EXIT_REVIEW')
held['asof']=now.isoformat()
held['state']['actual_positions']['US.SYNTHETIC_TEST']['reference_2R_price']='6.10'
check('reference_2R_does_not_force_profit_exit',evaluate(held)['status']=='PROTECT')
hard=deepcopy(held);hard['candidates'][0]['quote'].update(bid_price='5.54',ask_price='5.56')
check('hard_stop_never_ignored',evaluate(hard)['status']=='EXIT_REVIEW')
soft=deepcopy(held);soft['candidates'][0]['bars'][6].update(open='6.04',high='6.13',low='5.97',close='5.98')
check('completed_structure_failure_exits_early',evaluate(soft)['status']=='EXIT_REVIEW')
ordinary=deepcopy(held);ordinary['candidates'][0]['bars'][6]['low']='5.98'
check('intraminute_wick_recovery_does_not_force_structure_exit',evaluate(ordinary)['status']=='PROTECT')
weak=deepcopy(held)
weak['state']['actual_positions']['US.SYNTHETIC_TEST']['structural_exit_level']='5.70'
weak['candidates'][0]['bars'][5].update(open='5.98',high='6.15',low='5.90',close='5.91')
weak['candidates'][0]['bars'][6].update(open='5.91',high='5.95',low='5.88',close='5.89')
check('confirmed_volume_VWAP_weakness_exits_without_fixed_target',evaluate(weak)['status']=='EXIT_REVIEW')
held_future=deepcopy(held)
for b in held_future['candidates'][0]['bars']:
    if datetime.fromisoformat(b['available_at_et'])>now:
        for k in ['open','high','low','close']:b[k]='900'
        b['volume']=90000000
check('future_bars_cannot_change_actual_position_review',evaluate(held_future)==evaluate(held))
unprotected=deepcopy(held);unprotected['state']['actual_positions']['US.SYNTHETIC_TEST']['protection_acknowledged']=False
check('missing_protection_requires_user_action',evaluate(unprotected)['status']=='PROTECTION_REQUIRED')
etf=deepcopy(held);etf['state']['actual_positions']={'US.QQQ':{'quantity':'.1','initial_average':'500'}}
etf['asof']='2026-10-05T10:20:00-04:00'
check('long_term_ETF_excluded_from_intraday_exit',evaluate(etf)['cards']==[])
print(json.dumps({'tests_passed':len(passed),'synthetic_only':True,'actual_orders_submitted':False}))
(Path(__file__).parent/'integration_checks/advisor_tests.json').write_text(json.dumps({'passed':passed,
    'synthetic_only':True,'actual_orders_submitted':False},ensure_ascii=False,indent=2)+'\n')
(Path(__file__).parent/'integration_checks/example_conditional_card.json').write_text(json.dumps(r,
    ensure_ascii=False,indent=2,default=default)+'\n')
