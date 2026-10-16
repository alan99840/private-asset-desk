"""Read-only IG collector. Secrets never enter the browser projection."""
import copy
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from credentials import load_credentials, private_json, STATE
from ig_client import IGClient, IGError
from risk import evaluate_loss
from storage import Store


def now(): return datetime.now(timezone.utc).isoformat()
def number(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError): return None

def timestamp(v):
    if not v: return None
    try: return datetime.fromisoformat(str(v).replace('Z','+00:00')).replace(tzinfo=timezone.utc) if '+' not in str(v) else datetime.fromisoformat(str(v))
    except ValueError:
        for fmt in ('%Y/%m/%d %H:%M:%S','%Y/%m/%d %H:%M','%d/%m/%y %H:%M','%d/%m/%Y %H:%M:%S'):
            try:return datetime.strptime(str(v),fmt).replace(tzinfo=timezone.utc)
            except ValueError:pass
    return None

def amount(v):
    # Currency is validated separately. Do not infer FX or signs from instrument names.
    import re
    s=str(v or '').strip().replace(',','').replace('HK$','').replace('HKD','').strip()
    if s.startswith('(') and s.endswith(')'):s='-'+s[1:-1].strip()
    return number(s) if re.fullmatch(r'[+-]?\d+(\.\d+)?',s) else None


def base_state():
    return {'mode':'live','status':'waiting_for_credentials','message':'在本机配置页填写 IG API 信息后自动连接。',
      'environment':None,'account':None,'positions':None,'orders':None,'markets':[], 'stream':'DISCONNECTED',
      'account_at':None,'positions_at':None,'history':{'status':'not_started','statement_reconciled':False},
      'risk':{'status':'UNKNOWN'},'series':[],'news':{'status':'loading','items':[]},'alerts':[],
      'notification':'web_only','initial_capital':30000,'loss_alert_fraction':.60}


