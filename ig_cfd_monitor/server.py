"""Public read-only dashboard. Only this loopback port may be Funnel-proxied."""
import argparse
import csv
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from runtime import Monitor

ROOT=Path(__file__).resolve().parent
ASSETS={'/':('index.html','text/html; charset=utf-8'),'/index.html':('index.html','text/html; charset=utf-8'),
 '/style.css':('style.css','text/css; charset=utf-8'),'/live.js':('live.js','text/javascript; charset=utf-8'),
 '/favicon.svg':('favicon.svg','image/svg+xml')}
SLOTS=threading.BoundedSemaphore(32)
MONITOR=None


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def headers_for(self,mime,status=200,length=None):
        self.send_response(status);self.send_header('Content-Type',mime)
        if length is not None:self.send_header('Content-Length',str(length))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Robots-Tag','noindex, nofollow, noarchive')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'none'")
        self.end_headers()
    def reply(self,payload,status=200,mime='application/json; charset=utf-8'):
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode() if isinstance(payload,(dict,list)) else payload
        self.headers_for(mime,status,len(body))
        if self.command!='HEAD':self.wfile.write(body)
    def do_GET(self):
        uri=urlsplit(self.path)
        try:
            if uri.path in ASSETS:
                filename,mime=ASSETS[uri.path];self.reply((ROOT/'web'/filename).read_bytes(),mime=mime)
            elif uri.path=='/api/state':self.reply(MONITOR.project())
            elif uri.path=='/api/ledger':
                query=parse_qs(uri.query);page=max(1,min(100000,int(query.get('page',['1'])[0])))
                rows=MONITOR.ledger();kind=query.get('kind',['all'])[0]
                if kind!='all':rows=[r for r in rows if r['kind']==kind]
                self.reply({'rows':rows[(page-1)*50:page*50],'page':page,'total':len(rows),'page_size':50,'history':MONITOR.project()['history']})
            elif uri.path=='/api/ledger.csv':
                stream=io.StringIO();fields=['kind','at','reference','type','name','amount','currency','size','open','close','status','description']
                writer=csv.writer(stream);writer.writerow(fields)
                for row in MONITOR.ledger():
                    cells=[]
                    for key in fields:
                        v=str(row.get(key) or '')
                        if v[:1] in '=+-@\t\r':v="'"+v
                        cells.append(v)
                    writer.writerow(cells)
                self.reply(('\ufeff'+stream.getvalue()).encode(),mime='text/csv; charset=utf-8')
            elif uri.path=='/api/events' and self.command!='HEAD':self.events()
            else:self.reply({'error':'not_found'},404)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        except ValueError:self.reply({'error':'invalid_query'},400)
    do_HEAD=do_GET
    def events(self):
        if not SLOTS.acquire(blocking=False):self.reply({'error':'capacity'},503);return
        try:
            self.connection.settimeout(15);self.headers_for('text/event-stream; charset=utf-8')
            # IG ticks are projected to browsers at up to 4 Hz. No REST requests per viewer.
            end=time.monotonic()+55;last='';last_heartbeat=0
            while time.monotonic()<end:
                out=MONITOR.project()
                # Age fields make payload change; a 1 Hz heartbeat handles quiet markets.
                comparison=json.dumps({k:v for k,v in out.items() if k not in ('generated_at','account_age_seconds','markets')},sort_keys=True)
                comparison+=json.dumps([{k:v for k,v in q.items() if k!='age_seconds'} for q in out['markets']],sort_keys=True)
                if comparison!=last or time.monotonic()-last_heartbeat>=1:
                    self.wfile.write(('data: '+json.dumps(out,ensure_ascii=False,allow_nan=False)+'\n\n').encode());self.wfile.flush()
                    last=comparison;last_heartbeat=time.monotonic()
                time.sleep(.25)
        finally:SLOTS.release()
    def reject(self):self.reply({'error':'read_only'},405)
    do_POST=do_PUT=do_PATCH=do_DELETE=do_OPTIONS=reject


def main():
    global MONITOR
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8786);args=parser.parse_args()
    MONITOR=Monitor();MONITOR.start()
    print(f'Harbour read-only monitor: http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

if __name__=='__main__':main()
