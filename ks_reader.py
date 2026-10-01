"""Adapter to the supplied Dot reader. Reads only; no memory writes.
The explorer and client_dll schemas differ; use ONE schema, never mix them.
"""
from pathlib import Path
import json
import hashlib
from ks_modifiers import ModifierReader, talent_levels
import math
import struct
import time
from ks_core import Action, Unit, finite, Settings
from ks_inventory import read_inventory


def talent_levels_from_snapshot(dot, records, requested, classes):
    """Validate talent records exported by Dot.mk_hero from its chosen ability vector.
    This avoids guessing among several superficially plausible layouts of that vector.
    """
    requested = set(requested)
    if not requested:
        return ()
    if not isinstance(records, (list, tuple)):
        raise ValueError('talent records unavailable in hero snapshot')
    offset = classes['C_DOTABaseAbility']['fields']['m_iLevel']
    if type(offset) is not int or offset <= 0:
        raise ValueError('invalid talent level offset')
    found = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        name = record.get('name')
        if name not in requested:
            continue
        if name in found:
            raise ValueError('duplicate talent entity: ' + name)
        idx, entity = record.get('idx'), record.get('ent')
        handle = record.get('handle')
        if (type(idx) is not int or not 1 <= idx < 0x8000 or
                type(entity) is not int or entity <= 0 or
                (handle is not None and (type(handle) is not int or not 0 <= handle <= 0xFFFFFFFF or
                                         handle & 0x7FFF != idx))):
            raise ValueError('invalid talent entity identity: ' + name)
        current = dot.get_entity(idx)
        if not current or current[0] != entity or current[1] != name:
            raise ValueError('talent entity changed: ' + name)
        address = entity + offset
        first = dot.rb(address, 4)
        second = dot.rb(address, 4)
        if first is None or len(first) != 4 or first != second:
            raise ValueError('talent level changed/unreadable: ' + name)
        level = struct.unpack('<i', first)[0]
        if level not in (0, 1) or record.get('level') != level:
            raise ValueError('talent level invalid/mismatched: ' + name)
        if dot.get_entity(idx) != current:
            raise ValueError('talent identity changed during read: ' + name)
        found[name] = level
    if set(found) != requested:
        missing = ', '.join(sorted(requested - found.keys()))
        seen = sorted({r.get('name') for r in records if isinstance(r, dict) and isinstance(r.get('name'), str)})
        raise ValueError('requested talent missing from detected ability vector: ' + missing +
                         ('; detected: ' + ', '.join(seen[:16]) if seen else '; no talent names detected'))
    return tuple(sorted(found.items()))


