"""Pure Last Hit model. Timing/HP forecasts are estimates, not engine callbacks.
Tower shots retain their original full target identity after a target switch.
No random damage roll, no nearest-target attribution, no direct input here.
"""
from dataclasses import dataclass, replace
from collections import deque
import math


def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def distance(a,b):return math.hypot(a[0]-b[0],a[1]-b[1])
def armor_multiplier(armor):return 1.-.06*armor/(1.+.06*abs(armor))


def quelling_bonus(hero,target):
    # Quell does not increase denies, hero damage or tower damage.
    return (hero.quelling_bonus if hero.kind=='hero' and target.kind=='creep' and
            hero.team in (2,3) and target.team in (2,3) and hero.team!=target.team and
            hero.quelling_known and finite(hero.quelling_bonus) and 0<=hero.quelling_bonus<=100 else 0.)


@dataclass(frozen=True)
class Unit:
    uid: tuple
    name: str
    kind: str
    team: int
    hp: float
    maxhp: float
    pos: tuple
    screen: tuple
    yaw: float
    damage_min: float
    damage_max: float
    armor: float
    regen: float
    attack_range: float
    hull: float
    bat: float
    attack_point: float
    turn_rate: float
    projectile_speed: float
    interval: float
    last_attack: float
    target: tuple=()
    visible: bool=True
    damage_bonus: float=0.
    lane_type: str=''
    incoming_multiplier: object=1.
    speed_signature: tuple=()
    speed_source: str='base + agility estimate'
    quelling_bonus: float=0.
    quelling_known: bool=False
    quelling_note: str='Quell неизвестен; бонус не добавлен'
    quelling_items: tuple=()
    quelling_ranged: bool=False


@dataclass(frozen=True)
class Frame:
    at: float
    game_time: float
    viewport: tuple
    hero: Unit=None
    creeps: tuple=()
    towers: tuple=()
    error: str=''
    incomplete: bool=False
    diagnostics: tuple=()
    details: tuple=()


@dataclass(frozen=True)
class Shot:
    source: tuple
    target: tuple
    stamp: float
    early: float
    late: float
    damage_min: float
    damage_max: float


@dataclass(frozen=True)
class Choice:
    target: Unit
    kind: str
    impact_early: float
    impact_late: float
    hp_low: float
    hp_high: float
    damage: float
    reason: str


