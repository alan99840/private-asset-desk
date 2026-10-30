"""Read-only IG collector. Secrets never enter the browser projection."""
import copy
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from credentials import load_credentials, private_json, STATE, profile_directory
from ig_client import IGClient, IGError
from risk import evaluate_loss
from cashflows import money, currency, classify, ledger_summary
from storage import Store
from position_summary import group_positions


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
      'sync':{}, 'market_specs':{}, 'risk':{'status':'UNKNOWN'},'series':[],'news':{'status':'loading','items':[]},'alerts':[],
      'notification':'web_only','initial_capital':30000,'loss_alert_fraction':.60}


class Monitor:
    STREAM_RETRY_GRACE=30
    STREAM_RETRY_CAP=300

    def __init__(self, profile_id='default', feed_source=None):
        self.profile_id=profile_id;self.feed_source=feed_source
        directory=profile_directory(profile_id)
        if profile_id!='default':directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.lock=threading.RLock();self.state=base_state()
        self.store=Store() if profile_id=='default' else Store(directory/'monitor.sqlite3')
        self.client=None;self.stream=None;self.subscribe=None;self.price_sub=None
        self.scope=None;self.creds=None;self.generation=0;self.quotes={};self.market_info={}
        self.watch=[];self.subscribed=[];self.refresh=threading.Event();self.stop=threading.Event()
        self.funding=None;self.funding_at=None;self.history_jobs=[];self.last_saved=0
        self.retry_at=0;self.last_poll=0;self.last_funding=0;self.last_history=0
        self.funding_jobs=[];self.funding_rows={};self.funding_details={};self.last_metadata=0
        self.cost_summary={};self.market_specs={}
        self.clear_stream_retry()

    def clear_stream_retry(self):
        self.stream_retry_at=0;self.stream_failures=0;self.stream_retry_pending=False
        self.stream_fault=None

    def detach_stream_locked(self):
        # Invalidate callbacks before disconnecting: the SDK may emit its final
        # DISCONNECTED (or queued data) after a replacement has already started.
        self.generation+=1;stream=self.stream
        self.stream=self.subscribe=self.price_sub=None;self.subscribed=[]
        return stream

    @staticmethod
    def close_stream(stream):
        if stream:
            try:stream.disconnect()
            except Exception:pass

    def disconnect_stream(self):
        with self.lock:
            stream=self.detach_stream_locked()
        self.close_stream(stream)

    @staticmethod
    def stream_terminal(status):
        return status=='DISCONNECTED' or status.startswith(('ERROR_','SUBSCRIPTION_ERROR_'))

    def schedule_stream_retry(self,code=None):
        with self.lock:
            if code:self.state['stream']=code;self.stream_fault=code
            if self.stream_retry_pending:return
            delay=min(self.STREAM_RETRY_GRACE*2**min(self.stream_failures,4),self.STREAM_RETRY_CAP)
            self.stream_failures=min(self.stream_failures+1,5)
            self.stream_retry_at=time.monotonic()+delay;self.stream_retry_pending=True

    @staticmethod
    def stream_sdk_exception(exc):
        # The SDK also raises plain Exception. Application programming errors
        # still reach the collector's existing safe error handler, with a retry
        # already scheduled, instead of silently masquerading as network errors.
        return (isinstance(exc,(OSError,TimeoutError,ConnectionError)) or type(exc) is Exception
          or type(exc).__module__.startswith('lightstreamer.'))

    def start_stream(self,recovery_generation=None):
        with self.lock:
            if not self.client:return
            if recovery_generation is not None and (self.generation!=recovery_generation or
              not self.stream_retry_pending or time.monotonic()<self.stream_retry_at or
              (self.stream is not None and not self.stream_terminal(self.state['stream']))):return
            # Recovery's status/generation check and invalidation are atomic.
            # In particular, do not replace an SDK that resumed after the
            # watchdog decided it was due but before this lock was acquired.
            stream=self.detach_stream_locked()
            generation=self.generation;self.stream_retry_pending=False;self.stream_retry_at=0
            self.stream_fault=None;self.state['stream']='CONNECTING'
        self.close_stream(stream)
        try:
            self.stream,self.subscribe,_=self.client.make_stream(
                lambda k,i,v:self.on_stream(k,i,v,generation),lambda s:self.on_status(s,generation))
            self.update_subscriptions()
        except IGError as exc:
            self.disconnect_stream()
            self.schedule_stream_retry(exc.code)
            if exc.status==401 or 'token' in exc.code:raise
        except Exception as exc:
            self.disconnect_stream()
            self.schedule_stream_retry('ERROR_LOCAL')
            if not self.stream_sdk_exception(exc):raise

    def recover_stream(self):
        if not self.client:return
        with self.lock:
            # CONNECTING, STALLED and DISCONNECTED:WILL-RETRY belong to the SDK.
            # Only a missing stream or a terminal failure permits replacement.
            if self.stream is not None and not self.stream_terminal(self.state['stream']):return
            self.schedule_stream_retry()
            due=time.monotonic()>=self.stream_retry_at
            generation=self.generation
        if due:self.start_stream(recovery_generation=generation)

    def start(self):
        self.status('waiting_for_credentials','请在本机配置页填写 IG API 信息。')
        threading.Thread(target=self.run,daemon=True,name='ig-collector').start()
        if self.feed_source is not None:return
        from news import collect_news
        threading.Thread(target=collect_news,args=(self,),daemon=True,name='official-news').start()
        from events_calendar import collect_calendar
        threading.Thread(target=collect_calendar,args=(self,),daemon=True,name='official-calendar').start()

    def status(self,status,message='',accounts=None):
        with self.lock:self.state.update(status=status,message=message)
        private_json(profile_directory(self.profile_id)/'connection_status.json',{'status':status,'message':message,'at':now(),
          'accounts':[{k:a.get(k) for k in ('accountId','accountType','currency','status')} for a in accounts or []]})

    def reset(self,creds):
        self.disconnect_stream()
        with self.lock:
            news=self.state['news'];calendar=self.state.get('calendar',{});self.state=base_state();self.state['news']=news;self.state['calendar']=calendar
            self.client=self.stream=self.subscribe=self.price_sub=None;self.scope=None
            self.creds=creds;self.quotes={};self.market_info={};self.watch=[];self.subscribed=[]
            self.funding=None;self.funding_at=None;self.history_jobs=[]
            self.retry_at=self.last_poll=self.last_funding=self.last_history=0
            self.funding_jobs=[];self.funding_rows={};self.funding_details={};self.last_metadata=0
            self.cost_summary={};self.market_specs={}
            self.clear_stream_retry()

    def connect(self):
        self.status('connecting','正在验证 Live/Demo 环境、CFD 类型和 HKD 账户。')
        client=IGClient(self.creds);chosen,accounts=client.login()
        if chosen is None:
            self.status('account_selection_required','请选择启用状态的 HKD CFD 账户编号。',accounts)
            self.retry_at=time.time()+3600;return
        self.client=client;self.scope=self.creds['environment']+':'+client.account_id
        # Reconcile can fail after login. A valid REST session must still get a
        # stream attempt without requiring another username/password login.
        self.stream_retry_at=time.monotonic()+self.STREAM_RETRY_GRACE;self.stream_retry_pending=True
        self.state['environment']=self.creds['environment']
        self.state['series']=self.store.daily_series(self.scope)
        self.account(chosen)
        saved=self.store.get(self.scope,'gross_deposits_v2',{})
        if saved.get('since')==self.creds['history_since']:
            self.funding_details=saved;self.funding=saved.get('total_deposits');self.funding_at=saved.get('as_of')
            self.funding_rows=saved.get('rows',{})
        self.market_specs=self.store.get(self.scope,'market_specs_v1',{})
        self.cost_summary=ledger_summary(self.store.events(self.scope,'transactions'))
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
        self.start_stream()
        self.last_poll=time.time()

    def account(self,a):
        b=a.get('balance',{});cash=number(b.get('balance'));pnl=number(b.get('profitLoss'))
        with self.lock:
            self.state['account']={'name':a.get('accountName','CFD'),'currency':a.get('currency'),'type':a.get('accountType'),
              'cash':cash,'unrealized':pnl,'equity':cash+pnl if cash is not None and pnl is not None else None,
              'margin':number(b.get('deposit')),'available':number(b.get('available')),'source':'IG REST /accounts'}
            self.state['account_at']=now();self.state['account_rest_at']=self.state['account_at']
        self.save_snapshot()

    def reconcile(self):
        # Each component commits independently; a failed working-orders request
        # must not discard successful account or position reconciliation.
        errors = {}
        for component, path, version, key in (
            ('account','/accounts',1,'accounts'),
            ('positions','/positions',2,'positions'),
            ('orders','/workingorders',2,'workingOrders')):
            try:
                rows = self.client.request('GET',path,version).get(key)
                if not isinstance(rows,list):raise IGError('ig.invalid.'+component+'.response')
                if component == 'account':
                    selected = next((a for a in rows if a.get('accountId')==self.client.account_id),None)
                    if not selected:raise IGError('ig.selected.account.missing')
                    self.account(selected)
                else:
                    with self.lock:
                        self.state[component]=rows;self.state[component+'_at']=now()
                    if component == 'positions':
                        for p in rows:
                            m=p.get('market',{});epic=m.get('epic')
                            if epic:self.market_info[epic]=m
                with self.lock:self.state['sync'][component]={'status':'ok','at':now(),'error':None}
            except IGError as exc:
                with self.lock:
                    old=self.state['sync'].get(component,{})
                    self.state['sync'][component]={**old,'status':'retrying','error':exc.code,'attempt_at':now()}
                errors[component]=exc.code
                if exc.status==401 or 'token' in exc.code:raise
        self.update_subscriptions();self.save_snapshot()
        if errors:self.status('partial','部分查询正在重试；各项数据按自己的更新时间核验。')
        else:self.status('connected','账户与持仓已同步。')

    def metadata_step(self):
        held=list(dict.fromkeys(p.get('market',{}).get('epic') for p in self.state['positions'] or []))
        targets=[e for e in held if e]+[m.get('epic') for m in self.watch if m.get('epic') not in held]
        for epic in targets:
            old=self.market_specs.get(epic,{})
            if time.time()-old.get('fetched_epoch',0)<3600 or time.time()<old.get('retry_at',0):continue
            try:
                result=self.client.request('GET','/markets/'+epic,3)
                instrument=result.get('instrument');snapshot=result.get('snapshot')
                if not isinstance(instrument,dict) or instrument.get('epic')!=epic:raise IGError('ig.market.spec.invalid')
                with self.lock:
                    self.market_specs[epic]={'instrument':instrument,'snapshot':snapshot or {},
                        'dealingRules':result.get('dealingRules',{}),'as_of':now(),'fetched_epoch':time.time()}
                    self.store.put(self.scope,'market_specs_v1',self.market_specs)
            except IGError as exc:
                with self.lock:self.market_specs[epic]={**old,'error':exc.code,'retry_at':time.time()+120}
                if exc.status==401:raise
            break

    def update_subscriptions(self):
        held=[p.get('market',{}) for p in self.state['positions'] or []]
        for m in self.watch:
            if m.get('epic') and m['epic'] not in self.market_info:self.market_info[m['epic']]=m
        epics=list(dict.fromkeys(m['epic'] for m in held+self.watch if m.get('epic')))[:38]
        self.state['subscription_limit']=38
        self.state['unsubscribed_count']=max(0,len(set(self.market_info))-len(epics))
        if not self.subscribe or epics==self.subscribed:return
        if self.stream_retry_pending and self.stream_terminal(self.state['stream']):return
        try:
            if self.price_sub:self.stream.unsubscribe(self.price_sub)
            self.price_sub=None;self.subscribed=[]
            self.price_sub=self.subscribe('price',['PRICE:'+self.client.account_id+':'+e for e in epics],
                ['TIMESTAMP','BIDPRICE1','ASKPRICE1','NET_CHG_PCT','DLG_FLAG','DELAY'],adapter='Pricing') if epics else None
            self.subscribed=epics
        except IGError as exc:
            self.schedule_stream_retry('SUBSCRIPTION_ERROR_LOCAL')
            if exc.status==401 or 'token' in exc.code:raise
        except Exception as exc:
            self.schedule_stream_retry('SUBSCRIPTION_ERROR_LOCAL')
            if not self.stream_sdk_exception(exc):raise

    def on_status(self,status,generation):
        with self.lock:
            if generation!=self.generation:return
            if self.stream_terminal(status):self.schedule_stream_retry(status)
            elif status.startswith('CONNECTED'):
                # A healthy connection does not repair a rejected subscription.
                if self.stream_retry_pending and (self.stream_fault or '').startswith('SUBSCRIPTION_ERROR_'):
                    self.state['stream']=self.stream_fault
                else:self.state['stream']=status;self.clear_stream_retry()
            else:
                self.state['stream']=status
                if not (self.stream_fault or '').startswith('SUBSCRIPTION_ERROR_'):
                    self.stream_retry_pending=False;self.stream_retry_at=0;self.stream_fault=None
        if status.startswith('CONNECTED'):self.refresh.set()

    def on_stream(self,kind,item,v,generation):
        received=now()
        with self.lock:
            if generation!=self.generation:return
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
            source_at=timestamp(self.state.get('account_at'))
            if not source_at or (datetime.now(timezone.utc)-source_at).total_seconds()>30:return
            at=self.state['account_at'];data={k:a.get(k) for k in ('equity','cash','unrealized','margin','available')}
            self.store.save_snapshot(self.scope,at,data);self.last_saved=time.time()
            self.state['series']=self.store.daily_series(self.scope)

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
        if kind=='transactions':self.cost_summary=ledger_summary(self.store.events(self.scope,'transactions'))
        with self.lock:self.state['history'].update(status='syncing' if self.history_jobs else 'api_range_loaded',remaining_windows=len(self.history_jobs),last_update=now(),error=None)

    def funding_sync(self):
        """One bounded page per call, retaining coverage before publishing totals.

        Gross deposits come from explicit DEPO records. WITH interest never
        reduces the denominator. Cache permits incremental refresh after restart.
        """
        end=datetime.now(timezone.utc)
        if not self.funding_jobs:
            since=self.creds.get('history_since')
            start=timestamp(since+'T00:00:00Z') if since else timestamp(self.creds['risk_baseline_at'])
            if self.funding_details.get('since')==since and self.funding_details.get('as_of'):
                start=max(start,timestamp(self.funding_details['as_of'])-timedelta(days=2))
            while start<end:
                until=min(end,start+timedelta(days=28))
                self.funding_jobs.append({'from':start,'to':until,'page':1})
                start=until
            self.funding_target=end.isoformat()
        if not self.funding_jobs:return
        job=self.funding_jobs[0]
        result=self.client.request('GET','/history/transactions',2,params={'type':'DEPOSIT',
            'from':self.api_date(job['from'].isoformat(timespec='seconds')),
            'to':self.api_date(job['to'].isoformat(timespec='seconds')),'pageSize':500,'pageNumber':job['page']})
        rows=result.get('transactions');pages=result.get('metadata',{}).get('pageData',{}).get('totalPages')
        if not isinstance(rows,list) or not isinstance(pages,int) or pages<0:raise IGError('ig.funding.response.invalid')
        for row in rows:
            if classify(row)!='deposit':continue
            at=timestamp(row.get('dateUtc'))
            if not at:raise IGError('ig.funding.timestamp.unknown')
            if not job['from']<=at<job['to']:continue
            value=money(row.get('profitAndLoss'),row.get('currency'))
            if currency(row.get('currency'))!='HKD' or value is None or value<=0:
                raise IGError('ig.funding.amount.or.currency.unknown')
            identity=str(row.get('reference') or json.dumps(row,sort_keys=True))
            normalized={'at':at.isoformat(),'amount':value,'currency':'HKD'}
            previous=self.funding_rows.get(identity)
            if previous and previous!=normalized:raise IGError('ig.funding.conflicting.reference')
            self.funding_rows[identity]=normalized
            self.store.event(self.scope,'transactions',row,at.isoformat())
        if job['page']>=pages:self.funding_jobs.pop(0)
        else:job['page']+=1
        if not self.funding_jobs:
            total=round(sum(r['amount'] for r in self.funding_rows.values()),8)
            with self.lock:
                self.funding=total;self.funding_at=self.funding_target
                self.funding_details={'total_deposits':total,'as_of':self.funding_at,
                    'since':self.creds.get('history_since'),'deposit_count':len(self.funding_rows),
                    'rows':self.funding_rows,'basis':'IG_DEPO_gross_deposits'}
                self.store.put(self.scope,'gross_deposits_v2',self.funding_details)
                self.state.pop('funding_error',None)
            if (datetime.now(timezone.utc)-timestamp(self.funding_at)).total_seconds()>60:self.last_funding=0
            self.cost_summary=ledger_summary(self.store.events(self.scope,'transactions'))

    def project(self):
        if self.feed_source is not None:
            with self.feed_source.lock:
                feeds=copy.deepcopy(self.feed_source.state)
            with self.lock:self.state.update(feeds)
        with self.lock:
            out=copy.deepcopy(self.state);out['generated_at']=now();out['markets']=[]
            out['profile']={'id':self.profile_id,
                'name':(self.creds or {}).get('name','主账户' if self.profile_id=='default' else 'IG 账户'),
                'enabled':(self.creds or {}).get('enabled',True),'configured':bool(self.creds),
                'environment':(self.creds or {}).get('environment')}
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
            out['position_groups']=group_positions(out['positions'])
            out['orders']=None if out['orders'] is None else [{
              **{k:p.get('workingOrderData',{}).get(k) for k in ('dealId','createdDateUTC','direction','size','orderLevel','currencyCode','stopDistance','limitDistance','orderType','goodTillDate')},
              **{k:p.get('marketData',{}).get(k) for k in ('epic','instrumentName','expiry')}} for p in out['orders']]
            at=timestamp(out['account_at']);age=(datetime.now(timezone.utc)-at).total_seconds() if at else None
            out['account_age_seconds']=age;out['stale']=age is None or age<0 or age>30
            # ACCOUNT streaming does not confirm the current position/order book.
            # Keep these clocks independent even when equity is still updating.
            for component in ('positions','orders'):
                component_at=timestamp(out.get(component+'_at'))
                component_age=(datetime.now(timezone.utc)-component_at).total_seconds() if component_at else None
                out[component+'_age_seconds']=component_age
                out[component+'_stale']=(out.get(component) is None or component_age is None or
                    not 0<=component_age<=90 or out.get('sync',{}).get(component,{}).get('status')!='ok')
            fat=timestamp(self.funding_at);funding_age=(datetime.now(timezone.utc)-fat).total_seconds() if fat else None
            out['funding']={'total_deposits':self.funding,'as_of':self.funding_at,
                'since':self.creds.get('history_since') if self.creds else None,
                'deposit_count':len(self.funding_rows),'pending_windows':len(self.funding_jobs),
                'basis':'IG_DEPO_gross_deposits'}
            out['costs']=copy.deepcopy(self.cost_summary)
            from events_calendar import relevant, upcoming
            calendar=out.get('calendar',{})
            calendar_now=datetime.now(timezone.utc)
            calendar['items']=[{**e,'related':relevant(e,out['markets'])} for e in calendar.get('items',[]) if upcoming(e,calendar_now)][:30]
            out['calendar']=calendar
            out['market_specs']=copy.deepcopy(self.market_specs)
            eq=(out.get('account') or {}).get('equity')
            out['risk']=evaluate_loss(eq,self.funding,age_seconds=age) if self.funding is not None and funding_age is not None and 0<=funding_age<=360 else {'status':'UNKNOWN','reason':'funding_or_connection_not_verified'}
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
                c=load_credentials(self.profile_id);revision=c.get('saved_at') if c else None
                if revision!=previous:self.reset(c);previous=revision
                if not c:
                    if self.state['status']!='waiting_for_credentials':self.status('waiting_for_credentials')
                    continue
                if not c.get('enabled',True):
                    if self.state['status']!='paused':self.status('paused','此账户已在本机暂停监控。')
                    continue
                if not self.client:
                    if time.time()>=self.retry_at:self.connect()
                    continue
                self.recover_stream()
                if time.time()-self.last_poll>=20 or (self.refresh.is_set() and time.time()-self.last_poll>=5):
                    self.refresh.clear();self.last_poll=time.time();self.reconcile()
                if (self.funding_jobs and time.time()-self.last_funding>=3) or time.time()-self.last_funding>=240:
                    self.last_funding=time.time()
                    try:self.funding_sync()
                    except IGError as exc:
                        self.state['funding_error']=exc.code;self.last_funding=time.time()+25
                        if exc.status==401:raise
                if time.time()-self.last_metadata>=20:
                    self.last_metadata=time.time();self.metadata_step()
                if self.history_jobs and time.time()-self.last_history>=5:
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
                    self.disconnect_stream();self.clear_stream_retry()
                    self.client=None;self.retry_at=time.time()+60
                elif self.client is None:self.retry_at=time.time()+60
                else:self.last_poll=time.time()-10
            except Exception:
                # Never print exception text: network/SDK exceptions may include auth headers.
                self.status('error','local.collector.error');self.retry_at=time.time()+60
                self.stop.wait(5)
