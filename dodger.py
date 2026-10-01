"""Pure defensive planner. No memory or input. Effects are NOT interchangeable.
The bundled live provider uses inferred launches, not a projectile-manager feed.
"""
from dataclasses import dataclass, replace
import math
from ks_core import fresh, keycode, point_ok


@dataclass(frozen=True)
class Defense:
    name: str
    hero: str
    key: str
    mode: str
    cast: str = 'no_target'
    cast_point: float = 0.
    duration: tuple = (0.,)
    range: float = 0.
    priority: int = 10
    enabled: bool = True
    hold: float = .25
    blocked: str = ''

    @property
    def needs_cursor(self): return self.cast not in ('no_target','self_alt')


# Cast points/durations: research/dodge_ability_snapshot.json (not runtime proof).
DEFENSES = (
    Defense('item_manta', '*', 'inventory', 'disjoint'),
    Defense('antimage_counterspell', 'antimage', 'E', 'block_reflect', duration=(1.3,), priority=5),
    Defense('puck_phase_shift', 'puck', 'E', 'invulnerable', cast_point=.01, duration=(1.,1.75,2.5,3.25), hold=3.6),
    Defense('phantom_lancer_doppelwalk', 'phantom_lancer', 'W', 'disjoint', 'point_self', .1, (1.,), 575, hold=1.4),
    Defense('naga_siren_mirror_image', 'naga_siren', 'Q', 'disjoint', cast_point=.4, hold=1.),
    Defense('chaos_knight_phantasm', 'chaos_knight', 'R', 'disjoint', cast_point=.4, priority=50, enabled=False, hold=1.),
    Defense('nyx_assassin_spiked_carapace', 'nyx_assassin', 'E', 'damage', duration=(2.,), priority=80, enabled=False),
    Defense('item_lotus_orb', '*', 'inventory', 'reflect', 'self', duration=(5.,), priority=100, enabled=False),
    Defense('item_cyclone', '*', 'inventory', 'invulnerable', 'self', duration=(2.5,), priority=25, hold=2.9),
    Defense('item_wind_waker', '*', 'inventory', 'invulnerable', 'self', duration=(2.5,), priority=25, hold=2.9),
    Defense('life_stealer_infest', 'life_stealer', 'R', 'disjoint', 'ally', .2, (1.,), 150, 60, False, 2.),
    Defense('tusk_snowball', 'tusk', 'W', 'invulnerable', 'enemy', .1, (3.,), 1150, 45, False, 3.5),
    Defense('ember_spirit_sleight_of_fist', 'ember_spirit', 'W', 'invulnerable', 'point_enemy', 0., (.2,), 650, 20, False, .6,
            'Нужны точный ETA и подтверждённые цели/длительность прыжков; текущий источник недостаточен для безопасного тайминга.'),
    # Do not equate tooltip transformation time with a tested invulnerability window.
    Defense('monkey_king_mischief', 'monkey_king', 'D', 'damage', duration=(.1,), enabled=False,
            blocked='Окно 0.1 с слишком узкое для этого источника ETA; это damage immunity, не защита от stun.'),
    Defense('alchemist_chemical_rage', 'alchemist', 'R', 'invulnerable', enabled=False,
            blocked='Не подтверждены invulnerability frames для этой сборки; длительность трансформации не является доказательством.'),
    Defense('lone_druid_true_form', 'lone_druid', 'R', 'invulnerable', enabled=False,
            blocked='Не подтверждены invulnerability frames для этой сборки; старую механику True Form не предполагаем.'),
    Defense('phoenix_supernova', 'phoenix', 'R', 'invulnerable', enabled=False,
            blocked='Нужна проверка Scepter/режима цели и входа в яйцо; автоматическая трата ульта заблокирована.'),
)
BY_NAME = {p.name: p for p in DEFENSES}


@dataclass(frozen=True)
class Threat:
    id: tuple
    source: int
    target: int
    name: str
    earliest: float
    latest: float
    observed: float
    disjointable: bool = True
    blockable: bool = True
    reflectable: bool = True
    damage: bool = True
    disable: bool = True
    evidence: str = 'inferred_launch'


@dataclass(frozen=True)
class Choice:
    target: object
    action: object
    profile: Defense
    key: int
    threat: Threat
    hold: float


def distance(a, b):
    if not point_ok(a.pos) or not point_ok(b.pos): return math.inf
    return math.hypot(a.pos[0]-b.pos[0], a.pos[1]-b.pos[1])


def screen_ok(unit, viewport):
    return (point_ok(unit.screen) and 40 <= unit.screen[0] <= viewport[0]-40 and
            60 <= unit.screen[1] <= viewport[1]-180)


