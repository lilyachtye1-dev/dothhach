"""Build-pinned experimental launch inference for two launch sources plus a bounded Sniper phase guard; Chaos Bolt diagnostics.
No guessed global projectile RVA, nearby-caster heuristic or fake projectile feed.
"""
from dataclasses import dataclass
import hashlib
import math
import struct
import time
from dodger import Threat
from dodge_diagnostics import CastJournal
from dodge_assassinate import MarkedAssassinate, Mark, MARK, EVIDENCE, release_clock_matches

SOURCES = {
 'sniper_assassinate': ('C_DOTA_Ability_Sniper_Assassinate', 2500., True),
 'windrunner_shackleshot': ('C_DOTA_Ability_Windrunner_Shackleshot', 1650., False),
}
SCHEMA_SHA = '7dd78049388a98fa332c175cfa3bf648691da99ca070fc8e8f3b04fecf3e53d9'
CLIENT_TIMESTAMP, CLIENT_SIZE = 1790374934, 115027968


class UnstableCast(ValueError):
    """A torn phase/start sample: skip input now, retry within the continuity bound."""


@dataclass(frozen=True)
class Frame:
    at: float
    local: int
    threats: tuple = ()
    status: str = 'Нет данных'
    build_ok: bool = False
    observations: tuple = ()
    compact_reason: str = ""
    events: tuple = ()


class LaunchTracker:
    # Networked fields do not have to arrive in the same client sample.
    RELEASE_GRACE = .12

    def __init__(self):
        self.previous = {}; self.pending = {}; self.awaiting = {}; self.last_pos = None
        self.note = 'Нет подтверждённого запуска'

    @staticmethod
    def same_cast(old, new, release=False, edge=None):
        return (old['target']==new['target'] and old['handle']==new['handle'] and
                old['name']==new['name'] and old.get('ability_handle')==new.get('ability_handle') and
                (old['start']==new['start'] or (release and new['start'] in (-1.,0.)) or
                 (release and old['name']=='sniper_assassinate' and
                  release_clock_matches(old['start'],new['start'],old.get('phase_begin'),edge))))

    def update(self, samples, me, now):
        if self.last_pos:
            old, at = self.last_pos
            if math.dist(old[:2],me.pos[:2]) > 80+650*max(0,now-at):
                self.previous.clear(); self.pending.clear(); self.awaiting.clear()
        self.last_pos = (me.pos,now)
        next_previous = {}; seen = set(); self.note='Нет подтверждённого запуска'
        for s in samples:
            key = (s['caster'],s['entity']); seen.add(key)
            prev = self.previous.get(key); stable = 1
            continuous = prev is not None and 0 < now-prev['at'] <= .15
            began = None
            if s['phase']:
                self.awaiting.pop(key,None)
                if continuous and prev['phase'] and self.same_cast(prev,s):
                    stable=prev['stable']+1; began=prev.get('phase_begin')
                elif continuous and not prev['phase'] and prev['cd']<=.02:
                    began=now
                self.note='Наблюдаем фазу каста на вас (%d)'%stable
            elif continuous and prev['phase']:
                if prev['stable']>=2 and prev['cd']<=.02 and self.same_cast(prev,s,release=True,edge=now):
                    self.awaiting[key]=(dict(prev),now)
            waiting=self.awaiting.get(key)
            if waiting:
                cast,edge=waiting
                if now-edge > self.RELEASE_GRACE or not self.same_cast(cast,s,release=True,edge=edge):
                    self.awaiting.pop(key,None)
                elif not s['phase'] and s['cd']>.1 and s['target']==me.entity:
                    _,speed,damage=SOURCES[s['name']]
                    dist=math.dist(cast['pos'][:2],me.pos[:2])
                    early=cast['at']-.15+max(0,dist-150)/(speed+650)
                    late=now+(dist+150)/(speed-650)+.15
                    uid=(s['caster'],s['entity'],cast['start'],s['handle'],s.get('ability_handle'))
                    self.pending[uid]=Threat(uid,s['caster'],me.entity,s['name'],early,late,now,damage=damage)
                    self.awaiting.pop(key,None);self.note='Запуск подтверждён переходом фазы и CD'
                else:
                    self.note='Фаза закончилась, нового CD нет: отмена / No Cooldown / задержка'
            next_previous[key]=dict(s,at=now,stable=stable,phase_begin=began)
        self.previous=next_previous
        self.awaiting={k:v for k,v in self.awaiting.items() if k in seen and now-v[1]<=self.RELEASE_GRACE}
        from dataclasses import replace
        identities={(s['caster'],s['entity'],s['handle'],s.get('ability_handle')) for s in samples}
        self.pending={k:replace(t,observed=now) for k,t in self.pending.items()
                      if (t.source,k[1],k[3],k[4]) in identities and t.target==me.entity and now<=t.latest}
        return tuple(self.pending.values())


