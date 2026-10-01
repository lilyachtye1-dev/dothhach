"""Direct modifier-vector reader derived from the user's two independent captures.
No neighbourhood search during combat. No absolute heap pointers or foreign vtable RVAs.
Unknown is NOT zero. PE metadata pinning is a compatibility gate, not a cryptographic
proof of the running executable. RTTI, owner links and stable reads are also checked.
"""
import struct
from collect_live import Probe, pointer


def validate_layout(config):
    if not isinstance(config, dict):
        raise ValueError('live_modifier_layout must be an object')
    required = {'client_timestamp', 'client_size', 'schema_sha256', 'manager_owner',
                'count', 'data', 'capacity', 'modifier_manager', 'stack_count', 'max_count'}
    if set(config) != required:
        raise ValueError('invalid live_modifier_layout keys')
    for name in required - {'schema_sha256'}:
        if type(config[name]) is not int or config[name] < 0:
            raise ValueError('invalid live_modifier_layout.' + name)
    if not 1 <= config['max_count'] <= 256 or not 0x100000 <= config['client_size'] <= 0x40000000:
        raise ValueError('invalid live modifier bounds')
    if config['client_timestamp'] > 0xFFFFFFFF:
        raise ValueError('invalid PE timestamp')
    for name in ('manager_owner','count','data','capacity','modifier_manager','stack_count'):
        if not 0 <= config[name] <= 0x2000 or config[name] % 4:
            raise ValueError('invalid modifier offset: ' + name)
    digest = config['schema_sha256']
    if not isinstance(digest,str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('invalid schema digest')
    return dict(config)


class ModifierReader:
    def __init__(self, classes, layout, digest):
        self.classes = classes
        self.layout = validate_layout(layout) if layout is not None else None
        self.digest = digest

    def stacks(self, dot, hero, requested):
        return self._scan(dot, hero, requested)

    def present(self, dot, hero, requested):
        """Diagnostic membership only; does not decode caster, target or buff meaning.
        A present modifier can have zero stacks. Absence requires the same complete,
        stable, owner-checked vector as stacks(). Never used by the Dodger planner.
        """
        return self._scan(dot, hero, requested, presence=True)

    def instances(self, dot, hero, requested):
        """Owner/type/stability-checked object tokens; zero means proven absence.
        Duplicate classes, partial reads and unknown types remain exceptions.
        Does NOT decode the modifier's caster handle.
        """
        return self._scan(dot, hero, requested, presence=True, instances=True)

    def _scan(self, dot, hero, requested, presence=False, instances=False):
        c = self.layout
        if c is None or c['schema_sha256'] != self.digest:
            raise ValueError('live modifier schema not approved')
        probe = Probe(dot, self.classes, seconds=.015 if presence else .08,
                      max_reads=600 if presence else 1600)
        if (probe.image['timestamp'],probe.image['size']) != (c['client_timestamp'],c['client_size']):
            raise ValueError('client build differs from stack captures')
        def read(address,count):
            raw=probe.read(address,count)
            if raw is None: raise ValueError('unreadable modifier memory')
            return raw
        manager=hero+self.classes['C_DOTA_BaseNPC']['fields']['m_ModifierManager']
        size=max(c[k]+n for k,n in (('manager_owner',8),('count',4),('data',8),('capacity',4)))
        header=read(manager,size)
        if struct.unpack_from('<Q',header,c['manager_owner'])[0] != hero:
            raise ValueError('modifier manager owner mismatch')
        count=struct.unpack_from('<i',header,c['count'])[0]
        capacity=struct.unpack_from('<i',header,c['capacity'])[0]
        data=struct.unpack_from('<Q',header,c['data'])[0]
        if not 0 <= count <= capacity <= c['max_count'] or (count and not pointer(data)):
            raise ValueError('invalid modifier vector')
        raw=read(data,count*8) if count else b''
        pointers=struct.unpack('<'+'Q'*count,raw) if count else ()
        if len(set(pointers)) != count:
            raise ValueError('duplicate modifier pointers')
        result={name:0 if instances or not presence else False for name in requested}
        seen=set(); checks=[]
        for obj in pointers:
            if not pointer(obj): raise ValueError('invalid modifier object')
            link=read(obj+c['modifier_manager'],8)
            if struct.unpack('<Q',link)[0] != manager:
                raise ValueError('foreign modifier owner')
            vptr=read(obj,8)
            cls=probe.runtime_type(obj)
            if read(obj,8) != vptr: raise ValueError('modifier type changed during read')
            if not cls or not cls.startswith('CDOTA_Modifier_'):
                raise ValueError('unknown modifier type; cannot prove counter absence')
            checks.append((obj,vptr,obj+c['modifier_manager'],link))
            if cls not in result: continue
            if cls in seen: raise ValueError('ambiguous stack modifier')
            seen.add(cls)
            if presence:
                result[cls] = obj if instances else True
                continue
            value=read(obj+c['stack_count'],4)
            n=struct.unpack('<i',value)[0]
            if not 0 <= n <= 10000: raise ValueError('invalid stack count')
            result[cls]=n
            checks.append((obj+c['stack_count'],value,None,None))
        # No permanent cache: object destruction, hero changes and vector reallocations
        # must not leave a stale count active. A missing counter is zero ONLY if the
        # entire current vector was successfully classified and checked.
        for a,v,b,w in checks:
            if read(a,len(v)) != v or (b is not None and read(b,len(w)) != w):
                raise ValueError('modifier changed during read')
        if read(manager,size) != header or (count and read(data,count*8) != raw):
            raise ValueError('modifier vector changed during read')
        return tuple(sorted(result.items()))


def talent_levels(dot, hero, requested, classes, ability_offset=None):
    """Read actual talent ability levels, never infer selection from hero level.
    Uses the same vector layouts as Dot.abilities_of, but includes special_bonus.
    Multiple conflicting layouts or incomplete reads are treated as unknown.
    """
    npc=classes['C_DOTA_BaseNPC']['fields']
    offset = npc['m_vecAbilities'] if ability_offset is None else ability_offset
    if type(offset) is not int or offset <= 0:
        raise ValueError('invalid ability vector offset')
    base=hero+offset
    header=dot.rb(base,24)
    if header is None or len(header)!=24: raise ValueError('talent vector unreadable')
    candidates=[]
    for po,so in ((0,8),(8,16),(0,16),(8,8)):
        ptr=struct.unpack_from('<Q',header,po)[0]
        size=struct.unpack_from('<i',header,so)[0]
        if not pointer(ptr) or not 1<=size<=32: continue
        raw=dot.rb(ptr,size*4)
        if raw is None or len(raw)!=size*4: continue
        found={}; reads=[]; invalid=False
        for handle in struct.unpack('<'+'I'*size,raw):
            if handle in (0,0xFFFFFFFF): continue
            entity=dot.get_entity(handle & 0x7FFF)
            if not entity or entity[1] not in requested: continue
            ep,name=entity
            if name in found: invalid=True;break
            address=ep+classes['C_DOTABaseAbility']['fields']['m_iLevel']
            value=dot.rb(address,4)
            if value is None or len(value)!=4: invalid=True;break
            level=struct.unpack('<i',value)[0]
            if level not in (0,1): invalid=True;break
            found[name]=level
            reads.append((address,value,handle,entity))
        if invalid or set(found)!=set(requested): continue
        if dot.rb(base,24)!=header or dot.rb(ptr,size*4)!=raw: continue
        if any(dot.rb(a,4)!=v or dot.get_entity(h&0x7FFF)!=e for a,v,h,e in reads): continue
        candidates.append(tuple(sorted(found.items())))
    if not candidates or len(set(candidates))!=1:
        raise ValueError('learned talent state unknown')
    return candidates[0]
