"""Quantity-weighted entry prices of current positions, never mixed across sides/currencies."""
import math

def finite(value):
    if isinstance(value,bool):return None
    try:
        number=float(value)
        return number if math.isfinite(number) else None
    except (TypeError,ValueError,OverflowError):return None

def group_positions(positions):
    groups={}
    for p in positions or []:
        key=(p.get('epic'),p.get('currency'),p.get('expiry'))
        if not key[0]:continue
        g=groups.setdefault(key,{'epic':key[0],'currency':key[1],'expiry':key[2],
            'name':p.get('instrumentName') or key[0],'count':0,'unknown_direction_count':0,
            'BUY':{'count':0,'size':0,'weighted_sum':0,'invalid':0},
            'SELL':{'count':0,'size':0,'weighted_sum':0,'invalid':0}})
        g['count']+=1;direction=p.get('direction')
        if direction not in ('BUY','SELL'):g['unknown_direction_count']+=1;continue
        side=g[direction];side['count']+=1
        qty=finite(p.get('size'));level=finite(p.get('level'))
        if qty is None or qty<=0 or level is None:side['invalid']+=1;continue
        side['size']+=qty;side['weighted_sum']+=qty*level
    for g in groups.values():
        for direction in ('BUY','SELL'):
            s=g[direction];s['average_entry']=s['weighted_sum']/s['size'] if s['size']>0 and not s['invalid'] else None
            if s['invalid']:s['size']=None
            del s['weighted_sum']
    return list(groups.values())