class ThreatReader:
    def __init__(self, combat, schema_path):
        self.combat = combat
        self.schema_ok = hashlib.sha256(schema_path.read_bytes()).hexdigest() == SCHEMA_SHA
        self.tracker = LaunchTracker()
        self.hero = None
        self.checked = False
        self.build_ok = False
        self.journal = CastJournal()
        self.marker_probes = {}
        self.marker_results = {}
        self.marked = MarkedAssassinate()
        self.mark_cache = None
        self.mark_retryable = False
        self.mark_poll_at = -1.
        self.mark_signature = None
        self.mark_phase = None
        self.last_mark_flight = None

    def check_build(self,dot):
        from collect_live import Probe
        probe = Probe(dot,self.combat.classes,seconds=.03,max_reads=30)
        return probe.image['timestamp'] == CLIENT_TIMESTAMP and probe.image['size'] == CLIENT_SIZE

    def capture(self, dot, heroes):
        started = time.perf_counter()
        local = [h['_ks'] for h in heroes if h.get('is_local') and h.get('_ks')]
        me = local[0] if len(local)==1 else None
        if not me or not me.alive or not me.pos or not all(math.isfinite(x) for x in me.pos):
            self.tracker = LaunchTracker()
            self.reset_marked()
            self.journal.retain(set()); self.marker_probes.clear(); self.marker_results.clear()
            return Frame(started,0,status='Нет единственного живого героя',events=self.journal.snapshot())
        if self.hero != me.entity:
            self.tracker = LaunchTracker(); self.hero = me.entity; self.checked = False
            self.reset_marked()
            self.journal.retain(set()); self.marker_probes.clear(); self.marker_results.clear()
        try:
            if not self.checked:
                self.build_ok = self.schema_ok and self.check_build(dot)
                self.checked = True
            if not self.build_ok:
                return Frame(started,me.entity,status='Сборка/схема не совпадает с базой репозитория; ввод запрещён')
            samples = []
            observations = []
            candidates = 0
            seen = set(); reasons = []
            marked_candidates = []
            # Include unreadable/hidden/duplicate potential casters in ambiguity checks.
            possible = [h for h in heroes if not h.get('is_local') and
                        ((h.get('designer') or getattr(h.get('_ks'),'name',''))=='npc_dota_hero_sniper' or
                         any(a.get('name')=='sniper_assassinate' for a in h.get('abilities') or [])) and
                        (h.get('_ks') is None or h['_ks'].team!=me.team)]
            v = self.combat.value
            for h in heroes:
                unit = h.get('_ks')
                if not unit or unit.local or not unit.alive or not unit.visible or unit.illusion or unit.team == me.team:
                    if (h.get('designer') or (unit.name if unit else '')) in ('npc_dota_hero_sniper','npc_dota_hero_windrunner'):
                        observations.append('caster skipped: '+('unreadable' if not unit else 'local hero' if unit.local else 'ally/dead/fog/illusion'))
                    continue
                if unit.team not in (2,3) or not unit.pos: continue
                if not 0 <= started-unit.at <= .15: continue
                if unit.name in ('npc_dota_hero_sniper','npc_dota_hero_windrunner') and not any(a.get('name') in SOURCES for a in h.get('abilities') or []):
                    observations.append(unit.name+': supported ability missing from snapshot')
                for ab in h.get('abilities') or []:
                    name,ep = ab.get('name'),ab.get('ent')
                    if name=='chaos_knight_chaos_bolt' and ep:
                        diagnostic=self.observe_chaos(dot,unit,ep,me,started,seen)
                        observations.append(diagnostic);reasons.append((0,diagnostic))
                        continue
                    if name not in SOURCES or not ep: continue
                    # Revalidate pointer AND designer name each sample (no entity walk here).
                    candidates += 1
                    # abilities_of publishes ep, not index; resolve through entity identity.
                    ident_off = self.combat.offset('CEntityInstance','m_pEntity')
                    raw = dot.rb(ep+ident_off,8)
                    identity = struct.unpack('<Q',raw)[0] if raw and len(raw)==8 else 0
                    raw = dot.rb(identity,20) if identity else None
                    if not raw or len(raw)!=20 or struct.unpack_from('<Q',raw)[0]!=ep:
                        observations.append(name+': ability identity unreadable');continue
                    ah = struct.unpack_from('<I',raw,16)[0]
                    current = dot.get_entity(ah & 0x7fff)
                    if not current or current != (ep,name):
                        observations.append(name+': entity name/address mismatch');continue
                    cls,_,_ = SOURCES[name]
                    # Always observe phase/CD/start, including invalid or other targets.
                    # These fields are diagnostics, not permission to act on an unknown target.
                    phase = v(dot,ep,'C_DOTABaseAbility','m_bInAbilityPhase','<B')
                    cd = v(dot,ep,'C_DOTABaseAbility','m_fCooldown','<f')
                    start = v(dot,ep,'C_DOTABaseAbility','m_flCastStartTime','<f')
                    target = v(dot,ep,cls,'m_hTarget','<I')
                    label = 'Assassinate' if name=='sniper_assassinate' else 'Shackleshot'
                    cast_point=self.assassinate_cast_point(dot,ep) if name=='sniper_assassinate' else None
                    valid_fields = phase in (0,1) and math.isfinite(cd) and 0<=cd<=3600 and math.isfinite(start) and start>=-1 and (not phase or start>=0)
                    reason = 'verified_local'
                    if not valid_fields:
                        reason = 'invalid_cast_fields'
                    elif target in (0,0xffffffff):
                        reason = 'invalid_handle'
                    elif (target & 0x7fff) != me.idx:
                        reason = 'other_index'
                    else:
                        # Full serial-bearing handle AND local identity backlink.
                        raw = dot.rb(me.entity+ident_off,8)
                        local_ident = struct.unpack('<Q',raw)[0] if raw and len(raw)==8 else 0
                        raw = dot.rb(local_ident,20) if local_ident else None
                        if not raw or len(raw)!=20 or struct.unpack_from('<Q',raw)[0]!=me.entity:
                            reason = 'local identity unreadable'
                        elif struct.unpack_from('<I',raw,16)[0] != target:
                            reason = 'SERIAL MISMATCH'
                    if (target != v(dot,ep,cls,'m_hTarget','<I') or
                            phase != v(dot,ep,'C_DOTABaseAbility','m_bInAbilityPhase','<B')):
                        reason = 'unstable_fields'
                    target_index = None if target in (0,0xffffffff) else target & 0x7fff
                    observations.append('%s: phase=%s cd=%.3f start=%.3f target=%#x (idx=%s), local_idx=%d; %s' %
                                        (name,phase,cd,start,target,'invalid' if target_index is None else target_index,me.idx,reason))
                    key = (unit.entity,ep,ah); seen.add(key)
                    self.journal.observe(key,started,(me.entity,target,phase,cd>.1,start if phase and math.isfinite(start) else None,reason,cast_point),
                        local=hex(me.entity),source=hex(unit.entity),ability=hex(ep),ability_handle=hex(ah),name=name,
                        phase=phase,cast_point=cast_point,cd=round(cd,4) if math.isfinite(cd) else None,
                        start=round(start,4) if math.isfinite(start) else None,target_raw=hex(target),
                        target_index=target_index,local_index=me.idx,target_state=reason)
                    mark_eligible = (name=='sniper_assassinate' and unit.name=='npc_dota_hero_sniper' and
                                     len(possible)==1 and valid_fields and reason=='invalid_handle')
                    if mark_eligible:
                        marked_candidates.append(dict(caster=unit.entity,entity=ep,name=name,
                            phase=bool(phase),cd=cd,start=start,pos=unit.pos,ability_handle=ah,
                            local_unit=me,caster_unit=unit,cast_point=cast_point))
                    if not mark_eligible and name=='sniper_assassinate' and valid_fields and reason not in ('verified_local','unstable_fields'):
                        self.probe_markers(dot,me,key,bool(phase),started)
                    else:
                        self.marker_probes.pop(key,None)
                    if reason != 'verified_local':
                        explanation = ('нет валидной цели' if reason=='invalid_handle' else
                                       'цель не вы' if reason=='other_index' else
                                       'цель не подтверждена' if reason=='SERIAL MISMATCH' else 'данные не подтверждены')
                        reasons.append((0 if phase else 1, label+': '+('КАСТ · ' if phase else '')+explanation))
                        continue
                    samples.append(dict(caster=unit.entity,entity=ep,name=name,target=me.entity,
                                        handle=target,phase=bool(phase),cd=cd,start=start,pos=unit.pos,ability_handle=ah))
            self.journal.retain(seen)
            self.marker_probes = {k:v for k,v in self.marker_probes.items() if k in seen}
            self.marker_results = {k:v for k,v in self.marker_results.items() if k in seen}
            mark_threats = ()
            mark_active = len(marked_candidates)==1
            if mark_active:
                candidate = marked_candidates[0]
                try:
                    candidate['handle'] = self.entity_handle(dot,me)
                    candidate['caster_handle'] = self.entity_handle(dot,candidate['caster_unit'])
                    phase_key = (candidate['entity'],candidate['ability_handle'],candidate['phase'])
                    mark = self.read_mark(dot,me,started,force=phase_key!=self.mark_phase or self.marked.displaced(me,started))
                    self.mark_phase = phase_key
                    # Bracket the potentially slower modifier scan with identity/cast checks.
                    if (candidate['handle']!=self.entity_handle(dot,me) or
                            candidate['caster_handle']!=self.entity_handle(dot,candidate['caster_unit']) or
                            self.ability_handle(dot,candidate['entity'],'sniper_assassinate')!=candidate['ability_handle']):
                        raise ValueError('mark/cast identity changed during read')
                    if (v(dot,candidate['entity'],SOURCES['sniper_assassinate'][0],'m_hTarget','<I') not in (0,0xffffffff)):
                        raise ValueError('target changed during mark read')
                    if (candidate['phase']!=bool(v(dot,candidate['entity'],'C_DOTABaseAbility','m_bInAbilityPhase','<B')) or
                            candidate['start']!=v(dot,candidate['entity'],'C_DOTABaseAbility','m_flCastStartTime','<f')):
                        raise UnstableCast('cast fields changed during mark read')
                    if candidate['cast_point']!=self.assassinate_cast_point(dot,candidate['entity']):
                        raise UnstableCast('cast point changed during mark read')
                    if mark is None and self.mark_retryable and candidate['phase'] and self.marked.cast:
                        raise UnstableCast('modifier list changed during phase; fresh retry required')
                    mark_threats = self.marked.update(candidate,mark,me,started)
                    if self.marked.notice:
                        self.journal.add('tracking_change',started,local=hex(me.entity),**self.marked.notice)
                    observations.append(self.marked.note+'; path=local_mark')
                    if mark_threats and (mark_threats[0].id,mark_threats[0].evidence)!=self.last_mark_flight:
                        self.last_mark_flight=(mark_threats[0].id,mark_threats[0].evidence)
                        self.journal.add('cast_guard' if mark_threats[0].evidence=='local_mark_phase' else 'launch',started,evidence=mark_threats[0].evidence,local=hex(me.entity),
                                         source=hex(candidate['caster']),cast_point=candidate['cast_point'],guard_point=self.marked.guard_point,earliest=mark_threats[0].earliest,
                                         latest=mark_threats[0].latest)
                except UnstableCast as exc:
                    # No threat from this frame. Keep only the prior bounded episode;
                    # next valid update still requires <=150 ms continuity and fresh mark.
                    previous=self.marked.previous
                    if previous and started-previous['at']>self.marked.GAP:
                        self.reset_marked('Assassinate: повторное чтение превысило 150 мс')
                    else:
                        note='Assassinate: переход каста · повторное чтение'
                        if self.marked.note!=note:
                            self.journal.add('tracking_retry',started,reason=str(exc),use_for_input=False)
                        self.mark_cache=None;self.mark_phase=None
                        self.marked.note=note
                    observations.append(self.marked.note)
                except Exception as exc:
                    self.reset_marked('Assassinate: метка не подтверждена · '+str(exc)[:90])
                    observations.append(self.marked.note)
            else:
                self.reset_marked('Assassinate: неоднозначный/непрочитанный кастер или цель')
            threats = self.tracker.update(samples,me,started)+mark_threats
            compact = (self.tracker.note if samples else min(reasons)[1] if reasons else
                       'Нет читаемой Assassinate / Shackleshot')
            if mark_active: compact=self.marked.note
            elif len(possible)>1 and not samples:
                compact="Assassinate: несколько возможных кастеров · пропуск"
            target_count=len(samples)+int(bool(self.marked.cast and self.marked.cast['token']) or bool(mark_threats))
            return Frame(started,me.entity,threats,'Sniper / WR: способностей %d, цель — вы %d, запусков %d, предзащит %d; оценка, не projectile manager' % (candidates,target_count,sum(t.evidence!='local_mark_phase' for t in threats),sum(t.evidence=='local_mark_phase' for t in threats))+' · '+compact,True,tuple(observations[:12]),compact,self.journal.snapshot())
        except Exception as exc:
            self.tracker = LaunchTracker()
            self.reset_marked()
            return Frame(started,me.entity,status='Чтение угроз заблокировано: '+str(exc)[:160],
                         events=self.journal.snapshot())

    def assassinate_cast_point(self,dot,ep):
        try:
            value=self.combat.value(dot,ep,'C_DOTABaseAbility','m_flOverrideCastPoint','<f')
            # -1 = no override: pinned base cast point 2 s, consistent with user captures.
            # Zero/unknown is NOT guessed as a two-second cast.
            return 2. if value==-1. else value if math.isfinite(value) and .25<=value<=5. else None
        except Exception:return None

    def observe_chaos(self,dot,unit,ep,me,now,seen):
        name='chaos_knight_chaos_bolt'
        try:
            handle=self.ability_handle(dot,ep,name)
            caster_handle=self.entity_handle(dot,unit)
            v=self.combat.value
            phase=v(dot,ep,'C_DOTABaseAbility','m_bInAbilityPhase','<B')
            cd=v(dot,ep,'C_DOTABaseAbility','m_fCooldown','<f')
            start=v(dot,ep,'C_DOTABaseAbility','m_flCastStartTime','<f')
            if phase not in (0,1) or not math.isfinite(cd) or not 0<=cd<=3600 or not math.isfinite(start):
                raise ValueError('invalid cast fields')
            # Raw in-object header only. No guessed vector layout/pointer following and
            # NO interpretation as the next target: "already hit" is not flight proof.
            off=self.combat.offset('C_DOTA_Ability_ChaosKnight_Chaos_Bolt','hAlreadyHitList')
            raw=dot.rb(ep+off,24)
            key=(unit.entity,ep,handle);seen.add(key)
            self.journal.observe(key,now,(me.entity,phase,cd>.1,start if phase else None,raw),
                local=hex(me.entity),source=hex(unit.entity),ability=hex(ep),ability_handle=hex(handle),
                caster_handle=hex(caster_handle),name=name,phase=phase,cd=round(cd,4),start=start,
                target_state='unsupported_target',use_for_input=False,
                already_hit_header=raw.hex() if raw and len(raw)==24 else None)
            return 'Chaos Bolt: phase=%d cd=%.2f; цель неизвестна, diagnostic only'%(phase,cd)
        except Exception as exc:
            return 'Chaos Bolt: диагностика недоступна · '+str(exc)[:90]

    def probe_markers(self, dot, me, key, phase, now):
        """1 Hz idle sampling, two observations during a phase, plus its end.
        Idle sampling still works when enemy phase/CD are not replicated. No input fallback.
        The approved modifier-vector layout is reused, not a guessed new offset.
        RTTI membership does NOT establish which caster or spell phase owns a mark.
        """
        old = self.marker_probes.get(key, (False, 0, 0.))
        was_phase, count, at = old
        if phase and not was_phase: count = 0
        due = ((phase and count<2 and (not was_phase or now-at>=.15)) or
               (was_phase and not phase) or (not phase and now-at>=1.))
        self.marker_probes[key] = (phase, count+int(due), now if due else at)
        if not due: return
        names = {'CDOTA_Modifier_Sniper_Assassinate', 'CDOTA_Modifier_Sniper_Assassinate_Trigger'}
        try:
            result = dict(self.combat.modifiers.present(dot,me.entity,names))
            signature = (phase,tuple(sorted(result.items())))
            fields = dict(present=result)
        except Exception as exc:
            signature = (phase,str(exc)[:180])
            fields = dict(unknown=signature[1])
        # Constant absence/unknown must not emit a line every second.
        if self.marker_results.get(key) != signature:
            self.marker_results[key] = signature
            self.journal.add('modifier_diagnostic',now,local=hex(me.entity),source=hex(key[0]),
                             phase=phase,use_for_input=False,**fields)

    def reset_marked(self, note='Assassinate: ждём свежие данные'):
        if self.marked.cast or self.marked.flight:
            self.journal.add('tracking_reset',time.perf_counter(),reason=note,use_for_input=False)
        self.marked.reset(note)
        self.mark_cache = None
        self.mark_retryable = False
        self.mark_poll_at = -1.
        self.mark_phase = None

    def entity_handle(self, dot, unit):
        handle = self.ability_handle(dot,unit.entity,unit.name)
        if (handle & 0x7fff)!=unit.idx:
            raise ValueError('hero index/serial identity mismatch')
        return handle

    def ability_handle(self, dot, entity, name):
        off = self.combat.offset('CEntityInstance','m_pEntity')
        raw = dot.rb(entity+off,8)
        identity = struct.unpack('<Q',raw)[0] if raw and len(raw)==8 else 0
        raw = dot.rb(identity,20) if identity else None
        if not raw or len(raw)!=20 or struct.unpack_from('<Q',raw)[0]!=entity:
            raise ValueError('identity backlink unreadable')
        handle = struct.unpack_from('<I',raw,16)[0]
        if handle in (0,0xffffffff) or dot.get_entity(handle & 0x7fff)!=(entity,name):
            raise ValueError('identity handle/name mismatch')
        return handle

    def read_mark(self, dot, me, now, force=False):
        # One bounded owner-checked scan per ~40 ms, or on a phase edge. Cached
        # results retain THEIR timestamp; reader ticks never rejuvenate stale data.
        if not force and 0<=now-self.mark_poll_at<.04:
            return self.mark_cache
        self.mark_poll_at=now
        self.mark_retryable=False
        try:
            tokens=dict(self.combat.modifiers.instances(dot,me.entity,{MARK}))
            token=tokens[MARK]
            from collect_live import pointer
            if type(token) is not int or (token!=0 and not pointer(token)):
                raise ValueError('invalid mark token')
            self.mark_cache=Mark(now,me.entity,token)
            signature=(me.entity,'ok',token)
            fields=dict(present=bool(token),instance=hex(token))
        except Exception as exc:
            self.mark_cache=None
            self.mark_retryable=str(exc) in ('modifier changed during read','modifier vector changed during read')
            signature=(me.entity,'unknown',str(exc)[:140])
            fields=dict(unknown=signature[2])
        if signature!=self.mark_signature:
            self.mark_signature=signature
            self.journal.add('local_mark',now,local=hex(me.entity),modifier=MARK,
                             use_for_input=True,**fields)
        return self.mark_cache
