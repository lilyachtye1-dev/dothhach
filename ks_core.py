"""KS5: data-driven profiles, pure target selection, toggle and anti-repeat ledger.
No game access, Win32, network, eval or exec in this module.
"""
from dataclasses import dataclass
from pathlib import Path
import json
import math


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def number(value, label, lo, hi):
    if not finite(value) or not lo <= value <= hi:
        raise ValueError('%s: expected finite number in [%s, %s]' % (label, lo, hi))
    return float(value)


def keycode(value):
    if isinstance(value, str):
        value = value.upper().strip()
        if len(value) == 1 and value in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
            return ord(value)
        if value.startswith('F') and value[1:].isdigit() and 1 <= int(value[1:]) <= 12:
            return 111 + int(value[1:])
        if value in ('', 'NONE'):
            return 0
        raise ValueError('unknown key: ' + value)
    allowed = set(range(48, 58)) | set(range(65, 91)) | set(range(96, 106)) | set(range(112, 124)) | {0}
    if type(value) is not int or value not in allowed:
        raise ValueError('invalid key code: ' + str(value))
    return value


def table(value, label, lo, hi):
    values = value if isinstance(value, list) else [value]
    if not 1 <= len(values) <= 30:
        raise ValueError(label + ': empty/oversized table')
    return tuple(number(v, label, lo, hi) for v in values)


def at_level(values, level):
    if len(values) == 1:
        return values[0]
    return values[level - 1] if 1 <= level <= len(values) else None


