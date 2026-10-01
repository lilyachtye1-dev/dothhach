"""One-shot READ-ONLY Last Hit compatibility report, never an input approval.
Does not modify offsets, profiles, build pins, game memory or module enable state.
The candidate private layout is tested for diagnostics only; never installed.
"""
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import time

ROOT=Path(__file__).resolve().parent
REPORT='last_hit_patch_report.json'
NEW_SCHEMA='fabedd63041971c8ee09243fb229abd896d259b8e7351a61c1700f594868b1c6'
CLASS_LAYOUT='0a0c1eb77bb79f99a05e27c4c8620cca3f844831c020e1d397f9e686f8617f10'
EXPECTED_GLOBALS={
    'DW_GAME_ENTITY_SYSTEM':106159024,'DW_VIEW_MATRIX':102518656,'DW_VIEW_RENDER':102521144,
    'DW_GAME_RULES':102496688,'DW_GLOBAL_VARS':94946304,'DW_NETWORK_GAME_CLIENT':9493552,
    'DW_BUILD_NUMBER':6356260,'DW_HIGHEST_INDEX':8336,'OFF_NGC_LOCAL_PLAYER':248,
}
# A hypothesis inherited from the prior approved build, NOT an approval for the
# new executable. Candidate readings never enter LastHit, KS or a sender.
CANDIDATE_LAYOUT={'manager_owner':8,'count':16,'data':24,'capacity':32,
                  'modifier_manager':48,'stack_count':132,'max_count':128}
PUBLIC_FIELDS={
 'C_BaseEntity':{'m_iHealth':'<i','m_iMaxHealth':'<i','m_iTeamNum':'<B','m_lifeState':'<B'},
 'C_DOTA_BaseNPC':{'m_iDamageMin':'<i','m_iDamageMax':'<i','m_iDamageBonus':'<i',
     'm_flPhysicalArmorValue':'<f','m_flHealthRegen':'<f','m_flBaseAttackTime':'<f',
     'm_iBaseAttackSpeed':'<i','m_iAttackRange':'<i','m_flHullRadius':'<f','m_flLastAttackTime':'<f'},
 'C_DOTA_BaseNPC_Hero':{'m_flAgilityTotal':'<f'},
}


def sha(raw):return hashlib.sha256(raw).hexdigest()


def static_report(root=ROOT):
    from patch_update import load,assignments
    raw=(root/'client_dll.json').read_bytes();schema=load(raw);classes=schema['client.dll']['classes']
    fingerprint=sha(json.dumps(classes,sort_keys=True,separators=(',',':')).encode())
    values=assignments((root/'dota_esp.py').read_text(encoding='utf-8'),set(EXPECTED_GLOBALS))
    values={key:node.value for key,node in values.items()}
    mismatches={k:{'installed':values[k],'expected':v} for k,v in EXPECTED_GLOBALS.items() if values[k]!=v}
    result=dict(schema_sha256=sha(raw),raw_schema_matches_supplied=sha(raw)==NEW_SCHEMA,
                class_layout_sha256=fingerprint,class_layout_matches=fingerprint==CLASS_LAYOUT,
                installed_globals=values,global_mismatches=mismatches)
    return result,raw,classes


def candidate_config(image,digest):
    # Caller uses this ephemeral object solely inside this diagnostic command.
    return dict(CANDIDATE_LAYOUT,client_timestamp=image['timestamp'],client_size=image['size'],schema_sha256=digest)


def read_fields(probe,ep):
    result={}
    for cls,fields in PUBLIC_FIELDS.items():
        for name,fmt in fields.items():
            offset=probe.classes[cls]['fields'][name]
            if type(offset) is not int or not 0<offset<0x10000:raise ValueError('invalid field offset '+name)
            raw=probe.read(ep+offset,struct.calcsize(fmt))
            if raw is None:raise ValueError('unreadable '+name)
            value=struct.unpack(fmt,raw)[0]
            if isinstance(value,float) and not math.isfinite(value):raise ValueError('nonfinite '+name)
            result[name]=value
    if not (0<result['m_iHealth']<=result['m_iMaxHealth']<=100000 and result['m_iTeamNum'] in (2,3)
            and result['m_lifeState']==0):raise ValueError('local hero is not alive / fields invalid')
    if not (.1<=result['m_flBaseAttackTime']<=10 and 1<=result['m_iBaseAttackSpeed']<=1000
            and 0<=result['m_flAgilityTotal']<=10000 and 0<result['m_iAttackRange']<10000
            and -100<=result['m_flPhysicalArmorValue']<=2000 and 0<=result['m_flHullRadius']<=500):
        raise ValueError('hero combat fields outside bounds')
    if not 0<=result['m_iDamageMin']+result['m_iDamageBonus']<=result['m_iDamageMax']+result['m_iDamageBonus']<=50000:
        raise ValueError('damage fields outside bounds')
    return result


def sample_hero(dot,probe,ep,name,stats):
    from collect_targets import identity
    probe.deadline=time.perf_counter()+.10;probe.reads=0
    handle=identity(dot,probe,ep,name)
    if handle is None:raise ValueError('local hero identity unavailable')
    cls=probe.runtime_type(ep)
    if not cls or not (cls=='C_DOTA_BaseNPC_Hero' or cls.startswith('C_DOTA_Unit_Hero_')):
        raise ValueError('hero RTTI mismatch: '+str(cls))
    fields=read_fields(probe,ep)
    if identity(dot,probe,ep,name)!=handle:raise ValueError('hero recycled during sample')
    result=dict(entity=hex(ep),handle=hex(handle),name=name,runtime_type=cls,fields=fields,
                private_candidate={'approved_for_input':False})
    try:
        value,note=stats.read(dot,ep)
        probe.deadline=time.perf_counter()+.025;probe.reads=0
        if identity(dot,probe,ep,name)!=handle:raise ValueError('hero changed during modifier check')
        result['private_candidate'].update(structural_check='passed',partial_ias=value,sources=note)
    except Exception as exc:result['private_candidate'].update(structural_check='failed',reason=str(exc)[:200])
    return result


