"""Primary-source macro headlines; not an investment recommendation engine."""
import ssl
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
import certifi

FEEDS=[('美联储','https://www.federalreserve.gov/feeds/press_all.xml','美元、黄金及美股指数：关注货币政策和流动性。'),
       ('欧洲央行','https://www.ecb.europa.eu/rss/press.html','欧元与欧洲股指：关注政策和通胀背景。'),
       ('美国能源信息署','https://www.eia.gov/rss/todayinenergy.xml','原油与能源：关注供需、库存和生产变化。')]


def collect_news(monitor):
    context=ssl.create_default_context(cafile=certifi.where())
    while not monitor.stop.is_set():
        items=[];sources=[]
        for name,url,context_note in FEEDS:
            try:
                req=Request(url,headers={'User-Agent':'HarbourCFD/1.0 (personal read-only dashboard)'})
                with urlopen(req,timeout=15,context=context) as r:
                    data=r.read(2_000_001)
                    if len(data)>2_000_000:raise ValueError()
                root=ET.fromstring(data)
                for item in root.findall('.//item')[:10]:
                    title=item.findtext('title');link=item.findtext('link');published=item.findtext('pubDate')
                    if not title or not link or urlsplit(link).scheme!='https':continue
                    try:at=parsedate_to_datetime(published).astimezone(timezone.utc).isoformat()
                    except (ValueError,TypeError):at=None
                    items.append({'title':title,'url':link,'published_at':at,'source':name,'context':context_note,'relevance':'宏观背景，尚未完成具体持仓影响分析'})
                sources.append({'name':name,'status':'ok'})
            except Exception:sources.append({'name':name,'status':'unavailable'})
        with monitor.lock:
            old=monitor.state['news']
            monitor.state['news']={'status':'ok' if all(s['status']=='ok' for s in sources) else 'partial' if items else 'unavailable',
              'items':sorted(items,key=lambda x:x['published_at'] or '',reverse=True) if items else old.get('items',[]),
              'checked_at':datetime.now(timezone.utc).isoformat(),'sources':sources,
              'coverage':'官方宏观资讯；个股新闻、财报日历和持仓影响研判待配置。'}
        monitor.stop.wait(3600)