class Model:
    def __init__(self):self.reset()
    def reset(self):
        self.watch=None;self.rows=[];self.counts={};self.speed_signatures={};self.previous={};self.shots=[];self.history={};self.intervals={};self.now=None;self.hero_uid=None;self.own_impacts={};self.health_state={}
    def measured_interval(self,u,now):
        samples=[v for t,v in self.intervals.get(u.uid,()) if now-t<4]
        # Consecutive similar attack stamps reject long idle gaps; otherwise
        # base attack speed + agility remains an explicitly estimated fallback.
        if len(samples)>=2 and abs(samples[-1]-samples[-2])<=.12*max(samples[-1],samples[-2]):
            measured=max(.08,min(8.,sum(samples[-2:])/2.))
            if not u.speed_signature or measured<=u.interval*1.15:return measured
        return u.interval
    def update(self,frame):
        if self.now is not None and (frame.game_time<self.now or frame.game_time-self.now>2 or (frame.hero and frame.hero.uid!=self.hero_uid)):
            self.reset()
        self.now=frame.game_time;self.hero_uid=frame.hero.uid if frame.hero else None
        units=frame.creeps+frame.towers+((frame.hero,) if frame.hero else ())
        lookup={u.uid:u for u in units}
        now=frame.game_time
        self.shots=[s for s in self.shots if s.late>now and s.target in lookup]
        for u in units:
            if u.speed_signature and self.speed_signatures.get(u.uid)!=u.speed_signature:
                self.intervals.pop(u.uid,None)
            self.speed_signatures[u.uid]=u.speed_signature
            old=self.previous.get(u.uid)
            if old is not None and now-old[0]<=.35 and u.last_attack>old[1]+.001:
                delta=u.last_attack-old[1]
                if .08<=delta<=8:self.intervals.setdefault(u.uid,deque(maxlen=6)).append((now,delta))
                if u.kind=='tower' and u.target and old[2]==u.target and u.target in lookup and now-.35<=u.last_attack<=now+.05:
                    target=lookup[u.target]
                    if u.projectile_speed>0:
                        travel=distance(u.pos,target.pos)/u.projectile_speed
                        # The supplied LastAttackTime field is not a verified
                        # projectile-launch callback. Cover start/launch phases.
                        early=u.last_attack+travel
                        late=early+u.attack_point+.10
                        mult=armor_multiplier(target.armor)
                        self.shots.append(Shot(u.uid,u.target,u.last_attack,early,late,
                            max(0,u.damage_min*mult*.8),max(0,u.damage_max*mult*1.2)))
            self.previous[u.uid]=(now,u.last_attack,u.target)
            if u.kind=='creep':
                hist=self.history.setdefault(u.uid,deque(maxlen=32))
                # Discontinuities/heals must not join old HP losses into a new
                # continuous incoming-DPS forecast. Small regen is tolerated.
                if (self.health_state.get(u.uid,(u.maxhp,u.team))!=(u.maxhp,u.team) or
                        (hist and (now-hist[-1][0]>.35 or u.hp-hist[-1][1]>max(2.,max(0,u.regen)*max(0,now-hist[-1][0])+1.)))):
                    hist.clear()
                self.health_state[u.uid]=(u.maxhp,u.team)
                if not hist or now>hist[-1][0]:hist.append((now,u.hp))
        self.previous={k:v for k,v in self.previous.items() if k in lookup}
        self.history={k:v for k,v in self.history.items() if k in lookup}
        self.speed_signatures={k:v for k,v in self.speed_signatures.items() if k in lookup}
        self.intervals={k:v for k,v in self.intervals.items() if k in lookup}
        self.shots=self.shots[-128:]
        self.health_state={k:v for k,v in self.health_state.items() if k in lookup}
        self.own_impacts={k:v for k,v in self.own_impacts.items() if k in lookup and now-v<2}

    def record_own_attack(self,uid,impact_late):
        # Command acceptance is not hit proof. Exclude the ambiguous interval
        # rather than attributing our possible hit to repeatable outside DPS.
        self.own_impacts[uid]=max(self.own_impacts.get(uid,0),impact_late+.10)

    def observed_loss(self,uid):
        hist=list(self.history.get(uid,()))
        if len(hist)<2:return 0.,0.
        now=hist[-1][0]
        pairs=[(a,b) for a,b in zip(hist,hist[1:]) if 0<b[0]-a[0]<=.35 and now-a[0]<=.65]
        if not pairs:return 0.,0.
        return sum(max(0,a[1]-b[1]) for a,b in pairs),now-pairs[0][0][0]


    def dps(self,uid):
        hist=list(self.history.get(uid,()))
        if len(hist)<3:return 0.
        now=hist[-1][0]
        losses=[(b[0],a[1]-b[1]) for a,b in zip(hist,hist[1:])
                if 0<b[0]-a[0]<=.35 and b[1]<a[1] and now-b[0]<=.65 and
                   a[0]>self.own_impacts.get(uid,-float('inf'))]
        # A single burst/heal or an old hit is not continuous incoming damage.
        if len(losses)<2 or now-losses[-1][0]>.35:return 0.
        gaps=[b[0]-a[0] for a,b in zip(losses,losses[1:])]
        if not gaps or min(gaps)<=0 or max(gaps)>2.5*min(gaps):return 0.
        span=losses[-1][0]-losses[0][0]
        return sum(n for _,n in losses[1:])/span if span>=.08 else 0.

    def impact(self,hero,target,now,latency):
        interval=self.measured_interval(hero,now)
        cooldown=max(0.,hero.last_attack+interval-now) if hero.last_attack>0 else 0.
        angle=abs((math.degrees(math.atan2(target.pos[1]-hero.pos[1],target.pos[0]-hero.pos[0]))-hero.yaw+180)%360-180)
        # KV turn-rate is interpreted in radians per 30ms step, NOT rad/sec.
        turn=max(0,math.radians(angle)-.20)*.03/hero.turn_rate
        point=hero.attack_point*interval/hero.bat
        travel=distance(hero.pos,target.pos)/hero.projectile_speed if hero.projectile_speed>0 else 0.
        t=cooldown+turn+point+travel+latency
        return now+max(0,t-.04),now+t+.08

    def hp_bounds(self,target,now,early,late,shots):
        certain=sum(s.damage_min for s in shots if s.early>now and s.late<=early)
        possible=sum(s.damage_max for s in shots if s.late>now and s.early<=late)
        # With a known tower shot do not also extrapolate its historic HP drop.
        # Other attacks can still invalidate this estimate; never label exact.
        rate=0 if shots else self.dps(target.uid)
        low=target.hp-possible-rate*1.35*max(0,late-now)
        high=target.hp-certain-rate*.55*max(0,early-now)+max(0,target.regen)*(late-now)
        return low,high

    def choose(self,frame,deny=True,tower=True,latency=.08,margin=3.,frame_age=0.,excluded=(),damage_reserve=0,allow_current_hp=False):
        self.watch=None;self.rows=[];self.counts={'seen':len(frame.creeps),'range':0,'screen':0,'ready':0,'hp':0,'risk':0,'deny_locked':0,'damage_unknown':0,'ordered':0}
        hero=frame.hero
        if not hero or frame.error or frame.incomplete:return None,'Нет полного свежего кадра крипов'
        if not finite(frame_age) or not 0<=frame_age<=.15:return None,'Кадр устарел'
        now=frame.game_time+frame_age;choices=[];watch=[]
        interval=self.measured_interval(hero,now)
        ready=not (hero.last_attack>0 and hero.last_attack+interval-now>.04)
        for c in frame.creeps:
            if not c.visible or c.hp<=0:continue
            w,h=frame.viewport
            on_screen=len(c.screen)>=2 and 8<c.screen[0]<w-8 and .09*h<c.screen[1]<.79*h
            in_range=distance(hero.pos,c.pos)<=hero.attack_range+hero.hull+c.hull-8
            if on_screen:self.counts['screen']+=1
            if in_range:self.counts['range']+=1
            allied=c.team==hero.team
            multiplier=c.incoming_multiplier
            known=finite(multiplier) and 0<=multiplier<=2
            quell=quelling_bonus(hero,c)
            damage=max(0,(hero.damage_min+quell)*armor_multiplier(c.armor)*multiplier*(1-max(0,min(25,damage_reserve))/100)-margin) if known else 0.
            maximum=max(damage,(hero.damage_max+quell)*armor_multiplier(c.armor)*multiplier) if known else 0.
            early,late=self.impact(hero,c,now,latency)
            shots=[s for s in self.shots if s.target==c.uid] if tower else []
            low,high=self.hp_bounds(c,frame.game_time,early,late,shots)
            live_finish=(allow_current_hp and not shots and c.hp+max(0,c.regen)*max(0,late-frame.game_time)<=damage)
            state='wait';reason='HP выше расчётного урона';rank=0 if not allied else 1
            if c.uid in excluded:state='ordered';reason='Своя команда уже отправлена'
            elif not on_screen:state='screen';reason='Вне игровой области экрана'
            elif not in_range:state='range';reason='Вне дальности атаки'
            elif c.team not in (2,3):state='team';reason='Неизвестная команда'
            elif allied and (not deny or c.hp>.5*c.maxhp):state='deny_locked';reason='Денай выключен / HP выше 50%'
            elif not known:state='damage_unknown';reason='Не прочитан входящий урон катапульты'
            elif damage<=0:state='damage_unknown';reason='Расчётного урона недостаточно'
            elif (low>0 and high<=damage) or live_finish:
                state='deny' if allied else 'last_hit'
                reason='HP уже в пороге · риск чужого удара' if live_finish and low<=0 else 'HP к попаданию · оценка'
            else:
                if low<=0:state='risk';reason='Прогноз: может погибнуть до удара'
                if tower and not allied and c.hp>maximum:
                    shot=next((s for s in sorted(shots,key=lambda s:s.early) if late+.05<s.early),None)
                    if shot and len(shots)==1:
                        after_low=c.hp-maximum-shot.damage_max
                        after_high=c.hp-damage-shot.damage_min+max(0,c.regen)*(shot.late-frame.game_time)
                        if c.hp-shot.damage_max>damage and 0<after_low<=after_high<=damage:
                            low,high=after_low,after_high;state='prepare';rank=2;reason='Подготовка перед башней · оценка'
            eligible=state in ('last_hit','deny','prepare')
            if state in ('range','screen'):self.counts[state+'_reject']=self.counts.get(state+'_reject',0)+1
            elif state in self.counts:self.counts[state]+=1
            if state=='wait':self.counts['hp']+=1
            if eligible and ready:self.counts['ready']+=1
            choice=Choice(c,state if eligible else 'wait',early,late,max(0,low),max(0,high),damage,reason)
            self.rows.append(dict(choice=choice,state=state if ready or not eligible else 'cooldown',on_screen=on_screen,
                                  in_range=in_range,known=known,ready=eligible and ready,reason=reason))
            if eligible:choices.append((rank,high,choice))
            if on_screen and in_range and state not in ('ordered','deny_locked','damage_unknown','team'):
                watch.append((0 if not allied else 1,max(0,high-damage),c.uid,replace(choice,kind='wait',reason='Ожидание атаки' if not ready else reason)))
        if watch:self.watch=min(watch,key=lambda row:row[:3])[3]
        if not frame.creeps:return None,'Линейные крипы не прочитаны · см. счётчики'
        if not ready:return None,'Ожидание готовности атаки'
        if not choices:
            return None,'Крипов %d · в дальности %d · HP выше порога %d · риск %d · денай закрыт %d · урон неизвестен %d'%(
                self.counts['seen'],self.counts['range'],self.counts['hp'],self.counts['risk'],self.counts['deny_locked'],self.counts['damage_unknown'])
        return min(choices,key=lambda x:(x[0],x[1],x[2].target.uid))[2],''
