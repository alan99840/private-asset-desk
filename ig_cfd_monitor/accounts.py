"""Independent IG collectors and caches; public summaries never contain credentials."""
import copy
import threading
from types import SimpleNamespace
from credentials import profile_ids, credential_status, profile_directory
from runtime import Monitor, now
from chart_history import ChartHistory, ChartError
from trade_markers import TradeMarkers
from quant import Analytics, num


def summarize(projections):
    rows = []
    for state in projections:
        profile = state['profile']
        account = state.get('account') or {}
        rows.append({**profile, 'status': state['status'], 'message': state.get('message', ''),
            'account': {k: account.get(k) for k in ('currency','equity','cash','available','margin','unrealized')},
            'stale': state.get('stale', True), 'account_at': state.get('account_at'),
            'stream': state.get('stream'), 'risk': copy.deepcopy(state.get('risk', {})),
            'funding': copy.deepcopy(state.get('funding', {})),
            'positions_count': len(state['positions']) if state.get('positions') is not None else None,
            'positions_stale': state.get('positions_stale', True), 'positions_at': state.get('positions_at'),
            'orders_stale': state.get('orders_stale', True),
            'alerts': list(state.get('alerts', []))})
    totals = []
    for environment in ('live', 'demo'):
        active = [r for r in rows if r.get('enabled') and r.get('configured')
                  and r.get('environment') == environment]
        if not active:
            continue
        ready = [r for r in active if r['status'] in ('connected','partial')
                 and not r['stale'] and r['account']['currency'] == 'HKD'
                 and all(num(r['account'].get(k)) is not None
                         for k in ('equity','cash','available','margin','unrealized'))]
        complete = len(active) == len(ready)
        verified = complete and all(r['risk'].get('status') in ('BELOW_THRESHOLD','TRIGGERED')
                                   and num(r['risk'].get('total_deposits')) is not None for r in active)
        totals.append({'environment': environment, 'currency': 'HKD', 'accounts': len(active),
            'fresh_accounts': len(ready), 'complete': complete,
            **{k: sum(r['account'][k] for r in ready) if complete else None
               for k in ('equity','cash','available','margin','unrealized')},
            'total_deposits': sum(r['risk']['total_deposits'] for r in active) if verified else None,
            'triggered_accounts': [r['id'] for r in active if r['risk'].get('status') == 'TRIGGERED'],
            'unverified_accounts': [r['id'] for r in active if r not in ready
                                   or r['risk'].get('status') not in ('BELOW_THRESHOLD','TRIGGERED')]})
    return {'generated_at': now(), 'profiles': rows, 'totals': totals,
            'basis': 'enabled_accounts_only_live_demo_separate_no_cross_account_risk_netting'}


class AccountFleet:
    def __init__(self):
        self.lock = threading.RLock()
        self.bundles = {}
        self.stop = threading.Event()
        self.feeds = SimpleNamespace(lock=threading.RLock(), stop=self.stop,
            state={'news': {'status':'loading','items':[]}, 'calendar': {}})

    def refresh(self):
        with self.lock:
            for profile_id in profile_ids():
                if profile_id in self.bundles:
                    continue
                monitor = Monitor(profile_id, feed_source=self.feeds)
                charts = ChartHistory(monitor, path=profile_directory(profile_id)/'charts.sqlite3')
                self.bundles[profile_id] = SimpleNamespace(monitor=monitor, charts=charts,
                    trades=TradeMarkers(monitor), analytics=Analytics(monitor, charts))
                monitor.start()

    def start(self):
        self.refresh()
        from news import collect_news
        from events_calendar import collect_calendar
        threading.Thread(target=collect_news,args=(self.feeds,),daemon=True,name='shared-news').start()
        threading.Thread(target=collect_calendar,args=(self.feeds,),daemon=True,name='shared-calendar').start()
        def run():
            while not self.stop.wait(2):
                try:self.refresh()
                except (OSError,ValueError):pass
        threading.Thread(target=run,daemon=True,name='account-fleet').start()

    def get(self, profile_id):
        # Unknown IDs fail closed: never route a miss to the primary account.
        with self.lock:
            bundle = self.bundles.get(profile_id)
        if bundle is None:
            raise ChartError('account_profile_not_found', 404)
        return bundle

    def overview(self):
        with self.lock:
            bundles = list(self.bundles.items())
        states = []
        for profile_id, bundle in bundles:
            state = bundle.monitor.project()
            meta = credential_status(profile_id)
            state['profile'] = {k: meta.get(k) for k in ('id','name','enabled','configured','environment')}
            states.append(state)
        return summarize(states)
