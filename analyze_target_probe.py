"""Offline evidence audit only. Reads existing JSONL/ZIP, never attaches to Dota.
Order offsets and header word semantics below are OBSERVED HYPOTHESES, not a
runtime target decoder. Every report explicitly forbids automatic input use.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import struct
import zipfile

from collect_targets import iter_frames


LIMITATIONS = [
    'Operator stage labels are not independent verification of target or outcome.',
    'Order block bytes 40/44/48/52 correlate with sequence/kind/target index/ability index in this demo only.',
    'Index matching is not a serial-bearing handle validation or a replicated enemy order source.',
    'Particle header word at byte 16 is opaque: count-like behavior does not prove a container layout.',
    'A header pulse coincident with a local command may be a command marker, not a projectile.',
    'Unchanged sampled particle fields do not prove absence of an unsampled projectile or effect.',
    'Phase exit with cooldown is release evidence, not proof of victim, impact or defensive success.',
    'All correlations are retrospective. No target provider or defensive input is enabled.',
]


def raw_bytes(row, key='hex'):
    value = (row or {}).get(key)
    if not isinstance(value, str):return None
    try:return bytes.fromhex(value)
    except ValueError:return None


def header_word(hero):
    data = raw_bytes(hero.get('particles_opaque'))
    return struct.unpack_from('<I', data, 16)[0] if data is not None and len(data) >= 20 else None


def order_candidate(controller):
    """Inspect only the observed one-hop block at header byte 8. Never promote."""
    if controller.get('identity_after') is not True:return None
    for block in controller.get('orders_opaque', {}).get('one_hop_candidates', []):
        if block.get('header_byte') != 8:continue
        data = raw_bytes(block)
        if data is None or len(data) < 56:continue
        sequence, kind, target, ability = struct.unpack_from('<IIII', data, 40)
        return {'sequence_candidate': sequence, 'kind_candidate': kind,
                'target_index_candidate': target, 'ability_index_candidate': ability,
                'buffer_address': block.get('address'), 'use_for_input': False}
    return None


def stable_key(obj):
    # Pointer/serial/class changes must not be merged into one allegedly static object.
    return (obj.get('handle'), obj.get('address'), obj.get('class', obj.get('name')))


def summarize_particles(frames):
    variants = defaultdict(set); counts = Counter(); classes = {}
    for f in frames:
        for p in f.get('particle_entities', []):
            if p.get('identity_after') is not True:continue
            key = stable_key(p); classes[key] = p.get('class')
            fingerprint = json.dumps({name: value.get('bytes') for name, value in p['fields'].items()}, sort_keys=True)
            variants[key].add(fingerprint); counts[key] += 1
    return {'verified_sampled_identities': len(variants),
            'classes': dict(Counter(classes.values())),
            'identities_with_changing_recorded_fields': sum(len(v) > 1 for v in variants.values()),
            'single_observation_identities': sum(v == 1 for v in counts.values()),
            'observations': sum(counts.values()), 'use_for_input': False}


def summarize_stage(rows):
    order_events = []; phase_events = []; header_events = []
    previous_orders = {}; previous_abilities = {}; previous_headers = {}
    phase_samples = Counter(); cooldown_samples = Counter(); specific = defaultdict(set)
    for f in rows:
        t = f['seconds']
        heroes = {h['index']: h for h in f.get('heroes', []) if h.get('identity_after') is True}
        abilities = {}
        for a in f.get('abilities', []):
            if a.get('identity_after') is not True:continue
            handle = int(a['handle'], 16)
            abilities[handle & 0x7fff] = a
            state = (a['fields'].get('m_bInAbilityPhase'), (a['fields'].get('m_fCooldown') or 0) > .1)
            phase_samples[a['handle']] += state[0] == 1
            cooldown_samples[a['handle']] += state[1]
            specific[a['handle']].add(a.get('specific', {}).get('hex'))
            key = stable_key(a)
            prev = previous_abilities.get(key)
            if prev != state:
                phase_events.append({'seconds': t, 'ability': a['handle'], 'caster': a['caster'],
                                     'phase': state[0], 'cooldown_positive': state[1],
                                     'baseline': prev is None, 'cooldown': a['fields'].get('m_fCooldown')})
            previous_abilities[key] = state
        for c in f.get('controllers', []):
            candidate = order_candidate(c)
            if candidate is None:continue
            key = stable_key(c)
            state = tuple(candidate[k] for k in ('sequence_candidate','kind_candidate',
                                                'target_index_candidate','ability_index_candidate'))
            prev = previous_orders.get(key)
            if prev != state:
                h = heroes.get(candidate['target_index_candidate'])
                a = abilities.get(candidate['ability_index_candidate'])
                order_events.append(dict(candidate, seconds=t, baseline=prev is None,
                    controller=c['handle'], player_slot=c.get('player_slot'),
                    assigned_hero_handle=c.get('assigned_hero_handle'),
                    target_hero_candidate={'handle':h['handle'],'name':h['name']} if h else None,
                    ability_handle_candidate=a['handle'] if a else None))
            previous_orders[key] = state
        for h in f.get('heroes', []):
            if h.get('identity_after') is not True:continue
            word = header_word(h); key = stable_key(h)
            if word is None:continue
            old = previous_headers.get(key)
            if old != word:
                header_events.append({'seconds':t, 'hero':h['handle'], 'name':h['name'],
                                      'old_word':old, 'new_word':word, 'baseline':old is None})
            previous_headers[key] = word
    correlations = []
    for order in order_events:
        if order['baseline'] or order['kind_candidate'] != 6 or not order['target_hero_candidate']:continue
        t = order['seconds']; target = order['target_hero_candidate']['handle']
        # Exact same captured frame, not a guessed timing window.
        rise = next((e for e in header_events if e['hero']==target and e['seconds']==t
                     and not e['baseline'] and e['new_word']>e['old_word']), None)
        if rise is None:continue
        end = next((e for e in header_events if e['hero']==target and e['seconds']>t
                    and e['new_word']<=rise['old_word']), None)
        ah = order['ability_handle_candidate']
        next_order = next((o for o in order_events if o['seconds']>t and o['kind_candidate']==6
                           and o['ability_handle_candidate']==ah), None)
        cutoff = next_order['seconds'] if next_order else float('inf')
        phase = next((e for e in phase_events if e['ability']==ah and not e['baseline']
                      and t<=e['seconds']<cutoff and e['phase']==1), None)
        exit_event = next((e for e in phase_events if phase and e['ability']==ah
                           and phase['seconds']<e['seconds']<cutoff and e['phase']==0), None)
        correlations.append({'target_candidate':order['target_hero_candidate'], 'order_seconds':t,
            'header_rise_seconds':rise['seconds'], 'header_return_seconds':end['seconds'] if end else None,
            'header_pulse_seconds':end['seconds']-t if end else None,
            'phase_first_seconds':phase['seconds'] if phase else None,
            'phase_exit_seconds':exit_event['seconds'] if exit_event else None,
            'exit_has_cooldown':exit_event['cooldown_positive'] if exit_event else None,
            'candidate_kind':'command_correlated_header_pulse_NOT_target_proof', 'use_for_input':False})
    return {'frames':len(rows), 'complete_frames':sum(f.get('complete') is True for f in rows),
            'phase_samples':dict(phase_samples), 'cooldown_samples':dict(cooldown_samples),
            'specific_raw_values':{k:sorted(v, key=str) for k,v in specific.items()},
            'particles':summarize_particles(rows), 'phase_events':phase_events,
            'order_candidates':order_events, 'hero_header_changes':header_events,
            'command_header_correlations':correlations, 'use_for_input':False}


def analyze(frames, manifest=None):
    stages = defaultdict(list)
    for f in frames:stages[f['stage']].append(f)
    all_rows = [f for rows in stages.values() for f in rows]
    observed = len(all_rows)
    expected = manifest.get('frames') if manifest else None
    return {'format':'hachbob-offline-target-audit-v1', 'use_for_input':False,
            'observed_frames':observed, 'manifest_frames':expected,
            'manifest_count_matches':observed==expected if expected is not None else None,
            'manifest_error':manifest.get('error') if manifest else None,
            'particles_all_stages':summarize_particles(all_rows),
            'stages':{k:summarize_stage(v) for k,v in stages.items()}, 'limitations':LIMITATIONS}


def read_capture(path, manifest_path=None):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            if z.getinfo('frames.jsonl').file_size > 80*1024*1024:raise ValueError('capture exceeds offline reader limit')
            frames = list(iter_frames(z.read('frames.jsonl').decode('utf-8').splitlines()))
            manifest = json.loads(z.read('manifest.json'))
    else:
        with path.open(encoding='utf-8') as stream:frames=list(iter_frames(stream))
        manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path else None
    return frames, manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture',type=Path);p.add_argument('--manifest',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():p.error('Output exists; choose a new report name')
    frames,manifest=read_capture(args.capture,args.manifest)
    report=analyze(frames,manifest)
    report['input_sha256']=hashlib.sha256(args.capture.read_bytes()).hexdigest()
    with args.output.open('x',encoding='utf-8') as out:
        json.dump(report,out,ensure_ascii=False,indent=2,allow_nan=False)
    print('Offline audit saved:',args.output,'| no game access, no new recording required')


if __name__=='__main__':main()