def no_duplicate_keys(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError('duplicate JSON key: ' + k)
        result[k] = v
    return result


@dataclass(frozen=True)
class Profile:
    name: str
    hero: str
    kind: str
    variants: dict
    key: int
    cast: str
    damage_type: str
    damage: tuple
    int_scale: tuple
    ranges: tuple
    min_range: float
    delay: float
    projectile_speed: float
    priority: int
    stationary_only: bool
    requires_charges: bool
    notes: str
    self_damage_fraction: float = 0.0
    stack_scaling: dict = None

    @property
    def needs_cursor(self):
        return self.cast in ('unit', 'point')


class Settings:
    def __init__(self, path):
        doc = json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=no_duplicate_keys)
        if doc.get('schema_version') != 1:
            raise ValueError('unsupported ks_profiles.json schema_version')
        g = doc['settings']
        from ks_modifiers import validate_layout
        layout = g.get('live_modifier_layout')
        self.live_modifier_layout = validate_layout(layout) if layout is not None else None
        def flag(value, label):
            if type(value) is not bool:
                raise ValueError(label + ': expected true/false')
            return value
        self.auto_cast = flag(g['auto_cast'], 'auto_cast')
        self.toggle_vk = keycode(g.get('toggle_key', 'NONE'))
        if self.toggle_vk in (118, 119):
            raise ValueError('toggle_key conflicts with F7/F8')
        for attr, k, lo, hi in [
            ('stale', 'stale_seconds', .02, 1), ('stale_off', 'stale_off_seconds', 1, 30),
            ('margin', 'hp_margin', 0, 10000), ('latency', 'latency_seconds', 0, 2),
            ('reserve', 'mana_reserve', 0, 10000), ('interval', 'attempt_interval', .2, 30),
            ('confirm_timeout', 'confirm_timeout', .3, 10), ('impact_padding', 'impact_padding', 0, 2),
            ('aim_y', 'aim_y', -200, 200), ('screen_margin', 'screen_margin', 0, 500),
            ('hud_bottom', 'hud_bottom', 0, 1000), ('manual_pause', 'manual_pause_seconds', .05, 3),
            ('stationary_seconds', 'stationary_seconds', .1, 3), ('max_point_speed', 'max_point_speed', 0, 200)]:
            setattr(self, attr, number(g[k], k, lo, hi))
        for attr, name in [('local_mask', 'local_block_mask'), ('target_mask', 'target_block_mask')]:
            value = g[name]
            if type(value) is not int or not 0 <= value < 2**64:
                raise ValueError(name + ': expected uint64')
            setattr(self, attr, value)
        blocked = g['blocked_target_items']
        if not isinstance(blocked, list) or not all(isinstance(n, str) for n in blocked):
            raise ValueError('blocked_target_items: expected string list')
        self.blocked_items = set(blocked)
        self.item_keys = tuple(keycode(k) for k in g['item_slot_keys'])
        if len(self.item_keys) != 6 or len([k for k in self.item_keys if k]) != len({k for k in self.item_keys if k}):
            raise ValueError('item_slot_keys must contain six distinct bindings (or NONE)')
        reserved = {k for k in (self.toggle_vk, 118, 119) if k}
        if reserved.intersection(self.item_keys):
            raise ValueError('item key conflicts with toggle or overlay controls')
        self.profiles, self.by_name, self.disabled = {}, {}, []
        self.warnings, self.disabled_reasons = [], {}
        ids, hero_keys = set(), set()
        for entry in doc['abilities']:
            name = entry['id']
            if not isinstance(name, str) or not name or name in ids:
                raise ValueError('empty/duplicate profile id')
            ids.add(name)
            aliases_for_cloud = entry.get('variants')
            is_zuus_cloud = name == 'zuus_cloud' or (isinstance(aliases_for_cloud, dict) and 'zuus_cloud' in aliases_for_cloud)
            if not flag(entry['enabled'], name + '.enabled'):
                self.disabled.append(name)
                disabled_note = str(entry.get('notes', '')).strip()
                reason = 'Шаблон выключен; проверь значения и клавишу.'
                if disabled_note:
                    reason += ' ' + disabled_note
                if isinstance(aliases_for_cloud, dict):
                    for alias in aliases_for_cloud:
                        if isinstance(alias, str):
                            self.disabled_reasons[alias] = reason
                if is_zuus_cloud:
                    self.disabled_reasons['zuus_cloud'] = 'Zeus Cloud не поддерживается: периодический урон не рассчитан.'
                continue
            if is_zuus_cloud:
                reason = 'Zeus Cloud не загружен: периодический урон не поддержан; остальные профили продолжают работать.'
                self.disabled.append(name)
                self.warnings.append(name + ': ' + reason)
                if isinstance(aliases_for_cloud, dict):
                    for alias in aliases_for_cloud:
                        if isinstance(alias, str):
                            self.disabled_reasons[alias] = reason
                else:
                    self.disabled_reasons['zuus_cloud'] = reason
                continue
            if entry['model'] != 'flat':
                raise ValueError(name + ': only flat + int_scale is implemented; leave unsupported profiles disabled')
            cast, kind = entry['cast'], entry['kind']
            if cast not in ('unit', 'point', 'no_target', 'global') or kind not in ('ability', 'item'):
                raise ValueError(name + ': unsupported cast/kind')
            if entry['damage_type'] not in ('magical', 'pure'):
                raise ValueError(name + ': physical/dynamic damage needs a different verified model')
            damage = table(entry['damage'], name + '.damage', 0, 100000)
            scale = table(entry['int_scale'], name + '.int_scale', 0, 100)
            ranges = table(entry['range'], name + '.range', 0, 50000)
            if cast != 'global' and min(ranges) <= 0:
                raise ValueError(name + ': non-global range must be positive')
            if len(scale) not in (1, len(damage)) or len(ranges) not in (1, len(damage)):
                raise ValueError(name + ': per-level table lengths disagree')
            aliases = entry['variants']
            if not isinstance(aliases, dict) or not aliases:
                raise ValueError(name + ': variants must map entity names to level overrides (0=live)')
            for alias, level in aliases.items():
                if (not isinstance(alias, str) or not alias or alias in self.by_name or
                        type(level) is not int or not 0 <= level <= len(damage)):
                    raise ValueError(name + ': invalid/duplicate variant')
                if kind == 'item' and not alias.startswith('item_'):
                    raise ValueError(name + ': item variant name expected')
                if kind == 'ability' and alias.startswith('item_'):
                    raise ValueError(name + ': item name cannot use an ability binding')
            key = 0 if entry['key'] == 'inventory' else keycode(entry['key'])
            if kind == 'item' and entry['key'] != 'inventory':
                raise ValueError(name + ': item uses current inventory slot binding')
            hero = entry['hero']
            if not isinstance(hero, str) or not hero or (kind == 'ability' and hero == '*'):
                raise ValueError(name + ': specify owner hero for ability bindings')
            if key in reserved:
                raise ValueError(name + ': key conflicts with control key')
            if kind == 'ability' and key:
                if (hero, key) in hero_keys or key in self.item_keys:
                    raise ValueError(name + ': duplicate ability/item key for this hero')
                hero_keys.add((hero, key))
            priority = entry['priority']
            if type(priority) is not int or not -1000 <= priority <= 1000:
                raise ValueError(name + ': invalid priority')
            stacking = entry.get('stack_scaling')
            if stacking is not None:
                if not isinstance(stacking, dict) or set(stacking) != {'modifier','per_stack','talent','talent_bonus'}:
                    raise ValueError(name + ': invalid stack_scaling')
                if kind != 'ability' or not isinstance(stacking['modifier'], str) or not stacking['modifier'].startswith('CDOTA_Modifier_'):
                    raise ValueError(name + ': invalid stack modifier')
                if not isinstance(stacking['talent'], str) or not stacking['talent'].startswith('special_bonus_'):
                    raise ValueError(name + ': invalid stack talent')
                stacking = dict(stacking)
                for field in ('per_stack','talent_bonus'):
                    stacking[field] = number(stacking[field], name + '.' + field, 0, 10000)
            self_damage_fraction = number(entry.get('self_damage_fraction', 0),
                                          name + '.self_damage_fraction', 0, 1)
            p = Profile(name, hero, kind, dict(aliases), key, cast, entry['damage_type'], damage, scale,
                        ranges, number(entry['min_range'], name + '.min_range', 0, 50000),
                        number(entry['delay'], name + '.delay', 0, 10),
                        number(entry['projectile_speed'], name + '.projectile_speed', 0, 10000),
                        priority, flag(entry['stationary_only'], name + '.stationary_only'),
                        flag(entry['requires_charges'], name + '.requires_charges'), str(entry.get('notes', '')),
                        self_damage_fraction, stacking)
            self.profiles[name] = p
            for alias in aliases:
                self.by_name[alias] = p

    def match(self, action):
        p = self.by_name.get(action.name)
        return (p, p.variants[action.name] or action.level) if p else (None, 0)


