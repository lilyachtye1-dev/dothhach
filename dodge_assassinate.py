"""Assassinate local-mark path, based on the user's four alpha3 cast captures.
The victim is established by a stable, owner-checked modifier on the local hero.
Caster association is temporal correlation, NOT a decoded buff caster handle:
require a single eligible observed caster, an observed idle->phase edge and a new
local mark within 150 ms. Never substitute a nearby caster or override a different
valid target. Ambiguous observations drop the episode. A phase-only reposition requires a
fresh mark; a short torn modifier-list scan may be retried without authorizing input.
"""
from dataclasses import dataclass, replace
import math
from dodger import Threat

MARK = 'CDOTA_Modifier_Sniper_Assassinate'
EVIDENCE = 'local_mark_launch'
PHASE_EVIDENCE = 'local_mark_phase'


@dataclass(frozen=True)
class Mark:
    at: float
    local: int
    token: int  # 0 = validated absence, >0 = checked modifier object; unknown is None


def release_clock_matches(start, released, began, edge):
    if start == released or released in (-1., 0.): return True
    # User captures advance by ~2 s on release. Only Assassinate callers may use
    # this rule; compare GAME-time delta to the observed continuous phase duration,
    # not to absolute wall-clock time and not to an arbitrary positive timestamp.
    if began is None or edge is None: return False
    delta = released-start
    return .25 <= delta <= 5. and abs(delta-(edge-began)) <= .18


