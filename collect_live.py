"""Read-only, bounded diagnostic collector for missing dynamic combat data.
No SendInput, no injection, no memory writes, no use of foreign-build vtable RVAs.
RTTI matches are discovery candidates, NOT proof of modifier-list membership.
Results never feed the live kill-steal decision path automatically.
"""
from pathlib import Path
import json
import math
import re
import struct
import time
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parent
COUNTER = 'CDOTA_Modifier_Lion_FingerOfDeath_Kill_Counter'


class ProbeLimit(RuntimeError):
    pass


def pointer(p):
    return type(p) is int and 0x10000 <= p < (1 << 48) and p % 8 == 0


class Probe:
    def __init__(self, dot, classes, seconds=4., max_reads=3500):
        self.dot, self.classes = dot, classes
        self.deadline = time.perf_counter() + seconds
        self.max_reads, self.reads = max_reads, 0
        self.base = int(dot.client)
        self.image = self.image_info()
        self.type_cache = {}
        self.type_observations = {}
        self.base_buff_size = int(classes['CDOTA_Modifier_Lion_FingerOfDeath']['fields']['m_bIgnoreBonus'])

    def read(self, address, count):
        if self.reads >= self.max_reads or time.perf_counter() > self.deadline:
            raise ProbeLimit('read/time budget reached')
        self.reads += 1
        if not 0 < count <= 0x10000 or not 0x10000 <= address < (1 << 48) - count:
            return None
        data = self.dot.rb(address, count)
        return data if data is not None and len(data) == count else None

    def image_info(self):
        dos = self.read(self.base, 64)
        if not dos or dos[:2] != b'MZ':
            raise ValueError('client.dll PE header not readable')
        off = struct.unpack_from('<I', dos, 0x3C)[0]
        if not 0x40 <= off <= 0x100000:
            raise ValueError('invalid PE offset')
        pe = self.read(self.base + off, 0x100)
        if not pe or pe[:4] != b'PE\0\0' or struct.unpack_from('<H', pe, 24)[0] != 0x20B:
            raise ValueError('not a readable PE32+ client module')
        size = struct.unpack_from('<I', pe, 24 + 56)[0]
        if not 0x100000 <= size <= 0x40000000:
            raise ValueError('invalid PE SizeOfImage')
        return {'base': hex(self.base), 'size': size,
                'timestamp': struct.unpack_from('<I', pe, 8)[0]}

    def in_image(self, p, size=1):
        return self.base <= p and p + size <= self.base + self.image['size']

    def runtime_type(self, obj):
        if not pointer(obj):
            return None
        raw = self.read(obj, 8)
        if not raw:
            return None
        vtable = struct.unpack('<Q', raw)[0]
        if not self.in_image(vtable - 8, 16):
            return None
        if vtable in self.type_cache:
            return self.type_cache[vtable]
        self.type_cache[vtable] = None
        raw = self.read(vtable - 8, 8)
        if not raw:
            return None
        locator = struct.unpack('<Q', raw)[0]
        if not self.in_image(locator, 24):
            return None
        raw = self.read(locator, 24)
        if not raw:
            return None
        signature, offset, cd_offset, type_rva, hierarchy_rva, self_rva = struct.unpack('<6I', raw)
        # Only primary subobjects. Do not guess an enclosing object from secondary vptrs.
        if signature != 1 or offset != 0 or cd_offset != 0 or locator - self_rva != self.base:
            return None
        desc = self.base + type_rva
        if not self.in_image(desc, 16 + 192) or not self.in_image(self.base + hierarchy_rva, 16):
            return None
        raw = self.read(desc + 16, 192)
        if not raw:
            return None
        name = raw.split(b'\0', 1)[0].decode('ascii', 'replace')
        if len(self.type_observations) < 128:
            self.type_observations[hex(vtable - self.base)] = name
        match = re.fullmatch(r'\.\?A[VU]([A-Za-z0-9_]+)@@', name)
        if match is None or match[1] not in self.classes:
            return None
        self.type_cache[vtable] = match[1]
        return match[1]

    def field_samples(self, obj, cls):
        result = {}
        for name, off in self.classes[cls]['fields'].items():
            if not any(k in name.lower() for k in ('stack', 'amp', 'range', 'damage', 'bonus', 'level', 'cooldown', 'toggle', 'cast', 'charges')):
                continue
            raw = self.read(obj + int(off), 4)
            if raw:
                floating = struct.unpack('<f', raw)[0]
                result[name] = {'offset': hex(off), 'raw': raw.hex(' '),
                                'i32': struct.unpack('<i', raw)[0],
                                'f32': floating if math.isfinite(floating) else None}
        return result

    def modifiers(self, hero):
        manager_off = self.classes['C_DOTA_BaseNPC']['fields']['m_ModifierManager']
        inventory_off = self.classes['C_DOTA_BaseNPC']['fields']['m_Inventory']
        span = inventory_off - manager_off
        if not 64 <= span <= 4096:
            raise ValueError('unexpected manager extent')
        manager = hero + manager_off
        raw = self.read(manager, span)
        result = {'manager': hex(manager), 'span': span,
                  'manager_hex': raw.hex() if raw else None,
                  'candidates': [], 'neighbourhoods': [], 'truncated': False,
                  'warning': 'Bounded pointer-neighbourhood scan, NOT a validated modifier-list decoder.'}
        if raw is None:
            return result
        seen, containers = set(), []
        found = set()

        def inspect(obj, path):
            if not pointer(obj) or self.in_image(obj) or obj in seen:
                return False
            seen.add(obj)
            cls = self.runtime_type(obj)
            if not cls or not cls.startswith('CDOTA_Modifier_'):
                return False
            if obj in found:
                return True
            found.add(obj)
            fields = self.classes[cls]['fields']
            size = min(0x4000, max(self.base_buff_size, max(fields.values(), default=0) + 8))
            buf = self.read(obj, size)
            result['candidates'].append({'address': hex(obj), 'class': cls, 'path': path,
                'bytes_requested': size, 'raw_hex': buf.hex() if buf else None,
                'fields': self.field_samples(obj, cls)})
            return True

        try:
            for off in range(0, len(raw) - 7, 8):
                value = struct.unpack_from('<Q', raw, off)[0]
                path = 'manager+0x%X' % off
                if pointer(value) and not self.in_image(value):
                    if not inspect(value, path):
                        containers.append((value, path))
            # At most two levels of bounded heap neighbourhoods; no whole-process scan.
            for depth in range(2):
                next_level = []
                for address, path in containers[:48]:
                    buf = self.read(address, 0x400)
                    if not buf:
                        continue
                    if len(result['neighbourhoods']) < 32:
                        result['neighbourhoods'].append({'address': hex(address), 'path': path,
                                                       'depth': depth, 'raw_hex': buf.hex()})
                    for off in range(0, len(buf) - 7, 8):
                        value = struct.unpack_from('<Q', buf, off)[0]
                        if not pointer(value) or self.in_image(value) or value in seen:
                            continue
                        subpath = path + ' -> +0x%X' % off
                        if not inspect(value, subpath) and depth == 0:
                            next_level.append((value, subpath))
                        if len(result['candidates']) >= 64:
                            raise ProbeLimit('modifier candidate cap reached')
                containers = next_level
        except ProbeLimit as exc:
            result['truncated'] = True
            result['limit_reason'] = str(exc)
        result['reads'] = self.reads
        result['rtti_types_by_live_vtable_rva'] = self.type_observations
        return result


