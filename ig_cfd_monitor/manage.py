"""Install/status/stop a current-user Mac service in Application Support."""
import argparse
import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
LABEL='com.harbour.ig-monitor'
RUNTIME=Path.home()/'Library/Application Support/HarbourCFD'


def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['install','status','stop']);args=p.parse_args()
 domain=f'gui/{os.getuid()}';target=domain+'/'+LABEL
 if args.action=='status':
  subprocess.run(['launchctl','print',target]);return
 if args.action=='stop':
  subprocess.run(['launchctl','bootout',target],check=True);return
 if not (ROOT/'.venv/bin/python').exists():raise SystemExit('First create .venv and install requirements.txt.')
 existing=subprocess.run(['launchctl','print',target],capture_output=True)
 if existing.returncode==0:subprocess.run(['launchctl','bootout',target],check=True)
 RUNTIME.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(RUNTIME,0o700)
 if ROOT!=RUNTIME:
  for name in ('credentials.py','ig_client.py','runtime.py','storage.py','cashflows.py','position_summary.py','quant.py','events_calendar.py','calendar_seed.json','risk.py','news.py','server.py','setup_server.py','service.py','requirements.txt','chart_history.py','trade_markers.py'):
   shutil.copy2(ROOT/name,RUNTIME/name)
  for name in ('web','setup'):
   shutil.copytree(ROOT/name,RUNTIME/name,dirs_exist_ok=True,symlinks=True)
  if not (RUNTIME/'.venv').exists():shutil.copytree(ROOT/'.venv',RUNTIME/'.venv',symlinks=True)
  subprocess.run([str(RUNTIME/'.venv/bin/python'),'-m','pip','install','--disable-pip-version-check','-q','-r',str(ROOT/'requirements.txt')],check=True)
  # One-time private-state migration only. Subsequent source updates never overwrite live credentials/data.
  if not (RUNTIME/'state').exists() and (ROOT/'state').exists():shutil.copytree(ROOT/'state',RUNTIME/'state')
 state=RUNTIME/'state';state.mkdir(mode=0o700,exist_ok=True);os.chmod(state,0o700)
 for file in state.iterdir():
  if file.is_file():os.chmod(file,0o600)
 folder=Path.home()/'Library/LaunchAgents';folder.mkdir(exist_ok=True);plist=folder/(LABEL+'.plist')
 document={'Label':LABEL,'ProgramArguments':[str(RUNTIME/'.venv/bin/python'),str(RUNTIME/'service.py')],
  'WorkingDirectory':str(RUNTIME),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'ProcessType':'Background',
  'StandardOutPath':str(state/'service.log'),'StandardErrorPath':str(state/'service-error.log'),
  'EnvironmentVariables':{'PYTHONUNBUFFERED':'1','PATH':'/usr/bin:/bin:/usr/sbin:/sbin'}}
 plist.write_bytes(plistlib.dumps(document));os.chmod(plist,0o600)
 for name in ('service.log','service-error.log'):
  path=state/name;path.touch(mode=0o600,exist_ok=True);os.chmod(path,0o600)
 for attempt in range(10):
  result=subprocess.run(['launchctl','bootstrap',domain,str(plist)],capture_output=True)
  if result.returncode==0:break
  time.sleep(1)
 else:raise SystemExit('LaunchAgent bootstrap failed; inspect launchctl print for '+target)
 print('Installed:',LABEL,'at',RUNTIME)

if __name__=='__main__':main()