class MarkedAssassinate:
    GAP = .15
    MARK_AGE = .075
    RELEASE_GRACE = .12

    def __init__(self):
        self.previous = None
        self.cast = None
        self.flight = None
        self.flight_clock = None
        self.recent_rise = None
        self.last_pos = None
        self.timings = {}
        self.guard_point = None
        self.notice = None
        self.note = 'Assassinate: ждём новую метку и каст'

    def reset(self, note):
        self.timings.clear();self.guard_point=None
        self.previous = self.cast = self.flight = None
        self.flight_clock = None
        self.recent_rise = None
        self.last_pos = None
        self.note = note

    def displaced(self, me, now):
        return bool(self.last_pos and math.dist(self.last_pos[0][:2],me.pos[:2]) > 80+650*max(0,now-self.last_pos[1]))

    def update(self, sample, mark, me, now):
        self.notice = None
        if sample is None or mark is None or mark.local != me.entity or not 0 <= now-mark.at <= self.MARK_AGE:
            self.reset('Assassinate: нет свежей однозначной метки/кастера')
            return ()
        s = sample
        key = (me.entity, s['handle'], s['caster'], s['caster_handle'], s['entity'], s['ability_handle'])
        old = self.previous
        continuous = old is not None and old['key']==key and 0 < now-old['at'] <= self.GAP
        jumped = self.displaced(me,now)
        if jumped:
            # Blink BEFORE release does not prove that the enemy cancelled its cast.
            # Fresh owner-checked mark, same complete identity and same observed phase
            # are mandatory. After launch the old travel estimate must still be dropped.
            c=self.cast
            keep=bool(continuous and s['phase'] and c and c['key']==key and
                      c['start']==s['start'] and c['token'] and c['token']==mark.token and mark.at==now)
            if not keep:continuous=False
            self.notice=dict(reason='phase_reposition_revalidated' if keep else 'position_jump_reset',
                             distance=math.dist(self.last_pos[0][:2],me.pos[:2]),previous_at=self.last_pos[1])
        if not continuous:
            if old and not self.notice and (self.cast or self.flight):
                self.notice=dict(reason='identity_changed' if old['key']!=key else 'observation_gap',previous_at=old['at'],gap=now-old['at'])
            self.timings.clear();self.guard_point=None
            self.cast = self.flight = None
            self.recent_rise = None
        if continuous and old['token']==0 and mark.token:
            self.recent_rise = (mark.token,mark.at)
        if self.recent_rise and (mark.token!=self.recent_rise[0] or now-self.recent_rise[1]>self.GAP):
            self.recent_rise = None
        self.last_pos = (me.pos, now)
        if s['phase']:
            self.flight = None
            if (continuous and not old['phase'] and old['cd'] <= .02 and s['cd']<=.02 and
                    (old['token']==0 or self.recent_rise is not None)):
                self.cast = dict(start=s['start'], began=now, last_phase=now, token=0, stable=0,
                                 key=key, cast_point=s.get('cast_point'), point_changed=False, release=None, uid=(s['caster'],s['entity'],s['start'],s['ability_handle'],s['handle']))
            c = self.cast
            if c is not None:
                if c['cast_point']!=s.get('cast_point'):c['point_changed']=True
                if s['start'] != c['start'] or s['cd'] > .02 or c['release'] is not None:
                    self.notice=dict(reason='cast_start_cd_or_phase_changed')
                    self.cast = None
                elif not c['token']:
                    if now-c['began'] > self.GAP:
                        self.cast = None
                    elif mark.token:
                        c['token'] = mark.token
                elif mark.token != c['token']:
                    self.notice=dict(reason='mark_instance_changed_or_absent')
                    self.cast = None
                if self.cast is not None:
                    c['last_phase'] = now
                    if c['token']: c['stable'] += 1
            self.note = ('Assassinate: метка на вас · наблюдаем каст' if self.cast and self.cast['token'] else
                         'Assassinate: ждём совпадение новой метки и каста')
        elif self.cast is not None:
            c = self.cast
            if mark.token != c['token'] or not c['token'] or c['stable'] < 2:
                self.cast = None
            else:
                if c['release'] is None: c['release'] = now
                if (now-c['release'] > self.RELEASE_GRACE or
                        not release_clock_matches(c['start'],s['start'],c['began'],c['release'])):
                    self.cast = None
                elif s['cd'] > .1:
                    duration=c['release']-c['began']
                    if .25<=duration<=5.:
                        history=self.timings.get(key,())
                        self.timings[key]=(history+(duration,))[-2:]
                    distance = math.dist(s['pos'][:2],me.pos[:2])
                    uid = c['uid']+(c['token'],EVIDENCE)
                    self.flight_clock = (c['start'],c['began'],c['release'])
                    self.flight = Threat(uid,s['caster'],me.entity,'sniper_assassinate',
                        c['last_phase']-.15+max(0,distance-150)/(2500+650),
                        now+(distance+150)/(2500-650)+.15,mark.at,evidence=EVIDENCE)
                    self.cast = None
                    self.note = 'Assassinate: метка + конец каста + CD · запуск'
                else:
                    self.note = 'Assassinate: метка на вас · ждём CD после каста'
            if self.cast is None and self.flight is None:
                self.note = 'Assassinate: отмена / смена метки / нет подтверждения запуска'
        elif self.flight is None:
            self.note = ('Assassinate: старая метка без начала каста · пропуск' if mark.token else
                         'Assassinate: ожидание каста на вас')
        if self.flight is not None:
            if (mark.token != self.flight.id[-2] or now > self.flight.latest or not continuous or
                    not release_clock_matches(self.flight_clock[0],s['start'],self.flight_clock[1],self.flight_clock[2])):
                self.flight = None
                self.note = 'Assassinate: метка исчезла / наблюдение потеряно'
            else:
                self.flight = replace(self.flight, observed=mark.at)
        self.previous = dict(key=key,at=now,phase=s['phase'],cd=s['cd'],token=mark.token)
        if self.flight is not None:return (self.flight,)
        return self.phase_guard(s,mark,me,now)

    def phase_guard(self, sample, mark, me, now):
        # A verified TARGETED cast, not a proven launch. Counterspell only.
        # The same ID is used at release: phase+flight can never produce two casts.
        c=self.cast
        if not (sample['phase'] and c and c['token'] and c['stable']>=2 and not c['point_changed']):return ()
        point=c['cast_point'];spread=0.
        if point is None:
            history=self.timings.get(c['key'],())
            if len(history)!=2 or abs(history[0]-history[1])>.15:return ()
            point=(history[0]+history[1])/2;spread=abs(history[0]-history[1])/2
        if not math.isfinite(point) or not .25<=point<=5:return ()
        self.guard_point=point
        distance=math.dist(sample['pos'][:2],me.pos[:2])
        if distance>1400 or now>c['began']+point+.15:return ()
        early=c['began']+point-spread-.15+max(0,distance-150)/(2500+650)
        late=c['began']+point+spread+.15+(distance+150)/(2500-650)+.15
        if early<=now:return ()
        self.note='Assassinate: близкий каст на вас · предзащита (каст ещё может быть отменён)'
        return (Threat(c['uid']+(c['token'],EVIDENCE),sample['caster'],me.entity,'sniper_assassinate',
                       early,late,mark.at,evidence=PHASE_EVIDENCE),)
