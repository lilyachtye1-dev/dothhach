"""Last Hit controller on the EXISTING sender thread. Independent session IPC.
No key sender thread, no process writes. Release cancels NEW commands, not missiles.
"""
import json
from dataclasses import replace
from pathlib import Path
import time
from last_hit_core import Model, Frame, distance, quelling_bonus
from last_hit_orders import OwnedAttack
from last_hit_units import LABELS


def valid_bind(key):
    return type(key) is int and (key in (4,5,6) or 48<=key<=90 or 112<=key<=123)


def validate(doc,session,now):
    keys={'session','revision','utc_ms','enabled','key','attack_key','deny','tower','menu_open','latency_ms'}
    if not isinstance(doc,dict) or not keys<=set(doc) or set(doc)-keys-{'quick_attack','show_marker','click_mode','single_hit','stop_key','damage_reserve','pause_manual','helper_only','activation','rmb_only','return_cursor','rmb_deny_confirmed','mouse_priority'} or doc['session']!=session:return False
    if any(type(doc[k]) is not bool for k in ('quick_attack','show_marker','click_mode','single_hit','pause_manual','helper_only','rmb_only','return_cursor','rmb_deny_confirmed','mouse_priority') if k in doc):return False
    if doc.get('activation','hold') not in ('hold','toggle'):return False
    if type(doc['revision']) is not int or not 0<=doc['revision']<=2147483647:return False
    if type(doc['utc_ms']) is not int or not -500<=now-doc['utc_ms']<=2000:return False
    if not all(type(doc[k]) is bool for k in ('enabled','deny','tower','menu_open')):return False
    if not valid_bind(doc['key']) or type(doc['attack_key']) is not int or not 48<=doc['attack_key']<=90 or doc['key']==doc['attack_key']:return False
    if 'damage_reserve' in doc and (type(doc['damage_reserve']) is not int or not 0<=doc['damage_reserve']<=25):return False
    if 'stop_key' in doc and (type(doc['stop_key']) is not int or not 48<=doc['stop_key']<=90):return False
    if not doc.get('helper_only',False) and doc.get('single_hit') and doc.get('stop_key',83) in (doc['key'],doc['attack_key']):return False
    return type(doc['latency_ms']) is int and 0<=doc['latency_ms']<=300