@dataclass(frozen=True)
class Action:
    name: str
    entity: int
    level: int
    cooldown: float
    mana: float
    slot: int = -1
    activated: bool = True
    hidden: bool = False
    casting: bool = False
    charges: int = 0

    @property
    def identity(self):
        return self.name, self.entity


@dataclass(frozen=True)
class Unit:
    idx: int
    entity: int
    name: str
    team: int
    local: bool
    alive: bool
    hp: float
    mana: float
    mr: float
    regen: float
    pos: tuple
    screen: tuple
    at: float
    visible: bool = True
    illusion: bool = False
    state: int = 0
    items: tuple = ()
    actions: tuple = ()
    busy: bool = False
    inventory_known: bool = True
    uncertainty: str = ''
    intellect: float = 0
    motion_known: bool = False
    speed: float = 0
    stationary_for: float = 0
    stack_values: tuple = ()
    talent_levels: tuple = ()


@dataclass(frozen=True)
class Decision:
    target: Unit
    action: Action
    damage: float
    required: float
    distance: float
    profile: Profile
    key: int
    impact_delay: float


def fresh(unit, now, limit):
    return finite(now) and finite(unit.at) and 0 <= now - unit.at <= limit


def point_ok(p):
    return len(p) >= 2 and all(finite(n) and abs(n) < 100000 for n in p[:2])



def raw_damage_details(p, me, level):
    """Return pre-resistance damage and any conservative-estimate note.

    If a stack profile has a known nonnegative per-stack base but the talent
    state is unreadable, use the lower bound that omits the positive talent
    bonus. This never inflates the estimate; it can only defer a cast that
    would need the unknown bonus.
    """
    scale = at_level(p.int_scale, level)
    if scale and (not finite(me.intellect) or not 0 <= me.intellect <= 10000):
        raise ValueError('INT неизвестен')
    raw = p.damage[level - 1] + (scale * me.intellect if scale else 0)
    note = ''
    if p.stack_scaling is not None:
        rule = p.stack_scaling
        stacks = dict(me.stack_values).get(rule['modifier'])
        talent = dict(me.talent_levels).get(rule['talent'])
        if type(stacks) is not int or not 0 <= stacks <= 10000:
            raise ValueError('число стаков неизвестно')
        if stacks and (type(talent) is not int or talent not in (0, 1)):
            raw += stacks * rule['per_stack']
            note = ('Талант неизвестен; показана нижняя оценка без бонуса +%g за стак.' %
                    rule['talent_bonus'])
        else:
            raw += stacks * (rule['per_stack'] + (rule['talent_bonus'] if talent == 1 else 0))
    return raw, note


