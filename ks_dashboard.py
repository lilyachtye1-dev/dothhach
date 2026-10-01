"""Dashboard telemetry, supported-hero catalog and session-scoped switches.
This module does not arm automation, send input, edit offsets or rewrite profiles;
the catalog is read-only and is built exclusively from enabled developer profiles.
"""
import json
import math
import time
from ks_core import finite, at_level, raw_damage_details

NAMES = {'antimage':'Anti-Mage','lion':'Lion','zuus':'Zeus','queenofpain':'Queen of Pain','skywrath_mage':'Skywrath Mage',
         'vengefulspirit':'Vengeful Spirit','necrolyte':'Necrophos','magnataur':'Magnus',
         'lion_impale':'Earth Spike','lion_voodoo':'Hex','lion_mana_drain':'Mana Drain',
         'lion_finger_of_death':'Finger of Death','lion_to_hell_and_back':'To Hell and Back',
         'zuus_thundergods_wrath':"Thundergod’s Wrath",'zuus_lightning_bolt':'Lightning Bolt',
         'zuus_arc_lightning':'Arc Lightning','item_dagon':'Dagon'}

def label(name):
    return NAMES.get(name, name.removeprefix('npc_dota_hero_').removeprefix('item_').replace('_',' ').title())

def key_name(vk):
    if 112<=vk<=123:return 'F'+str(vk-111)
    if 48<=vk<=90:return chr(vk)
    return '—' if not vk else 'VK '+str(vk)

def safe_number(value, fallback=0):
    return float(value) if finite(value) else fallback


def calculation_unknown_status(reason):
    text = str(reason or '').casefold()
    if 'талант' in text:
        return 'talent_unknown'
    if 'стак' in text:
        return 'stacks_unknown'
    if 'int' in text:
        return 'stats_unknown'
    return 'unknown'


def supported_catalog(service):
    """Return only heroes with enabled, developer-maintained ability profiles."""
    settings = getattr(service, 'settings', None)
    if settings is None:
        return []
    groups = {}
    disabled = set(getattr(service, 'ui_disabled', set()) or ())
    for profile in settings.profiles.values():
        if profile.kind != 'ability':
            continue
        hero = str(profile.hero or '').removeprefix('npc_dota_hero_')
        if not hero or hero == '*':
            continue
        row = dict(
            id='catalog@' + profile.name, name=profile.name, title=label(profile.name),
            kind=profile.kind, profile=profile.name, supported=True,
            enabled=profile.name not in disabled, ready=False, catalog=True,
            status='catalog', reason='Поддерживается профилем, добавленным разработчиком',
            damage_note='', key=key_name(profile.key), level=len(profile.damage),
            cooldown=0., mana=0., damage=None, range='', cast=profile.cast,
            damage_type=profile.damage_type, slot=-1)
        groups.setdefault(hero, []).append((profile.priority, profile.name, row))
    result = []
    for hero, entries in sorted(groups.items(), key=lambda item: label(item[0]).casefold()):
        entries.sort(key=lambda item: (-item[0], item[1]))
        result.append(dict(
            id=hero, hero_id='npc_dota_hero_' + hero, hero=label(hero),
            abilities=[row for _, _, row in entries]))
    return result