def stack_candidates(snapshots):
    usable = []
    for snap in snapshots:
        expected = snap.get('expected_stacks')
        if type(expected) is not int:
            continue
        entries = [m for m in snap.get('modifiers', {}).get('candidates', [])
                   if m.get('class') == COUNTER and m.get('raw_hex')]
        if len(entries) != 1:
            continue
        usable.append((snap, expected, bytes.fromhex(entries[0]['raw_hex'])))
    if len(usable) < 3 or len({n for _, n, _ in usable}) < 3:
        return {'status': 'insufficient', 'reason': 'Need three distinct observed stack counts and exactly one RTTI counter candidate per snapshot.'}
    identities = {(s['hero']['entity'], s['client']['timestamp'], s['client']['size']) for s, _, _ in usable}
    if len(identities) != 1:
        return {'status': 'insufficient', 'reason': 'Hero or client module changed between snapshots.'}
    limit = min(len(raw) for _, _, raw in usable)
    offsets = [off for off in range(8, limit - 3, 4)
               if all(struct.unpack_from('<i', raw, off)[0] == expected for _, expected, raw in usable)]
    return {'status': 'candidates_only', 'offsets_i32': [hex(o) for o in offsets],
            'warning': 'Correlation is not semantic validation. No binding is installed or used for casts.'}


