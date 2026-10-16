"""IG transport with an explicit read-only business endpoint allowlist."""
import base64
import json
import re
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler


class IGError(Exception):
    def __init__(self, code, status=0):
        self.code = code if re.fullmatch(r'[a-zA-Z0-9_.-]{1,150}', str(code)) else 'ig.unclassified.error'
        self.status = status
        super().__init__(self.code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise IGError('ig.unexpected.redirect')


class IGClient:
    STREAM_TLS_CONFIGURED = False
    ROOTS = {'live':'https://api.ig.com/gateway/deal', 'demo':'https://demo-api.ig.com/gateway/deal'}
    READ = re.compile(r'^/(accounts|positions|workingorders|watchlists(?:/[A-Za-z0-9_-]+)?|markets(?:/[A-Za-z0-9_.-]+)?|prices/[A-Za-z0-9_.-]+|history/(?:activity|transactions)|session(?:/encryptionKey)?)$')

    def __init__(self, credentials):
        self.credentials = credentials
        self.root = self.ROOTS[credentials['environment']]
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
        self.opener = build_opener(HTTPSHandler(context=context), NoRedirect())
        self.tokens = {}
        self.lock = threading.RLock()
        self.last_request = 0
        self.account_id = None
        self.stream_endpoint = None

    @classmethod
    def validate_path(cls, method, path):
        uri = urlsplit(path)
        if uri.scheme or uri.netloc or uri.fragment or '..' in uri.path:
            raise IGError('local.endpoint.forbidden')
        if method == 'GET' and cls.READ.fullmatch(uri.path):
            return
        if method in ('POST','PUT') and uri.path == '/session' and not uri.query:
            return
        raise IGError('local.endpoint.forbidden')

    def request(self, method, path, version=1, payload=None, params=None):
        self.validate_path(method,path)
        if method == 'PUT' and set(payload or {}) != {'accountId','defaultAccount'}:
            raise IGError('local.session.write.forbidden')
        if method == 'PUT' and payload['defaultAccount'] is not False:
            raise IGError('local.default.account.change.forbidden')
        if params:
            path += ('&' if '?' in path else '?') + urlencode(params)
        with self.lock:
            # IG's per-account non-trading limit is 30 REST requests/minute.
            # Streaming quotes do not use this REST budget.
            time.sleep(max(0,2.1-(time.monotonic()-self.last_request)))
            self.last_request=time.monotonic()
            headers={'X-IG-API-KEY':self.credentials['api_key'],'Version':str(version),
                     'Accept':'application/json; charset=UTF-8','Content-Type':'application/json; charset=UTF-8',**self.tokens}
            req=Request(self.root+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers,method=method)
            try:
                with self.opener.open(req,timeout=20) as response:
                    for header in ('CST','X-SECURITY-TOKEN'):
                        if response.headers.get(header):
                            self.tokens[header]=response.headers[header]
                    raw=response.read(8_000_001)
                    if len(raw)>8_000_000:
                        raise IGError('ig.response.too.large')
                    return json.loads(raw) if raw else {}
            except HTTPError as exc:
                try:
                    code=json.loads(exc.read(4000)).get('errorCode',f'ig.http.{exc.code}')
                except (ValueError,AttributeError):
                    code=f'ig.http.{exc.code}'
                raise IGError(code,exc.code) from None
            except (URLError,TimeoutError,ConnectionError,OSError):
                raise IGError('ig.network.or.tls.error') from None
            except ValueError:
                raise IGError('ig.invalid.json') from None

    def login(self):
        body={'identifier':self.credentials['identifier'],'password':self.credentials['password'],'encryptedPassword':False}
        try:
            result=self.request('POST','/session',2,body)
        except IGError as exc:
            if exc.code != 'error.public-api.failure.encryption.required':
                raise
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            key=self.request('GET','/session/encryptionKey')
            public=serialization.load_der_public_key(base64.b64decode(key['encryptionKey']))
            value=base64.b64encode(f"{self.credentials['password']}|{key['timeStamp']}".encode())
            body['password']=base64.b64encode(public.encrypt(value,padding.PKCS1v15())).decode()
            body['encryptedPassword']=True
            result=self.request('POST','/session',2,body)
        self.account_id=result.get('currentAccountId')
        self.stream_endpoint=result.get('lightstreamerEndpoint')
        accounts=self.request('GET','/accounts').get('accounts',[])
        candidates=[a for a in accounts if a.get('accountType')=='CFD' and a.get('currency')=='HKD' and a.get('status')=='ENABLED']
        wanted=self.credentials.get('account_id')
        chosen=next((a for a in candidates if a['accountId']==wanted),None) if wanted else (candidates[0] if len(candidates)==1 else None)
        if chosen is None:
            return None,accounts
        if chosen['accountId']!=self.account_id:
            self.request('PUT','/session',1,{'accountId':chosen['accountId'],'defaultAccount':False})
            self.account_id=chosen['accountId']
        if not all(self.tokens.get(k) for k in ('CST','X-SECURITY-TOKEN')):
            raise IGError('ig.session.tokens.missing')
        return chosen,accounts

    def make_stream(self, callback, status_callback):
        from lightstreamer.client import LightstreamerClient, Subscription, SubscriptionListener, ClientListener
        endpoint=urlsplit(self.stream_endpoint or '')
        allowed = ('ig.com', 'marketdatasystems.com')
        if endpoint.scheme!='https' or not endpoint.hostname or not any(endpoint.hostname == d or endpoint.hostname.endswith('.'+d) for d in allowed):
            raise IGError('ig.stream.endpoint.invalid')
        if not IGClient.STREAM_TLS_CONFIGURED:
            import certifi
            LightstreamerClient.setTrustManagerFactory(ssl.create_default_context(cafile=certifi.where()))
            IGClient.STREAM_TLS_CONFIGURED=True
        client=LightstreamerClient(self.stream_endpoint,None)
        client.connectionDetails.setUser(self.account_id)
        client.connectionDetails.setPassword('CST-'+self.tokens['CST']+'|XST-'+self.tokens['X-SECURITY-TOKEN'])
        client.connectionOptions.setStalledTimeout(10000)
        class ClientEvents(ClientListener):
            def onStatusChange(self,status):
                status_callback(status)
            def onServerError(self,code,message):
                status_callback('ERROR_'+str(code))
        class DataEvents(SubscriptionListener):
            def __init__(self,fields,kind):
                self.fields,self.kind=fields,kind
            def onItemUpdate(self,update):
                callback(self.kind,update.getItemName(),{f:update.getValue(f) for f in self.fields})
            def onSubscriptionError(self,code,message):
                status_callback('SUBSCRIPTION_ERROR_'+str(code))
        client.addListener(ClientEvents())
        subscriptions=[]
        def subscribe(kind,items,fields,mode='MERGE',adapter=None):
            sub=Subscription(mode,items,fields)
            if adapter:sub.setDataAdapter(adapter)
            sub.setRequestedSnapshot('yes')
            sub.addListener(DataEvents(fields,kind))
            client.subscribe(sub);subscriptions.append(sub)
            return sub
        subscribe('account',['ACCOUNT:'+self.account_id],['PNL','DEPOSIT','AVAILABLE_CASH','FUNDS','MARGIN','AVAILABLE_TO_DEAL','EQUITY','EQUITY_USED'])
        subscribe('trade',['TRADE:'+self.account_id],['CONFIRMS','OPU','WOU'],'DISTINCT')
        client.connect()
        return client,subscribe,subscriptions