class Monitor:
    def __init__(self):
        self.lock=threading.RLock();self.state=base_state();self.store=Store()
        self.client=None;self.stream=None;self.subscribe=None;self.price_sub=None
        self.scope=None;self.creds=None;self.generation=0;self.quotes={};self.market_info={}
        self.watch=[];self.subscribed=[];self.refresh=threading.Event();self.stop=threading.Event()
        self.funding=None;self.funding_at=None;self.history_jobs=[];self.last_saved=0
        self.retry_at=0;self.last_poll=0;self.last_funding=0;self.last_history=0

    def start(self):
        self.status('waiting_for_credentials','请在本机配置页填写 IG API 信息。')
        threading.Thread(target=self.run,daemon=True,name='ig-collector').start()
        from news import collect_news
        threading.Thread(target=collect_news,args=(self,),daemon=True,name='official-news').start()

    def status(self,status,message='',accounts=None):
        with self.lock:self.state.update(status=status,message=message)
        private_json(STATE/'connection_status.json',{'status':status,'message':message,'at':now(),
          'accounts':[{k:a.get(k) for k in ('accountId','accountType','currency','status')} for a in accounts or []]})

    def reset(self,creds):
        self.generation+=1
        if self.stream:
            try:self.stream.disconnect()
            except Exception:pass
        with self.lock:
            news=self.state['news'];self.state=base_state();self.state['news']=news
            self.client=self.stream=self.subscribe=self.price_sub=None;self.scope=None
            self.creds=creds;self.quotes={};self.market_info={};self.watch=[];self.subscribed=[]
            self.funding=None;self.funding_at=None;self.history_jobs=[]
            self.retry_at=self.last_poll=self.last_funding=self.last_history=0

    def connect(self):
        self.status('connecting','正在验证 Live/Demo 环境、CFD 类型和 HKD 账户。')
        client=IGClient(self.creds);chosen,accounts=client.login()
        if chosen is None:
            self.status('account_selection_required','请选择启用状态的 HKD CFD 账户编号。',accounts)
            self.retry_at=time.time()+3600;return
        self.client=client;self.scope=self.creds['environment']+':'+client.account_id
        self.state['environment']=self.creds['environment']
        self.state['series']=self.store.series(self.scope)
        self.account(chosen)
        self.history_jobs=self.make_jobs()
        self.state['history']={'status':'syncing','since':self.creds['history_since'],'remaining_windows':len(self.history_jobs),'statement_reconciled':False}
        self.status('connected','账户已连接；只读采集运行中。')
        self.reconcile()
        try:
            lists=client.request('GET','/watchlists').get('watchlists',[])
            for item in lists[:5]:
                if item.get('id'):
                    self.watch.extend(client.request('GET','/watchlists/'+str(item['id'])).get('markets',[]))
        except IGError:pass
        generation=self.generation
        try:
            self.stream,self.subscribe,_=client.make_stream(
                lambda k,i,v:self.on_stream(k,i,v,generation),lambda s:self.on_status(s,generation))
            self.update_subscriptions()
        except IGError as exc:self.state['stream']=exc.code
        self.last_poll=time.time()

    def account(self,a):
        b=a.get('balance',{});cash=number(b.get('balance'));pnl=number(b.get('profitLoss'))
        with self.lock:
            self.state['account']={'name':a.get('accountName','CFD'),'currency':a.get('currency'),'type':a.get('accountType'),
              'cash':cash,'unrealized':pnl,'equity':cash+pnl if cash is not None and pnl is not None else None,
              'margin':number(b.get('deposit')),'available':number(b.get('available')),'source':'IG REST /accounts'}
            self.state['account_at']=now()
        self.save_snapshot()

    def reconcile(self):
        accounts=self.client.request('GET','/accounts').get('accounts',[])
        a=next((a for a in accounts if a.get('accountId')==self.client.account_id),None)
        if not a:raise IGError('ig.selected.account.missing')
        self.account(a)
        positions=self.client.request('GET','/positions',2).get('positions')
        orders=self.client.request('GET','/workingorders',2).get('workingOrders')
        if not isinstance(positions,list) or not isinstance(orders,list):raise IGError('ig.invalid.position.response')
        with self.lock:
            self.state['positions']=positions;self.state['orders']=orders;self.state['positions_at']=now()
        for p in positions:
            m=p.get('market',{});epic=m.get('epic')
            if epic:self.market_info[epic]=m
        self.update_subscriptions()
        self.save_snapshot()

    def update_subscriptions(self):
        held=[p.get('market',{}) for p in self.state['positions'] or []]
        for m in self.watch:
            if m.get('epic') and m['epic'] not in self.market_info:self.market_info[m['epic']]=m
        epics=list(dict.fromkeys(m['epic'] for m in held+self.watch if m.get('epic')))[:38]
        self.state['subscription_limit']=38
        self.state['unsubscribed_count']=max(0,len(set(self.market_info))-len(epics))
        if not self.subscribe or epics==self.subscribed:return
        if self.price_sub:self.stream.unsubscribe(self.price_sub)
        self.subscribed=epics
        self.price_sub=self.subscribe('price',['PRICE:'+self.client.account_id+':'+e for e in epics],
            ['TIMESTAMP','BIDPRICE1','ASKPRICE1','NET_CHG_PCT','DLG_FLAG','DELAY'],adapter='Pricing') if epics else None

    def on_status(self,status,generation):
        if generation!=self.generation:return
        with self.lock:self.state['stream']=status
        if status.startswith('CONNECTED'):self.refresh.set()

    def on_stream(self,kind,item,v,generation):
        if generation!=self.generation:return
        received=now()
        with self.lock:
            if kind=='price':
                epic=item.split(':',2)[-1];ts=number(v.get('TIMESTAMP'))
                old=self.quotes.get(epic,{})
                if ts is not None and old.get('timestamp') is not None and ts<old['timestamp']:return
                self.quotes[epic]={'bid':number(v.get('BIDPRICE1')),'ask':number(v.get('ASKPRICE1')),
                   'change':number(v.get('NET_CHG_PCT')),'timestamp':ts,'received_at':received,
                   'market_status':v.get('DLG_FLAG'),'delayed':v.get('DELAY')=='1','source':'IG PRICE stream'}
            elif kind=='account' and self.state['account']:
                a=self.state['account']
                for field,key in [('PNL','unrealized'),('FUNDS','cash'),('DEPOSIT','margin'),('AVAILABLE_CASH','available'),('EQUITY','equity')]:
                    value=number(v.get(field))
                    if value is not None:a[key]=value
                if number(v.get('EQUITY')) is None and a.get('cash') is not None and a.get('unrealized') is not None:
                    a['equity']=a['cash']+a['unrealized']
                a['source']='IG ACCOUNT stream';self.state['account_at']=received
            elif kind=='trade':
                for field,value in v.items():
                    if not value:continue
                    try:
                        record=json.loads(value)
                        if not isinstance(record,dict):continue
                        record['eventType']=field
                        self.store.event(self.scope,'stream',record,record.get('timestamp') or received)
                    except (ValueError,TypeError):pass
                self.refresh.set()
        if kind=='account':self.save_snapshot()

    def save_snapshot(self):
        if not self.scope or time.time()-self.last_saved<30:return
        with self.lock:
            a=self.state.get('account')
            if not a:return
            at=now();data={k:a.get(k) for k in ('equity','cash','unrealized','margin','available')}
            self.store.save_snapshot(self.scope,at,data);self.last_saved=time.time()
            self.state['series']=self.store.series(self.scope)

    def make_jobs(self):
        start=datetime.fromisoformat(self.creds['history_since']).replace(tzinfo=timezone.utc)
        end=datetime.now(timezone.utc);jobs=[]
        # Latest windows first; each completed window is durable. Re-fetch latest window every login.
        while end>start:
            begin=max(start,end-timedelta(days=28))
            for kind in ('transactions','activity'):
                jobs.append({'kind':kind,'from':begin.isoformat(timespec='seconds'),'to':end.isoformat(timespec='seconds'),'page':1,'next':None})
            end=begin
        return jobs

    @staticmethod
    def api_date(v):return v.replace('+00:00','')

    def history_step(self):
        if not self.history_jobs:return
        j=self.history_jobs[0];kind=j['kind'];path='/history/'+kind
        params={'from':self.api_date(j['from']),'to':self.api_date(j['to']),'pageSize':500}
        if kind=='transactions':params.update(type='ALL',pageNumber=j['page'])
        else:params['detailed']='true'
        if j['next']:
            path=j['next'];params=None
            if urlsplit(path).path!='/history/activity':raise IGError('ig.history.next.invalid')
        result=self.client.request('GET',path,2 if kind=='transactions' else 3,params=params)
        rows=result.get('transactions' if kind=='transactions' else 'activities')
        if not isinstance(rows,list):raise IGError('ig.history.response.invalid')
        for r in rows:
            at=timestamp(r.get('dateUtc') if kind=='transactions' else r.get('date'))
            self.store.event(self.scope,kind,r,at.isoformat() if at else '')
        metadata=result.get('metadata',{})
        if kind=='transactions':
            total=metadata.get('pageData',{}).get('totalPages')
            if total is None:raise IGError('ig.history.paging.missing')
            more=j['page']<total;j['page']+=1
        else:
            nxt=metadata.get('paging',{}).get('next');more=bool(nxt)
            if nxt==j['next'] and nxt:raise IGError('ig.history.paging.loop')
            j['next']=nxt
        if not more:self.history_jobs.pop(0)
        with self.lock:self.state['history'].update(status='syncing' if self.history_jobs else 'api_range_loaded',remaining_windows=len(self.history_jobs),last_update=now(),error=None)

    def funding_sync(self):
        # Use IG's explicit DEPOSIT/WITHDRAWAL filters; never guess cash-flow type from descriptions.
        baseline=timestamp(self.creds['risk_baseline_at']);end=datetime.now(timezone.utc);total=0.0;seen=set()
        for direction in ('DEPOSIT','WITHDRAWAL'):
            start=baseline
            while start<end:
                until=min(end,start+timedelta(days=28));page=1
                while True:
                    result=self.client.request('GET','/history/transactions',2,params={'type':direction,
                       'from':self.api_date(start.isoformat(timespec='seconds')),'to':self.api_date(until.isoformat(timespec='seconds')),
                       'pageSize':500,'pageNumber':page})
                    rows=result.get('transactions')
                    if not isinstance(rows,list):raise IGError('ig.funding.response.invalid')
                    for r in rows:
                        identity=(direction,json.dumps(r,sort_keys=True))
                        if identity in seen:continue
                        seen.add(identity)
                        at=timestamp(r.get('dateUtc'))
                        if not at:raise IGError('ig.funding.timestamp.unknown')
                        if at<baseline or at>=end:continue
                        value=amount(r.get('profitAndLoss'))
                        if value is None or r.get('currency')!='HKD':raise IGError('ig.funding.amount.or.currency.unknown')
                        total+=abs(value)*(1 if direction=='DEPOSIT' else -1)
                    pages=result.get('metadata',{}).get('pageData',{}).get('totalPages')
                    if pages is None:raise IGError('ig.funding.paging.missing')
                    if page>=pages:break
                    page+=1
                start=until
        self.funding=total;self.funding_at=now()

    def project(self):
        with self.lock:
            out=copy.deepcopy(self.state);out['generated_at']=now();out['markets']=[]
            for epic,m in self.market_info.items():
                q=self.quotes.get(epic)
                quote=copy.deepcopy(q) if q else {'bid':number(m.get('bid')),'ask':number(m.get('offer')),
                     'source':'IG REST snapshot','timestamp':None,'market_status':m.get('marketStatus'),'delayed':None}
                ts=quote.get('timestamp');age=(time.time()-ts/1000) if ts else None
                quote['age_seconds']=age;quote['stale']=age is None or age>30 or age<0 or not out['stream'].startswith('CONNECTED')
                out['markets'].append({'epic':epic,'name':m.get('instrumentName',epic),'expiry':m.get('expiry'),**quote})
            # Position and order projections are explicit; no broker response/token passthrough.
            out['positions']=None if out['positions'] is None else [{
              **{k:p.get('position',{}).get(k) for k in ('dealId','createdDateUTC','direction','size','level','currency','stopLevel','limitLevel','controlledRisk','contractSize')},
              **{k:p.get('market',{}).get(k) for k in ('epic','instrumentName','expiry','instrumentType')}} for p in out['positions']]
            out['orders']=None if out['orders'] is None else [{
              **{k:p.get('workingOrderData',{}).get(k) for k in ('dealId','createdDateUTC','direction','size','orderLevel','currencyCode','stopDistance','limitDistance','orderType','goodTillDate')},
              **{k:p.get('marketData',{}).get(k) for k in ('epic','instrumentName','expiry')}} for p in out['orders']]
            at=timestamp(out['account_at']);age=(datetime.now(timezone.utc)-at).total_seconds() if at else None
            out['account_age_seconds']=age;out['stale']=age is None or age>30 or out['status']!='connected'
            fat=timestamp(self.funding_at);funding_age=(datetime.now(timezone.utc)-fat).total_seconds() if fat else None
            out['funding']={'net':self.funding,'as_of':self.funding_at,'baseline_at':self.creds.get('risk_baseline_at') if self.creds else None}
            eq=(out.get('account') or {}).get('equity')
            out['risk']=evaluate_loss(eq,self.funding,age_seconds=age) if self.funding is not None and funding_age is not None and funding_age<=360 and out['status']=='connected' else {'status':'UNKNOWN','reason':'funding_or_connection_not_verified'}
            a=out.get('account') or {};margin=a.get('margin');equity=a.get('equity')
            out['margin']={'status':'UNKNOWN' if out['stale'] or equity is None or margin is None else ('PRESSURE' if margin>0 and equity<=margin else 'OBSERVED'),
              'equity_to_margin':equity/margin if margin and equity is not None else None,'broker_liquidation_rule':'not_verified'}
            out['ledger_count']=self.store.count(self.scope) if self.scope else 0
            out['alerts']=[]
            if out['risk']['status']=='TRIGGERED':out['alerts'].append('累计亏损已达到 60% 提醒线。请在 IG 核对账户；系统不会自动平仓。')
            if out['margin']['status']=='PRESSURE':out['alerts'].append('账户权益不高于已用保证金，请立即核对 IG 保证金状态。')
            return out

    def ledger(self):
        if not self.scope:return []
        rows=[]
        for e in self.store.events(self.scope):
            r=e['raw'];d=r.get('details') or {}
            rows.append({'id':e['id'],'kind':e['kind'],'at':e['at'],'reference':r.get('reference') or r.get('dealId') or r.get('dealReference'),
             'type':r.get('transactionType') or r.get('type') or r.get('eventType'),
             'name':r.get('instrumentName') or d.get('marketName') or r.get('description') or r.get('epic'),
             'amount':r.get('profitAndLoss'),'currency':r.get('currency') or d.get('currency'),
             'size':r.get('size',d.get('size')),'open':r.get('openLevel',d.get('level')),'close':r.get('closeLevel'),
             'status':r.get('status') or r.get('dealStatus'),'description':r.get('description'),
             'source_date':r.get('dateUtc') or r.get('date'),
             'actions':d.get('actions',[]),'cash':r.get('cashTransaction')})
        return rows

    def run(self):
        previous=None
        while not self.stop.wait(1):
            try:
                c=load_credentials();revision=c.get('saved_at') if c else None
                if revision!=previous:self.reset(c);previous=revision
                if not c:
                    if self.state['status']!='waiting_for_credentials':self.status('waiting_for_credentials')
                    continue
                if not self.client:
                    if time.time()>=self.retry_at:self.connect()
                    continue
                if time.time()-self.last_poll>=20 or (self.refresh.is_set() and time.time()-self.last_poll>=5):
                    self.refresh.clear();self.reconcile();self.last_poll=time.time()
                    self.status('connected','账户与持仓已同步。')
                if time.time()-self.last_funding>=300:
                    self.last_funding=time.time()
                    try:self.funding_sync()
                    except IGError as exc:
                        self.funding=None;self.state['funding_error']=exc.code
                        if exc.status==401:raise
                if self.history_jobs and time.time()-self.last_history>=2:
                    self.last_history=time.time()
                    try:self.history_step()
                    except IGError as exc:
                        self.state['history'].update(status='incomplete',error=exc.code)
                        self.last_history=time.time()+60
                        if exc.status==401:raise
                elif not self.history_jobs and time.time()-self.last_history>=300:
                    end=datetime.now(timezone.utc);begin=end-timedelta(days=2)
                    self.history_jobs=[{'kind':k,'from':begin.isoformat(timespec='seconds'),'to':end.isoformat(timespec='seconds'),'page':1,'next':None} for k in ('transactions','activity')]
            except IGError as exc:
                self.status('error',exc.code)
                if self.client is None and exc.status in (400,401,403) and 'allowance' not in exc.code:
                    # Invalid login/key/agreements require user correction; avoid repeated account lockouts.
                    self.retry_at=float('inf')
                elif exc.status==401 or 'token' in exc.code:
                    self.generation+=1
                    if self.stream:self.stream.disconnect()
                    self.client=None;self.retry_at=time.time()+60
                elif self.client is None:self.retry_at=time.time()+60
                else:self.last_poll=time.time()+40
            except Exception:
                # Never print exception text: network/SDK exceptions may include auth headers.
                self.status('error','local.collector.error');self.retry_at=time.time()+60
                self.stop.wait(5)
