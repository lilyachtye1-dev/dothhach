"""Read selected LIVE modifier values through the existing approved owner layout.
Partial IAS is labelled partial, not a universal effective-attack-speed decoder.
No other-build constants or writes. IAS copies add; Quell copies use maximum.
"""
import math
import struct
from collect_live import Probe,pointer
from ks_modifiers import validate_layout

IAS={
 'CDOTA_Modifier_Item_PowerTreads':'bonus_attack_speed',
 'CDOTA_Modifier_Item_PhaseBoots':'bonus_attack_speed',
 'CDOTA_Modifier_Item_WraithBand':'bonus_attack_speed',
 'CDOTA_Modifer_Item_GlovesOfHaste':'bonus_attack_speed',
 'CDOTA_Modifier_Item_Hyperstone':'bonus_attack_speed',
 'CDOTA_Modifier_Item_Moonshard':'bonus_attack_speed',
 'CDOTA_Modifier_Item_Moonshard_Consumed':'consumed_bonus',
 'CDOTA_Modifier_Item_Assault_Cuirass':'bonus_attack_speed',
 'CDOTA_Modifier_Item_Assault_Cuirass_Positive':'aura_attack_speed',
}
SIEGE='CDOTA_Modifier_Creep_Siege'
QUELL={
 'CDOTA_Modifier_Item_QuellingBlade':('item_quelling_blade','damage_bonus','damage_bonus_ranged'),
 'CDOTA_Modifier_Item_Battlefury':('item_bfury','quelling_bonus','quelling_bonus_ranged'),
}


def numeric(raw,limit=2000):
    if not raw or len(raw)!=4:raise ValueError('stat unreadable')
    if raw in (b'\0'*4,b'\0\0\0\x80'):return 0.
    f=struct.unpack('<f',raw)[0];i=struct.unpack('<i',raw)[0]
    # Schema dump omits types. These bounded integer and normal-float encodings
    # are disjoint; reject NaN, denormals outside integer bounds, and huge values.
    candidates=[]
    if math.isfinite(f) and .001<=abs(f)<=limit:candidates.append(float(f))
    if 0<abs(i)<=limit:candidates.append(float(i))
    if len(candidates)!=1:raise ValueError('stat numeric representation unknown')
    return candidates[0]


class LiveStats:
    def __init__(self,classes,layout,digest,require_siege_modifier=False):
        self.classes=classes;self.layout=validate_layout(layout);self.digest=digest;self.type_cache={}
        self.require_siege_modifier=bool(require_siege_modifier)
    def read(self,dot,hero,siege=False):
        if not siege:self.quelling=None  # Never reuse a previous successful private read.
        p=Probe(dot,self.classes,seconds=.035,max_reads=1800);p.type_cache=self.type_cache;c=self.layout
        if c['schema_sha256']!=self.digest or (p.image['timestamp'],p.image['size'])!=(c['client_timestamp'],c['client_size']):
            raise ValueError('stats build/layout mismatch')
        def read(address,n):
            raw=p.read(address,n)
            if raw is None:raise ValueError('stat/vector unreadable')
            return raw
        manager=hero+self.classes['C_DOTA_BaseNPC']['fields']['m_ModifierManager']
        size=max(c[k]+n for k,n in [('manager_owner',8),('count',4),('data',8),('capacity',4)])
        header=read(manager,size)
        if struct.unpack_from('<Q',header,c['manager_owner'])[0]!=hero:raise ValueError('stats owner mismatch')
        count=struct.unpack_from('<i',header,c['count'])[0];capacity=struct.unpack_from('<i',header,c['capacity'])[0]
        data=struct.unpack_from('<Q',header,c['data'])[0]
        if not 0<=count<=capacity<=c['max_count'] or (count and not pointer(data)):raise ValueError('stats vector invalid')
        raw=read(data,count*8) if count else b'';objects=struct.unpack('<'+'Q'*count,raw) if count else ()
        if len(set(objects))!=count:raise ValueError('duplicate modifier pointer')
        total=0.;sources=[];checks=[];penalty=None;quelling={};quell_valid=True
        for obj in objects:
            if not pointer(obj):raise ValueError('modifier pointer invalid')
            link=read(obj+c['modifier_manager'],8);vptr=read(obj,8)
            if struct.unpack('<Q',link)[0]!=manager:raise ValueError('foreign stat modifier')
            cls=p.runtime_type(obj)
            if not cls or not cls.startswith(('CDOTA_Modifier_','CDOTA_Modifer_')):raise ValueError('unknown modifier type')
            checks.extend([(obj,vptr),(obj+c['modifier_manager'],link)])
            if not siege and cls in QUELL:
                name,melee,ranged=QUELL[cls]
                try:
                    values=[]
                    for field_name in (melee,ranged):
                        address=obj+self.classes[cls]['fields'][field_name]
                        value=read(address,4);checks.append((address,value))
                        n=numeric(value,100)
                        if n<0:raise ValueError('negative Quell')
                        values.append(n)
                    # Duplicate blades/Battle Fury do not sum their Quell bonuses.
                    previous=quelling.get(name,(0.,0.))
                    quelling[name]=tuple(max(a,b) for a,b in zip(previous,values))
                except (ValueError,KeyError):quell_valid=False
            field='incoming_hero_damage_penalty' if siege and cls==SIEGE else IAS.get(cls) if not siege else None
            if not field:continue
            offset=self.classes[cls]['fields'][field];value=read(obj+offset,4);n=numeric(value,100 if siege else 2000)
            checks.append((obj+offset,value))
            if siege:
                if penalty is not None:raise ValueError('duplicate siege modifier')
                penalty=abs(n)
            else:total+=n;sources.append(cls+':'+str(round(n,2)))
        if read(manager,size)!=header or (count and read(data,count*8)!=raw) or any(read(a,len(v))!=v for a,v in checks):
            raise ValueError('stats changed during capture')
        if siege:
            if penalty is None and self.require_siege_modifier:
                raise ValueError('siege modifier absent; reduction unknown on this build')
            return 1.-(penalty or 0.)/100,('siege modifier' if penalty is not None else 'verified absent')
        self.quelling=(hero,quelling) if quell_valid else None
        return total,(', '.join(sources) if sources else 'no supported IAS modifiers')