class CombatReader:
    from dodger import BY_NAME as defensive_names

    def __init__(self, reader_module, schema_path, settings=None):
        self.r = reader_module
        self.settings = settings or Settings(Path(schema_path).with_name("ks_profiles.json"))
        self.motion = {}
        self.classes = json.loads(Path(schema_path).read_text(encoding='utf-8'))['client.dll']['classes']
        # Shared reader offsets must agree. This is NOT runtime build verification.
        checks = [
            ('C_BaseEntity', 'm_iHealth', 'M_I_HEALTH'),
            ('C_BaseEntity', 'm_lifeState', 'M_LIFE_STATE'),
            ('C_BaseEntity', 'm_iTeamNum', 'M_I_TEAM_NUM'),
            ('C_DOTA_BaseNPC', 'm_vecAbilities', 'M_VEC_ABILITIES'),
            ('C_DOTA_BaseNPC', 'm_Inventory', 'M_INVENTORY'),
            ('C_DOTA_UnitInventory', 'm_hItems', 'INV_H_ITEMS'),
            ('C_DOTA_BaseNPC', 'm_flMana', 'M_FL_MANA'),
            ('C_DOTA_BaseNPC', 'm_flMagicalResistanceValue', 'M_FL_MAGICAL_RESIST'),
            ('C_DOTA_BaseNPC_Hero', 'm_flIntellectTotal', 'M_FL_INTELLECT_TOTAL'),
            ('C_DOTABaseAbility', 'm_fCooldown', 'AB_F_COOLDOWN'),
            ('C_DOTABaseAbility', 'm_iManaCost', 'AB_I_MANA_COST'),
            ('C_DOTABaseAbility', 'm_iLevel', 'AB_I_LEVEL'),
        ]
        for cls, field, const in checks:
            if self.offset(cls, field) != getattr(reader_module, const):
                raise ValueError('schema/reader mismatch: ' + field)
        self.modifiers = ModifierReader(self.classes, self.settings.live_modifier_layout,
                                        hashlib.sha256(Path(schema_path).read_bytes()).hexdigest())
        self.last_error = ''
        self.inventory_bytes = (self.offset('C_DOTA_UnitInventory', 'm_bItemQueried') -
                                self.offset('C_DOTA_UnitInventory', 'm_hItems'))
        if not 8 <= self.inventory_bytes <= 260 or (self.inventory_bytes - 4) % 4:
            raise ValueError('invalid inventory field extent in schema')

    def offset(self, cls, field):
        return int(self.classes[cls]['fields'][field])

    def value(self, dot, ep, cls, field, fmt):
        size = struct.calcsize(fmt)
        raw = dot.rb(ep + self.offset(cls, field), size)
        if raw is None or len(raw) != size:
            raise ValueError('unreadable: ' + field)
        value = struct.unpack(fmt, raw)[0]
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('non-finite: ' + field)
        return value

    def ability(self, dot, name, ep, slot=-1, game_time=None):
        def v(field, fmt):
            return self.value(dot, ep, 'C_DOTABaseAbility', field, fmt)
        level = v('m_iLevel', '<i')
        cd = v('m_fCooldown', '<f')
        mana = v('m_iManaCost', '<i')
        active = v('m_bActivated', '<B')
        hidden = v('m_bHidden', '<B')
        phase = v('m_bInAbilityPhase', '<B')
        indefinite = v('m_bInIndefiniteCooldown', '<B')
        channel = v('m_flChannelStartTime', '<f')
        if not (0 <= level <= 30 and 0 <= cd <= 3600 and 0 <= mana <= 100000 and
                all(x in (0, 1) for x in (active, hidden, phase, indefinite))):
            raise ValueError('invalid ability: ' + name)
        p = self.settings.by_name.get(name)
        charges = 0
        if p is not None and p.requires_charges:
            charges = v('m_nAbilityCurrentCharges', '<i')
            if not 0 <= charges <= 100:
                raise ValueError('invalid charges: ' + name)
        action = Action(name, ep, level, cd, mana, slot, bool(active and not indefinite),
                        bool(hidden), bool(phase), charges)
        return action, bool(phase or self.channel_busy(channel, game_time))

    @staticmethod
    def channel_busy(start, game_time):
        # Start time is NOT a channel-active flag. An old timestamp must not
        # disable KS forever. 12s is a conservative, documented heuristic.
        if start <= 0:
            return False
        return not finite(game_time) or game_time < start or game_time - start <= 12.0

    def activity(self, dot, ep, game_time):
        phase = self.value(dot, ep, 'C_DOTABaseAbility', 'm_bInAbilityPhase', '<B')
        start = self.value(dot, ep, 'C_DOTABaseAbility', 'm_flChannelStartTime', '<f')
        if phase not in (0, 1):
            raise ValueError('invalid ability phase')
        return bool(phase or self.channel_busy(start, game_time))

    def capture(self, dot, heroes):
        """Attach immutable records; timestamp the START, not end, of collection."""
        started = time.perf_counter()
        result = []
        self.last_error = ''
        try:
            game_time = dot.curtime()
        except Exception:
            game_time = None
        for source in heroes:
            h = dict(source)
            h.pop('_ks', None)
            h['_ks_error'] = ''
            h['_ks_warnings'] = []
            h['_ks_busy_reason'] = ''
            h['_ks_busy_verified'] = False
            h['_ks_active_casts'] = ()
            h['_ks_stacks'] = {}
            h['_ks_talents'] = {}
            try:
                ep, idx = h.get('ent'), h.get('idx')
                if not ep or not idx:
                    raise ValueError('missing entity')
                current = dot.get_entity(idx)
                if not current or current[0] != ep:
                    raise ValueError('entity identity changed')
                def npc(field, fmt):
                    return self.value(dot, ep, 'C_DOTA_BaseNPC', field, fmt)
                hp = self.value(dot, ep, 'C_BaseEntity', 'm_iHealth', '<i')
                life = self.value(dot, ep, 'C_BaseEntity', 'm_lifeState', '<B')
                team = self.value(dot, ep, 'C_BaseEntity', 'm_iTeamNum', '<B')
                mana = npc('m_flMana', '<f')
                illusion = npc('m_bIsIllusion', '<B')
                regen = npc('m_flHealthRegen', '<f')
                state = npc('m_nUnitState64', '<Q')
                if hp < 0 or hp > 10000000 or life not in (0, 1, 2) or illusion not in (0, 1):
                    raise ValueError('invalid hero scalar')
                base_mr = npc('m_flMagicalResistanceValue', '<f')
                intellect = self.value(dot, ep, 'C_DOTA_BaseNPC_Hero', 'm_flIntellectTotal', '<f')
                # The inherited estimator returns zero on bad data; reject it BEFORE that fallback.
                if not -100 <= base_mr <= 100 or not 0 <= intellect <= 10000:
                    raise ValueError('invalid MR input')
                if not 0 <= base_mr / 100 + intellect * 0.001 <= 1:
                    raise ValueError('MR outside inherited estimator model')
                items, actions, busy = [], [], False
                activity_ok = True
                active_casts = []
                inv_base = ep + self.offset('C_DOTA_BaseNPC', 'm_Inventory') + self.offset('C_DOTA_UnitInventory', 'm_hItems')
                inventory = read_inventory(dot, inv_base, field_size=self.inventory_bytes)
                h['_ks_inventory'] = inventory
                if not inventory.known:
                    h['_ks_warnings'].append(inventory.reason)
                for slot, handle, item_ep, name in inventory.entries:
                    items.append(name)
                    if h.get('is_local') and slot < 6:
                        try:
                            if name in self.settings.by_name or name in self.defensive_names:
                                a, is_busy = self.ability(dot, name, item_ep, slot, game_time)
                                actions.append(a)
                            else:
                                is_busy = self.activity(dot, item_ep, game_time)
                            if is_busy:
                                busy = True
                                h['_ks_busy_reason'] = name
                                active_casts.append((name,item_ep))
                        except Exception as exc:
                            activity_ok = False
                            # An invalid ITEM does not destroy known HERO abilities.
                            h['_ks_warnings'].append(name + ': ' + str(exc))
                if h.get('is_local'):
                    abilities = dot.abilities_of({}, ep, h.get('designer'))
                    if not abilities:
                        activity_ok = False
                        h['_ks_warnings'].append('abilities unavailable')
                    for ab in abilities:
                        try:
                            if ab['name'] in self.settings.by_name or ab['name'] in self.defensive_names:
                                a, is_busy = self.ability(dot, ab['name'], ab['ent'], game_time=game_time)
                                actions.append(a)
                            else:
                                is_busy = self.activity(dot, ab['ent'], game_time)
                            if is_busy:
                                busy = True
                                h['_ks_busy_reason'] = ab['name']
                                active_casts.append((ab['name'],ab['ent']))
                        except Exception as exc:
                            h['_ks_warnings'].append(ab.get('name', '?') + ': ' + str(exc))
                            activity_ok = False
                            # Unknown HERO cast activity remains a block, not a fabricated zero.
                            busy = True
                            h['_ks_busy_reason'] = 'unreadable ability activity'
                h['_ks_active_casts'] = tuple(active_casts)
                h['_ks_busy_verified'] = bool(busy and active_casts and activity_ok)
                stacks, talents = (), ()
                rules = [p.stack_scaling for p in self.settings.profiles.values()
                         if p.stack_scaling is not None and p.hero == h.get('designer','').removeprefix('npc_dota_hero_')]
                if h.get('is_local') and rules:
                    try:
                        stacks = self.modifiers.stacks(dot, ep, {x['modifier'] for x in rules})
                        h['_ks_stacks'] = dict(stacks)
                    except Exception as exc:
                        h['_ks_warnings'].append('live stacks: ' + str(exc))
                    requested_talents = {x['talent'] for x in rules}
                    try:
                        try:
                            talents = talent_levels_from_snapshot(
                                dot, h.get('talents'), requested_talents, self.classes)
                        except Exception as snapshot_error:
                            # Keep the conservative schema-vector reader as a fallback for
                            # older snapshots and unusual layouts; never infer 0 or 1.
                            try:
                                talents = talent_levels(
                                    dot, ep, requested_talents, self.classes,
                                    ability_offset=getattr(dot, '_ab_off', None))
                            except Exception as fallback_error:
                                raise ValueError(str(snapshot_error) + '; fallback: ' + str(fallback_error))
                        h['_ks_talents'] = dict(talents)
                    except Exception as exc:
                        h['_ks_warnings'].append('live talents: ' + str(exc))
                mr = self.r.compute_mr_from_live_base(base_mr, intellect, items)
                visible = combat_visible(h)
                known_motion, speed, still = self.observe_motion(ep, h.get('pos'), started, visible)
                h['_ks'] = Unit(
                    idx, ep, h.get('designer', '?'), team, bool(h.get('is_local')),
                    life == 0 and hp > 0 and bool(h.get('alive')), hp, mana, mr, regen,
                    tuple(h.get('pos') or ()), tuple(h.get('screen') or ()), started,
                    visible,
                    bool(illusion or h.get('illusion')), state, tuple(items), tuple(actions), busy,
                    inventory_known=inventory.known,
                    uncertainty='' if inventory.known else inventory.reason, intellect=intellect,
                    motion_known=known_motion, speed=speed, stationary_for=still,
                    stack_values=stacks, talent_levels=talents)
            except Exception as exc:
                h['_ks_error'] = str(exc)
                if h.get('is_local') or not self.last_error:
                    self.last_error = str(exc)
            result.append(h)
        live_entities = {h.get('ent') for h in heroes}
        self.motion = {ep: record for ep, record in self.motion.items() if ep in live_entities}
        return result

    def observe_motion(self, entity, pos, now, visible):
        if not visible or not pos or len(pos) < 2 or not all(finite(v) for v in pos[:2]):
            self.motion.pop(entity, None)
            return False, 0., 0.
        previous = self.motion.get(entity)
        start = now
        known, speed = False, 0.
        if previous is not None:
            old_pos, old_time, old_start = previous
            dt = now - old_time
            if .001 <= dt <= self.settings.stale:
                speed = math.hypot(pos[0] - old_pos[0], pos[1] - old_pos[1]) / dt
                known = finite(speed) and speed <= 10000
                if known and speed <= self.settings.max_point_speed:
                    start = old_start
        self.motion[entity] = (tuple(pos[:2]), now, start)
        return known, speed, max(0, now - start)


def combat_visible(hero):
    if 'ks_combat_visible' in hero:
        return bool(hero.get('ks_visibility_known') and hero['ks_combat_visible'])
    # Legacy/unannotated snapshots stay conservative; never ignore an unknown hide reason.
    return not any(hero.get(k) for k in ('fog', 'hide', 'dormant'))
