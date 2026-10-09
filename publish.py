"""Publish reviewed structured morning reports, never fabricate research from headlines."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from context import HERE, write_json
from advisor import dt, HK

REQUIRED = ['主要利多 / 利空','过去24小时最重要事件','未来7天重要数据和会议','组合共同风险','零基础金融术语','来源和验证渠道']

def publish(report):
    stamp=dt(report['asof']);now=datetime.now(timezone.utc)
    if stamp>now: raise ValueError('Report timestamp cannot be in the future')
    titles=[s['title'] for s in report['sections']]
    if titles!=REQUIRED: raise ValueError('Morning report must contain the six agreed sections in order')
    if any(not s.get('text','').strip() for s in report['sections']): raise ValueError('Empty report section')
    day=stamp.astimezone(HK).date().isoformat()
    chunks=[f"# {day} 资产晨报",f"{report.get('label','资产晨报')}｜实际生成：{stamp.astimezone(HK).isoformat()}",report.get('coverage_note','')]
    for section in report['sections']:
        chunks.extend(['',f"## {section['title']}",section['text']])
        for s in section.get('sources',[]):chunks.append(f"[{s.get('title','来源')}]({s['url']})")
    report=dict(report,markdown='\n\n'.join(chunks)+'\n')
    write_json(HERE/'state/brief.json',report)
    folder=HERE/'briefs'/day;folder.mkdir(parents=True,exist_ok=True)
    write_json(folder/('report_'+stamp.strftime('%H%M%S_UTC')+'.json'),report)
    (folder/'latest.md').write_text(report['markdown'])
    return {'status':'PUBLISHED','day':day,'actual_asof':report['asof'],'sections':len(report['sections'])}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);args=p.parse_args()
    print(publish(json.loads(Path(args.input).read_text())))
