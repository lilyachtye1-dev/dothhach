"""Offline evidence audit tests, including sparse real CK observations.
These never attach to a process, replay input, or infer a verified threat.
"""
import copy
import json
from pathlib import Path
import struct
import unittest
from helpers import ROOT
import analyze_target_probe as a


class ProbeAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads((ROOT/'tests/fixtures/ck_probe_fix1_sparse.json').read_text())
        cls.report=a.analyze(cls.fixture['frames'])

    def test_real_sparse_fixture_does_not_pretend_to_be_full_capture(self):
        self.assertEqual(self.fixture['source_frames'],594)
        self.assertEqual(len(self.fixture['frames']),46)
        self.assertIsNone(self.report['manifest_count_matches'])
        self.assertEqual(self.report['observed_frames'],46)

    def test_static_particle_fields_are_not_claimed_to_be_projectiles(self):
        p=self.report['particles_all_stages']
        self.assertEqual(p['verified_sampled_identities'],112)
        self.assertEqual(p['identities_with_changing_recorded_fields'],0)
        self.assertEqual(p['single_observation_identities'],0)
        self.assertFalse(p['use_for_input'])

    def test_real_cancel_contains_same_early_header_pulse_without_cooldown(self):
        stage=self.report['stages']['ck_cancel']
        c=stage['command_header_correlations'][0]
        self.assertEqual(c['target_candidate']['name'],'npc_dota_hero_antimage')
        self.assertAlmostEqual(c['order_seconds'],1.1619556,places=5)
        self.assertEqual(c['order_seconds'],c['header_rise_seconds'])
        self.assertAlmostEqual(c['header_pulse_seconds'],.4545899,places=5)
        self.assertFalse(c['exit_has_cooldown'])
        self.assertFalse(c['use_for_input'])
        self.assertIn('NOT_target_proof',c['candidate_kind'])

    def test_real_other_target_is_bloodseeker_not_any_nearest_hero(self):
        c=self.report['stages']['ck_other']['command_header_correlations'][0]
        self.assertEqual(c['target_candidate']['handle'],'0xbf8381')
        self.assertEqual(c['target_candidate']['name'],'npc_dota_hero_bloodseeker')
        self.assertTrue(c['exit_has_cooldown'])
        self.assertFalse(c['use_for_input'])

    def test_unreadable_or_short_header_stays_unknown(self):
        for value in (None,'zz','00'*8):
            self.assertIsNone(a.header_word({'particles_opaque':{'hex':value}}))

    def test_order_hypothesis_requires_observed_block_and_identity(self):
        data=bytearray(64);struct.pack_into('<IIII',data,40,8,6,631,282)
        c={'identity_after':True,'orders_opaque':{'one_hop_candidates':[{'header_byte':8,'hex':data.hex()}]}}
        self.assertEqual(a.order_candidate(c)['target_index_candidate'],631)
        c['identity_after']=False;self.assertIsNone(a.order_candidate(c))
        c['identity_after']=True;c['orders_opaque']['one_hop_candidates'][0]['header_byte']=24
        self.assertIsNone(a.order_candidate(c))

    def test_changed_particle_bytes_are_detected_and_replaced_identity_not_merged(self):
        p={'handle':'0x18007','address':'0x20000','class':'C_ParticleSystem','identity_after':True,
           'fields':{'m_bActive':{'bytes':'00'}}}
        q=copy.deepcopy(p);q['fields']['m_bActive']['bytes']='01'
        r=a.summarize_particles([{'particle_entities':[p]},{'particle_entities':[q]}])
        self.assertEqual(r['identities_with_changing_recorded_fields'],1)
        q['handle']='0x28007'
        r=a.summarize_particles([{'particle_entities':[p]},{'particle_entities':[q]}])
        self.assertEqual(r['verified_sampled_identities'],2)
        self.assertEqual(r['identities_with_changing_recorded_fields'],0)
        self.assertEqual(r['single_observation_identities'],2)

    def test_manifest_mismatch_is_explicit(self):
        r=a.analyze(self.fixture['frames'],{'frames':594,'error':'partial'})
        self.assertFalse(r['manifest_count_matches']);self.assertEqual(r['manifest_error'],'partial')

    def test_initial_stale_order_is_not_new_command(self):
        stage=self.fixture['frames'][0]['stage']
        frames=[f for f in self.fixture['frames'] if f['stage']==stage]
        actual=next(i for i,f in enumerate(frames) if f['seconds']>=.555)
        r=a.analyze(frames[actual:])['stages'][stage]
        # Starting on a command buffer is a baseline, not evidence of a fresh order.
        self.assertEqual(r['command_header_correlations'],[])

    def test_no_memory_reader_or_automation_imports(self):
        import ast
        tree=ast.parse((ROOT/'analyze_target_probe.py').read_text())
        modules=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):modules.extend(x.name for x in node.names)
            if isinstance(node,ast.ImportFrom):modules.append(node.module or '')
        self.assertFalse(any(x in modules for x in ('dota_esp','pymem','ctypes','dodge_service','ks_service')))
        self.assertFalse(self.report['use_for_input'])
