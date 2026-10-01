"""Independent menu-controlled Dodger. Runs on the SAME sender/input thread as KS.
One action per threat; cooldown confirmation, quarantine, priority and hold prevent spam.
"""
from types import SimpleNamespace
import time
import json
import math
from ks_core import fresh
from dodger import DEFENSES, BY_NAME, choose_defense
from dodge_input import COUNTERSPELL, keyboard_reason
from dodge_recovery import CounterspellRecovery


class Dodger:
    def __init__(self, ks):
        self.ks = ks
        self.enabled = self.arm_pending = False
        self.requested = 0.
        self.disabled = {p.name for p in DEFENSES if not p.enabled}
        self.status = 'OFF · отдельное включение в меню'
        self.last_hero = None
        self.pending = None
        self.quarantined = set()
        self.alt_selfcast = False
        self.pending_meta = None
        self.recovery = CounterspellRecovery()
        self.keyboard_pause_until = 0.
        self.used = {}
        self.hold_until = self.pause_until = 0.
        self.error = ''
        self.off_reason = 'Включи Dodger на его странице'
        self.last_event = 'Действий ещё не было'
        self.last_event_at = 0.
        self.last_action = 'Нажатий ещё не было'
        self.event_sequence = 0
        self.log_at = -999.
        self.log_key = ''
        self.last_observations = ()
        self.last_decision_diagnostic = None
        self.decision_diagnostic_at = -999.

    def control(self, arm, disabled, menu_open):
        self.enabled = False
        if disabled is not None: self.disabled = set(disabled)
        self.arm_pending = arm is True
        self.requested = time.perf_counter() if self.arm_pending else 0.
        if self.arm_pending and self.hold_until > self.requested+1000 and self.pending is None:
            self.hold_until = 0.  # explicit re-arm after exiting Infest, not automatic resume
        if menu_open: self.arm_pending = False
        if self.arm_pending: self.recovery.request_reset()
        self.off_reason = 'Меню открыто или настройки изменены'
        self.status = 'Ожидание фокуса и свежих данных' if self.arm_pending else 'OFF · меню/настройки'
        self.ks.log.info('DODGER CONTROL arm=%s menu=%s mode=%s',arm,menu_open,'AUTO' if self.ks.settings.auto_cast else 'PREVIEW')

    def feedback(self, code, identity, now, reason):
        label=self.readable(identity[0])
        self.last_action=code+' · '+label
        self.last_event=self.last_action+' · '+reason
        self.last_event_at=now
        print('[DODGER '+self.last_event+']',flush=True)

    def sync(self, me, now):
        # Old cached cooldown values must not lift quarantine.
        for a in me.actions if me and fresh(me,now,self.ks.settings.stale) else ():
            if math.isfinite(a.cooldown) and a.cooldown > .1:
                self.quarantined.discard(a.identity)
                self.recovery.confirmed(a.identity)
        if self.pending:
            identity, old_cd, sent = self.pending
            meta=self.pending_meta
            same_owner=bool(me and (not meta or me.entity==meta['hero']))
            current = next((a for a in me.actions if a.identity == identity),None) if same_owner else None
            if current and me.at > sent and fresh(me,now,self.ks.settings.stale) and math.isfinite(current.cooldown) and current.cooldown > old_cd+.1:
                self.pending = None
                self.ks.log.info('DODGER CD_CONFIRMED %s latency=%.4fs (not a confirmed dodge)',identity,now-sent)
                if not meta or meta['hero']==self.last_hero:
                    self.feedback('CD подтверждён',identity,now,'активация, не подтверждение отражения')
                self.pending_meta=None
            elif now-sent >= 1.5:
                self.quarantined.add(identity); self.pending = None
                meta=self.pending_meta
                if identity[0]==COUNTERSPELL and meta:
                    self.recovery.failure(identity,meta['hero'],meta['threat'],meta['latest'],now,meta['sent'])
                reason=self.recovery.reason() if identity==self.recovery.identity else 'Повторы заблокированы до наблюдения CD'
                self.ks.log.warning('DODGER QUARANTINE no cooldown feedback: %s; %s',identity,reason)
                if not meta or meta['hero']==self.last_hero:self.feedback('Нет CD',identity,now,reason)
                self.pending_meta=None
        self.recovery.observe(me,now,self.ks.settings.stale)
        self.used = {k:expiry for k,expiry in self.used.items() if expiry >= now}

    def tick(self, heroes, age):
        try: return self._tick(heroes,age)
        except Exception as exc:
            self.enabled = self.arm_pending = False
            self.status = 'OFF · ошибка: '+str(exc)[:120]
            self.ks.log.exception('Dodger failed closed')
            return bool(self.pending or time.perf_counter()<self.hold_until)
        finally:
            self.trace(heroes,age)

    def trace(self, heroes, age):
        now=time.perf_counter()
        mode='AUTO' if self.ks.settings.auto_cast else 'PREVIEW'
        state='WAIT' if self.arm_pending else 'ON' if self.enabled else 'OFF'
        h=next((h for h in heroes if h.get('is_local') and h.get('_ks')), {})
        me=h.get('_ks');frame=h.get('_dodge')
        # Disabled module: no periodic heartbeat or stale reader/event replay.
        # Keep pending-cast feedback in sync()/feedback(), including failures.
        if state=='OFF':
            events=getattr(frame,'events',())
            if events:self.event_sequence=max(self.event_sequence,max(e[0] for e in events))
            key=('OFF',)
            if self.log_key!=key:
                self.log_key=key;self.log_at=now
                line='[DODGER %s OFF] Выключен'%mode
                self.ks.log.info(line);print(line,flush=True)
            return
        # Flush reader transitions independently of the .8 s console heartbeat.
        # A short phase observed by the reader must not disappear between sender ticks.
        events=getattr(frame,'events',())
        if events and events[0][0]>self.event_sequence+1:
            self.ks.log.warning('DODGER OBS overflow: %d older events unavailable',events[0][0]-self.event_sequence-1)
        for sequence,text in events:
            if sequence<=self.event_sequence: continue
            line='[DODGER OBS #%d] %s'%(sequence,text)
            self.ks.log.info(line)
            print(line,flush=True)
            self.event_sequence=sequence
        observed=getattr(frame,'observations',())
        if observed: self.last_observations=observed
        key=(mode,state,self.status,frame.status if frame else '',observed)
        if (key!=self.log_key and now-self.log_at>=.8) or now-self.log_at>=3:
            self.log_at=now;self.log_key=key
            line='[DODGER %s %s] hero=%s | %s | reader=%s | trace=%s'%(
                mode,state,me.name if me else '?',self.status,frame.status if frame else 'нет кадра',
                json.dumps(observed,ensure_ascii=False,separators=(',',':')))
            self.ks.log.info(line)
            print(line,flush=True)

    def _tick(self, heroes, age):
        now = time.perf_counter(); ks = self.ks; b = ks.backend; s = ks.settings
        units = [h['_ks'] for h in heroes if h.get('_ks')]
        local = [h for h in heroes if h.get('is_local')]
        h = local[0] if len(local)==1 else {}
        me = h.get('_ks'); frame = h.get('_dodge')
        self.sync(me,now)
        held = bool(self.pending or now < self.hold_until)
        context = b.context()
        menu_pause=(ks.ui_menu_open or getattr(ks,'menu_foreground',False) or b.down(45) or (not context and now<getattr(ks,'menu_transition_until',0.)))
        stop=('End' if b.down(35) else '') if ks.ui_menu_open or getattr(ks,'menu_foreground',False) else b.hard_stop_reason()
        hard = ((not context and not menu_pause) or bool(stop) or len(local)!=1)
        if me and self.last_hero not in (None,me.entity):
            hard = True; self.used.clear(); self.hold_until = 0.
            # Keep pending feedback and quarantine: switching heroes is not confirmation.
            self.arm_pending=False
            self.last_action='Нажатий на этом герое ещё не было'; self.last_event_at=0.
            self.last_event='Смена героя; история предыдущего героя только в логе'
            self.off_reason='Сменился защищаемый герой; выбери героя и включи Dodger заново'
            self.ks.log.info('DODGER HERO_CHANGED %s -> %s; explicit re-arm required',self.last_hero,me.entity)
        if me: self.last_hero = me.entity
        if hard:
            self.enabled = False
            # A request may wait for focus after menu closes, not survive in-game Enter/Tab or End.
            if stop:self.arm_pending = False
            if not me or self.last_hero == me.entity:
                if 'Сменился защищаемый' not in self.off_reason:
                    self.off_reason='Открыто меню' if ks.ui_menu_open else 'Нажато '+b.hard_stop_reason() if b.hard_stop_reason() else 'Dota не в фокусе' if not context else 'Нет единственного локального героя'
            self.status = ('ОЖИДАНИЕ · ' if self.arm_pending else 'OFF · ')+self.off_reason
            return held
        if menu_pause:
            if not (me and frame and frame.build_ok and frame.local==me.entity and fresh(me,now,s.stale) and
                    0<=age<=s.stale and 0<=now-frame.at<=min(.15,s.stale)):
                self.enabled=self.arm_pending=False
                self.off_reason='Нет свежего проверенного кадра в меню; включи Dodger заново'
                self.status='OFF · '+self.off_reason
                return held
            self.status=('ON' if self.enabled else 'WAIT' if self.arm_pending else 'OFF')+' · меню / ожидание фокуса; без ввода'
            return held
        valid = bool(me and frame and frame.build_ok and frame.local==me.entity and
                     0<=age<=s.stale and fresh(me,now,s.stale) and 0<=now-frame.at<=min(.15,s.stale) and
                     tuple(h.get('_ks_viewport',())) == tuple(context[3:5]))
        if not valid:
            self.enabled = False
            self.off_reason=(frame.status if frame and not frame.build_ok else 'Устарел кадр/нет героя/не совпадает геометрия') if frame else 'Нет кадра угроз'
            self.status = ('ОЖИДАНИЕ · ' if self.arm_pending else 'OFF · ')+self.off_reason
            if self.arm_pending and now-self.requested>20: self.arm_pending=False
            return held
        bindings = set(s.item_keys) | {ord(k) for k in 'QWERDF'}
        held_keys={k for k in bindings if b.down(k)}
        if frame.threats:
            diagnostic=(me.busy,h.get('_ks_busy_reason',''),h.get('_ks_busy_verified'),tuple(sorted(held_keys)))
            if diagnostic!=self.last_decision_diagnostic and now-self.decision_diagnostic_at>=.1:
                self.last_decision_diagnostic=diagnostic;self.decision_diagnostic_at=now
                ks.log.info('DODGER INPUT_STATE threats=%d busy=%s reason=%s verified=%s held_game_keys=%s',
                            len(frame.threats),*diagnostic)
        busy_reason=h.get('_ks_busy_reason','')
        known_names={a.name for a in me.actions}|{a.get('name') for a in h.get('abilities',())}
        interrupt_cast=bool(me.busy and (h.get('_ks_busy_verified') is True if '_ks_busy_verified' in h else busy_reason in known_names))
        owns_counterspell=(me.name=='npc_dota_hero_antimage' and COUNTERSPELL not in self.disabled and
                           any(a.name==COUNTERSPELL for a in me.actions))
        if self.arm_pending and now-self.requested>20:self.arm_pending=False
        if self.arm_pending and me.alive and (not me.busy or interrupt_cast):
            self.enabled=True;self.arm_pending=False;self.off_reason=''
            self.ks.log.info('DODGER ARMED hero=%s mode=%s',me.name,'AUTO' if s.auto_cast else 'PREVIEW')
        if not self.enabled:
            self.status=('ОЖИДАНИЕ · свежие данные / состояние героя' if self.arm_pending else 'OFF · '+(self.off_reason or 'Включи Dodger отдельно'));return held
        if not me.alive or (me.busy and not interrupt_cast) or b.down(91) or b.down(92):
            self.status='ON · пауза: '+('герой мёртв' if not me.alive else 'герой кастует/занят: состояние неизвестно' if me.busy else 'клавиша Win');return held
        if now<self.pause_until:
            self.status='ON · повторная проверка после пропущенного ввода';return held
        if held:
            self.status='ON · ожидание CD / защитное окно; KS приостановлен'; return True
        # Do not overlap a KS command that has already been sent or may be partially inserted.
        if not ks.ledger.ready(now):
            self.status='ON · ожидаем завершения команды Killsteal'; return False
        if (COUNTERSPELL not in self.disabled and self.recovery.identity in self.quarantined and
                self.recovery.allows(me,frame.threats,self.used,now,s.stale)):
            self.quarantined.discard(self.recovery.identity)
            self.recovery.unlocked()
            self.ks.log.info('DODGER RECOVER Counterspell: ready >=0.5s; NEW threat only; not a CD confirmation')
            self.feedback('Готов к новой угрозе',self.recovery.identity,now,'предыдущая попытка НЕ подтверждена')
        notes=[]
        decision = choose_defense(me,units,frame.threats,now,context[3:5],s,self.disabled,
                                  self.quarantined,self.used,notes=notes,held_keys=held_keys,interrupt_cast=interrupt_cast,alt_selfcast=self.alt_selfcast)
        if decision is None:
            reader_reason=getattr(frame,'compact_reason','') if not frame.threats else ''
            self.status='ON · '+(reader_reason or ' · '.join(self.readable(x) for x in notes[:2]) or 'Ожидаем угрозу')
            if owns_counterspell and self.recovery.identity in self.quarantined and any(t.target==me.entity for t in frame.threats):
                self.status='ON · Counterspell: '+self.recovery.reason()
            return False
        label=self.readable(decision.profile.name+' ← '+decision.threat.name)
        if not s.auto_cast:
            self.status='PREVIEW · '+label+' · БЕЗ ВВОДА'
            self.last_event='PREVIEW: выбран '+label+'; клавиша не нажималась'
            self.last_action='PREVIEW · '+self.readable(decision.profile.name)+' · без ввода'
            self.last_event_at=now
            return False
        # Reuse the existing guarded quickcast backend, not a second input implementation.
        p=decision.profile
        profile=SimpleNamespace(needs_cursor=p.needs_cursor,self_cast=p.cast=='self_alt',
                                cast='unit' if p.cast in ('self','ally','enemy') else 'point')
        command=SimpleNamespace(target=decision.target,caster=me,action=decision.action,key=decision.key,profile=profile)
        input_settings=SimpleNamespace(auto_cast=s.auto_cast,toggle_vk=0,stale=s.stale,aim_y=s.aim_y,
                                       defensive_input=True,defense_item_keys=s.item_keys,defense_alt_selfcast=self.alt_selfcast)
        def armed():
            t=time.perf_counter()
            proof_age=min(s.stale,.075) if decision.threat.evidence in ('local_mark_launch','local_mark_phase') else s.stale
            return (self.enabled and not ks.ui_menu_open and fresh(me,t,s.stale) and
                    0<=t-frame.at<=min(.15,s.stale) and 0<=t-decision.threat.observed<=proof_age and t-now<=.03 and
                    not b.down(decision.key) and not b.down(45))
        mouse_at_submit=(bool(b.down(1)),bool(b.down(2)))
        started=time.perf_counter()
        ks.log.info('DODGER INPUT_ATTEMPT source=%s threat=%s busy=%s verified=%s held_game_keys=%s early_in=%.4f',
                    p.name,decision.threat.id,busy_reason,interrupt_cast,sorted(held_keys),decision.threat.earliest-started)
        try: result,reason=b.cast(command,input_settings,context,armed)
        except Exception as exc: result,reason='failed',str(exc)
        if result=='skipped':
            ks.log.info('DODGER INPUT_SKIPPED source=%s evidence=%s reason=%s',p.name,decision.threat.evidence,reason)
            self.status='ON · ввод пропущен: '+reason
            self.pause_until=now+.03
            return False
        # Reserve even on partial/uncertain input. Never retry this threat.
        self.used[decision.threat.id]=decision.threat.latest+2
        self.pending=(decision.action.identity,decision.action.cooldown,started)
        self.pending_meta=dict(hero=me.entity,threat=decision.threat.id,latest=decision.threat.latest,sent=result=='sent')
        self.hold_until=started+decision.hold
        if result!='sent':
            self.enabled=False; self.arm_pending=False
            ks.toggle.enabled=False; ks.ui_arm_pending=False
            ks.off_reason='Ошибка ввода Dodger; оба модуля OFF'
            self.off_reason='Ошибка защитного ввода: '+reason[:160]
        self.status=('SENT' if result=='sent' else 'FAILED / OFF')+' · '+label
        self.last_event=self.status+' · '+reason
        self.last_action=('SENT' if result=='sent' else 'FAILED')+' · '+self.readable(decision.profile.name)
        self.last_event_at=now
        self.ks.log.info('DODGER %s mode=%s evidence=%s input_policy=%s mouse_LR=%s: %s',
                         self.status,p.mode,decision.threat.evidence,
                         'defense_priority',mouse_at_submit,reason)
        print('[DODGER '+self.status+' · '+reason+']',flush=True)
        return True

    @staticmethod
    def readable(text):
        names={'antimage_counterspell':'Counterspell','puck_phase_shift':'Phase Shift','item_manta':'Manta Style',
               'item_lotus_orb':'Lotus Orb','item_cyclone':"Eul’s Scepter",'item_wind_waker':'Wind Waker',
               'phantom_lancer_doppelwalk':'Doppelganger','nyx_assassin_spiked_carapace':'Spiked Carapace',
               'naga_siren_mirror_image':'Mirror Image','chaos_knight_phantasm':'Phantasm',
               'tusk_snowball':'Snowball','life_stealer_infest':'Infest',
               'sniper_assassinate':'Assassinate','windrunner_shackleshot':'Shackleshot'}
        for key,value in names.items(): text=text.replace(key,value)
        return text

    def dashboard(self, heroes):
        h=next((h for h in heroes if h.get('is_local') and h.get('_ks')), {})
        me=h.get('_ks');frame=h.get('_dodge')
        actions={a.name:a for a in me.actions} if me else {}
        titles={'item_manta':'Manta Style','antimage_counterspell':'Counterspell','puck_phase_shift':'Phase Shift',
                'phantom_lancer_doppelwalk':'Doppelganger','naga_siren_mirror_image':'Mirror Image',
                'chaos_knight_phantasm':'Phantasm','nyx_assassin_spiked_carapace':'Spiked Carapace',
                'item_lotus_orb':'Lotus Orb','item_cyclone':"Eul's Scepter",'item_wind_waker':'Wind Waker',
                'life_stealer_infest':'Infest','tusk_snowball':'Snowball','ember_spirit_sleight_of_fist':'Sleight of Fist',
                'monkey_king_mischief':'Mischief','alchemist_chemical_rage':'Chemical Rage',
                'lone_druid_true_form':'True Form','phoenix_supernova':'Supernova'}
        modes={'disjoint':'Сброс снаряда','block_reflect':'Блок / отражение','invulnerable':'Неуязвимость',
               'damage':'Защита от урона','reflect':'Только отражение'}
        rows=[]
        for p in DEFENSES:
            if not me or p.hero not in ('*',me.name.removeprefix('npc_dota_hero_')): continue
            a=actions.get(p.name)
            key=self.ks.settings.item_keys[a.slot] if a and p.key=='inventory' and 0<=a.slot<6 else None
            binding=chr(key) if key else p.key if p.key!='inventory' else '—'
            reason=p.blocked or ('Не отменяет все вторичные эффекты/исходное попадание.' if p.mode in ('damage','reflect') else 'Реакция по подтверждённой цели и оценке запуска.')
            if not p.blocked:reason+=' Приоритет над ручными клавишами/мышью; собственная удерживаемая клавиша не перенажимается.'
            known=a is not None
            ready=bool(a and a.level>0 and a.activated and not a.hidden and not a.casting and
                       math.isfinite(a.cooldown) and 0<=a.cooldown<=.02 and
                       math.isfinite(a.mana) and 0<=a.mana<=me.mana and
                       (p.key!='inventory' or me.inventory_known and 0<=a.slot<6))
            state=('unsupported' if p.blocked else 'absent' if not a and me.inventory_known and p.key=='inventory' else
                   'unknown' if not a else 'quarantined' if a.identity in self.quarantined else
                   'unlearned' if a.level<=0 else 'cooldown' if a.cooldown>.02 else 'mana' if a.mana>me.mana else
                   'ready' if ready else 'unavailable')
            if state=='absent':reason='Предмета нет в прочитанном активном инвентаре.'
            if state=='unknown':reason='Источник не прочитан. Проверь выбранного героя и его способности.'
            if state=='quarantined':
                ready=False
                reason=self.recovery.reason() if a.identity==self.recovery.identity else 'Предыдущая команда не подтверждена CD; повторы заблокированы.'
            rows.append(dict(name=p.name,title=titles.get(p.name,p.name),kind='item' if p.key=='inventory' else 'ability',
                             mode=modes[p.mode],key=binding,enabled=p.name not in self.disabled,
                             supported=not bool(p.blocked),status=state,reason=reason,owned=known,ready=ready,
                             cooldown=a.cooldown if a else 0.,mana=a.mana if a else 0.,level=a.level if a else 0))
        rows.sort(key=lambda row:row['kind']=='item')
        auto=self.ks.settings.auto_cast
        why=('PREVIEW.bat / --preview: нажатия запрещены. Для AUTO запусти start.bat.' if getattr(self.ks,'preview_requested',False) else
             'settings.auto_cast=false: ввод отключён конфигурацией; start.bat это не переопределяет.')
        compact=self.status
        if compact in ('ON · Ожидаем угрозу','ON · Нет распознанной угрозы'):
            compact=getattr(frame,'compact_reason','') or 'Ожидаем подтверждённую угрозу'
        elif compact.startswith('ON · '): compact=compact[5:]
        event_age=max(0.,time.perf_counter()-self.last_event_at) if self.last_event_at else -1.
        return dict(alt_selfcast=self.alt_selfcast,compact_reason=compact,last_action=self.last_action,event_age=event_age,armed=self.enabled,pending=self.arm_pending,status=self.status,mode='AUTO' if auto else 'PREVIEW',
                    mode_hint='Приоритет защиты над ручным вводом: может отменить ваш каст. Insert приостанавливает ввод в меню, но не выключает модуль.' if auto else why,
                    last_event=self.last_event,observations=list(getattr(frame,'observations',())),
                    provider=frame.status if frame else 'Нет данных источника угроз',
                    available=bool(frame and frame.build_ok),disabled=sorted(self.disabled),sources=rows)
