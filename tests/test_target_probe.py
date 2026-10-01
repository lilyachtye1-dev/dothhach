"""Offline tests for read-only target comparison, not a Windows/game validation."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
from helpers import ROOT
import collect_targets as c


class Probe:
    def __init__(self):
        self.classes=json.loads((ROOT/'client_dll.json').read_text())['client.dll']['classes']
        self.memory={};self.reads=0;self.calls=[]
    def put(self, address, data):
        for i,b in enumerate(data):self.memory[address+i]=b
    def read(self,a,n):
        self.reads+=1;self.calls.append((a,n))
        if all(a+i in self.memory for i in range(n)):return bytes(self.memory[a+i] for i in range(n))
        return None
    def in_image(self,p):return 0x10000000<=p<0x11000000
    def runtime_type(self,p):return None


class TargetProbeTests(unittest.TestCase):
    def setUp(self):self.p=Probe()
    def test_build_pin_accepts_only_matching_schema_and_image(self):
        schema=(ROOT/'client_dll.json').read_bytes()
        c.check_build(schema,dict(timestamp=c.BUILD[0],size=c.BUILD[1]))
        with self.assertRaises(ValueError):c.check_build(schema+b' ',dict(timestamp=c.BUILD[0],size=c.BUILD[1]))
        with self.assertRaises(ValueError):c.check_build(schema,dict(timestamp=1,size=c.BUILD[1]))
    def test_invalid_offset_never_becomes_memory_address(self):
        for value in (True,-1,0,65536,None):
            with self.subTest(value=value),self.assertRaises(ValueError):
                c.offset({'X':{'fields':{'f':value}}},'X','f')
    def test_full_handle_match_not_index_only(self):
        known={0x18007:{'name':'hero'}}
        data=struct.pack('<III',7,0x28007,0x18007)
        self.assertEqual(c.handle_matches(data,known),[{'byte':8,'handle':'0x18007','entity':{'name':'hero'}}])
    def test_unreadable_is_null_not_zero(self):
        self.assertIsNone(c.scalar(self.p,0x20000,'C_DOTABaseAbility','m_fCooldown','<f'))
    def test_nan_scalar_is_null(self):
        off=c.offset(self.p.classes,'C_DOTABaseAbility','m_fCooldown')
        self.p.put(0x20000+off,struct.pack('<f',float('nan')))
        self.assertIsNone(c.scalar(self.p,0x20000,'C_DOTABaseAbility','m_fCooldown','<f'))
    def test_container_chase_one_hop_two_blocks_and_no_module_or_owner(self):
        ep=0x20000;off=c.offset(self.p.classes,'C_BaseEntity','m_Particles')
        self.p.put(ep+off,struct.pack('<6Q',ep,0x10000000,0x30000,0x40000,0x50000,0x60000))
        self.p.put(0x30000,struct.pack('<Q',0x70000)+bytes(376))
        r=c.opaque_container(self.p,ep,'C_BaseEntity','m_Particles',48,{})
        self.assertEqual([x['address'] for x in r['one_hop_candidates']],['0x30000','0x40000'])
        self.assertEqual(self.p.calls,[(ep+off,48),(0x30000,384),(0x40000,384)])
    def test_missing_container_does_not_follow_anything(self):
        r=c.opaque_container(self.p,0x20000,'C_BaseEntity','m_Particles',48,{})
        self.assertIsNone(r['hex']);self.assertEqual(r['one_hop_candidates'],[])
    def test_identity_checks_backlink_name_and_full_handle(self):
        ep=0x20000;ident=0x30000;handle=0x18007
        off=c.offset(self.p.classes,'CEntityInstance','m_pEntity')
        self.p.put(ep+off,struct.pack('<Q',ident))
        self.p.put(ident,struct.pack('<QQI',ep,0,handle))
        dot=SimpleNamespace(get_entity=lambda i:(ep,'hero'))
        self.assertEqual(c.identity(dot,self.p,ep,'hero'),handle)
        self.assertIsNone(c.identity(dot,self.p,ep,'other'))
        self.p.put(ident,struct.pack('<Q',ep+8))
        self.assertIsNone(c.identity(dot,self.p,ep,'hero'))
    def test_archive_is_valid_when_closed_with_interrupted_manifest(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'p.zip';a=c.Archive(path)
            self.assertTrue(a.append({'stage':'wr_cancel','complete':False}))
            a.close({'interrupted':True})
            with zipfile.ZipFile(path) as z:
                m=json.loads(z.read('manifest.json'))
                self.assertTrue(m['interrupted']);self.assertEqual(m['frames'],1)
                self.assertIsNone(z.testzip());self.assertIn(b'wr_cancel',z.read('frames.jsonl'))
            with self.assertRaises(FileExistsError):c.Archive(path)
    def test_size_cap_refuses_before_write(self):
        with tempfile.TemporaryDirectory() as t:
            a=c.Archive(Path(t)/'p.zip')
            with patch.object(c,'MAX_BYTES',1):self.assertFalse(a.append({'a':1}))
            a.close({});self.assertEqual(a.frames,0)
    def test_oversize_frame_is_explicitly_truncated(self):
        with tempfile.TemporaryDirectory() as t:
            a=c.Archive(Path(t)/'p.zip')
            with patch.object(c,'MAX_FRAME_BYTES',10):self.assertTrue(a.append({'stage':'ck_other','data':'x'*100}))
            a.close({});self.assertEqual(a.truncated,1)
    def test_recorder_collects_non_profile_raw_fields_without_auto_target(self):
        entries={7:(0x20000,'npc_dota_hero_windrunner'),8:(0x30000,'npc_dota_hero_antimage'),
                 9:(0x40000,'dota_player_controller'),10:(0x50000,'windrunner_shackleshot')}
        for idx,(ep,name) in entries.items():
            ip=0x90000+idx*256
            self.p.put(ep+c.offset(self.p.classes,'CEntityInstance','m_pEntity'),struct.pack('<Q',ip))
            self.p.put(ip,struct.pack('<QQI',ep,0,0x18000+idx))
        for f,fmt,v in [('m_bInAbilityPhase','<B',1),('m_fCooldown','<f',0.),('m_flCastStartTime','<f',20.)]:
            self.p.put(0x50000+c.offset(self.p.classes,'C_DOTABaseAbility',f),struct.pack(fmt,v))
        dot=SimpleNamespace(entities=lambda **k:dict(entries),get_entity=lambda i:entries.get(i),
                            pos=lambda e:(0.,0.,0.),dormant_state=lambda e:False,
                            abilities_of=lambda *a:[dict(name='windrunner_shackleshot',ent=0x50000)])
        f=c.Recorder(dot,self.p).frame(0.,'wr')
        self.assertTrue(f['complete'],f.get('error'));self.assertFalse(f['use_for_input'])
        self.assertEqual(len(f['abilities']),1);self.assertEqual(len(f['controllers']),1)
        self.assertTrue(f['abilities'][0]['identity_after'])
        self.assertEqual(f['abilities'][0]['fields']['m_bInAbilityPhase'],1)
        self.assertIsNone(f['abilities'][0]['specific']['hex'])
        self.assertNotIn('verified_target',f['abilities'][0])
    def test_reader_exception_is_partial_frame(self):
        dot=SimpleNamespace(entities=lambda **kw:None)
        f=c.Recorder(dot,self.p).frame(0.,'wr')
        self.assertFalse(f['complete']);self.assertIn('unavailable',f['error'])
    def test_standalone_has_no_sender_or_game_writes(self):
        text=(ROOT/'collect_targets.py').read_text()
        for symbol in ('SendInput','WriteProcessMemory','WinInput','pymem.write','ks_service'):
            self.assertNotIn(symbol,text)


class ProbeFixTests(unittest.TestCase):
    def test_lossless_blob_roundtrip_and_repeated_blocks_not_rewritten(self):
        raw='0123456789abcdef'*48
        frames=[{'stage':'ck_protected','at':i,'complete':True,
                 'a':{'hex':raw},'b':[{'bytes':raw},{'hex':None}]} for i in range(12)]
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'p.zip';a=c.Archive(path)
            for f in frames:self.assertTrue(a.append(f))
            a.close({})
            with zipfile.ZipFile(path) as z:
                data=z.read('frames.jsonl').decode()
                self.assertEqual(list(c.iter_frames(data.splitlines())),frames)
                self.assertEqual(data.count(raw),1)
                self.assertLess(len(data),len(json.dumps(frames))//3)

    def test_decoder_accepts_old_renamed_jsonl(self):
        original={'stage':'wr_other','a':{'hex':'ab'*384}}
        self.assertEqual(list(c.iter_frames(['\n',json.dumps(original)])),[original])

    def test_missing_reference_is_not_silently_zero_or_empty(self):
        with self.assertRaises(ValueError):
            list(c.iter_frames(['{"a":{"raw_ref":77}}']))

    def test_interning_budget_falls_back_to_literal_without_losing_bytes(self):
        f={'stage':'ck_other','a':{'hex':'ab'*384}}
        e=c.RawBlobEncoder()
        with patch.object(c,'MAX_BLOB_CHARS',1):
            encoded,pending=e.prepare(f)
        self.assertEqual(encoded,f);self.assertEqual(pending,{})

    def test_rejected_frame_does_not_commit_unwritten_blob_references(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'p.zip';a=c.Archive(path)
            f={'stage':'wr_other','a':{'hex':'ab'*384}}
            with patch.object(c,'MAX_STAGE_BYTES',1):self.assertFalse(a.append(f))
            self.assertEqual(a.encoder.known,{})
            self.assertTrue(a.append(dict(f,stage='ck_other')))
            a.close({})
            with zipfile.ZipFile(path) as z:
                decoded=list(c.iter_frames(z.read('frames.jsonl').decode().splitlines()))
            self.assertEqual(decoded,[dict(f,stage='ck_other')])

    def test_stage_limit_does_not_consume_another_stages_budget(self):
        with tempfile.TemporaryDirectory() as t:
            a=c.Archive(Path(t)/'p.zip')
            with patch.object(c,'MAX_STAGE_BYTES',100):
                self.assertTrue(a.append({'stage':'wr_protected','v':'x'*35}))
                self.assertFalse(a.append({'stage':'wr_protected','v':'x'*35}))
                self.assertEqual(a.last_limit,'stage_byte_limit')
                self.assertTrue(a.append({'stage':'ck_protected','v':'x'*35}))
            a.close({})
            self.assertEqual(a.frames,2)

    def test_particle_rotation_covers_beyond_first_sixteen(self):
        r=c.Recorder(None,None)
        r.particles={i:(0x20000+i*4096,'particle','C_ParticleSystem') for i in range(41)}
        seen=[]
        for _ in range(3):
            batch=r.particle_batch()
            for idx,obj in batch:r.particle_attempted(idx,obj);seen.append(idx)
        self.assertEqual(set(seen),set(range(41)))
        self.assertEqual(seen[:41],list(range(41)))

    def test_new_and_replaced_particles_get_priority(self):
        r=c.Recorder(None,None)
        r.particles={i:(0x20000+i*4096,'p','C_ParticleSystem') for i in range(20)}
        for idx,obj in r.particles.items():r.particle_attempted(idx,obj)
        r.particles[99]=(0x90000,'new','C_ParticleSystem')
        r.particles[0]=(0xa0000,'replacement','C_ParticleSystem')
        self.assertEqual({i for i,_ in r.particle_batch(2)},{0,99})
        self.assertFalse(any(k[0]==0 and k[1]==0x20000 for k in r.particle_attempts))

    def test_invalid_particle_attempt_does_not_permanently_starve_others(self):
        r=c.Recorder(None,None)
        r.particles={i:(i*4096+0x20000,'p','C_ParticleSystem') for i in range(32)}
        for idx,obj in r.particle_batch():r.particle_attempted(idx,obj)
        self.assertEqual([i for i,_ in r.particle_batch()],list(range(16,32)))

    def test_announcers_excluded_without_removing_actual_heroes(self):
        for name in ('npc_dota_hero_announcer','npc_dota_hero_announcer_killing_spree',None,''):
            self.assertFalse(c.is_playable_hero(name))
        for name in ('npc_dota_hero_antimage','npc_dota_hero_windrunner','npc_dota_hero_chaos_knight'):
            self.assertTrue(c.is_playable_hero(name))

    def _run_main(self, tmp, frame_callback, stage_cap=None):
        import itertools,sys,types
        from contextlib import ExitStack
        (tmp/'client_dll.json').write_bytes((ROOT/'client_dll.json').read_bytes())
        module=types.ModuleType('dota_esp');module.Dot=lambda:None
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules,{'dota_esp':module}))
            stack.enter_context(patch('collect_live.Probe',return_value=SimpleNamespace(image={'timestamp':c.BUILD[0],'size':c.BUILD[1]})))
            stack.enter_context(patch.object(c,'ROOT',tmp))
            stack.enter_context(patch.object(c,'Recorder',return_value=SimpleNamespace(frame=frame_callback)))
            stack.enter_context(patch.object(c.time,'sleep'))
            stack.enter_context(patch.object(c.time,'perf_counter',side_effect=itertools.count(0,.1)))
            stack.enter_context(patch('builtins.input',return_value=''))
            stack.enter_context(patch('builtins.print'))
            stack.enter_context(patch.object(sys,'argv',['collect_targets.py','--spell','both']))
            if stage_cap is not None:stack.enter_context(patch.object(c,'MAX_STAGE_BYTES',stage_cap))
            c.main()
        with zipfile.ZipFile(next(tmp.glob('target_probe_*.zip'))) as z:
            return json.loads(z.read('manifest.json')),list(c.iter_frames(z.read('frames.jsonl').decode().splitlines()))

    def test_main_continues_to_all_six_stages_after_per_stage_limit(self):
        with tempfile.TemporaryDirectory() as t:
            m,frames=self._run_main(Path(t),lambda *args:{'complete':True,'abilities':[]},stage_cap=1)
        self.assertEqual(len(m['stages']),6)
        self.assertTrue(all(s['status']=='stage_byte_limit' for s in m['stages']))
        self.assertNotIn('error',m);self.assertEqual(frames,[])

    def test_main_interrupt_preserves_running_stage_counts(self):
        calls=0
        def sample(*args):
            nonlocal calls
            calls+=1
            if calls==3:raise KeyboardInterrupt()
            return {'complete':True,'abilities':[]}
        with tempfile.TemporaryDirectory() as t:
            m,frames=self._run_main(Path(t),sample)
        self.assertTrue(m['interrupted']);self.assertEqual(len(frames),1)
        self.assertEqual(m['stages'][0]['frames'],1)
        self.assertEqual(m['stages'][0]['status'],'interrupted')
        self.assertEqual(m['stages'][0]['complete_frames'],1)
