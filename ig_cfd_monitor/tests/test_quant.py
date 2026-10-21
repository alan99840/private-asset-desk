import math
import unittest
from copy import deepcopy
from datetime import datetime,timezone
from quant import indicators,point_spec,portfolio,stress,correlation,ema,rsi_band,chart_reading

NOW=1800000000

def candles(count=100):
 return [{'time':NOW-(count-i)*3600,'open':100+i,'high':102+i,'low':98+i,'close':100+i} for i in range(count)]
def fixture():
 p={'epic':'X','dealId':'one','currency':'USD','direction':'BUY','size':2,'level':99,'stopLevel':90}
 details={'fetched_epoch':NOW,'instrument':{'unit':'CONTRACTS','type':'INDICES','valueOfOnePip':'1','onePipMeans':'1 Index Point','currencies':[{'code':'USD','baseExchangeRate':.125}]},'snapshot':{'scalingFactor':1}}
 state={'positions':[p],'positions_at':datetime.fromtimestamp(NOW,timezone.utc).isoformat(),'stale':False,'markets':[{'epic':'X','bid':100,'ask':102,'stale':False}], 'market_specs':{'X':details},'account':{'equity':1000,'unrealized':16,'margin':100,'available':900},'risk':{'threshold_equity':400}}
 return state

