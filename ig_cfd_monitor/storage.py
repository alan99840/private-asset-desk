"""Private SQLite storage with account/environment separation and event dedup."""
import hashlib
import json
import os
import sqlite3
import threading
from credentials import STATE, prepare_state


class Store:
    def __init__(self,path=None):
        prepare_state()
        self.path=path or STATE/'monitor.sqlite3'
        self.lock=threading.RLock()
        self.db=sqlite3.connect(self.path,check_same_thread=False)
        if str(self.path)!=':memory:':os.chmod(self.path,0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS events (scope TEXT, kind TEXT, event_key TEXT, at TEXT, payload TEXT, PRIMARY KEY(scope,kind,event_key));
          CREATE TABLE IF NOT EXISTS snapshots (scope TEXT, at TEXT, payload TEXT, PRIMARY KEY(scope,at));
          CREATE TABLE IF NOT EXISTS metadata (scope TEXT, key TEXT, payload TEXT, PRIMARY KEY(scope,key));
        ''')
        self.db.commit()

    def event(self,scope,kind,data,at):
        encoded=json.dumps(data,sort_keys=True,ensure_ascii=False)
        key=hashlib.sha256(encoded.encode()).hexdigest()
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',(scope,kind,key,at,encoded))
        return key

    def events(self,scope,kind=None):
        with self.lock:
            rows=self.db.execute('SELECT kind,event_key,at,payload FROM events WHERE scope=?'+(' AND kind=?' if kind else '')+' ORDER BY at DESC',(scope,kind) if kind else (scope,)).fetchall()
        return [{'kind':k,'id':key,'at':at,'raw':json.loads(p)} for k,key,at,p in rows]

    def count(self,scope):
        with self.lock:return self.db.execute('SELECT COUNT(*) FROM events WHERE scope=?',(scope,)).fetchone()[0]

    def save_snapshot(self,scope,at,data):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?)',(scope,at,json.dumps(data)))

    def series(self,scope,limit=2000):
        with self.lock:
            rows=self.db.execute('SELECT at,payload FROM snapshots WHERE scope=? ORDER BY at DESC LIMIT ?',(scope,limit)).fetchall()
        return [{'at':at,**json.loads(p)} for at,p in reversed(rows)]

    def put(self,scope,key,payload):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?,?)',(scope,key,json.dumps(payload)))

    def get(self,scope,key,default=None):
        with self.lock:row=self.db.execute('SELECT payload FROM metadata WHERE scope=? AND key=?',(scope,key)).fetchone()
        return json.loads(row[0]) if row else default