def capture(dot, classes, stage, count):
    snap = dot.snapshot()
    heroes = [h for h in (snap or {}).get('heroes', []) if h.get('is_local') and h.get('ent') and not h.get('illusion')]
    if len(heroes) != 1:
        raise ValueError('Нужен ровно один локальный герой; найдено %d' % len(heroes))
    h = heroes[0]
    from ks_inventory import read_inventory
    from ks_reader import CombatReader
    import dota_esp
    reader = CombatReader(dota_esp, ROOT / 'client_dll.json')
    inv_base = h['ent'] + classes['C_DOTA_BaseNPC']['fields']['m_Inventory'] + classes['C_DOTA_UnitInventory']['fields']['m_hItems']
    inv = read_inventory(dot, inv_base, reader.inventory_bytes)
    probe = Probe(dot, classes, seconds=8., max_reads=5000)
    result = {'stage': stage, 'expected_stacks': count, 'time': time.time(), 'client': probe.image,
              'hero': {'entity': hex(h['ent']), 'idx': h['idx'], 'name': h.get('designer'), 'level': h.get('level')},
              'inventory': {'known': inv.known, 'layout': inv.layout, 'reason': inv.reason, 'entries': inv.entries},
              'abilities': [], 'notes': []}
    npc = classes['C_DOTA_BaseNPC']['fields']
    raw = probe.read(h['ent'] + npc['m_flRangeDisplayDist'], 4)
    result['display_radius_not_cast_range'] = raw.hex(' ') if raw else None
    objects = [(a['ent'], a['name']) for a in dot.abilities_of({}, h['ent'], h.get('designer'))]
    objects += [(entry[2], entry[3]) for entry in inv.entries]
    try:
        for ep, name in objects:
            cls = probe.runtime_type(ep)
            raw = probe.read(ep, 0x780)
            result['abilities'].append({'name': name, 'entity': hex(ep), 'rtti_class': cls,
                'base_fields': probe.field_samples(ep, 'C_DOTABaseAbility'),
                'class_fields': probe.field_samples(ep, cls) if cls else {},
                'raw_hex': raw.hex() if raw else None})
    except ProbeLimit as exc:
        result['notes'].append(str(exc))
    # Independent budget for the modifier neighbourhood.
    mods = Probe(dot, classes, seconds=8., max_reads=5000)
    result['modifiers'] = mods.modifiers(h['ent'])
    return result


def save(snapshots):
    doc = {'format': 'KS-live-probe-1', 'schema_sha256': hashlib.sha256((ROOT/'client_dll.json').read_bytes()).hexdigest(),
           'read_only': True, 'snapshots': snapshots, 'stack_analysis': stack_candidates(snapshots)}
    path = ROOT / 'live_probe.zip'
    temporary = path.with_suffix('.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('live_probe.json', json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False))
    temporary.replace(path)
    return path


def main():
    from dota_esp import Dot
    # This program never imports ks_input or sends any keys/clicks.
    dot = Dot()
    classes = json.loads((ROOT/'client_dll.json').read_text(encoding='utf-8'))['client.dll']['classes']
    print('Только сбор данных локального героя. Перед сбором выключи Killsteal кнопкой в программе.')
    print('Используй одного и того же Lion; не меняй уровень и предметы в первых трёх снимках.')
    print('Вводи ФАКТИЧЕСКОЕ число стаков Finger с иконки; s — пропустить, q — закончить.')
    stages = [('baseline', 'Сначала получи хотя бы 1 стак Finger, чтобы счётчик уже существовал. Исходный снимок.'),
              ('after_kill_1', 'Вернись в игру, получи ещё один стак Finger. Затем вернись в эту консоль.'),
              ('after_kill_2', 'Получи ещё один стак Finger, не меняя уровень/предметы.'),
              ('amp_item', 'Необязательно: надень Kaya или предмет, усиление которого проверяешь. Стаки не меняй.'),
              ('range_item', 'Необязательно: надень Aether Lens / свой предмет дальности, без изменения стаков.')]
    snapshots = []
    try:
        for stage, text in stages:
            print('\n' + text)
            while True:
                answer = input('Когда готов: число стаков + Enter (либо s/q): ').strip().lower()
                if answer in ('s', 'q') or (answer.isdigit() and 0 <= int(answer) <= 10000):
                    break
                print('Нужно целое число стаков или s/q.')
            if answer == 'q':
                break
            if answer == 's':
                continue
            try:
                print('Читаю данные…')
                snap = capture(dot, classes, stage, int(answer))
                snapshots.append(snap)
                path = save(snapshots)
                print('Сохранено. Кандидатов модификаторов:', len(snap['modifiers']['candidates']))
                print('Архив:', path)
            except Exception as exc:
                print('Ошибка этого снимка:', str(exc))
    except KeyboardInterrupt:
        print('\nОстановлено.')
    finally:
        if snapshots:
            path = save(snapshots)
            print('Пришли этот ОДИН файл:', path)
            print('Никакие найденные адреса не установлены в автокаст автоматически.')
        else:
            print('Снимков нет. Архив не создан; если старый live_probe.zip существует, он НЕ обновлён.')


if __name__ == '__main__':
    main()
