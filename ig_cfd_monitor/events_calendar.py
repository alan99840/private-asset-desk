"""Official schedule cache. Date-only decisions never get invented release times."""
import calendar
import html
import json
import re
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import certifi

SOURCES={'Fed':'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm',
 'BoJ':'https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm',
 'BLS':'https://www.bls.gov/schedule/{year}/home.htm',
 'EIA':'https://www.eia.gov/petroleum/supply/weekly/schedule.php'}
MONTHS={s.lower()[:3]:i for i,s in enumerate(calendar.month_name) if s}

def clean(value):return ' '.join(html.unescape(re.sub('<[^>]+>',' ',value)).split())

def event(source,title,day,clock=None,zone='America/New_York'):
    at=datetime.fromisoformat(day+'T'+clock).replace(tzinfo=ZoneInfo(zone)).astimezone(timezone.utc).isoformat() if clock else None
    return {'source':source,'title':title,'date':day,'at':at,'timezone':zone,
        'precision':'time' if at else 'date','url':SOURCES[source].format(year=day[:4]),
        'context':{'Fed':'利率、美元、黄金及全球股指','BoJ':'日本利率、日元及日本股指','BLS':'美国通胀、就业与利率预期','EIA':'美国原油、成品油库存与能源价格'}[source]}

def parse_schedule(source,body,year):
    items=[]
    if source=='BLS':
        for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',body,re.S|re.I):
            cells=[clean(c) for c in re.findall(r'<td\b[^>]*>(.*?)</td>',row,re.S|re.I)]
            if len(cells)!=3 or not any(t in cells[2] for t in ('Employment Situation','Consumer Price Index','Producer Price Index','Job Openings and Labor','Employment Cost Index')):continue
            when=datetime.strptime(cells[0]+' '+cells[1],'%A, %B %d, %Y %I:%M %p')
            items.append(event(source,cells[2],when.date().isoformat(),when.strftime('%H:%M')))
    elif source=='Fed':
        section=re.search(r'>'+str(year)+r' FOMC Meetings.*?(?=>\d{4} FOMC Meetings|\Z)',body,re.S)
        if not section:raise ValueError('year_missing')
        pairs=re.findall(r'fomc-meeting__month[^>]*>(.*?)</div>.*?fomc-meeting__date[^>]*>(.*?)</div>',section.group(),re.S)
        for month,days in pairs:
            m=MONTHS.get(clean(month).lower()[:3]);numbers=re.findall(r'\d+',clean(days))
            if m and numbers:items.append(event(source,'FOMC 会议结束日（公布时间待确认）',f'{year}-{m:02}-{int(numbers[-1]):02}'))
    elif source=='BoJ':
        section=re.search(r'<h2 id="p'+str(year)+r'">.*?</table>',body,re.S)
        if not section:raise ValueError('year_missing')
        for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',section.group(),re.S):
            cells=re.findall(r'<td\b[^>]*>(.*?)</td>',row,re.S)
            if not cells:continue
            text=clean(cells[0]).split('[PDF')[0];m=MONTHS.get(text.lower()[:3])
            dates=re.findall(r'(\d+)\s*\(',text)
            if m and dates:items.append(event(source,'日本央行会议结束日（公布时间待确认）',f'{year}-{m:02}-{int(dates[-1]):02}',zone='Asia/Tokyo'))
    else:raise ValueError('unsupported_parser')
    if not items:raise ValueError('empty_schedule')
    return items

def upcoming(item,now=None):
    now=now or datetime.now(timezone.utc)
    if item.get('at'):return datetime.fromisoformat(item['at'])>=now
    # Date-only decisions stay visible for their entire official local date,
    # which can continue into the next Hong Kong calendar day.
    return item['date']>=now.astimezone(ZoneInfo(item['timezone'])).date().isoformat()

def relevant(event,markets):
    names=[(m['epic'],m.get('name','')) for m in markets]
    if event['source'] in ('Fed','BLS'):return [name for _,name in names]
    pattern=r'Japan|Nikkei|JPY|日本|日经|日經' if event['source']=='BoJ' else r'oil|crude|brent|WTI|CL\.|原油|石油|汽油'
    return [name for epic,name in names if re.search(pattern,epic+' '+name,re.I)]

def collect_calendar(monitor):
    seed=json.loads((Path(__file__).parent/'calendar_seed.json').read_text())
    cache=seed['sources'];context=ssl.create_default_context(cafile=certifi.where())
    while not monitor.stop.is_set():
        now=datetime.now(timezone.utc);year=now.year;states=[];allitems=[]
        for source,url in SOURCES.items():
            existing=cache.get(source,{'items':[],'checked_at':None})
            try:
                req=Request(url.format(year=year),headers={'User-Agent':'Mozilla/5.0 Harbour Research Monitor','Accept':'text/html'})
                with urlopen(req,timeout=12,context=context) as response:body=response.read(2_000_001).decode('utf-8')
                if len(body)>2_000_000:raise ValueError('too_large')
                if source=='EIA':
                    # Only publish the holiday-adjusted dates explicitly verified
                    # in the seed. A successful fetch alone cannot extend coverage.
                    if '10:30' not in body:raise ValueError('schedule_changed')
                    items=existing['items']
                else:items=parse_schedule(source,body,year)
                existing={**existing,'items':items,'checked_at':now.isoformat() if source!='EIA' else existing.get('checked_at')};cache[source]=existing;status='seed_only' if source=='EIA' else 'ok'
            except Exception:status='cached_unverified'
            states.append({'name':source,'status':status,'checked_at':existing.get('checked_at'),
                'coverage_end':max((e['date'] for e in existing['items']),default=None)})
            allitems+=existing['items']
        with monitor.lock:
            monitor.state['calendar']={'items':sorted(allitems,key=lambda e:e['at'] or e['date']),
                'sources':states,'checked_at':now.isoformat(),'coverage':'Fed / BoJ / BLS / EIA；非完整全球日历。时间待确认的事件只标官方当地日期。实际值与市场预期未接入。'}
        monitor.stop.wait(86400)