def raw_damage(p, me, level):
    """Shared numeric estimate for target selection; unknown positive bonuses are omitted."""
    return raw_damage_details(p, me, level)[0]


def choose(units, settings, now, viewport, notes=None, excluded=(), disabled=()):
    def reject(text):
        if notes is not None and text not in notes and len(notes) < 12:
            notes.append(text)
    local = [u for u in units if u.local]
    if len(local) != 1:
        reject('Нет единственного локального героя')
        return None
    me = local[0]
    if (not me.alive or me.illusion or me.team not in (2, 3) or me.busy or
            me.state & settings.local_mask or not fresh(me, now, settings.stale) or
            not finite(me.hp) or me.hp <= 0 or not finite(me.mana) or me.mana < 0):
        reject('Локальный герой занят / данные не подходят')
        return None
    if len(viewport) != 2 or not all(finite(v) and v > 0 for v in viewport):
        reject('Неверная геометрия окна')
        return None
    width, height = viewport
    ready = []
    for a in me.actions:
        p, level = settings.match(a)
        if p is None or (p.hero != '*' and p.hero != me.name.removeprefix('npc_dota_hero_')):
            continue
        label = p.name
        if p.name in disabled:
            reject(label + ': выключено в меню')
            continue
        if a.identity in excluded:
            reject(label + ': нет подтверждения CD после предыдущей попытки')
            continue
        if type(level) is not int or not 1 <= level <= len(p.damage):
            reject(label + ': уровень не поддержан / не изучен')
        elif not a.activated or a.hidden or a.casting:
            reject(label + ': отключён / скрыт / кастуется')
        elif not finite(a.cooldown) or a.cooldown != 0:
            reject('%s: CD %.2f' % (label, a.cooldown))
        elif not finite(a.mana) or a.mana < 0 or a.mana + settings.reserve > me.mana:
            reject('%s: мана %.0f, нужно %.0f' % (label, me.mana, a.mana + settings.reserve))
        elif p.requires_charges and (type(a.charges) is not int or a.charges <= 0):
            reject(label + ': нет подтверждённых зарядов')
        else:
            if p.kind == 'item':
                if not me.inventory_known or not 0 <= a.slot <= 5:
                    reject(label + ': неизвестен активный слот')
                    continue
                key = settings.item_keys[a.slot]
            else:
                key = p.key
            if settings.auto_cast and not key:
                reject(label + ': клавиша не задана')
                continue
            try:
                raw = raw_damage(p, me, level)
            except ValueError as exc:
                reject(label + ': ' + str(exc))
                continue
            ready.append((a, p, key, at_level(p.ranges, level), raw))
    if not ready:
        if not any(settings.match(a)[0] for a in me.actions):
            reject('Нет прочитанных способностей из ks_profiles.json')
        return None
    candidates, seen = [], 0
    for target in units:
        if (target.local or target.entity == me.entity or target.idx == me.idx or
                target.team not in (2, 3) or target.team == me.team or not target.alive or
                not target.visible or target.illusion):
            continue
        seen += 1
        name = target.name.removeprefix('npc_dota_hero_')
        if (target.state & settings.target_mask or not fresh(target, now, settings.stale) or
                not finite(target.hp) or target.hp <= 0 or not finite(target.regen) or abs(target.regen) > 10000):
            reject(name + ': данные цели не подходят')
            continue
        if settings.blocked_items.intersection(target.items):
            reject(name + ': защитный предмет')
            continue
        if settings.auto_cast and not target.inventory_known:
            reject(name + ': инвентарь неизвестен, AUTO запрещён')
            continue
        world_known = point_ok(target.pos) and point_ok(me.pos)
        dist = math.hypot(target.pos[0] - me.pos[0], target.pos[1] - me.pos[1]) if world_known else math.inf
        for a, p, key, radius, raw in ready:
            if p.needs_cursor:
                if not point_ok(target.screen):
                    reject(name + ': нет экранной точки для направленного каста')
                    continue
                x, y = target.screen[:2]
                y += settings.aim_y if p.cast == 'unit' else 0
                if not (settings.screen_margin <= x <= width - settings.screen_margin and
                        settings.screen_margin <= y <= height - settings.hud_bottom):
                    reject(name + ': вне окна / зона HUD')
                    continue
            if not world_known and (p.cast != 'global' or p.min_range > 0 or p.projectile_speed > 0):
                reject(name + ': нет мировых координат для проверки дальности')
                continue
            if dist < p.min_range or (p.cast != 'global' and dist > radius):
                reject('%s: дистанция %.0f вне %.0f..%.0f (%s)' % (name, dist, p.min_range, radius, p.name))
                continue
            if p.cast in ('point', 'no_target') and p.stationary_only and (
                    not target.motion_known or not finite(target.speed) or target.speed > settings.max_point_speed or
                    not finite(target.stationary_for) or target.stationary_for < settings.stationary_seconds):
                reject(name + ': каст ждёт неподвижную цель')
                continue
            if p.damage_type == 'magical':
                if not finite(target.mr) or not -1 <= target.mr <= 1:
                    reject(name + ': MR неизвестен')
                    continue
                damage = raw * (1 - target.mr)
            else:
                damage = raw
            if p.self_damage_fraction:
                self_damage = damage * p.self_damage_fraction
                if me.hp <= self_damage:
                    reject('%s: оценка самоурона %.0f может убить героя' % (label, self_damage))
                    continue
            delay = p.delay + settings.latency + (dist / p.projectile_speed if p.projectile_speed else 0)
            needed = target.hp + settings.margin + max(0, target.regen) * (delay + max(0, now - target.at))
            if damage < needed:
                reject('%s: урон %.0f < нужно %.0f (%s)' % (name, damage, needed, p.name))
                continue
            d = Decision(target, a, damage, needed, dist, p, key, delay)
            rank = (a.mana, p.priority, damage - needed, target.hp, target.idx, p.name, a.slot)
            candidates.append((rank, d))
    if not seen:
        reject('Нет живого видимого врага')
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