class QuantTests(unittest.TestCase):
 def test_rsi_four_disjoint_bands(self):
  for value,expected in [(0,'oversold'),(29.999,'oversold'),(30,'weak'),(49.999,'weak'),(50,'strong'),(70,'strong'),(70.001,'overbought'),(100,'overbought')]:
   self.assertEqual(rsi_band(value),expected)
  for value in [None,True,'invalid',float('nan'),float('inf'),-1,101]:self.assertIsNone(rsi_band(value))
 def chart_quality(self):
  return dict(status='READY',resolution='HOUR',observations=99,as_of=NOW-7200,reasons=[],close=110,ema20=105,ema50=100,atr14=2,rsi14=60,low20=95,high20=109,low60=90,high60=115)
 def test_chart_trend_requires_ema_and_rsi_confirmation(self):
  q=self.chart_quality();a=chart_reading(q,{'status':'ready'})
  self.assertEqual(a['regime'],'UP');self.assertEqual(a['extension_atr'],2.5)
  self.assertEqual(a['levels'][-2]['price'],108);self.assertEqual(a['levels'][-1]['price'],112)
  self.assertTrue(any('2 ATR' in r for r in a['risks']))
  for rsi in [50,49,20]:
   q['rsi14']=rsi;self.assertEqual(chart_reading(q,{'status':'ready'})['regime'],'RANGE')
  q.update(close=90,ema20=95,ema50=100,rsi14=40)
  self.assertEqual(chart_reading(q,{'status':'ready'})['regime'],'DOWN')
  q.update(rsi14=55);self.assertEqual(chart_reading(q,{'status':'ready'})['regime'],'RANGE')
  q.update(close=100,ema20=100,rsi14=60);self.assertEqual(chart_reading(q,{'status':'ready'})['regime'],'RANGE')
 def test_chart_quality_blocks_current_levels_and_regime(self):
  for status in ['LIMITED','INSUFFICIENT']:
   q=self.chart_quality();q.update(status=status,reasons=[{'message':'合成数据缺口'}]);out=chart_reading(q,{'status':'ready'})
   self.assertEqual(out['regime'],'WAIT');self.assertEqual(out['levels'],[]);self.assertEqual(out['values'],{});self.assertIsNone(out['rsi_band'])
  for response in [{'status':'error'},{'status':'loading'},{'status':'limited'},{'status':'ready','skipped_rows':1}]:
   self.assertEqual(chart_reading(self.chart_quality(),response)['status'],'WAIT')
  for field,value in [('rsi14',101),('atr14',-1),('ema20',None),('low20',float('inf'))]:
   q=self.chart_quality();q[field]=value;self.assertEqual(chart_reading(q,{'status':'ready'})['status'],'WAIT')
 def test_chart_zero_atr_and_neutral_boundary(self):
  q=self.chart_quality();q.update(atr14=0,rsi14=50,close=0)
  out=chart_reading(q,{'status':'ready'});self.assertEqual(out['regime'],'RANGE');self.assertIsNone(out['extension_atr']);self.assertIsNone(out['atr_percent']);self.assertEqual(len(out['levels']),6)
  self.assertTrue(any('RSI = 50' in r for r in out['risks']))
 def test_chart_ranges_and_tail_remain_no_lookahead(self):
  bars=candles();q=indicators(bars,'HOUR',NOW);a=chart_reading(q,{'status':'ready'})
  self.assertEqual(a['values']['high20'],199)
  bars[-1].update(close=1e9,high=1e9)
  self.assertEqual(chart_reading(indicators(bars,'HOUR',NOW),{'status':'ready'}),a)
 def test_completed_tail_and_prior_ranges(self):
  bars=candles();a=indicators(bars,'HOUR',NOW);bars[-1]['close']=1e9;bars[-1]['high']=1e9
  self.assertEqual(a,indicators(bars,'HOUR',NOW));self.assertEqual(a['observations'],99)
  self.assertEqual(a['high20'],199);self.assertEqual(a['low60'],136);self.assertEqual(a['atr14'],4);self.assertEqual(a['rsi14'],100)
  self.assertAlmostEqual(a['ema20'],188.5);self.assertAlmostEqual(a['ema50'],173.5)
 def test_constant_and_short_series(self):
  bars=candles();
  for b in bars:b.update(open=100,high=100,low=100,close=100)
  out=indicators(bars,'HOUR',NOW);self.assertEqual(out['rsi14'],50);self.assertEqual(out['atr14'],0)
  self.assertEqual(indicators(bars[:60],'HOUR',NOW)['status'],'INSUFFICIENT')
 def test_internal_missing_weekday_gates_conditions(self):
  bars=candles(120);index=next(i for i in range(65,100) if datetime.fromtimestamp(bars[i]['time'],timezone.utc).weekday()==2)
  del bars[index:index+3];out=indicators(bars,'HOUR',NOW)
  self.assertEqual(out['status'],'LIMITED');self.assertEqual(out['conditions'],[])
 def test_point_value_no_double_contract_size(self):
  state=fixture();details=state['market_specs']['X'];details['instrument']['contractSize']=10000
  details['instrument'].update(onePipMeans='0.01 JPY/USD',valueOfOnePip='100');details['snapshot']['scalingFactor']=100
  self.assertEqual(point_spec(state['positions'][0],details,NOW)['hkd_per_point_per_contract'],800)
  details['instrument']['type']='OPTIONS';self.assertEqual(point_spec(state['positions'][0],details,NOW)['status'],'UNVERIFIED')
 def test_long_short_stops_and_spread(self):
  state=fixture();signals={'X':{'atr14':2,'status':'READY'}};b=portfolio(state,signals,NOW)
  self.assertEqual(b['positions'][0]['hkd_per_point'],16);self.assertEqual(b['positions'][0]['loss_to_stop_hkd'],160)
  r=stress(state,b,signals,'points',-5,spread_multiple=2,cost_hkd=3)
  self.assertEqual(r['status'],'READY');self.assertEqual(r['delta_hkd'],-99)
  self.assertEqual(r['equity_hkd'],901);self.assertEqual(r['available_if_margin_unchanged'],801)
  short=deepcopy(state['positions'][0]);short.update(dealId='two',direction='SELL',level=102,stopLevel=110)
  state['positions'].append(short);b=portfolio(state,signals,NOW)
  self.assertEqual(b['groups'][0]['net_hkd_per_point'],0);self.assertGreater(b['gross_notional_hkd'],0)
  self.assertEqual(stress(state,b,signals,'points',20)['delta_hkd'],0)
 def test_quality_and_reconciliation_gate(self):
  state=fixture();signals={'X':{'atr14':2,'status':'LIMITED'}};b=portfolio(state,signals,NOW)
  self.assertIsNone(stress(state,b,signals)['equity_hkd']);self.assertEqual(stress(state,b,signals,'points',1)['status'],'READY')
  state['account']['unrealized']=500;b=portfolio(state,signals,NOW)
  self.assertEqual(b['reconciliation']['status'],'CHECK_REQUIRED');self.assertIsNone(stress(state,b,signals,'points',1)['equity_hkd'])
  state['positions_at']=datetime.fromtimestamp(NOW-91,timezone.utc).isoformat()
  self.assertEqual(portfolio(state,signals,NOW)['status'],'DATA_LIMITED')
 def test_crossed_quote_unknown_side_block(self):
  state=fixture();state['markets'][0]['ask']=99
  self.assertEqual(portfolio(state,{},NOW)['unverified_positions'],1)
  state=fixture();state['positions'][0]['direction']='UNKNOWN'
  self.assertEqual(portfolio(state,{},NOW)['unverified_positions'],1)
 def test_fx_exposure_is_pnl_not_notional(self):
  state=fixture();b=portfolio(state,{},NOW)
  self.assertAlmostEqual(stress(state,b,{},'points',0,fx_percent=10)['delta_hkd'],1.6)
 def test_correlation_requires_same_intervals_and_positive_prices(self):
  a=candles();b=deepcopy(a)
  self.assertAlmostEqual(correlation(a,b)['value'],1)
  for c in b:c['time']+=1
  self.assertEqual(correlation(a,b)['status'],'INSUFFICIENT')
  for c in a:c['close']=-1
  self.assertIsNone(correlation(a,a)['value'])

if __name__=='__main__':unittest.main()
