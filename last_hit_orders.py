"""Bounded ownership of ONE external attack order, not an engine order decoder."""
from dataclasses import dataclass


@dataclass
class OwnedAttack:
    hero: tuple
    target: tuple
    token: int
    revision: int
    context: tuple
    due: float
    expires: float
    stamp: float
    clock: float
    windup: float
    seen: bool=False
    missing: int=0
    sample: float=-1.

    def observe(self,frame,now):
        if (not frame.hero or frame.hero.uid!=self.hero or frame.game_time<self.clock or now>self.expires):return 'expired'
        if frame.error or frame.incomplete or not 0<=now-frame.at<=.15:return 'wait'
        if frame.at!=self.sample:
            self.sample=frame.at
            present=any(c.uid==self.target and c.visible and c.hp>0 for c in frame.creeps)
            self.missing=0 if present else self.missing+1
        if not self.seen and frame.hero.last_attack>self.stamp+.001:
            self.seen=True
            # LastAttackTime phase is not proven. Also wait through a scaled
            # windup after its timestamp; never Stop immediately on its change.
            self.due=max(self.due,frame.at+max(0,frame.hero.last_attack+self.windup+.06-frame.game_time))
        return 'stop' if now>=self.due and (self.seen or self.missing>=2) else 'wait'