def choose_defense(me, units, threats, now, viewport, settings, disabled=(), quarantined=(), used=(), notes=None, paused=(), held_keys=(), interrupt_cast=False, alt_selfcast=False):
    if not me.alive or (me.busy and not interrupt_cast) or not fresh(me, now, settings.stale): return None
    def note(text):
        if notes is not None and text not in notes and len(notes)<5: notes.append(text)
    candidates = []
    if not threats: note('Нет распознанной угрозы')
    for t in threats:
        proof_age=min(settings.stale,.075) if t.evidence in ('local_mark_launch','local_mark_phase') else settings.stale
        if (t.id in used or t.target != me.entity or (t.evidence != 'inferred_launch' and not (t.evidence in ('local_mark_launch','local_mark_phase') and t.name=='sniper_assassinate')) or
            not all(math.isfinite(x) for x in (t.earliest,t.latest,t.observed)) or
            not 0 <= now-t.observed <= proof_age or t.latest < t.earliest or t.earliest <= now):
            note('Угроза уже обработана, устарела или окно реакции прошло');continue
        for action in me.actions:
            p = BY_NAME.get(action.name)
            if p is None: continue
            if alt_selfcast and p.cast=='self':p=replace(p,cast='self_alt')
            if t.evidence=='local_mark_phase' and p.name!='antimage_counterspell':
                note(p.name+': до запуска разрешена только предзащита Counterspell');continue
            reason = (p.blocked if p.blocked else 'выключен в меню' if p.name in disabled else
                      'источник временно исключён' if p.name in paused else
                      'нет ответа CD после предыдущей команды' if action.identity in quarantined else
                      'не подходит этому герою' if p.hero not in ('*', me.name.removeprefix('npc_dota_hero_')) else '')
            if reason:
                note(p.name+': '+reason);continue
            if not (action.level > 0 and action.activated and not action.hidden and not action.casting and
                    math.isfinite(action.cooldown) and 0 <= action.cooldown <= .02 and
                    math.isfinite(action.mana) and 0 <= action.mana <= me.mana):
                reason=('не изучено' if action.level<=0 else 'способность уже кастуется' if action.casting else
                        'скрыто / не активно' if action.hidden or not action.activated else
                        'CD %.2f с'%action.cooldown if math.isfinite(action.cooldown) and action.cooldown>.02 else
                        'не хватает маны' if math.isfinite(action.mana) and action.mana>me.mana else 'невалидные CD/мана')
                note(p.name+': '+reason);continue
            if p.key == 'inventory':
                if not me.inventory_known or not 0 <= action.slot < 6:
                    note(p.name+': нет подтверждённого активного слота');continue
                key = settings.item_keys[action.slot]
            else: key = keycode(p.key)
            if p.cast=='self_alt' and not (48<=key<=57 or 65<=key<=90):
                note(p.name+': Alt self-cast требует букву/цифру, не F-клавишу');continue
            if key in held_keys:
                note(p.name+': собственная клавиша удерживается');continue
            targets = [me]
            if p.cast in ('ally','enemy','point_enemy'):
                targets = [u for u in units if u.entity != me.entity and u.alive and u.visible and not u.illusion and
                           fresh(u, now, settings.stale) and
                           ((u.team == me.team) if p.cast == 'ally' else (u.team in (2,3) and u.team != me.team)) and
                           distance(me,u) <= p.range-50]
                targets.sort(key=lambda u: (distance(me,u),u.entity))
            if not targets: note(p.name+': нет подходящей цели в дальности')
            for target in targets:
                if p.needs_cursor and not screen_ok(target, viewport):
                    note(p.name+': цель вне безопасной области экрана');continue
                # Deliberately broad input/turn uncertainty; never use frame-perfect windows here.
                activation_min = .04 + p.cast_point
                activation_max = .18 + p.cast_point + (.25 if p.needs_cursor else 0.)
                if now + activation_max >= t.earliest:
                    note(p.name+': поздно, до ранней границы %.2f с'%(t.earliest-now));continue
                duration = p.duration[min(action.level-1, len(p.duration)-1)]
                full = True
                if p.mode == 'disjoint':
                    if not t.disjointable: continue
                else:
                    if now + activation_min + duration <= t.latest + .04:
                        note(p.name+': рано / защита не перекрывает весь интервал');continue
                    if p.mode == 'block_reflect' and not t.blockable: continue
                    if p.mode == 'reflect':
                        if not t.reflectable: continue
                        full = False  # Lotus does not cancel the original spell.
                    if p.mode == 'damage':
                        if not t.damage: continue  # Carapace is useless against zero-damage Shackleshot.
                        full = not t.disable
                hold = max(p.hold, activation_max+.2)
                if p.name == 'puck_phase_shift': hold = duration + activation_max + .3
                if p.name == 'life_stealer_infest': hold = 99999.  # explicit re-arm after exiting host
                candidates.append(((not full,p.priority,t.earliest,p.name,target.entity),
                                   Choice(target,action,p,key,t,hold)))
                break
    if not candidates and notes is not None and not notes: note('Нет прочитанной подходящей защиты')
    return min(candidates,key=lambda x:x[0])[1] if candidates else None
