"""Input authorization for the shared sender.
Alpha6 uses defensive_command; the alpha5 opt-in helpers remain for compatibility.
No second sending thread. The physical modifier observer never sends input.
"""
COUNTERSPELL = 'antimage_counterspell'
CONTROL_KEYS = ((13,'Enter'),(9,'Tab'),(16,'Shift'),(17,'Ctrl'),(18,'Alt'),
                (91,'Win-L'),(92,'Win-R'),(35,'End'),(45,'Insert'))


def keyboard_reason(backend, bindings=()):
    reasons = [name for key,name in CONTROL_KEYS if backend.down(key)]
    for key in sorted(set(bindings)-{0}):
        if backend.down(key):
            label = 'F'+str(key-111) if 112<=key<=123 else chr(key) if 48<=key<=90 else 'VK '+str(key)
            reasons.append(label)
    return ', '.join(reasons)


def mouse_independent_command(decision, settings):
    a = getattr(decision,'action',None)
    me = getattr(decision,'target',None)
    return bool(getattr(settings,'counterspell_mouse_passthrough',False) is True and
                a is not None and a.name==COUNTERSPELL and a.slot<0 and decision.key==ord('E') and
                not decision.profile.needs_cursor and me is not None and me.local and me.alive and
                me.name=='npc_dota_hero_antimage' and
                any(x.identity==a.identity for x in me.actions))


def defensive_command(decision, settings):
    """Alpha6 explicit opt-in; validate source/key/cursor shape, not arbitrary macros."""
    if getattr(settings,'defensive_input',False) is not True:return False
    from dodger import BY_NAME
    from ks_core import keycode
    a=getattr(decision,'action',None)
    caster=getattr(decision,'caster',getattr(decision,'target',None))
    p=BY_NAME.get(a.name) if a else None
    alt=bool(p and p.cast=='self' and getattr(settings,'defense_alt_selfcast',False))
    if not (p and not p.blocked and caster and caster.local and caster.alive and
            p.hero in ('*',caster.name.removeprefix('npc_dota_hero_')) and
            any(x==a for x in caster.actions) and decision.profile.needs_cursor==(p.needs_cursor and not alt) and
            bool(getattr(decision.profile,'self_cast',False))==alt and
            (p.cast not in ('no_target','self','point_self') or decision.target.entity==caster.entity)):return False
    keys=getattr(settings,'defense_item_keys',())
    key=keys[a.slot] if p.key=='inventory' and caster.inventory_known and 0<=a.slot<len(keys) else keycode(p.key) if p.key!='inventory' else 0
    if alt and not (48<=key<=57 or 65<=key<=90):return False
    return bool(key and key==decision.key)