class LastHit:
    def __init__(self,ks,root):
        self.ks=ks;self.root=Path(root);self.path=self.root/'last_hit_controls.json'
        self.reader=None;self.hint=None;self.enabled=False;self.key=5;self.attack_key=65
        self.mouse_priority=False;self.mouse_window=None;self.mouse_owner=None
        self.input_rejects={}
        self.rmb_only=False;self.return_cursor=True;self.rmb_deny_confirmed=False
        self.click_mode=False;self.single_hit=False;self.stop_key=83;self.damage_reserve=0;self.owned=None;self.stops=0;self.missing_feedback=0;self.feedback_sample=-1.;self.manual_token=None;self.manual_pause_until=0.
        self.pause_manual=False;self.helper_only=False;self.activation='hold';self.active=False;self.toggle_active=False;self.prev_down=False
        self.helpers=[];self.pipeline='';self.pipeline_at=0.
        self.quick_attack=False;self.show_marker=True;self.marker=None;self.in_flight={}
        self.deny=True;self.tower=True;self.latency_ms=80;self.menu_open=True
        self.revision=-1;self.lease=0;self.poll_at=0;self.model=Model();self.last_frame=None
        self.pending=None;self.block_until=0.;self.fault=False;self.hold=False
        self.status='OFF · удержание Mouse4';self.target='';self.reason='';self.sent=0;self.logged='';self.log_at=0.
    def attach(self,dot):
        from last_hit_reader import Reader
        self.reader=Reader(dot,self.root,lambda:self.enabled and not self.fault,lambda:self.hint)
    def set_hint(self,heroes):
        local=[h for h in heroes if h.get('is_local') and h.get('_ks')]
        self.hint=(local[0]['_ks'].entity,local[0].get('designer') or local[0]['_ks'].name) if len(local)==1 else None
    def command(self):
        try:
            if self.path.stat().st_size>4096:return None
            doc=json.loads(self.path.read_text(encoding='utf-8-sig'))
            return doc if validate(doc,self.ks.ui_session,int(time.time()*1000)) else None
        except (OSError,ValueError,TypeError):return None
    def poll(self,now):
        if now<self.poll_at:return
        self.poll_at=now+.05;doc=self.command()
        if doc is None:
            if now>self.lease:self.enabled=False
            return
        if doc['revision']<self.revision:return
        self.lease=now+min(2.,max(0,(doc['utc_ms']+2000-int(time.time()*1000))/1000))
        self.menu_open=doc['menu_open']
        if doc['revision']==self.revision:return
        if doc.get('activation','hold')!=self.activation or doc['key']!=self.key or not doc['enabled'] or not self.enabled:self.toggle_active=False
        self.activation=doc.get('activation','hold');self.pause_manual=doc.get('pause_manual',False);self.helper_only=doc.get('helper_only',False)
        self.revision=doc['revision'];self.enabled=doc['enabled'];self.key=doc['key'];self.attack_key=doc['attack_key']
        self.mouse_priority=doc.get('mouse_priority',False)
        self.rmb_only=doc.get('rmb_only',False);self.return_cursor=doc.get('return_cursor',True)
        self.rmb_deny_confirmed=doc.get('rmb_deny_confirmed',False)
        self.click_mode=doc.get('click_mode',False);self.single_hit=doc.get('single_hit',False)
        self.stop_key=doc.get('stop_key',83);self.damage_reserve=doc.get('damage_reserve',0)
        self.quick_attack=doc.get('quick_attack',False);self.show_marker=doc.get('show_marker',True)
        self.deny=doc['deny'];self.tower=doc['tower'];self.latency_ms=doc['latency_ms'];self.fault=False
        self.ks.log.info('LASTHIT CONTROL enabled=%s key=%s deny=%s tower=%s rmb=%s deny_confirmed=%s return_cursor=%s single_hit=%s pause_manual=%s activation=%s mouse_priority=%s input_fix=alpha7_mouse_window',
                         self.enabled,self.key,self.deny,self.tower,self.rmb_only,self.rmb_deny_confirmed,self.return_cursor,self.single_hit,self.pause_manual,self.activation,self.mouse_priority)
    def permit(self,context,uid,screen,hp,manual_token=None,expected_hero=None):
        ks=self.ks;b=ks.backend;now=time.perf_counter()
        doc=self.command()  # Revoke immediately even if sender is between polling ticks.
        frame=self.reader.latest if self.reader else None
        local_uid=frame.hero.uid if frame and frame.hero else None
        return bool(self.enabled and not self.fault and doc and doc['enabled'] and not doc['menu_open'] and
                    doc['revision']==self.revision and doc['key']==self.key and doc['attack_key']==self.attack_key and
                    doc.get('mouse_priority',False)==self.mouse_priority and doc.get('rmb_only',False)==self.rmb_only and doc.get('return_cursor',True)==self.return_cursor and
                    doc.get('rmb_deny_confirmed',False)==self.rmb_deny_confirmed and
                    (manual_token is None or b.activity_token()==manual_token) and
                    doc.get('activation','hold')==self.activation and doc.get('quick_attack',False)==self.quick_attack and doc.get('click_mode',False)==self.click_mode and
                    self.activation_live() and not self.helper_only and not doc.get('helper_only',False) and not ks.ui_menu_open and not getattr(ks,'menu_foreground',False) and
                    b.context()==context and frame and not frame.error and not frame.incomplete and
                    0<=now-frame.at<=.15 and local_uid==self.model.hero_uid and
                    any(u.uid==uid and u.visible and u.hp>0 for u in frame.creeps) and
                    (expected_hero is None or (expected_hero.uid==local_uid and self.reader.verify_quelling(expected_hero))) and
                    self.reader.verify_target(uid,local_uid,screen,hp) and
                    0<=time.perf_counter()-frame.at<=.15 and self.activation_live() and
                    (manual_token is None or b.activity_token()==manual_token))
    def end_mouse_window(self,reason):
        window=self.mouse_window
        if window is None:return
        b=self.ks.backend
        if getattr(b,'_lh_mouse_priority',None) is window:
            b.finish_last_hit_mouse_priority(reason,resume=not(self.menu_open or self.ks.ui_menu_open or getattr(self.ks,'menu_foreground',False)))
        self.ks.log.info('LASTHIT MOUSE end=%s resumed=%s failed=%s',window.reason or reason,window.resumed,window.failed)
        self.mouse_window=None;self.mouse_owner=None
        # Returning physical mouse ownership must also revoke any delayed Stop.
        self.owned=None;self.pending=None
        self.block_until=max(self.block_until,time.perf_counter()+.12)
        if window.failed:self.enabled=False;self.fault=True

    def update_mouse_window(self,frame,heroes,age,blocked,now):
        window=self.mouse_window
        if window is None:return
        b=self.ks.backend
        if getattr(b,'_lh_mouse_priority',None) is not window:
            self.end_mouse_window('backend ended');return
        doc=self.command()
        local=[h['_ks'] for h in heroes if h.get('is_local') and h.get('_ks')]
        valid=(not blocked and self.enabled and self.active and not self.fault and self.mouse_priority and
               not self.helper_only and not self.menu_open and not self.ks.ui_menu_open and not getattr(self.ks,'menu_foreground',False) and
               doc and doc['enabled'] and not doc['menu_open'] and doc['revision']==self.revision and
               doc.get('mouse_priority',False) and not doc.get('helper_only',False) and self.activation_live() and
               frame.hero and not frame.error and not frame.incomplete and 0<=now-frame.at<=.15 and
               self.mouse_owner==(frame.hero.uid,self.revision) and len(local)==1 and local[0].alive and
               not local[0].busy and local[0].entity==frame.hero.uid[0] and 0<=age<=.15 and 0<=now-local[0].at<=.15)
        if not valid:self.end_mouse_window('control / priority / frame changed');return
        b.watch_last_hit_mouse_priority(now)
        if getattr(b,'_lh_mouse_priority',None) is not window:self.end_mouse_window('backend ended');return
        if frame.hero.last_attack>window.stamp+.001:
            window.due=min(window.deadline,max(window.due,frame.at+max(0,frame.hero.last_attack+window.windup+.06-frame.game_time)))

    def cursor_return_permitted(self,blocked):
        # Motion cleanup is on the existing sender, never a timer/input thread.
        # Higher-priority casts, deactivation and a changed menu revision revoke it.
        if (blocked or not self.enabled or not self.active or self.helper_only or self.fault or
                self.menu_open or self.ks.ui_menu_open or getattr(self.ks,'menu_foreground',False)):
            return False
        doc=self.command()
        return bool(doc and doc['enabled'] and not doc['menu_open'] and doc['revision']==self.revision and
                    not doc.get('helper_only',False) and doc.get('return_cursor',True) and
                    self.return_cursor and self.activation_live())

    def tick(self,heroes,age,blocked=False):
        try:return self._tick(heroes,age,blocked)
        finally:
            if self.mouse_window and (self.fault or not self.enabled or not self.active):self.end_mouse_window('deactivated')
            now=time.perf_counter()
            if (self.enabled and self.active or self.fault) and not self.status.startswith('ATTACK') and self.status!=self.logged and now-self.log_at>=1:
                self.logged=self.status;self.log_at=now
                self.ks.log.info('LASTHIT STATE %s',self.status)
                print('[LAST HIT] '+self.status,flush=True)

    def _tick(self,heroes,age,blocked=False):
        now=time.perf_counter();self.poll(now);b=self.ks.backend
        self.hold=bool(b.down(self.key));self.target='';self.reason='';self.marker=None;self.helpers=[]
        if self.activation=='toggle' and self.hold and not self.prev_down and self.enabled and not self.menu_open and not self.ks.ui_menu_open and b.context():self.toggle_active=not self.toggle_active
        self.prev_down=self.hold
        self.active=self.hold if self.activation=='hold' else self.toggle_active
        flush=getattr(b,'flush_last_hit_cursor',None)
        if flush:
            cleanup=flush(now,lambda:self.cursor_return_permitted(blocked))
            if cleanup and cleanup!='waiting':self.ks.log.info('LASTHIT CURSOR deferred=%s',cleanup)
        frame=self.reader.latest if self.reader else Frame(0,0,(0,0),error='Reader не подключён')
        if frame is not self.last_frame:
            if frame.hero and not frame.error:self.model.update(frame)
            self.last_frame=frame
        self.update_mouse_window(frame,heroes,age,blocked,now)
        token=b.activity_token() if hasattr(b,'activity_token') else None
        if token is not None and self.manual_token is not None and token!=self.manual_token:
            self.manual_pause_until=now+.12 if self.pause_manual else 0.
            if self.pending:
                uid,stamp,_,hero_uid=self.pending
                if self.rmb_only and frame.hero and frame.hero.uid==hero_uid and frame.hero.last_attack<=stamp+.001:
                    # Unlaunched order superseded by the user: do not preserve its
                    # fictitious projectile or an entire old attack interval.
                    self.in_flight.pop(uid,None);self.block_until=min(self.block_until,now+.05)
                self.pending=None
                self.ks.log.info('LASTHIT manual command superseded attack; no feedback fault')
        self.manual_token=token
        if self.owned and (token!=self.owned.token or token is None or not self.single_hit or self.revision!=self.owned.revision or
                           not self.active or not self.enabled or blocked or self.menu_open or self.ks.ui_menu_open or
                           b.context()!=self.owned.context):
            self.ks.log.info('LASTHIT ownership revoked; no delayed Stop')
            self.owned=None
        if self.pending and (not self.active or blocked or not self.enabled or self.menu_open or
                             self.ks.ui_menu_open or getattr(self.ks,'menu_foreground',False) or not b.context()):
            # Explicit release / higher-priority action may cancel the game
            # attack before its timestamp changes. Don't call that an input fault.
            self.pending=None;self.block_until=max(self.block_until,now+.15)
        if self.pending:
            uid,stamp,deadline,hero_uid=self.pending
            if not frame.hero or frame.hero.uid!=hero_uid:
                self.pending=None;self.block_until=now+.2
            elif frame.hero.last_attack>stamp+.001:
                self.ks.log.info('LASTHIT FEEDBACK attack_timestamp %.4f -> %.4f; target=%s; not hit confirmation',stamp,frame.hero.last_attack,uid)
                self.pending=None
            elif not frame.error and not frame.incomplete and 0<=now-frame.at<=.15 and frame.at!=self.feedback_sample:
                self.feedback_sample=frame.at
                present=any(c.uid==uid and c.visible and c.hp>0 for c in frame.creeps)
                self.missing_feedback=0 if present else self.missing_feedback+1
                if self.missing_feedback>=2:
                    self.pending=None;self.block_until=min(self.block_until,now+.12)
                    self.ks.log.info('LASTHIT target unavailable before feedback; no kill claim, module stays ON')
            if self.pending and now>deadline:
                self.pending=None;self.fault=True;self.enabled=False
                self.ks.log.warning('LASTHIT no attack timestamp feedback; OFF, no automatic retry')
        choice,note=self.build_helper(frame,now)
        if self.fault:self.status='OFF · нет подтверждения атаки / ошибка ввода; включи заново';return
        if not self.enabled:self.status='OFF · Last Hit выключен';return
        if self.helper_only:self.status='HELPER · только подсветка';self.owned=None;return
        if not self.active:self.status='Готов · '+('удерживай кнопку' if self.activation=='hold' else 'нажми кнопку для старта');return
        context=b.context()
        if self.menu_open or self.ks.ui_menu_open or getattr(self.ks,'menu_foreground',False) or not context:
            self.status='Пауза · меню / фокус';return
        if any(b.down(k) for k in (13,9,35,45,91,92)):
            self.status='Пауза · служебная клавиша';return
        if blocked:self.status='Пауза · приоритет KS / Dodger';return
        local=[h['_ks'] for h in heroes if h.get('is_local') and h.get('_ks')]
        if len(local)!=1 or not local[0].alive or local[0].busy or not 0<=age<=.15 or not 0<=now-local[0].at<=.15:
            self.owned=None
            self.status='Пауза · герой занят / данные устарели';return
        if frame.error or not frame.hero or frame.incomplete or not 0<=now-frame.at<=.15 or tuple(context[3:5])!=frame.viewport or local[0].entity!=frame.hero.uid[0]:
            self.owned=None
            self.status='Пауза · '+(frame.error or 'кадр / геометрия / identity');return
        if self.owned:
            action=self.owned.observe(frame,now)
            if action=='expired':self.owned=None
            elif action=='stop':
                owned=self.owned
                result,reason=b.last_hit_stop(self.stop_key,owned.context,lambda:self.permit_stop(owned,heroes))
                self.owned=None
                self.ks.log.info('LASTHIT STOP %s %s activity_seen=%s missing_samples=%s',result,reason,owned.seen,owned.missing)
                if result=='sent':
                    self.end_mouse_window('own Stop')
                    self.stops+=1;self.status='Одна атака · Stop отправлен';return
                if result=='failed':self.enabled=False;self.fault=True;self.status='OFF · ошибка Stop';return
        if self.pause_manual and now<self.manual_pause_until:
            self.status='Пауза · новая ручная команда';return
        if self.single_hit and token is None:
            self.status='Пауза · нет наблюдения ручных команд для режима одной атаки';return
        if self.mouse_window:
            if self.owned is None and now>=self.mouse_window.due:
                self.end_mouse_window('estimated swing finished');self.status='Мышь возвращена пользователю';return
            self.status='Приоритет Last Hit · завершаю свою тычку';return
        self.in_flight={uid:data for uid,data in self.in_flight.items()
                        if data[0]>now and data[1]==frame.hero.uid and any(c.uid==uid and c.hp>0 for c in frame.creeps)}
        display=choice or self.model.watch
        if self.owned or self.pending or now<self.block_until:
            ordered=self.in_flight.get(self.pending[0]) if self.pending else None
            if not ordered and self.in_flight:ordered=max(self.in_flight.values(),key=lambda x:x[0])
            if ordered:
                current=next((c for c in frame.creeps if c.uid==ordered[2].target.uid),None)
                if current:display=replace(ordered[2],target=current,kind='sent',reason='Команда отправлена · попадание не подтверждено')
            elif display:display=replace(display,kind='wait',reason='Ожидание атаки / интервала')
        elif not display and self.in_flight:
            ordered=max(self.in_flight.values(),key=lambda x:x[0]);current=next((c for c in frame.creeps if c.uid==ordered[2].target.uid),None)
            if current:display=replace(ordered[2],target=current,kind='sent',reason='Команда отправлена · попадание не подтверждено')
        self.mark(display,frame,now)
        if self.owned or self.pending or now<self.block_until:self.status='Ожидание атаки / интервала';return
        if choice is None:self.status=note;return
        self.target=choice.target.name;self.reason=choice.reason
        if not self.ks.settings.auto_cast:self.status='PREVIEW · '+choice.kind;return
        forecast_rate=0. if self.tower and any(s.target==choice.target.uid for s in self.model.shots) else self.model.dps(choice.target.uid)
        args=(choice.target.screen[:2],self.attack_key,context,
              lambda:self.permit(context,choice.target.uid,choice.target.screen[:2],choice.target.hp,token,
                                 frame.hero if quelling_bonus(frame.hero,choice.target)>0 else None))
        if self.rmb_only and self.mouse_priority:
            travel=distance(frame.hero.pos,choice.target.pos)/frame.hero.projectile_speed if frame.hero.projectile_speed>0 else 0.
            hold=max(.12,frame.at+choice.impact_late-frame.game_time-travel+.05-now)
            result,reason=b.last_hit_priority_rmb(args[0],context,args[3],hold,deny=choice.kind=='deny',return_cursor=self.return_cursor)
            self.mouse_window=getattr(b,'_lh_mouse_priority',None)
            if self.mouse_window:
                self.mouse_owner=(frame.hero.uid,self.revision)
                self.mouse_window.stamp=frame.hero.last_attack
                self.mouse_window.windup=frame.hero.attack_point*self.model.measured_interval(frame.hero,frame.game_time)/frame.hero.bat
                self.ks.log.info('LASTHIT MOUSE begin held=%s window=%.3fs max=.90s',sorted(self.mouse_window.held),hold)
        elif self.rmb_only:
            result,reason=b.last_hit_rmb(args[0],context,args[3],deny=choice.kind=='deny',return_cursor=self.return_cursor)
        elif self.click_mode:result,reason=b.last_hit_target_click(*args,deny=choice.kind=='deny')
        else:result,reason=b.last_hit_attack(*args,**({'quick':True} if self.quick_attack else {}))
        self.status={'sent':'ATTACK','skipped':'Пропуск','failed':'OFF'}.get(result,'OFF')+' · '+reason
        if result=='skipped':
            label=reason[:100]
            if label in self.input_rejects or len(self.input_rejects)<12:
                self.input_rejects[label]=self.input_rejects.get(label,0)+1
            return
        self.missing_feedback=0;self.feedback_sample=frame.at
        self.pending=(choice.target.uid,frame.hero.last_attack,now+2.,frame.hero.uid)
        self.block_until=now+max(.12,self.model.measured_interval(frame.hero,frame.game_time))
        if result!='sent':self.enabled=False;self.fault=True
        else:
            self.sent+=1
            self.model.record_own_attack(choice.target.uid,choice.impact_late)
            if self.single_hit and token is not None and b.activity_token()==token:
                windup=frame.hero.attack_point*self.model.measured_interval(frame.hero,frame.game_time)/frame.hero.bat
                travel=distance(frame.hero.pos,choice.target.pos)/frame.hero.projectile_speed if frame.hero.projectile_speed>0 else 0
                due=max(now+.12,frame.at+choice.impact_late-frame.game_time-travel+.05)
                self.owned=OwnedAttack(frame.hero.uid,choice.target.uid,token,self.revision,context,due,
                                       now+max(2.5,self.model.measured_interval(frame.hero,frame.game_time)+.6),
                                       frame.hero.last_attack,frame.game_time,windup)
            self.in_flight[choice.target.uid]=(max(now+.12,frame.at+choice.impact_late-frame.game_time+.10),frame.hero.uid,choice)
            self.mark(replace(choice,kind='sent',reason='Команда отправлена · попадание не подтверждено'),frame,now)
        self.ks.log.info('LASTHIT %s kind=%s target=%s handle=%s hp=%.1f predicted=%.1f..%.1f damage_min=%.1f impact=%.3f..%.3f %s',
            result,choice.kind,choice.target.name,hex(choice.target.uid[1]),choice.target.hp,choice.hp_low,choice.hp_high,choice.damage,
            choice.impact_early-frame.game_time,choice.impact_late-frame.game_time,reason)
        self.ks.log.info('LASTHIT DAMAGE hero_min=%.2f hero_max=%.2f generic_bonus=%.2f quell=%.2f quell_known=%s armor=%.2f reserve=%s%% effective_min=%.2f source=%s',
                         frame.hero.damage_min,frame.hero.damage_max,frame.hero.damage_bonus,quelling_bonus(frame.hero,choice.target),frame.hero.quelling_known,choice.target.armor,self.damage_reserve,choice.damage,frame.hero.quelling_note)
        loss,span=self.model.observed_loss(choice.target.uid)
        self.ks.log.info('LASTHIT HP_OBS loss=%.1f span=%.3f extrapolated_dps=%.1f source=unknown; not projectile/hit confirmation',loss,span,forecast_rate)
        print('[LAST HIT] '+self.status,flush=True)
    def permit_stop(self,owned,heroes):
        b=self.ks.backend;doc=self.command();frame=self.reader.latest if self.reader else None
        local=[h['_ks'] for h in heroes if h.get('is_local') and h.get('_ks')]
        return bool(self.owned is owned and self.enabled and not self.fault and self.single_hit and not self.helper_only and doc and doc['enabled'] and
                    not doc.get('helper_only',False) and doc.get('single_hit') and doc.get('stop_key',83)==self.stop_key and doc['revision']==owned.revision and
                    doc['key']==self.key and doc['attack_key']==self.attack_key and
                    not doc['menu_open'] and not self.ks.ui_menu_open and not getattr(self.ks,'menu_foreground',False) and
                    self.activation_live() and b.activity_token()==owned.token and b.context()==owned.context and frame and
                    not frame.error and not frame.incomplete and frame.hero and frame.hero.uid==owned.hero and
                    0<=time.perf_counter()-frame.at<=.15 and len(local)==1 and local[0].alive and not local[0].busy and
                    0<=time.perf_counter()-local[0].at<=.15 and self.reader.verify_hero(owned.hero) and
                    b.activity_token()==owned.token and self.activation_live() and 0<=time.perf_counter()-frame.at<=.15)

    def activation_live(self):
        if self.activation=='hold':return self.ks.backend.down(self.key)
        # A second press racing the sender must revoke before the next tick.
        return bool(self.toggle_active and not (self.ks.backend.down(self.key) and not self.prev_down))

    def build_helper(self,frame,now):
        self.helpers=[]
        if not self.enabled or not frame.hero or frame.error or frame.incomplete or not 0<=now-frame.at<=.15:
            self.pipeline='Reader: '+(frame.error or 'кадр не готов')
            return None,self.pipeline
        self.in_flight={uid:v for uid,v in self.in_flight.items() if v[0]>now and v[1]==frame.hero.uid and any(c.uid==uid for c in frame.creeps)}
        choice,note=self.model.choose(frame,self.deny and (not self.rmb_only or self.rmb_deny_confirmed),self.tower,self.latency_ms/1000,
                                      frame_age=max(0,now-frame.at),excluded=self.in_flight,damage_reserve=self.damage_reserve,allow_current_hp=self.click_mode)
        counts=dict(frame.diagnostics)
        types=' / '.join('%s:%d'%(LABELS[k],counts.get(k,sum(u.lane_type==k for u in frame.creeps))) for k in ('melee','ranged','flagbearer','siege'))
        self.pipeline=('Найдено %d · %s · в дальности %d · готовы %d · ошибки %d · классы? %d · интервал ≈%.2fс'%(
            len(frame.creeps),types,self.model.counts.get('range',0),self.model.counts.get('ready',0),counts.get('invalid',0),counts.get('unsupported',0),self.model.measured_interval(frame.hero,frame.game_time)))
        self.pipeline+=(' · Quell +%g'%frame.hero.quelling_bonus if frame.hero.quelling_known else ' · Quell ?')
        if self.rmb_only and self.deny and not self.rmb_deny_confirmed:
            self.pipeline+=' · ДЕНАЙ OFF: подтверди ПКМ в меню'
        if self.show_marker:
            rows=sorted(self.model.rows,key=lambda row:(not row['ready'],not row['in_range'],distance(frame.hero.pos,row['choice'].target.pos)))
            for row in rows:
                if not row['on_screen']:continue
                c=row['choice'];u=c.target
                self.helpers.append(dict(visible=True,uid=str(u.uid),x=u.screen[0],y=u.screen[1],width=frame.viewport[0],height=frame.viewport[1],
                    hero_min=frame.hero.damage_min,hero_max=frame.hero.damage_max,armor=u.armor,reserve=self.damage_reserve,
                    quell=quelling_bonus(frame.hero,u),hp_loss=self.model.observed_loss(u.uid)[0],hp_loss_span=self.model.observed_loss(u.uid)[1],
                    state=row['state'],label=LABELS.get(u.lane_type,'Крип')+' · '+row['reason'],hp=u.hp,hp_low=c.hp_low,hp_high=c.hp_high,
                    damage=c.damage,eta_ms=max(0,(c.impact_late-frame.game_time)*1000),sample_at=frame.at))
                if len(self.helpers)>=24:break
        if now-self.pipeline_at>=2:
            self.pipeline_at=now
            self.ks.log.info('LASTHIT PIPELINE %s discovery=%s rejected=%s speed=%s',self.pipeline,counts,frame.details,frame.hero.speed_source)
            self.ks.log.info('LASTHIT INPUT skips=%s deny_effective=%s return_cursor=%s single_hit=%s pause_manual=%s',
                             self.input_rejects,self.deny and (not self.rmb_only or self.rmb_deny_confirmed),self.return_cursor,self.single_hit,self.pause_manual)
            self.input_rejects={}
            print('[LAST HIT] '+self.pipeline,flush=True)
        return choice,note

    def mark(self,choice,frame,now):
        if not choice or not choice.target.visible or choice.target.hp<=0 or len(choice.target.screen)<2:return
        self.target=choice.target.name;self.reason=choice.reason
        if not self.show_marker:return
        self.marker=dict(visible=True,uid=str(choice.target.uid),x=choice.target.screen[0],y=choice.target.screen[1],
                         width=frame.viewport[0],height=frame.viewport[1],hero_min=frame.hero.damage_min,hero_max=frame.hero.damage_max,armor=choice.target.armor,reserve=self.damage_reserve,
                         quell=quelling_bonus(frame.hero,choice.target),hp_loss=self.model.observed_loss(choice.target.uid)[0],hp_loss_span=self.model.observed_loss(choice.target.uid)[1],state=choice.kind,label=choice.reason,hp=choice.target.hp,hp_low=choice.hp_low,
                         hp_high=choice.hp_high,damage=choice.damage,eta_ms=max(0,(choice.impact_late-frame.game_time-(now-frame.at))*1000),
                         sample_at=frame.at)

    def dashboard(self):
        frame=self.reader.latest if self.reader else None
        marker=None
        if self.marker and self.enabled and self.active and self.show_marker and not self.menu_open and not self.fault:
            marker=dict(self.marker);marker['age']=max(0,time.perf_counter()-marker.pop('sample_at'))
            if marker['age']>.15:marker=None
        helpers=[dict(m,age=max(0,time.perf_counter()-m['sample_at'])) for m in self.helpers if 0<=time.perf_counter()-m['sample_at']<=.15] if self.enabled and self.show_marker else []
        for m in helpers:m.pop('sample_at',None)
        return dict(damage_status=frame.hero.quelling_note if frame and frame.hero else '',
                    incoming_status='Потери HP: наблюдение; источник неизвестен. Прогноз не подтверждает следующий удар.',mouse_priority=self.mouse_priority,rmb_only=self.rmb_only,return_cursor=self.return_cursor,rmb_deny_confirmed=self.rmb_deny_confirmed,speed_source=frame.hero.speed_source if frame and frame.hero else '',active=self.active,activation=self.activation,pause_manual=self.pause_manual,helper_only=self.helper_only,markers=helpers,pipeline=self.pipeline,click_mode=self.click_mode,single_hit=self.single_hit,stop_key=self.stop_key,damage_reserve=self.damage_reserve,stops=self.stops,quick_attack=self.quick_attack,show_marker=self.show_marker,marker=marker,enabled=self.enabled,held=self.hold,key=self.key,attack_key=self.attack_key,deny=self.deny,tower=self.tower,
                    latency_ms=self.latency_ms,revision=self.revision,status=self.status,target=self.target,reason=self.reason,sent=self.sent,
                    creeps=len(frame.creeps) if frame else 0,shots=len(self.model.shots),
                    reader_error=frame.error if frame else '',age=max(0,time.perf_counter()-frame.at) if frame else 99999.)
