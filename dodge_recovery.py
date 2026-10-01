"""Bounded Counterspell recovery after a SENT command with no cooldown feedback.
Never call missing CD success. Never repeat the same threat. One automatic retry
on a NEW verified threat is allowed after stable readiness and a quiet interval;
a second consecutive miss needs explicit menu re-arm or a real cooldown observation.
Partial/failed OS input is never automatically or menu-reset recovered here.
"""
import math
from ks_core import fresh
from dodge_input import COUNTERSPELL


class CounterspellRecovery:
    def __init__(self):
        self.identity = None
        self.hero = None
        self.failures = 0
        self.origin = None
        self.after = 0.
        self.safe_sent = False
        self.requested = False
        self.ready_since = None
        self.last_sample = None

    def failure(self, identity, hero, origin, latest, now, sent):
        if (identity,hero)!=(self.identity,self.hero):
            self.failures=0
        self.identity=identity; self.hero=hero; self.origin=origin
        self.failures+=1
        self.safe_sent=bool(sent)
        self.after=max(now+.5,latest+.25)
        self.requested=False
        self.ready_since=self.last_sample=None

    def request_reset(self):
        if self.identity and self.safe_sent and self.failures:
            self.requested=True

    def confirmed(self, identity):
        if identity==self.identity: self.__init__()

    def observe(self, me, now, stale):
        if self.identity is None:return
        a=next((x for x in me.actions if x.identity==self.identity),None) if me else None
        ready=bool(me and me.entity==self.hero and fresh(me,now,stale) and me.alive and not me.busy and
                   a and a.name==COUNTERSPELL and a.level>0 and a.activated and not a.hidden and not a.casting and
                   math.isfinite(a.cooldown) and 0<=a.cooldown<=.02 and math.isfinite(a.mana) and 0<=a.mana<=me.mana)
        if not ready:
            self.ready_since=self.last_sample=None;return
        if self.last_sample is None or not 0<=me.at-self.last_sample<=stale:
            self.ready_since=me.at
        self.last_sample=me.at

    def allows(self, me, threats, used, now, stale):
        if (not self.identity or not self.safe_sent or not (self.failures==1 or self.requested) or
                self.ready_since is None or self.last_sample is None or
                not me or me.entity!=self.hero or not fresh(me,now,stale) or
                not 0<=now-self.last_sample<=stale or now-self.ready_since<.5 or now<self.after):
            return False
        for t in threats:
            if t.id==self.origin or t.id in used or t.target!=me.entity:continue
            proof_age=min(stale,.075) if t.evidence in ('local_mark_launch','local_mark_phase') else stale
            if not (t.evidence=='inferred_launch' or t.evidence in ('local_mark_launch','local_mark_phase') and t.name=='sniper_assassinate'):continue
            if (all(math.isfinite(x) for x in (t.observed,t.earliest,t.latest)) and
                    0<=now-t.observed<=proof_age and now<t.earliest<=t.latest):return True
        return False

    def unlocked(self):
        if self.requested:self.failures=0
        self.requested=False
        self.ready_since=self.last_sample=None

    def reason(self):
        if not self.safe_sent:return 'Ошибка/частичный ввод: нужен подтверждённый CD; автоповтор запрещён'
        if self.failures>=2 and not self.requested:
            return 'Две попытки без CD: проверь выбранного героя/бинд и включи Dodger заново'
        return 'Нет ответа CD: ждём стабильную готовность и новую угрозу; старую не повторяем'
