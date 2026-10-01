"""Independent READ-ONLY Last Hit sampling; never sends input or writes memory.
Entity discovery is asynchronous. Fast sampling never scans all process memory.
"""
import hashlib
import copy
import json
import math
from pathlib import Path
import struct
import threading
import time
from dataclasses import replace
from collect_live import Probe
from collect_targets import identity, offset
from last_hit_build import check_build, reviewed_layout
from feed_stability import AsyncSnapshotWorker
from last_hit_assets import AttackData, game_directory
from last_hit_core import Frame, Unit, distance
from last_hit_units import candidate_name,entity_kind,lane_type
from last_hit_stats import LiveStats
from last_hit_quelling import Quelling, read_quelling, active_items


class Reader:
    def __init__(self, dot, root, requested, hero_hint):
        self.dot=copy.copy(dot);self.dot._names=dict(getattr(dot,'_names',{}))
        self.verify_dot=copy.copy(dot);self.verify_dot._names=dict(getattr(dot,'_names',{}));self.verify_probe=None
        scan=copy.copy(dot);scan._names=dict(getattr(dot,'_names',{}))
        self.scan=AsyncSnapshotWorker(lambda:scan.entities(full_scan=True))
        self.scan.start()
        self.root=Path(root);self.requested=requested;self.hero_hint=hero_hint
        self.latest=Frame(0,0,(0,0),error='Last Hit выключен')
        self.entries={};self.scan_at=-999.;self.started=False;self.assets=None;self.probe=None
        self.stats=None;self.stat_cache={};self.stats_note='modifier layout not initialized'
        self.build_label=''
        self.running=True
        threading.Thread(target=self.run,name='Last Hit read-only',daemon=True).start()

    def value(self,ep,cls,field,fmt):
        p=self.probe;raw=p.read(ep+offset(p.classes,cls,field),struct.calcsize(fmt))
        if raw is None:raise ValueError('Не читается '+field)
        result=struct.unpack(fmt,raw)[0]
        if isinstance(result,float) and not math.isfinite(result):raise ValueError('Нечисловое '+field)
        return result

    def aim_point(self,d,p,ep,pos,matrix):
        # Aim inside the collision body, not at projected ground between feet.
        height=32.  # Geometric fallback only; never a damage/timing default.
        raw=p.read(ep+offset(p.classes,'C_BaseEntity','m_pCollision'),8)
        collision=struct.unpack('<Q',raw)[0] if raw else 0
        if 0x10000<=collision<1<<48:
            raw=p.read(collision+offset(p.classes,'CCollisionProperty','m_vecMins'),24)
            if raw:
                lo=struct.unpack_from('<fff',raw,0);hi=struct.unpack_from('<fff',raw,12)
                if all(math.isfinite(n) for n in lo+hi) and -32<=lo[2]<=100 and 12<=hi[2]-lo[2]<=300:
                    height=lo[2]+.5*(hi[2]-lo[2])
        return d.w2s(matrix,(pos[0],pos[1],pos[2]+height))

    def verify_hero(self,uid):
        try:
            p=self.verify_probe;d=self.verify_dot
            if p is None:return False
            p.deadline=time.perf_counter()+.025;p.reads=0;d.esys=0
            ep,handle=uid;got=d.get_entity(handle&0x7fff)
            return bool(got and got[0]==ep and identity(d,p,ep,got[1])==handle and
                        p.read(ep+offset(p.classes,'C_BaseEntity','m_lifeState'),1)==b'\0')
        except Exception:return False

    def modifier_stats(self,uid,siege=False):
        now=time.perf_counter();cache=getattr(self,'stat_cache',{})
        key=(uid,siege)
        if key in cache and now-cache[key][0]<.20:return cache[key][1:]
        try:
            if getattr(self,'stats',None) is None:raise ValueError(getattr(self,'stats_note','modifier layout not initialized'))
            value,note=self.stats.read(self.dot,uid[0],siege)
            if not siege:
                q=getattr(self.stats,'quelling',None)
                values=q[1] if q and q[0]==uid[0] else None
                qc=getattr(self,'quell_cache',{})
                qc[uid]=(now,values);self.quell_cache={k:v for k,v in qc.items() if now-v[0]<2}
        except Exception as exc:
            getattr(self,'quell_cache',{}).pop(uid,None)
            value=None;note='unknown: '+str(exc)[:100]
        cache[key]=(now,value,note);self.stat_cache={k:v for k,v in cache.items() if now-v[0]<2}
        return value,note

    def target_unit(self,ep,designer,clock,matrix,cls='C_DOTA_BaseNPC_Creep_Lane'):
        d=self.dot;p=self.probe;handle=identity(d,p,ep,designer)
        if handle is None:raise ValueError('target identity changed')
        v=lambda f,fmt='<f':self.value(ep,'C_DOTA_BaseNPC',f,fmt)
        base=lambda f,fmt='<i':self.value(ep,'C_BaseEntity',f,fmt)
        hp=base('m_iHealth');maxhp=base('m_iMaxHealth');team=base('m_iTeamNum','<B')
        if not 0<hp<=maxhp<=100000 or team not in (2,3) or base('m_lifeState','<B')!=0 or d.dormant_state(ep) is not False:return None
        pos=d.pos(ep)
        if not pos or len(pos)!=3 or not all(math.isfinite(x) and abs(x)<50000 for x in pos):raise ValueError('target position unreadable')
        # Target eligibility requires HP, armor, hull and position, NOT its own
        # projectile speed, attack point, damage roll or turn rate.
        armor=v('m_flPhysicalArmorValue');regen=v('m_flHealthRegen');hull=v('m_flHullRadius')
        if not (-100<=armor<=2000 and -1000<=regen<=5000 and 0<=hull<=500):raise ValueError('target combat fields invalid')
        name=designer
        ptrraw=p.read(ep+offset(p.classes,'C_DOTA_BaseNPC','m_iszUnitName'),8)
        ptr=struct.unpack('<Q',ptrraw)[0] if ptrraw else 0
        candidate=d.cs(ptr,128) if 0x10000<=ptr<1<<48 else None
        if isinstance(candidate,str) and candidate.startswith('npc_dota_'):name=candidate
        def optional(field,fmt='<i'):
            try:return v(field,fmt)
            except (ValueError,KeyError):return 0
        role=lane_type(name,cls,optional('m_iAttackCapabilities')) or 'lane'
        incoming=1.
        if role=='siege':incoming,_=self.modifier_stats((ep,handle),True)
        bonus=optional('m_iDamageBonus')
        # Optional target attack fields remain unused by the last-hit model.
        low=optional('m_iDamageMin')+bonus;high=optional('m_iDamageMax')+bonus
        yaw=0.;node=d.scene_node(ep);r=p.read(node+offset(p.classes,'CGameSceneNode','m_angAbsRotation'),12) if node else None
        if r:
            n=struct.unpack('<fff',r)[1]
            if math.isfinite(n):yaw=n
        point=self.aim_point(d,p,ep,pos,matrix)
        if identity(d,p,ep,designer)!=handle:raise ValueError('target changed during read')
        return Unit((ep,handle),name,'creep',team,hp,maxhp,tuple(pos),tuple(point or ()),yaw,
                    low,high,armor,regen,0,hull,0,0,0,0,0,0,damage_bonus=bonus,lane_type=role,incoming_multiplier=incoming)

    def unit(self,ep,designer,kind,clock,matrix):
        if kind=='creep':return self.target_unit(ep,designer,clock,matrix)
        d=self.dot;p=self.probe
        handle=identity(d,p,ep,designer)
        if handle is None:raise ValueError('Сменилась entity identity')
        v=lambda f,fmt='<f':self.value(ep,'C_DOTA_BaseNPC',f,fmt)
        base=lambda f,fmt='<i':self.value(ep,'C_BaseEntity',f,fmt)
        hp=base('m_iHealth');maxhp=base('m_iMaxHealth');team=base('m_iTeamNum','<B')
        if not 0<hp<=maxhp<=100000 or team not in (2,3) or base('m_lifeState','<B')!=0:return None
        if d.dormant_state(ep) is not False:return None
        pos=d.pos(ep)
        if not pos or len(pos)!=3 or not all(math.isfinite(x) and abs(x)<50000 for x in pos):raise ValueError('Некорректная позиция')
        node=d.scene_node(ep)
        raw=p.read(node+offset(p.classes,'CGameSceneNode','m_angAbsRotation'),12)
        if raw is None:raise ValueError('Не читается направление')
        yaw=struct.unpack('<fff',raw)[1]
        if not math.isfinite(yaw) or abs(yaw)>100000:raise ValueError('Некорректное направление')
        name=designer
        ptr=v('m_iszUnitName','<Q')
        candidate=d.cs(ptr,128) if 0x10000<=ptr<1<<48 else None
        if isinstance(candidate,str) and candidate.startswith('npc_dota_'):name=candidate
        ranged=v('m_iAttackCapabilities','<i')
        if ranged not in (1,2):raise ValueError('Неизвестный тип атаки '+name)
        point,turn,speed=self.assets.timing(name,ranged==2,kind=='hero')
        bat=v('m_flBaseAttackTime');base_speed=v('m_iBaseAttackSpeed','<i')
        agi=self.value(ep,'C_DOTA_BaseNPC_Hero','m_flAgilityTotal','<f') if kind=='hero' else 0.
        if not .1<=bat<=10 or not 0<=agi<=10000 or not 1<=base_speed<=1000:raise ValueError('Некорректные параметры атаки')
        extra=0.;speed_note='base + agility estimate'
        if kind=='hero':
            observed,speed_note=self.modifier_stats((ep,handle))
            if observed is not None:extra=observed;speed_note='partial live IAS: '+speed_note
        interval=bat*100/max(20,min(700,base_speed+agi+extra))
        bonus=v('m_iDamageBonus','<i')
        dmg_min=v('m_iDamageMin','<i')+bonus
        dmg_max=v('m_iDamageMax','<i')+bonus
        armor=v('m_flPhysicalArmorValue');regen=v('m_flHealthRegen')
        attack_range=v('m_iAttackRange','<i');hull=v('m_flHullRadius');last=v('m_flLastAttackTime')
        if not (0<=dmg_min<=dmg_max<=50000 and -100<=armor<=2000 and -1000<=regen<=5000 and
                0<attack_range<10000 and 0<=hull<=500 and last<=max(clock+.1,0)):
            raise ValueError('Поля атаки вне диапазона: '+name)
        target=()
        if kind=='tower':
            target_handle=self.value(ep,'C_DOTA_BaseNPC_Tower','m_hTowerAttackTarget','<I')
            if target_handle not in (0,0xffffffff):
                pair=d.get_entity(target_handle&0x7fff)
                if pair and identity(d,p,pair[0],pair[1])==target_handle:target=(pair[0],target_handle)
        quell=read_quelling(self,(ep,handle),clock,ranged==2) if kind=='hero' else Quelling()
        if identity(d,p,ep,designer)!=handle:raise ValueError('Entity изменилась во время чтения')
        return Unit((ep,handle),name,kind,team,hp,maxhp,tuple(pos),tuple((self.aim_point(d,p,ep,pos,matrix) if kind=='creep' else d.w2s(matrix,pos)) or ()),yaw,
                    dmg_min,dmg_max,armor,regen,attack_range,hull,bat,point,turn,speed,interval,last,target,damage_bonus=bonus,speed_signature=(bat,base_speed,agi,extra),speed_source=speed_note,
                    quelling_bonus=quell.bonus,quelling_known=quell.known,quelling_note=quell.note,quelling_items=quell.signature,quelling_ranged=ranged==2)

    def verify_quelling(self,hero):
        # Revoke a bonus-dependent order if the item left an active slot while
        # the already computed damage/choice was waiting for the sender.
        try:
            p=self.verify_probe;d=self.verify_dot
            if p is None or not hero.quelling_known or not hero.quelling_items:return False
            p.deadline=time.perf_counter()+.025;p.reads=0;d.esys=0
            ep,handle=hero.uid;got=d.get_entity(handle&0x7fff)
            if not got or got[0]!=ep or identity(d,p,ep,got[1])!=handle:return False
            raw=p.read(ep+offset(p.classes,'C_DOTA_BaseNPC','m_iAttackCapabilities'),4)
            if raw!=struct.pack('<i',2 if hero.quelling_ranged else 1):return False
            clock=d.curtime()
            if not isinstance(clock,(int,float)) or not math.isfinite(clock):return False
            return active_items(d,p,hero.uid,clock)==hero.quelling_items
        except Exception:return False

    def verify_target(self,uid,hero_uid,screen,hp):
        # Independent read-only verifier on the existing sender thread; does not
        # share Probe budgets or Dot name caches with background sampling.
        try:
            p=self.verify_probe;d=self.verify_dot
            if p is None:return False
            p.deadline=time.perf_counter()+.04;p.reads=0;d.esys=0
            for ep,handle in (uid,hero_uid):
                got=d.get_entity(handle&0x7fff)
                if not got or got[0]!=ep or identity(d,p,ep,got[1])!=handle:return False
            ep=uid[0]
            health=p.read(ep+offset(p.classes,'C_BaseEntity','m_iHealth'),4)
            life=p.read(ep+offset(p.classes,'C_BaseEntity','m_lifeState'),1)
            if not health or life!=b'\0' or abs(struct.unpack('<i',health)[0]-hp)>2 or d.dormant_state(ep) is not False:return False
            d.screen=d._screen_size();point=self.aim_point(d,p,ep,d.pos(ep),d.raw_matrix())
            return bool(point and math.hypot(point[0]-screen[0],point[1]-screen[1])<=8)
        except Exception:return False

    def capture(self,hint):
        at=time.perf_counter();d=self.dot;d.esys=0
        p=self.probe;p.deadline=at+.10;p.reads=0
        d.screen=d._screen_size();clock=d.curtime();matrix=d.raw_matrix()
        if not isinstance(clock,(int,float)) or not math.isfinite(clock) or not matrix:raise ValueError('Нет игрового времени/матрицы')
        hero=self.unit(hint[0],hint[1],'hero',clock,matrix)
        if not hero:return Frame(at,clock,tuple(d.screen),error='Нет живого наблюдаемого героя')
        candidates=[];incomplete=False;errors=[];counts={'scanned':len(self.entries),'named':0,'near':0,'unsupported':0,'invalid':0,'unavailable':0}
        for ep,name in self.entries.values():
            if not candidate_name(name):continue
            counts['named']+=1
            pos=d.pos(ep)
            if not pos or not all(math.isfinite(v) for v in pos) or distance(hero.pos,pos)>2400:continue
            candidates.append((distance(hero.pos,pos),ep,name))
        candidates.sort();counts['near']=len(candidates)
        if len(candidates)>64:incomplete=True
        creeps=[];towers=[]
        for _,ep,name in candidates[:64]:
            try:
                cls=p.runtime_type(ep)
                kind=entity_kind(cls,name)
                if kind is None and cls=='C_DOTA_BaseNPC_Creep':
                    raw=p.read(ep+offset(p.classes,'C_DOTA_BaseNPC','m_iszUnitName'),8)
                    ptr=struct.unpack('<Q',raw)[0] if raw else 0
                    actual=d.cs(ptr,128) if 0x10000<=ptr<1<<48 else None
                    kind=entity_kind(cls,actual)
                if kind is None:
                    counts['unsupported']+=1;errors.append('class '+str(cls)+': '+name);continue
                unit=self.target_unit(ep,name,clock,matrix,cls) if kind=='creep' else self.unit(ep,name,kind,clock,matrix)
                if unit:(creeps if kind=='creep' else towers).append(unit)
                else:counts['unavailable']+=1
            except ValueError as exc:
                counts['invalid']+=1;errors.append(name+': '+str(exc))
                # A broken tower sample must not silently remove its predicted damage.
                if 'tower' in name:incomplete=True
        for u in creeps:
            counts[u.lane_type]=counts.get(u.lane_type,0)+1
            if u.lane_type=='siege' and u.incoming_multiplier is None:
                counts['siege_damage_unknown']=counts.get('siege_damage_unknown',0)+1
                errors.append('siege damage: '+self.modifier_stats(u.uid,True)[1])
        return Frame(at,clock,tuple(d.screen),hero,tuple(creeps),tuple(towers),
                     ('; '.join(errors[:2]) if errors and incomplete else ''),incomplete,tuple(sorted(counts.items())),tuple(errors[:6]))

    def initialize(self):
        # Construct locally and publish only after ALL mandatory checks succeed.
        # Previously an exception after assigning assets/probe could bypass the
        # initialization branch on the next iteration (notably layout=None).
        import dota_esp
        raw=(self.root/'client_dll.json').read_bytes()
        classes=json.loads(raw)['client.dll']['classes']
        probe=Probe(self.dot,classes,seconds=5,max_reads=10000)
        build=check_build(raw,probe.image,vars(dota_esp))
        verify_probe=Probe(self.verify_dot,classes,seconds=5,max_reads=300)
        check_build(raw,verify_probe.image,vars(dota_esp))
        assets=AttackData(game_directory(self.dot))
        layout=reviewed_layout(build)
        stats=None;note='modifier layout unavailable; base + agility estimate'
        if not build.reviewed_stats:
            # Legacy profiles remain optional; no layout is inferred from None.
            try:
                config=json.loads((self.root/'ks_profiles.json').read_text(encoding='utf-8'))
                layout=config.get('settings',{}).get('live_modifier_layout')
            except (OSError,ValueError,AttributeError) as exc:
                note='modifier profile unavailable: '+str(exc)[:100]
        if layout is not None:
            try:
                stats=LiveStats(classes,layout,hashlib.sha256(raw).hexdigest(),
                                require_siege_modifier=build.reviewed_stats)
                # A syntactically valid but stale optional profile is unavailable,
                # never a reason to bypass the mandatory public-build gate.
                c=stats.layout
                if (c['schema_sha256'],c['client_timestamp'],c['client_size'])!=(build.schema,build.timestamp,build.size):
                    raise ValueError('optional modifier profile belongs to another build')
                note='partial live IAS; per-read owner/type/stability checks'
            except (ValueError,TypeError,KeyError) as exc:
                stats=None;note='modifier layout unavailable: '+str(exc)[:100]
        self.verify_probe=verify_probe;self.assets=assets;self.stats=stats
        self.stats_note=note;self.stat_cache={};self.quell_cache={};self.build_label=build.label
        self.probe=probe

    def run(self):
        while self.running:
            start=time.perf_counter()
            try:
                hint=self.hero_hint()
                if not self.requested() or hint is None:
                    self.latest=Frame(start,0,(0,0),error='Ожидание включения / локального героя')
                    time.sleep(.10);continue
                if self.probe is None:self.initialize()
                ready=self.scan.poll()
                if ready is not None:
                    data,error,_=ready
                    if error:raise ValueError(error)
                    self.entries=data or {};self.scan_at=start
                if start-self.scan_at>1.0:self.scan.request()
                self.latest=self.capture(hint)
            except Exception as exc:
                self.latest=Frame(start,0,tuple(getattr(self.dot,'screen',(0,0))),error=str(exc)[:220])
                time.sleep(5. if self.probe is None else .25)
            time.sleep(max(.002,.025-(time.perf_counter()-start)))
