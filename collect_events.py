"""HACHBOB: bounded read-only event diagnostics, NOT dodge or fog detection.
Records schema-backed particle ENTITY candidates and opaque hero particle headers.
Does not follow an unverified particle-manager layout or infer projectile targets.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import time
import zipfile

ROOT = Path(__file__).resolve().parent
PARTICLES = {
    'C_ParticleSystem': {'m_bActive': 1, 'm_bFrozen': 1, 'm_iEffectIndex': 4,
                        'm_flStartTime': 4, 'm_vServerControlPoints': 48,
                        'm_iServerControlPointAssignments': 4, 'm_hControlPointEnts': 16,
                        'm_iszEffectName': 8},
    'C_DOTAWorldParticleSystem': {'m_iClientEffectIndex': 4, 'm_szEffectName': 8,
                                'm_szTargetName': 8, 'm_szControlPoint': 8, 'm_bShowInFow': 1},
}


def finite_position(value):
    if value is None or len(value) != 3:
        return None
    return list(value) if all(math.isfinite(x) and abs(x) <= 15000 for x in value) else None


def raw_fields(probe, entity, cls):
    fields = probe.classes[cls]['fields']
    out = {}
    for name, size in PARTICLES[cls].items():
        offset = fields.get(name)
        if type(offset) is not int or not 0 < offset <= 0x100000:
            continue
        data = probe.read(entity + offset, size)
        # Opaque bytes intentionally not promoted to targets, names, ETA or CP positions.
        out[name] = {'offset': offset, 'bytes': data.hex() if data is not None else None}
    return out


def capture(dot, probe, duration, hz, clock=time.perf_counter, sleep=time.sleep):
    from collect_live import ProbeLimit
    import dota_esp as offsets
    start = clock()
    frames, cursor = [], 0
    while clock() - start < duration and len(frames) < 600:
        tick = clock()
        probe.deadline = time.perf_counter() + .15
        probe.reads = 0
        frame = {'t': round(tick - start, 4), 'heroes': [], 'particle_entities': [],
                 'complete': False, 'rtti_scan_complete': False}
        try:
            entities = dot.entities()
            if entities is None:
                raise ValueError('entity system unavailable')
            entries = sorted(entities.items())
            frame['entity_count'] = len(entries)
            # Runtime-type sampling rotates so high indices are not permanently excluded.
            subset = (entries[cursor:] + entries[:cursor])[:256]
            cursor = (cursor + len(subset)) % max(1, len(entries))
            frame['rtti_scan_complete'] = len(subset) == len(entries)
            for idx, (ep, name) in subset:
                cls = probe.runtime_type(ep)
                if cls not in PARTICLES:
                    continue
                frame['particle_entities'].append({'index': idx, 'address': hex(ep), 'class': cls,
                    'designer_name': name, 'scene_position_unverified': finite_position(dot.pos(ep)),
                    'dormant': dot.dormant_state(ep), 'fields': raw_fields(probe, ep, cls)})
                if len(frame['particle_entities']) >= 32:
                    frame['particle_limit'] = True
                    break
            for idx, (ep, name) in entries:
                if not (name or '').startswith('npc_dota_hero_'):
                    continue
                if len(frame['heroes']) >= 32:
                    frame['hero_limit'] = True
                    break
                particle_offset = probe.classes['C_BaseEntity']['fields']['m_Particles']
                header = probe.read(ep + particle_offset, 48)
                phases = []
                for ability in dot.abilities_of(entities, ep, name)[:32]:
                    data = probe.read(ability['ent'] + offsets.AB_B_IN_ABILITY_PHASE, 1)
                    active = bool(data[0]) if data is not None and data[0] in (0, 1) else None
                    phases.append({'name': ability['name'], 'phase': active})
                frame['heroes'].append({'index': idx, 'address': hex(ep), 'name': name,
                    'team': dot.u8(ep + offsets.M_I_TEAM_NUM), 'dormant': dot.dormant_state(ep),
                    'position_may_be_last_seen': finite_position(dot.pos(ep)),
                    'particle_header_opaque': header.hex() if header is not None else None,
                    'cast_phases_not_targets': phases})
            frame['complete'] = True
        except ProbeLimit as e:
            frame['limit'] = str(e)
        except Exception as e:
            frame['error'] = type(e).__name__ + ': ' + str(e)
        frame['read_count'] = probe.reads
        frames.append(frame)
        sleep(max(0., 1. / hz - (clock() - tick)))
    return frames


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds', type=float, default=15)
    p.add_argument('--hz', type=float, default=8)
    p.add_argument('--output', type=Path, default=ROOT / 'event_probe.zip')
    a = p.parse_args()
    if not (1 <= a.seconds <= 60 and 1 <= a.hz <= 10):
        p.error('seconds: 1..60; hz: 1..10')
    if (ROOT / '.patch_pending').exists():
        p.error('Restore the pending patch transaction first')
    if a.output.exists():
        p.error('Output exists; rename it or choose --output to avoid overwriting evidence')
    from dota_esp import Dot
    from collect_live import Probe
    schema = (ROOT / 'client_dll.json').read_bytes()
    classes = json.loads(schema)['client.dll']['classes']
    dot = Dot()
    probe = Probe(dot, classes, seconds=2, max_reads=6000)
    print('HACHBOB: read-only recording starts in 5 seconds. Return to the demo.')
    print('No input, no dodging. Reproduce one spell/projectile or particle event.')
    time.sleep(5)
    report = {'format': 'hachbob-event-probe-v1', 'schema_sha256': hashlib.sha256(schema).hexdigest(),
              'client_image': probe.image,
              'limits': ['Entity sampling may miss short-lived effects.',
                         'Particle-manager containers are NOT decoded.',
                         'Casting phase does not identify a projectile target or impact time.',
                         'Hero coordinates may be stale; NOT evidence of a hidden hero.',
                         'Empty particle list does not prove absence of particles.'],
              'frames': capture(dot, probe, a.seconds, a.hz)}
    with zipfile.ZipFile(a.output, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('event_probe.json', json.dumps(report, ensure_ascii=False, allow_nan=False))
    print('Saved:', a.output, '| frames:', len(report['frames']))


if __name__ == '__main__':
    main()