def apply_control(service, doc):
    """Validate the entire menu request before committing. UI arming is deferred until the menu is closed and runtime safety checks pass."""
    if not isinstance(doc,dict) or doc.get('session')!=service.ui_session:
        return False
    if set(doc)-{'session','revision','disabled','menu_open','arm_after_close','dodge_arm','dodge_disabled','preserve_armed','menu_hwnd','menu_pid','dodge_alt_selfcast'} or type(doc.get('menu_open',False)) is not bool:
        return False
    if 'preserve_armed' in doc and type(doc['preserve_armed']) is not bool:return False
    if 'arm_after_close' in doc and type(doc['arm_after_close']) is not bool:
        return False
    if 'menu_hwnd' in doc or 'menu_pid' in doc:
        hwnd,pid=doc.get('menu_hwnd'),doc.get('menu_pid')
        if type(hwnd) is not int or not -(1<<63)<=hwnd<(1<<63) or hwnd==0 or type(pid) is not int or not 0<pid<(1<<31):return False
    revision=doc.get('revision'); disabled=doc.get('disabled')
    if type(revision) is not int or not service.ui_revision<revision<=2147483647:
        return False
    if not isinstance(disabled,list) or len(disabled)>len(service.settings.profiles):return False
    if not all(isinstance(n,str) and n in service.settings.profiles for n in disabled):return False
    dodge = getattr(service, 'dodger', None)
    if 'dodge_arm' in doc and type(doc['dodge_arm']) is not bool: return False
    dd = doc.get('dodge_disabled')
    if dd is not None:
        from dodger import BY_NAME
        if not isinstance(dd,list) or len(dd)>len(BY_NAME) or not all(isinstance(x,str) and x in BY_NAME for x in dd): return False
    if ('dodge_arm' in doc or 'dodge_disabled' in doc or 'dodge_alt_selfcast' in doc) and dodge is None:return False
    if 'dodge_alt_selfcast' in doc and type(doc['dodge_alt_selfcast']) is not bool:return False
    if 'dodge_alt_selfcast' in doc:dodge.alt_selfcast=doc['dodge_alt_selfcast']
    if 'menu_hwnd' in doc:
        service.ui_menu_hwnd=doc['menu_hwnd']&((1<<64)-1);service.ui_menu_pid=doc['menu_pid']
    if doc.get('preserve_armed',False):
        service.ui_disabled=set(disabled);service.ui_revision=revision
        service.ui_menu_open=doc.get('menu_open',False)
        if not service.ui_menu_open:
            service.menu_transition_until=time.perf_counter()+.5
        if dodge:
            if dd is not None:dodge.disabled=set(dd)
            if 'dodge_arm' in doc:dodge.control(doc['dodge_arm'],dd,service.ui_menu_open)
        if 'arm_after_close' in doc:
            service.toggle.enabled=False
            service.ui_arm_pending=doc['arm_after_close']
            service.ui_arm_requested_at=time.perf_counter() if service.ui_arm_pending else 0.
            service.off_reason='Ожидание включения' if service.ui_arm_pending else 'Выключено кнопкой Killsteal'
        return True
    if dodge:
        dodge.control(doc.get('dodge_arm'), dd, doc.get('menu_open',False))
    service.ui_disabled=set(disabled)
    service.ui_revision=revision
    service.ui_menu_open=doc.get('menu_open',False)
    arm_after_close=doc.get('arm_after_close')
    if arm_after_close is True:
        service.ui_arm_pending=True
        service.ui_arm_requested_at=time.perf_counter()
    elif arm_after_close is False or service.ui_menu_open:
        service.ui_arm_pending=False
        service.ui_arm_requested_at=0.0
    service.toggle.enabled=False
    service.off_reason='Настройки меню изменены. Вернись в игру; включение из меню будет ждать проверки фокуса и свежих данных.'
    return True


