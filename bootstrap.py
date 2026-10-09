"""Initialize empty local state after cloning; contains no actual portfolio or credentials."""
import json
from pathlib import Path

root=Path(__file__).resolve().parent
defaults={
 'portfolio.json':{'version':'V2.3','planned_trading_cash_usd':'1500','actual_settled_cash_usd':None,'actual_positions':{},'fills':[],'reserve_usd':'1000','unallocated_original_usd':'500','fees_complete':False,'stop_order_support_verified':False},
 'watchlist.json':{'core':[],'discretionary':[]},
 'frozen_watchlist.json':{'session':None,'status':'NOT_FROZEN','candidates':[]},
 'action_cards.json':{'status':'WAIT','cards':[],'reason':['Initialize your own reviewed watchlist and actual account confirmations.']},
 'research.json':{'asof':None,'events':[],'watchlist_review_complete':False},
 'deployment.json':{'remote_enabled':False,'allowed_hosts':[]},
}
for name,value in defaults.items():
 p=root/'state'/name
 if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,indent=2)+'\n')
print('Local empty state initialized. No credentials or orders created.')
