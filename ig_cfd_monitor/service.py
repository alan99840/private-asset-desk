"""LaunchAgent supervisor for the public reader and private setup service."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
children={};stopping=False

def stop(*_):
 global stopping
 stopping=True
 for p in children.values():
  if p.poll() is None:p.terminate()

signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
# Prevent idle sleep while this explicitly enabled service is running.
# Lid-close, power loss and shutdown can still interrupt the monitor.
if sys.platform=='darwin':children['awake']=subprocess.Popen(['/usr/bin/caffeinate','-i','-s','-w',str(os.getpid())])
try:
 while not stopping:
  for name in ('server.py','setup_server.py'):
   p=children.get(name)
   if p is None or p.poll() is not None:
    children[name]=subprocess.Popen([sys.executable,str(ROOT/name)],cwd=ROOT)
  time.sleep(5)
finally:
 stop()
 for p in children.values():
  try:p.wait(timeout=5)
  except subprocess.TimeoutExpired:p.kill()
