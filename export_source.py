"""Allowlist source export: private runtime files cannot enter a GitHub upload."""
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parent
FILES=['README.md','LICENSE','.gitignore','advisor.py','collect.py','context.py','evaluate.py','market.py','quote_client.py','rvol.py','server.py','publish.py','export_source.py','test_advisor.py','test_context.py','test_server.py','web/index.html','web/style.css','web/app.js','bootstrap.py','ensure_server.py']

def export(destination):
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        path=ROOT/name
        if not path.is_file():raise ValueError('Missing reviewed source '+name)
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    rules=json.loads((ROOT/'rules.json').read_text())
    rules['base_contract']='Reference trading rules; see README'
    rules['asset_context']['dashboard_access']='Authenticated loopback backend with private HTTPS remote proxy'
    # Public capital fields are illustrative, never the owner's real financial plan.
    rules['capital'].update(original_usd='10000',trading_plan_usd='5000',reserve_usd='5000',unallocated_original_usd='0',monthly_external_ETF_usd='0')
    rules['status']='Public illustrative configuration; set your own local risk parameters'
    (destination/'rules.json').write_text(json.dumps(rules,ensure_ascii=False,indent=2)+'\n')
    return list(FILES)+['rules.json']

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('destination');args=p.parse_args();print(json.dumps(export(args.destination)))