class Toggle:
    def __init__(self):
        self.enabled = False
        self.previous = None  # held at startup is NOT a fresh press

    def update(self, down, hard_off=False, may_enable=True):
        edge = self.previous is not None and down and not self.previous
        self.previous = down
        if hard_off:
            self.enabled = False
        elif edge:
            if self.enabled:
                self.enabled = False
            elif may_enable:
                self.enabled = True
        return self.enabled


@dataclass
class Pending:
    action: Action
    sent: float
    deadline: float
    until_impact: float
    uses_charges: bool


class CastLedger:
    """No retry storm: one pending send. Unknown result quarantines that action.
A new cooldown or charge decrement is evidence of acceptance, NOT of a kill.
The quarantine clears only on observed cooldown > 0 (then normal cd gating applies).
"""
    def __init__(self):
        self.hero = None
        self.pending = None
        self.quarantined = set()
        self.next_send = 0
        self.message = ''

    def sync(self, me, now):
        if me is not None and self.hero != me.entity:
            self.hero = me.entity
            self.pending = None
            self.quarantined.clear()
            self.next_send = now
            self.message = ''
        if me is not None:
            for a in me.actions:
                if finite(a.cooldown) and a.cooldown > 0:
                    self.quarantined.discard(a.identity)
        p = self.pending
        if p is None:
            return
        if me is not None and me.at > p.sent:
            a = next((a for a in me.actions if a.identity == p.action.identity), None)
            if a is not None and ((finite(a.cooldown) and a.cooldown > 0) or
                                  (p.uses_charges and a.charges >= 0 and a.charges < p.action.charges)):
                self.pending = None
                self.next_send = max(self.next_send, p.until_impact)
                self.message = 'Изменение CD/заряда наблюдалось; убийство не подтверждено'
                return
        if now >= p.deadline:
            self.quarantined.add(p.action.identity)
            self.pending = None
            self.next_send = max(self.next_send, p.until_impact)
            self.message = 'Нет подтверждения CD: ' + p.action.name + ' заблокирован до наблюдения CD>0'

    def ready(self, now):
        return self.pending is None and now >= self.next_send

    def begin(self, decision, settings, now):
        if not self.ready(now) or decision.action.identity in self.quarantined:
            return False
        until = now + max(settings.interval, decision.impact_delay + settings.impact_padding)
        self.pending = Pending(decision.action, now,
                               now + settings.confirm_timeout + decision.profile.delay,
                               until, decision.profile.requires_charges)
        self.next_send = until
        return True


if __name__ == '__main__':
    import sys
    cfg = Settings(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name('ks_profiles.json'))
    print('KS5 config OK: %d enabled profiles, %d disabled templates, %d entity names' %
          (len(cfg.profiles), len(cfg.disabled), len(cfg.by_name)))
    for warning in cfg.warnings:
        print('WARNING: ' + warning)
