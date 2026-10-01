"""Quell is target-conditional damage, NOT part of generic m_iDamageBonus.
Only validated active inventory + owned live modifier fields; no balance fallback.
The inventory decoder is reused without changing KS. Unknown bonus contributes 0.
"""
from dataclasses import dataclass
import math
import struct
from collect_targets import offset,identity
from ks_inventory import read_inventory

NAMES=frozenset(('item_quelling_blade','item_bfury'))


@dataclass(frozen=True)
class Quelling:
    bonus: float=0.
    known: bool=False
    note: str='Quell неизвестен; бонус не добавлен'
    signature: tuple=()


class InventoryView:
    def __init__(self,dot,probe):self.dot=dot;self.probe=probe
    def rb(self,a,n):return self.probe.read(a,n)
    def get_entity(self,index):return self.dot.get_entity(index)


def active_items(dot,probe,hero,clock):
    """Serial-bearing identities and item activation, no cooldown/Chop Tree test."""
    base=hero[0]+offset(probe.classes,'C_DOTA_BaseNPC','m_Inventory')
    items=offset(probe.classes,'C_DOTA_UnitInventory','m_hItems')
    size=offset(probe.classes,'C_DOTA_UnitInventory','m_bItemQueried')-items
    inv=read_inventory(InventoryView(dot,probe),base+items,field_size=size)
    if not inv.known:raise ValueError(inv.reason)
    result=[];checks=[]
    for slot,handle,ep,name in inv.entries:
        if slot not in range(6) or name not in NAMES:continue
        if identity(dot,probe,ep,name)!=handle:raise ValueError('Quell item identity changed')
        enabled_at=ep+offset(probe.classes,'C_DOTA_Item','m_bItemEnabled')
        time_at=ep+offset(probe.classes,'C_DOTA_Item','m_flEnableTime')
        enabled=probe.read(enabled_at,1);raw=probe.read(time_at,4)
        if enabled not in (b'\0',b'\1') or not raw:raise ValueError('Quell item activation unreadable')
        enable_time=struct.unpack('<f',raw)[0]
        if not math.isfinite(enable_time):raise ValueError('Quell item enable time invalid')
        checks.extend(((enabled_at,enabled),(time_at,raw)))
        if enabled==b'\1' and enable_time<=clock:
            result.append((slot,handle,ep,name))
    if checks:
        after=read_inventory(InventoryView(dot,probe),base+items,field_size=size)
        if not after.known or after.entries!=inv.entries:raise ValueError('Quell inventory changed')
        if any(probe.read(a,len(raw))!=raw for a,raw in checks):raise ValueError('Quell activation changed')
        for _,handle,ep,name in result:
            if identity(dot,probe,ep,name)!=handle:raise ValueError('Quell item recycled')
    return tuple(result)


def resolve(items,values,ranged):
    if not items:return Quelling(0.,True,'Quell +0: нет активного топорика / Battle Fury')
    if values is None:return Quelling()
    bonuses=[]
    for _,_,_,name in items:
        pair=values.get(name)
        if pair is None:return Quelling(note='Quell неизвестен: активный предмет без прочитанного модификатора')
        if len(pair)!=2 or any(type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=100 for v in pair):return Quelling()
        bonuses.append(pair[1 if ranged else 0])
    bonus=max(bonuses,default=0.)
    return Quelling(bonus,True,'Quell +%g (%s), до брони; не для деная'%(bonus,'дальний' if ranged else 'ближний'),items)


def read_quelling(reader,uid,clock,ranged):
    try:
        items=active_items(reader.dot,reader.probe,uid,clock)
        cached=getattr(reader,'quell_cache',{}).get(uid)
        import time
        values=cached[1] if cached and 0<=time.perf_counter()-cached[0]<=.20 else None
        return resolve(items,values,ranged)
    except Exception as exc:return Quelling(note='Quell неизвестен: '+str(exc)[:100])
