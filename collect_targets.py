"""Bounded read-only WR/CK comparison recorder. NOT an automatic target decoder.
Only schema-backed objects and ONE bounded hop from named opaque containers.
No global memory search, input hook, injected calls, process writes or sender.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent
SPELLS = {'wr': ('windrunner_shackleshot', 'C_DOTA_Ability_Windrunner_Shackleshot'),
          'ck': ('chaos_knight_chaos_bolt', 'C_DOTA_Ability_ChaosKnight_Chaos_Bolt')}
MAX_STAGE_BYTES = 12 * 1024 * 1024
MAX_BYTES = 6 * MAX_STAGE_BYTES
MAX_BLOB_CHARS = 4 * 1024 * 1024
MAX_FRAME_BYTES = 96 * 1024
SCHEMA_SHA = '7dd78049388a98fa332c175cfa3bf648691da99ca070fc8e8f3b04fecf3e53d9'
BUILD = (1790374934, 115027968)


def check_build(schema, image):
    if hashlib.sha256(schema).hexdigest() != SCHEMA_SHA:
        raise ValueError('Схема не соответствует проверенной базе; сбор остановлен')
    if (image['timestamp'], image['size']) != BUILD:
        raise ValueError('Другая сборка client.dll; сбор остановлен, offsets не подставляются автоматически')


def offset(classes, cls, field):
    n = classes[cls]['fields'][field]
    if type(n) is not int or not 0 < n < 0x10000:
        raise ValueError('invalid schema offset: ' + field)
    return n


def raw_field(probe, ep, cls, field, size):
    off = offset(probe.classes, cls, field)
    raw = probe.read(ep + off, size)
    return {'offset': off, 'hex': raw.hex() if raw is not None else None}


def identity(dot, probe, ep, name):
    """Read identity backlink and FULL serial-bearing handle, not masked index only."""
    off = offset(probe.classes, 'CEntityInstance', 'm_pEntity')
    p = probe.read(ep + off, 8)
    if not p:
        return None
    ident = struct.unpack('<Q', p)[0]
    data = probe.read(ident, 20)
    if not data or struct.unpack_from('<Q', data)[0] != ep:
        return None
    handle = struct.unpack_from('<I', data, 16)[0]
    if handle in (0, 0xffffffff) or dot.get_entity(handle & 0x7fff) != (ep, name):
        return None
    return handle


def handle_matches(data, known):
    """Exact integer matches are discovery candidates, NEVER proof of a target."""
    if data is None:
        return []
    return [{'byte': i, 'handle': hex(n), 'entity': known[n]}
            for i in range(0, len(data)-3, 4)
            for n in (struct.unpack_from('<I', data, i)[0],) if n in known][:24]


def opaque_container(probe, ep, cls, field, size, known, blocked=()):
    from collect_live import pointer
    result = raw_field(probe, ep, cls, field, size)
    data = bytes.fromhex(result['hex']) if result['hex'] is not None else None
    result['handle_matches_unverified'] = handle_matches(data, known)
    result['one_hop_candidates'] = []
    visited = set(blocked) | {ep}
    # Deliberately no recursive chase or guessed count/vector interpretation.
    for i in range(0, len(data or b'')-7, 8):
        p = struct.unpack_from('<Q', data, i)[0]
        if not pointer(p) or p in visited or probe.in_image(p):
            continue
        visited.add(p)
        block = probe.read(p, 384)
        result['one_hop_candidates'].append({'header_byte': i, 'address': hex(p),
            'hex': block.hex() if block is not None else None,
            'handle_matches_unverified': handle_matches(block, known)})
        if len(result['one_hop_candidates']) == 2:
            break
    return result


def scalar(probe, ep, cls, field, fmt):
    data = probe.read(ep + offset(probe.classes, cls, field), struct.calcsize(fmt))
    if data is None:
        return None
    n = struct.unpack(fmt, data)[0]
    return n if not isinstance(n, float) or math.isfinite(n) else None


class Recorder:
    def __init__(self, dot, probe):
        self.dot, self.probe = dot, probe
        self.entries = {}; self.discovery_at = -999.; self.cursor = 0
        self.particles = {}; self.ability_cache = {}; self.discovery_seconds = 0.
        self.particle_attempts = {}; self.particle_tick = 0

    def particle_batch(self, limit=16):
        """Unseen objects first, then least recently attempted; never fixed [:16]."""
        live = {(idx, *obj) for idx, obj in self.particles.items()}
        self.particle_attempts = {k: v for k, v in self.particle_attempts.items() if k in live}
        return sorted(self.particles.items(),
                      key=lambda item: (self.particle_attempts.get((item[0], *item[1]), -1), item[0]))[:limit]

    def particle_attempted(self, idx, obj):
        self.particle_tick += 1
        self.particle_attempts[(idx, *obj)] = self.particle_tick

    def frame(self, now, spell):
        from collect_events import PARTICLES, raw_fields, finite_position
        dot, p = self.dot, self.probe
        frame = {'at': now, 'complete': False, 'use_for_input': False,
                 'heroes': [], 'controllers': [], 'abilities': [], 'particle_entities': []}
        p.deadline = time.perf_counter() + .08; p.reads = 0
        try:
            if now-self.discovery_at >= .5:
                t = time.perf_counter()
                # A standalone diagnostic scan; not installed into the live sender.
                found = dot.entities(full_scan=True)
                if found is None:
                    raise ValueError('entity system unavailable')
                self.entries = dict(found); self.discovery_at = time.perf_counter()
                self.discovery_seconds = time.perf_counter()-t
                self.ability_cache.clear()
                # Dot discovery has its own implementation: measured separately, not
                # falsely included in Probe's read/time budget.
                p.deadline = time.perf_counter()+.08
            frame['field_sample_at'] = time.perf_counter()
            frame['discovery_seconds'] = self.discovery_seconds
            frame['entity_count'] = len(self.entries)
            known = {}; objects = []
            for idx, (ep, name) in sorted(self.entries.items()):
                if is_playable_hero(name) or 'player_controller' in (name or ''):
                    if len(objects) >= 40:
                        frame['object_limit'] = True; break
                    h = identity(dot, p, ep, name)
                    if h is not None:
                        row = {'index': idx, 'address': hex(ep), 'name': name, 'handle': hex(h)}
                        known[h] = dict(row); objects.append((ep, name, h, row))
            blocked = {x[0] for x in objects}
            wanted_hero='npc_dota_hero_'+('windrunner' if spell=='wr' else 'chaos_knight')
            objects.sort(key=lambda x: (x[1]!=wanted_hero,x[1]!='npc_dota_hero_antimage','player_controller' not in x[1],x[0]))
            for ep, name, h, row in objects:
                if name.startswith('npc_dota_hero_'):
                    row['position_may_be_last_seen'] = finite_position(dot.pos(ep))
                    row['dormant'] = dot.dormant_state(ep)
                    row['particles_opaque'] = opaque_container(p, ep, 'C_BaseEntity', 'm_Particles', 48, known, blocked)
                    row['team'] = scalar(p, ep, 'C_BaseEntity', 'm_iTeamNum', '<B')
                    frame['heroes'].append(row)
                    if ep not in self.ability_cache:
                        self.ability_cache[ep] = dot.abilities_of(self.entries, ep, name) if name==wanted_hero else []
                    for ab in self.ability_cache[ep][:32]:
                        if ab['name'] != SPELLS[spell][0]:
                            continue
                        ae = ab['ent']; ah = identity(dot, p, ae, ab['name'])
                        if ah is None:
                            continue
                        ar = {'read_at': time.perf_counter(), 'name': ab['name'], 'address': hex(ae), 'handle': hex(ah), 'caster': hex(h)}
                        fields = {'m_bInAbilityPhase': '<B', 'm_fCooldown': '<f', 'm_flCastStartTime': '<f',
                                  'm_flChannelStartTime': '<f', 'm_flOverrideCastPoint': '<f', 'm_flLastCastClickTime': '<f'}
                        ar['fields'] = {f: scalar(p, ae, 'C_DOTABaseAbility', f, fmt) for f, fmt in fields.items()}
                        # Bounded base-ability tail up to its last known scalar, no unknown object-size sweep.
                        lo = offset(p.classes, 'C_DOTABaseAbility', 'm_bActivated')
                        hi = offset(p.classes, 'C_DOTABaseAbility', 'm_flLastCastClickTime')+4
                        if not 0 < hi-lo <= 256:
                            raise ValueError('unexpected ability field span')
                        raw = p.read(ae+lo, hi-lo)
                        ar['base_tail_opaque'] = {'offset': lo, 'hex': raw.hex() if raw else None}
                        cls = SPELLS[spell][1]
                        ar['specific'] = (raw_field(p, ae, cls, 'm_hTarget', 4) if spell == 'wr' else
                                          opaque_container(p, ae, cls, 'hAlreadyHitList', 24, known, blocked))
                        if spell=='wr':
                            ar['arrow_fields_unverified'] = {f: raw_field(p,ae,cls,f,12) for f in (
                                'm_vArrowAvgPos','m_vArrowStartPos','m_vArrowStartPos2','m_vArrowStartPos3')}
                            ar['shackle_count'] = scalar(p,ae,cls,'shackle_count','<i')
                        ar['identity_after'] = identity(dot, p, ae, ab['name']) == ah and identity(dot, p, ep, name) == h
                        ar['phase_after'] = scalar(p, ae, 'C_DOTABaseAbility', 'm_bInAbilityPhase', '<B')
                        ar['start_after'] = scalar(p, ae, 'C_DOTABaseAbility', 'm_flCastStartTime', '<f')
                        ar['read_end'] = time.perf_counter()
                        frame['abilities'].append(ar)
                else:
                    cls = 'C_DOTAPlayerController'
                    row['assigned_hero_handle'] = scalar(p, ep, cls, 'm_hAssignedHero', '<I')
                    row['player_slot'] = scalar(p, ep, cls, 'm_nPlayerID', '<i')
                    row['orders_opaque'] = opaque_container(p, ep, cls, 'm_unitorders', 24, known, blocked)
                    row['selected_opaque'] = raw_field(p, ep, cls, 'm_nSelectedUnits', 24)
                    row['sequence'] = {f: scalar(p, ep, cls, f, '<I') for f in (
                        'm_nNextOutgoingOrderSequenceNumber', 'm_nServerOrderSequenceNumber', 'm_nMaxSentOutgoingOrderSequenceNumber')}
                    row['send_time'] = scalar(p, ep, cls, 'm_flLastUnitOrdersSendTime', '<f')
                    frame['controllers'].append(row)
                row['identity_after'] = identity(dot, p, ep, name) == h
            entries = sorted(self.entries.items())
            # Rotating RTTI scan discovers entities, including unnamed particle objects.
            subset = (entries[self.cursor:]+entries[:self.cursor])[:96]
            self.cursor = (self.cursor+len(subset)) % max(1,len(entries))
            frame['rtti_sampled'] = len(subset)
            frame['rtti_scan_complete'] = len(subset)==len(entries)
            for idx, (ep, name) in subset:
                cls = p.runtime_type(ep)
                if cls in PARTICLES:
                    self.particles[idx] = (ep, name, cls)
            self.particles = {idx: x for idx, x in self.particles.items() if self.entries.get(idx) == x[:2]}
            batch = self.particle_batch()
            frame['particle_coverage'] = {'discovered_live': len(self.particles),
                'batch_size': len(batch), 'attempted': 0, 'policy': 'unseen_then_least_recent_attempt'}
            for idx, (ep, name, cls) in batch:
                self.particle_attempted(idx, (ep, name, cls))
                frame['particle_coverage']['attempted'] += 1
                h = identity(dot, p, ep, name)
                if h is None:continue
                pf=raw_fields(p,ep,cls)
                for value in pf.values():
                    value['handle_matches_unverified']=handle_matches(bytes.fromhex(value['bytes']) if value['bytes'] is not None else None,known)
                frame['particle_entities'].append({'index': idx, 'address': hex(ep), 'class': cls,
                    'handle': hex(h), 'name': name, 'fields': pf,
                    'identity_after': identity(dot, p, ep, name) == h})
            frame['complete'] = True
        except Exception as exc:
            frame['error'] = type(exc).__name__+': '+str(exc)[:180]
        frame['probe_reads'] = p.reads
        return frame


def is_playable_hero(name):
    return (name or '').startswith('npc_dota_hero_') and not name.startswith('npc_dota_hero_announcer')


class RawBlobEncoder:
    """Lossless bounded interning of repeated raw hex only. Commit after write."""
    def __init__(self):
        self.known = {}; self.chars = 0

    def prepare(self, frame):
        pending = {}; chars = self.chars
        def walk(value, key=None):
            nonlocal chars
            if isinstance(value, str) and key in ('hex', 'bytes') and len(value) >= 64:
                if value in self.known:
                    return {'raw_ref': self.known[value]}
                if value in pending:
                    return {'raw_ref': pending[value]}
                if chars + len(value) <= MAX_BLOB_CHARS:
                    index = len(self.known) + len(pending)
                    pending[value] = index; chars += len(value)
                    return {'raw_ref': index}
            if isinstance(value, dict):return {k: walk(v, k) for k, v in value.items()}
            if isinstance(value, list):return [walk(v) for v in value]
            return value
        result = walk(frame)
        if pending:result['_raw_blobs'] = {str(i): raw for raw, i in pending.items()}
        return result, pending

    def commit(self, pending):
        self.known.update(pending); self.chars += sum(map(len, pending))


def iter_frames(lines):
    """Read original v1 OR self-contained v2 JSONL, including renamed .json files."""
    blobs = {}
    def expand(value):
        if isinstance(value, dict):
            if set(value) == {'raw_ref'}:
                index = value['raw_ref']
                if type(index) is not int or index < 0 or str(index) not in blobs:
                    raise ValueError('missing or invalid raw blob reference')
                return blobs[str(index)]
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):return [expand(v) for v in value]
        return value
    for line in lines:
        if not line.strip():continue
        frame = json.loads(line)
        additions = frame.pop('_raw_blobs', {})
        if not isinstance(additions, dict) or any(k in blobs for k in additions):
            raise ValueError('invalid or repeated raw blob definitions')
        blobs.update(additions)
        yield expand(frame)


class Archive:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED)
        self.stream = self.zip.open('frames.jsonl', 'w')
        self.bytes = self.frames = self.truncated = 0
        self.stage_bytes = {}; self.last_limit = None; self.encoder = RawBlobEncoder()

    def append(self, frame):
        # Limit ORIGINAL frame size first; dedup must not hide an oversize sample.
        raw = json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        truncated = len(raw.encode('utf8')) > MAX_FRAME_BYTES
        if truncated:
            frame = {'at': frame.get('at'), 'stage': frame.get('stage'),
                     'complete': False, 'use_for_input': False, 'error': 'frame size limit'}
        encoded, pending = self.encoder.prepare(frame)
        data = (json.dumps(encoded, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode('utf8')
        stage = frame.get('stage', 'unknown')
        self.last_limit = None
        if self.bytes+len(data) > MAX_BYTES:
            self.last_limit = 'session_byte_limit'; return False
        if self.stage_bytes.get(stage, 0)+len(data) > MAX_STAGE_BYTES:
            self.last_limit = 'stage_byte_limit'; return False
        self.stream.write(data)
        self.encoder.commit(pending)
        self.bytes += len(data); self.frames += 1; self.truncated += truncated
        self.stage_bytes[stage] = self.stage_bytes.get(stage, 0)+len(data)
        return True

    def close(self, manifest):
        self.stream.close()
        manifest.update(frames=self.frames, uncompressed_bytes=self.bytes, truncated_frames=self.truncated,
                        stage_bytes=self.stage_bytes, raw_blob_count=len(self.encoder.known),
                        frames_encoding='v2-inline-raw-blobs; use collect_targets.iter_frames to expand')
        self.zip.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False))
        self.zip.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spell', choices=('wr','ck','both'), default='both')
    parser.add_argument('--seconds', type=float, default=10.)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 20:parser.error('seconds must be 5..20')
    if (ROOT/'.patch_pending').exists():parser.error('Сначала заверши/отмени ожидающий импорт патча')
    print('READ-ONLY: закрой start.bat/оверлей, оставь Dota и демо открытыми.')
    print('Это диагностика, не новый додж. Никакие игровые клавиши программа не нажимает.')
    input('Нажми Enter после закрытия автоматизации... ')
    from dota_esp import Dot
    from collect_live import Probe
    schema = (ROOT/'client_dll.json').read_bytes()
    # Refuse a changed schema before even opening the game process.
    if hashlib.sha256(schema).hexdigest() != SCHEMA_SHA:raise ValueError('Неизвестная схема')
    dot = Dot(); probe = Probe(dot, json.loads(schema)['client.dll']['classes'], seconds=2, max_reads=1600)
    check_build(schema, probe.image)
    path = ROOT/('target_probe_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]+'.zip')
    archive = Archive(path); recorder = Recorder(dot, probe)
    manifest = {'format': 'hachbob-target-probe-v2', 'schema_sha256': SCHEMA_SHA, 'client_image': probe.image,
                'use_for_input': False, 'labels_are_user_declared_not_verified': True, 'stages': [],
                'limits': ['No projectile manager layout decoded; no target inferred from pointer/handle matches.',
                           'One hop from named containers, max 2 blocks of 384 bytes each; no recursive search.',
                           'Entity discovery every 0.5s, sampled RTTI; short-lived entities may be missed.',
                           'Probe frame budget 80ms/1600 reads excludes existing Dot discovery/helpers; timings recorded.',
                           'No hidden-hero or position freshness guarantee; partial frames are explicitly labeled.',
                           'Lossless inline raw-blob references; 12MiB per stage, 72MiB total, 96KiB original frame.',
                           'Stage size limit ends that stage only; later stages retain their own budget.',
                           'Particles: unseen then least-recently attempted; 16/frame; discovery may miss brief effects.']}
    try:
        for spell in (('wr','ck') if args.spell=='both' else (args.spell,)):
            for label, text in (('protected','Один завершённый каст В ANTI-MAGE.'),
                                ('other','Один завершённый каст В ДРУГОГО доступного юнита, НЕ Anti-Mage.'),
                                ('cancel','Начни каст в Anti-Mage и отмени ДО вылета (S или движение).')):
                stage = spell+'_'+label
                print('\n'+stage+': '+text)
                print('Подготовь героя/цель и готовую способность. После Enter — 5 секунд вернуться в игру, затем 10 секунд записи.' if args.seconds==10 else 'После Enter — 5 секунд вернуться в игру, затем запись.')
                input('Готов? Enter... ')
                time.sleep(5)
                recorder.frame(time.perf_counter(),spell)  # Warm caches BEFORE the start cue.
                try:
                    import winsound
                    winsound.Beep(880,150)
                except ImportError:pass
                except RuntimeError:pass
                print('ЗАПИСЬ '+stage, flush=True)
                start = time.perf_counter(); count = seen = phases = cds = 0
                record = {'id': stage, 'operator_instruction': text, 'duration_requested': args.seconds,
                          'frames': 0, 'ability_samples': 0, 'phase_samples': 0, 'cooldown_samples': 0,
                          'complete_frames': 0, 'partial_frames': 0, 'oversize_frames': 0, 'status': 'recording'}
                manifest['stages'].append(record)
                while time.perf_counter()-start < args.seconds and count < 400:
                    now = time.perf_counter(); frame = recorder.frame(now, spell)
                    frame.update(stage=stage, seconds=now-start)
                    frame['capture_seconds'] = time.perf_counter()-now
                    before_truncated = archive.truncated
                    if not archive.append(frame):
                        record['status'] = archive.last_limit
                        print('Лимит текущей записи: этап сохранён частично; дальнейшие этапы не удаляются.')
                        if archive.last_limit == 'session_byte_limit':raise RuntimeError('Лимит размера всей записи достигнут')
                        break
                    record['complete_frames' if frame['complete'] and archive.truncated==before_truncated else 'partial_frames'] += 1
                    record['oversize_frames'] += archive.truncated-before_truncated
                    count += 1
                    for a in frame['abilities']:
                        seen += 1; phases += a['fields']['m_bInAbilityPhase']==1
                        cds += (a['fields']['m_fCooldown'] or 0) > .1
                    record.update(frames=count, ability_samples=seen, phase_samples=phases, cooldown_samples=cds,
                                  duration_recorded=time.perf_counter()-start)
                    time.sleep(max(0., .05-(time.perf_counter()-now)))
                record.update(frames=count, ability_samples=seen, phase_samples=phases, cooldown_samples=cds)
                if record['status']=='recording':record['status']='duration_complete'
                try:
                    import winsound
                    winsound.Beep(440,150)
                except (ImportError, RuntimeError):pass
                print('ГОТОВО. Кадров:', count, 'образцов способности:', seen, 'фаз:', phases)
                if not seen:print('ВНИМАНИЕ: нужная способность не найдена. Результат сохранён, это не успешный тест цели.')
                elif not phases:print('ВНИМАНИЕ: начало каста не попало в выборку; этап не считается подтверждённым.')
    except KeyboardInterrupt:
        manifest['interrupted'] = True
        if manifest['stages'] and manifest['stages'][-1]['status']=='recording':
            manifest['stages'][-1]['status']='interrupted'
    except Exception as exc:
        manifest['error'] = str(exc); print('Сбор остановлен:',exc)
        if manifest['stages'] and manifest['stages'][-1]['status']=='recording':
            manifest['stages'][-1]['status']='error'
    finally:
        archive.close(manifest)
        print('\nПРИШЛИ ТОЛЬКО ЭТОТ ZIP:\n'+str(path), flush=True)
        print('Обычные killsteal.log/overlay.log для этого теста не нужны.')


if __name__ == '__main__':
    try:main()
    except Exception as exc:
        print('ОШИБКА: '+str(exc))
        raise SystemExit(1)
