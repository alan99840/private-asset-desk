"""Restart the private local service from an authorized local app runtime if needed."""
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
OPENER = build_opener(ProxyHandler({}))

def healthy():
    try:
        with OPENER.open(Request('http://127.0.0.1:8765/health'), timeout=2) as response:
            data = json.load(response)
            return data.get('status') == 'OK' and data.get('auth_required') is True
    except (OSError, ValueError):
        return False

def ensure():
    if healthy():
        return {'status': 'RUNNING', 'authentication_required': True}
    folder = HERE/'state'; folder.mkdir(parents=True, exist_ok=True)
    with (folder/'startup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'START_IN_PROGRESS'}
        if healthy():
            return {'status': 'RUNNING', 'authentication_required': True}
        with (folder/'server.log').open('a') as log:
            process = subprocess.Popen([sys.executable, str(HERE/'server.py')], cwd=HERE,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(20):
            if healthy():
                return {'status': 'RESTARTED', 'pid': process.pid, 'authentication_required': True}
            if process.poll() is not None:
                break
            time.sleep(0.1)
        return {'status': 'START_FAILED', 'pid': process.pid}

if __name__ == '__main__':
    print(json.dumps(ensure()))
