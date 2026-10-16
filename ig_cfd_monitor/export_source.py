"""Export only reviewed source files. Never scan or read state/ or credentials."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def export():
    names=json.loads((ROOT/'source_manifest.json').read_text())
    result=[]
    for name in names:
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or any(p in ('state','.venv','qa','raw','logs') for p in path.parts):
            raise ValueError('Forbidden source path')
        source=ROOT/path
        if source.is_symlink():raise ValueError('Source symlinks are not exported')
        content=source.read_text()
        result.append({'path':'ig_cfd_monitor/'+name,'mode':'100755' if name.endswith('.command') else '100644','type':'blob','content':content})
    return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--json',action='store_true');args=parser.parse_args()
    files=export()
    if args.json:print(json.dumps(files,ensure_ascii=False))
    else:
        for f in files:print(hashlib.sha256(f['content'].encode()).hexdigest(),f['path'])
