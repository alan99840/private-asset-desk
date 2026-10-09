"""Concrete V2.3 risks: stale index, future story, duplicate narrative and protection bypass."""
import json
from copy import deepcopy
from datetime import timedelta
from context import market_gate, select_events, normalize_quotes, entry_context
from evaluate import evaluate
from advisor import dt
import test_advisor as base

now=base.now
market={'calendar_verified':True,'trading_dates':[base.day],'status':'CAPTURED','quotes':[
 {'symbol':s,'exchange_at':(now-timedelta(seconds=2)).isoformat(),'data_date':base.day}
 for s in ['US..DJI','US..IXIC','US..SPX']]}
event={'event_key':'same-real-event','fact':'Synthetic verified event','mechanism':'Quantified business path',
 'published_at':(now-timedelta(minutes=20)).isoformat(),'reviewed_at':(now-timedelta(minutes=1)).isoformat(),
 'review_status':'verified','sources':[{'primary':True,'url':'https://example.com/original'}],
 'affected_assets':[{'symbol':base.candidate['symbol'],'path':'Revenue and margin change','evidence':'Original quantified source'}], 'strength':'中'}
research={'asof':now.isoformat(),'watchlist_review_complete':True,'events':[event]}
passed=[]
def check(name,ok):assert ok,name;passed.append(name)
check('current_index_bundle_passes',market_gate(market,now) is None)
old=deepcopy(market);old['quotes'][0]['exchange_at']=(now-timedelta(seconds=91)).isoformat()
check('old_index_blocks_new_entry',evaluate(base.packet,old,research)['status']=='DATA_BLOCKED')
missing=deepcopy(market);missing['quotes'].pop()
check('ETF_does_not_substitute_missing_index',market_gate(missing,now) is not None)
future=deepcopy(event);future['published_at']=(now+timedelta(seconds=1)).isoformat()
check('future_news_ignored',not select_events({'events':[future]},now,{base.candidate['symbol']}))
duplicate=deepcopy(event);duplicate['sources'][0]['url']='https://example.com/republished'
check('duplicate_news_is_one_event',len(select_events({'events':[event,duplicate]},now,{base.candidate['symbol']}))==1)
weak=deepcopy(event);weak['affected_assets'][0].pop('evidence')
check('unsupported_industry_link_ignored',not select_events({'events':[weak]},now,{base.candidate['symbol']}))
pending=deepcopy(research);pending['watchlist_review_complete']=False
check('incomplete_context_blocks_entry',evaluate(base.packet,market,pending)['status']=='DATA_BLOCKED')
future_review=deepcopy(research);future_review['asof']=(now+timedelta(seconds=1)).isoformat()
check('future_research_snapshot_blocks_entry',evaluate(base.packet,market,future_review)['status']=='DATA_BLOCKED')
check('verified_context_keeps_conditional_status',evaluate(base.packet,market,research)['status']=='CONDITIONAL_PLAN')
adverse=deepcopy(research);adverse['events'][0]['entry_effect']='exclude_new_entry'
check('verified_adverse_event_blocks_symbol',evaluate(base.packet,market,adverse)['status']=='DATA_BLOCKED')
regular=normalize_quotes({'ret_code':0,'data':{'quote_list':[{'code':'US..DJI','data_time':int((now-timedelta(seconds=100)).timestamp()*1000),'data_date':base.day,'last_price':100,'prev_close_price':99}]}},now,[base.day])
check('successful_fetch_cannot_relabel_old_quote',regular[0]['status']=='STALE')
close=normalize_quotes({'ret_code':0,'data':{'quote_list':[{'code':'US..DJI','data_time':int(now.timestamp()*1000),'data_date':base.day,'last_price':100,'prev_close_price':99}]}},now+timedelta(hours=10),[base.day])
check('after_hours_closing_index_is_labelled',close[0]['status']=='LAST_SESSION')
hold=deepcopy(base.packet);hold['state']['actual_positions']={base.candidate['symbol']:{'quantity':10,'initial_average':'6.14','entry_at':now.isoformat(),'confirmed_stop':'5.53','protection_acknowledged':False,'structural_exit_level':'5.99'}}
check('protection_not_delayed_for_missing_indices',evaluate(hold,{}, {})['cards'][0]['status']=='PROTECTION_REQUIRED')
print(json.dumps({'v23_context_checks_passed':len(passed),'synthetic_only':True,'checks':passed}))