def collect(report,raw,classes):
    from dota_esp import Dot
    from collect_live import Probe
    from collect_targets import identity
    from last_hit_stats import LiveStats
    from last_hit_units import candidate_name,entity_kind
    from last_hit_reader import Reader
    dot=Dot();probe=Probe(dot,classes,seconds=2,max_reads=5000)
    report['client_image']=probe.image
    report['process_id']=getattr(dot.pm,'process_id',None)
    report['private_layout_candidate']=dict(CANDIDATE_LAYOUT)
    stats=LiveStats(classes,candidate_config(probe.image,sha(raw)),sha(raw))
    snapshot=dot.snapshot(full_scan=True)
    heroes=(snapshot or {}).get('heroes',())
    local=[h for h in heroes if h.get('is_local') and h.get('ent') and h.get('alive') and str(h.get('designer','')).startswith('npc_dota_hero_')]
    assigned=((snapshot or {}).get('local') or {}).get('hero_assigned')
    if assigned:
        local=[h for h in local if h.get('idx')==assigned]
    if len(local)!=1:
        report['observation']='PE headers read; no unique live local hero. This is not a build approval.'
        return
    ep=local[0]['ent'];name=local[0]['designer'];report['hero_samples']=[]
    for _ in range(3):
        try:report['hero_samples'].append(sample_hero(dot,probe,ep,name,stats))
        except Exception as exc:report['hero_samples'].append({'error':str(exc)[:200]})
        time.sleep(.12)
    # Small read-only target pass. Reuse the real target reader but never start
    # its worker/controller and never give it a candidate private approval.
    r=Reader.__new__(Reader);r.dot=dot;r.probe=probe;r.stats=None;r.stat_cache={}
    dot.screen=dot._screen_size();matrix=dot.raw_matrix();pos=dot.pos(ep);clock=dot.curtime()
    targets=[];deadline=time.perf_counter()+1.
    entries=(snapshot or {}).get('ents') or {}
    for tep,tname in entries.values():
        if len(targets)>=12 or time.perf_counter()>deadline:break
        if not candidate_name(tname):continue
        probe.deadline=min(deadline,time.perf_counter()+.08);probe.reads=0
        tpos=dot.pos(tep)
        if not pos or not tpos or math.hypot(pos[0]-tpos[0],pos[1]-tpos[1])>2400:continue
        try:
            cls=probe.runtime_type(tep)
            if entity_kind(cls,tname)!='creep':continue
            target=r.target_unit(tep,tname,clock,matrix,cls)
            if target is None:continue
            row=asdict(target);row['runtime_type']=cls
            if target.lane_type=='siege':
                try:
                    value,note=stats.read(dot,tep,True)
                    probe.deadline=time.perf_counter()+.025;probe.reads=0
                    if identity(dot,probe,tep,tname)!=target.uid[1]:raise ValueError('siege recycled')
                    row['private_candidate']={'approved_for_input':False,'structural_check':'passed','incoming_multiplier':value,'source':note}
                except Exception as exc:row['private_candidate']={'approved_for_input':False,'structural_check':'failed','reason':str(exc)[:200]}
            targets.append(row)
        except Exception as exc:targets.append({'name':tname,'error':str(exc)[:200]})
    report['nearby_targets']=targets
    report['observation']='Read-only samples collected; review required. No build/layout approval was changed.'


def run(root=ROOT,collector=collect):
    start=time.perf_counter()
    report={'format':'hachbob-last-hit-patch-report-v1','utc':datetime.now(timezone.utc).isoformat(),
            'approved_for_input':False,'writes_to_game':False,'profile_changes':False,'samples_are_not_kill_confirmation':True}
    code=0
    try:
        check,raw,classes=static_report(root);report['installation']=check
        if not check['class_layout_matches']:raise ValueError('class layout differs from the supplied set; not probing unknown offsets')
        if check['global_mismatches']:raise ValueError('installed globals differ: run in the folder where the new offsets were applied')
        if struct.calcsize('P')!=8:raise ValueError('64-bit Python is required')
        collector(report,raw,classes)
        if 'client_image' not in report:raise ValueError('client.dll PE data not collected')
    except Exception as exc:
        report['error']=str(exc)[:500];code=2
    report['elapsed_seconds']=round(time.perf_counter()-start,3)
    try:
        data=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
        if len(data)>256*1024:raise ValueError('report exceeds size limit')
    except (ValueError,TypeError):
        # Preserve executable identity even when optional sample data is bad.
        report.pop('hero_samples',None);report.pop('nearby_targets',None)
        report['error']='Optional samples could not be serialized within bounds';code=2
        data=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
    path=root/REPORT;temp=path.with_suffix('.json.tmp');temp.write_bytes(data);temp.replace(path)
    print('Report saved: '+str(path))
    print('No offsets, profiles, build checks or game state changed.')
    if report.get('error'):print('Observation error: '+report['error'])
    return code


if __name__=='__main__':
    raise SystemExit(run())