def source_row(service, me, action, name, slot, fresh_data, index, warnings=()):
    s=service.settings; p=s.by_name.get(name)
    row=dict(id=name+'@'+str(index),name=name,title=label(name),kind='item' if slot>=0 else 'ability',
             profile='',supported=False,enabled=False,ready=False,status='unsupported',
             reason='Нет включённой модели урона в ks_profiles.json',damage_note='',key='—',level=0,
             cooldown=0.,mana=0.,damage=None,range='',cast='',damage_type='',slot=slot)
    if p is None:
        row['reason'] = s.disabled_reasons.get(name, row['reason'])
        if name == 'zuus_cloud' and name not in s.disabled_reasons:
            row['reason'] = 'Zeus Cloud не поддерживается: периодический урон не рассчитан.'
        return row
    if p.hero not in ('*',me.name.removeprefix('npc_dota_hero_')):
        row['reason']='Профиль привязан к другому герою; биндинг не проверен';return row
    row.update(profile=p.name,supported=True,enabled=p.name not in service.ui_disabled,
               cast=p.cast,damage_type=p.damage_type,key=key_name(s.item_keys[slot]) if p.kind=='item' and 0<=slot<6 else key_name(p.key))
    if action is None:
        detail=next((str(w) for w in warnings if isinstance(w,str) and w.startswith(name+':')), '')
        reason='Не удалось прочитать состояние источника'
        if detail:
            reason+=' · '+detail.split(':',1)[1].strip()
        row.update(status='unknown',reason=reason);return row
    _,level=s.match(action)
    row.update(level=level,cooldown=safe_number(action.cooldown),mana=safe_number(action.mana))
    if type(level) is int and 1<=level<=len(p.damage):
        row['range']='Глобальная' if p.cast=='global' else '%g'%at_level(p.ranges,level)
        try:
            row['damage'],row['damage_note']=raw_damage_details(p,me,level)
        except ValueError as e:
            row['reason']=str(e)
    else:
        row.update(status='unlearned',reason='Способность не изучена / уровень не поддержан');return row
    status='ready';reason='Ресурсы готовы; цель, дальность, защита и ввод проверяются отдельно'
    if not row['enabled']:status,reason='disabled','Выключено в меню на эту сессию'
    elif not fresh_data:status,reason='stale','Нет свежего состояния героя'
    elif not me.alive:status,reason='blocked','Локальный герой мёртв'
    elif me.illusion or me.team not in (2,3) or me.state&s.local_mask:status,reason='blocked','Состояние локального героя запрещает применение'
    elif action.identity in service.ledger.quarantined:status,reason='blocked','Нет подтверждения CD после предыдущего ввода'
    elif service.ledger.pending is not None and service.ledger.pending.action.identity==action.identity:
        status,reason='pending','Ожидается подтверждение CD'
    elif not action.activated or action.hidden:status,reason='blocked','Источник отключён или скрыт игрой'
    elif me.busy or action.casting:status,reason='blocked','Герой занят / выполняется способность'
    elif not finite(action.cooldown) or action.cooldown<0:status,reason='unknown','CD не прочитан'
    elif action.cooldown!=0:status,reason='cooldown','Перезарядка'
    elif not finite(action.mana) or action.mana<0:status,reason='unknown','Цена маны не прочитана'
    elif action.mana+s.reserve>me.mana:status,reason='mana','Недостаточно маны с учётом резерва'
    elif p.requires_charges and (type(action.charges) is not int or action.charges<=0):status,reason='blocked','Нет подтверждённых зарядов'
    elif p.kind=='item' and (not me.inventory_known or not 0<=slot<6):status,reason='blocked','Неизвестен активный слот предмета'
    elif row['damage'] is None:
        status,reason=calculation_unknown_status(row['reason']),row['reason']
    elif s.auto_cast and not (s.item_keys[slot] if p.kind=='item' and 0<=slot<6 else p.key):status,reason='blocked','Клавиша не задана'
    ready=status=='ready'
    if ready and row['damage_note']:
        status,reason='talent_unknown',row['damage_note']
        ready=True  # the floor is safe for kill-steal selection; the bonus is not assumed
    row.update(status=status,reason=reason,ready=ready)
    return row


def dashboard(service, heroes, reader_age, now=None):
    now=time.perf_counter() if now is None else now
    s=service.settings
    result=dict(version=1,generation=int(now*1000000),session=service.ui_session,revision=service.ui_revision,disabled=sorted(service.ui_disabled),
        hero='',hero_id='',level=0,team='',hp=0.,maxhp=0.,mana=0.,maxmana=0.,alive=False,
        fresh=False,age=safe_number(reader_age,99999.),armed=service.toggle.enabled,arm_pending=bool(getattr(service,'ui_arm_pending',False)),
        mode='AUTO' if s and s.auto_cast else 'PREVIEW',status=service.status,detail=service.detail,
        stacks='—',talent='—',stacks_reason='',talent_reason='',inventory_known=False,sources=[],items=[],
        catalog=supported_catalog(service),warnings=[],
        toggle=key_name(s.toggle_vk) if s else '—',margin=s.margin if s else 0,
        interval=s.interval if s else 0,stale_limit=s.stale if s else .25,
        profiles=len(s.profiles) if s else 0)
    if getattr(service, 'dodger', None): result['dodger']=service.dodger.dashboard(heroes)
    if getattr(service, 'last_hit', None): result['last_hit']=service.last_hit.dashboard()
    local=[h for h in heroes if h.get('is_local') and h.get('_ks')]
    if s is None or len(local)!=1:
        result['warnings']=['Нет единственного прочитанного локального героя'] + [str(h['_ks_error'])[:200] for h in heroes if h.get('is_local') and h.get('_ks_error')][:4]
        return result
    h=local[0];me=h['_ks']
    age=max(safe_number(reader_age,99999.),max(0.,now-me.at))
    live=finite(reader_age) and 0<=reader_age<=s.stale and 0<=now-me.at<=s.stale
    result.update(hero=label(me.name.removeprefix('npc_dota_hero_')),hero_id=me.name,level=h.get('level') or 0,
        team='Radiant' if me.team==2 else 'Dire' if me.team==3 else 'Unknown',hp=me.hp,maxhp=h.get('maxhp') or 0,
        mana=me.mana,maxmana=h.get('maxmana') or 0,alive=me.alive,fresh=live,age=age,
        inventory_known=me.inventory_known,warnings=[str(x)[:200] for x in h.get('_ks_warnings',[])][:8])
    result['stacks_reason']=next((str(x) for x in h.get('_ks_warnings',[]) if str(x).startswith('live stacks:')),'')
    result['talent_reason']=next((str(x) for x in h.get('_ks_warnings',[]) if str(x).startswith('live talents:')),'')
    for p in s.profiles.values():
        if p.stack_scaling and p.hero==me.name.removeprefix('npc_dota_hero_'):
            r=p.stack_scaling;n=dict(me.stack_values).get(r['modifier']);t=dict(me.talent_levels).get(r['talent'])
            result['stacks']='?' if n is None else str(n)
            result['talent']='неизвестно' if t is None else 'изучен · +%g'%r['talent_bonus'] if t==1 else 'не изучен'
    abilities={a.name:(a.entity,a) for a in me.actions if a.slot<0}
    short=me.name.removeprefix('npc_dota_hero_')
    for a in h.get('abilities') or []:
        name=a.get('name','')
        if name in s.by_name or name.startswith(short+'_'):
            abilities.setdefault(name,(a.get('ent',0),None))
    for name,(ep,a) in list(abilities.items())[:32]:
        result['sources'].append(source_row(service,me,a,name,-1,live,ep,h.get('_ks_warnings',[])))
    inventory=h.get('_ks_inventory')
    if inventory is not None and inventory.known:
        actions={a.entity:a for a in me.actions if a.slot>=0}
        filled=set()
        for slot,handle,ep,name in inventory.entries:
            if slot not in (0,1,2,3,4,5,16):continue
            filled.add(slot)
            row=source_row(service,me,actions.get(ep),name,slot,live,ep,h.get('_ks_warnings',[]))
            row['key']=key_name(s.item_keys[slot]) if slot<6 else 'NTR'
            result['items'].append(row)
        for slot in range(6):
            if slot not in filled:
                result['items'].append(dict(id='empty:'+str(slot),name='',title='Пустой слот',kind='item',profile='',
                    supported=False,enabled=False,ready=False,status='empty',reason='В активном слоте нет предмета',
                    key=key_name(s.item_keys[slot]),level=0,cooldown=0.,mana=0.,damage=None,range='',cast='',damage_type='',slot=slot))
        result['items'].sort(key=lambda x:x['slot'])
    return result


def wire_dashboard(service, heroes, reader_age):
    return 'UI1\t'+json.dumps(dashboard(service,heroes,reader_age),ensure_ascii=False,separators=(',',':'),allow_nan=False)
