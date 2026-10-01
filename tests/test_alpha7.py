"""Targeted alpha7 regressions. Synthetic memory/positions, not live Dota replay."""
from dataclasses import replace
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch
import test_dodger as base
import test_reader_input as reader_base
from test_assassinate_mark import MemoryHarness
from helpers import ROOT, settings
from dodger import choose_defense
from ks_inventory import Inventory


class PhaseContinuityTests(unittest.TestCase):
    def setUp(self):
        self.h=MemoryHarness()
        self.h.f.enemy=replace(self.h.f.enemy,pos=(1100.,0.))
        self.h.f.field('C_DOTABaseAbility','m_flOverrideCastPoint','<f',-1.)
        self.h.frame(9.96,0,start=50.)
        self.h.frame(10.,1,token=0x90000)
        self.h.frame(10.04,1,token=0x90000)
    def tick(self,now,token=0x90000):return self.h.frame(now,1,token=token)
    def test_phase_blink_forces_scan_and_preserves_same_threat_id(self):
        old=self.tick(10.08).threats[0];scans=self.h.scan_count
        self.h.f.me=replace(self.h.f.me,pos=(600.,0.))
        f=self.tick(10.09)
        self.assertEqual(self.h.scan_count,scans+1)
        self.assertEqual(f.threats[0].id,old.id)
        self.assertLess(f.threats[0].earliest,old.earliest)
        self.assertTrue(any('phase_reposition_revalidated' in t for _,t in f.events))
    def test_blink_then_release_can_select_defense(self):
        self.h.f.me=replace(self.h.f.me,pos=(500.,0.))
        for i in range(47):self.tick(10.08+i*.04)
        f=self.h.frame(12.,0,10,102.,0x90000)
        self.assertEqual(f.threats[0].evidence,'local_mark_launch')
    def test_blink_with_absent_mark_never_preserves_cast(self):
        self.h.f.me=replace(self.h.f.me,pos=(600.,0.))
        self.assertFalse(self.tick(10.05,0).threats)
        self.assertIsNone(self.h.f.reader.marked.cast)
    def test_blink_with_changed_mark_never_rebinds(self):
        self.h.f.me=replace(self.h.f.me,pos=(600.,0.))
        self.assertFalse(self.tick(10.05,0x91000).threats)
        self.assertIsNone(self.h.f.reader.marked.cast)
    def test_cached_mark_cannot_authorize_phase_reposition_in_tracker(self):
        tracker=self.h.f.reader.marked
        old=self.tick(10.08).threats[0]
        # Bypass reader to check its fresh-scan requirement independently.
        from dodge_assassinate import Mark
        sample=dict(caster=self.h.f.enemy.entity,caster_handle=self.h.caster_handle,
                    entity=self.h.f.ep,ability_handle=tracker.previous['key'][-1],
                    handle=tracker.previous['key'][1],phase=True,cd=0.,start=100.,
                    pos=self.h.f.enemy.pos,cast_point=2.)
        moved=replace(self.h.f.me,pos=(600.,0.))
        self.assertFalse(tracker.update(sample,Mark(10.08,moved.entity,old.id[-2]),moved,10.09))
    def test_phase_modifier_vector_race_pauses_then_fresh_same_mark_recovers(self):
        self.h.error='modifier vector changed during read'
        f=self.tick(10.09)
        self.assertFalse(f.threats);self.assertIsNotNone(self.h.f.reader.marked.cast)
        self.assertTrue(any('tracking_retry' in text for _,text in f.events))
        self.h.error=None
        self.assertTrue(self.tick(10.13).threats)
    def test_repeated_race_does_not_extend_150ms_continuity(self):
        self.h.error='modifier changed during read'
        for now in (10.09,10.13,10.17,10.21):self.assertFalse(self.tick(now).threats)
        self.h.error=None
        self.assertFalse(self.tick(10.25).threats)
        self.assertIsNone(self.h.f.reader.marked.cast)
    def test_owner_failure_is_not_retryable(self):
        self.h.error='owner mismatch'
        self.assertFalse(self.tick(10.09).threats)
        self.h.error=None
        self.assertFalse(self.tick(10.13).threats)
    def test_retry_then_different_token_drops_episode(self):
        self.h.error='modifier changed during read';self.tick(10.09)
        self.h.error=None
        self.assertFalse(self.tick(10.13,0x91000).threats)
    def test_retry_cannot_create_launch_across_unreadable_release(self):
        self.h.error='modifier changed during read';self.tick(10.09)
        self.assertFalse(self.h.frame(10.12,0,10,100.,0x90000).threats)
        self.h.error=None
        self.assertFalse(self.h.frame(10.16,0,10,100.,0x90000).threats)
    def test_retry_then_known_foreign_target_drops_episode(self):
        self.h.error='modifier changed during read';self.tick(10.09)
        self.h.error=None;self.h.f.field(self.h.f.cls,'m_hTarget','<I',0x8007)
        self.assertFalse(self.tick(10.13).threats)
        self.assertIsNone(self.h.f.reader.marked.cast)
    def test_gap_has_explicit_diagnostic(self):
        f=self.tick(10.3)
        self.assertFalse(f.threats)
        self.assertTrue(any('observation_gap' in text for _,text in f.events))
    def test_real_missed_cast_timestamps_with_modelled_blink_reach_choice(self):
        captures=json.loads((ROOT/'tests/fixtures/alpha6_manual_casts.json').read_text())['missed_casts']
        self.assertEqual(len(captures),3)
        for c in captures:
            # Timestamps are real; positions, Blink, readiness and polls are modeled.
            h=MemoryHarness();h.f.enemy=replace(h.f.enemy,pos=(1100.,0.))
            h.f.field('C_DOTABaseAbility','m_flOverrideCastPoint','<f',-1.)
            start=c['begin']['at'];end=c['release']['at']
            h.frame(start-.04,0,start=c['begin']['start']-10)
            selected=None
            for i in range(50):
                now=start+i*.04
                if i==20:h.f.me=replace(h.f.me,pos=(600.,0.))
                f=h.frame(now,1,start=c['begin']['start'],token=0x90000)
                me=h.heroes[0]['_ks']
                d=choose_defense(me,[u['_ks'] for u in h.heroes],f.threats,now,(1920,1080),settings())
                if d:selected=selected or now
            self.assertIsNotNone(selected);self.assertLess(selected,end)

    def test_torn_modifier_after_launch_still_drops_old_flight(self):
        for i in range(47):self.tick(10.08+i*.04)
        self.assertTrue(self.h.frame(12.,0,10,102.,0x90000).threats)
        self.h.error='modifier vector changed during read'
        self.assertFalse(self.h.frame(12.05,0,10,102.,0x90000).threats)
        self.h.error=None
        self.assertFalse(self.h.frame(12.09,0,10,102.,0x90000).threats)

    def test_reader_to_real_sender_once_after_blink_under_held_qr_item(self):
        from test_alpha6 import InputHarness
        f=base.DodgeServiceTests();f.setUp();self.addCleanup(f.tearDown)
        real=InputHarness();real.f.keys={81,82,67};f.ks.backend=real.b;f.d.enabled=True
        h=MemoryHarness();h.f.enemy=replace(h.f.enemy,pos=(1100.,0.))
        h.f.field('C_DOTABaseAbility','m_flOverrideCastPoint','<f',-1.)
        h.frame(9.96,0,start=50.)
        for i in range(55):
            now=10+i*.04;phase=i<50
            if i==20:h.f.me=replace(h.f.me,pos=(600.,0.))
            h.f.me=replace(h.f.me,busy=True)
            if real.events():h.f.me=replace(h.f.me,actions=(replace(h.f.me.actions[0],cooldown=3.),))
            h.error='modifier changed during read' if i in (25,26) else None
            h.frame(now,int(phase),0 if phase else 10,100. if phase else 102.,0x90000)
            h.heroes[0]['_ks_busy_reason']='antimage_blink';h.heroes[0]['_ks_busy_verified']=True
            with patch('ks_service.time.perf_counter',return_value=now):f.ks.tick(h.heroes,0.)
        self.assertEqual(real.events().count((69,False)),1)
        self.assertEqual(real.f.keys,{81,82,67});self.assertIsNone(f.d.pending)


class ActivityReaderTests(unittest.TestCase):
    def setUp(self):
        self.f=reader_base.ReaderTests();self.f.setUp()
    def capture(self,abilities,inventory=Inventory(True),broken=()):
        dot=SimpleNamespace(get_entity=lambda i:(100,'npc_dota_hero_antimage'),curtime=lambda:100.,
                            abilities_of=lambda *args:abilities)
        h=dict(idx=1,ent=100,is_local=True,designer='npc_dota_hero_antimage',alive=True,pos=(0,0),screen=(800,500))
        def values(d,e,c,f,fmt):
            if e in broken:raise ValueError('unreadable activity')
            if f=='m_bInAbilityPhase':return int(e!=100)
            return self.f.fields[f]
        with patch.object(self.f.r,'value',side_effect=values),patch('ks_reader.read_inventory',return_value=inventory):
            return self.f.r.capture(dot,[h])[0]
    def test_non_profile_hero_spell_has_reader_verified_activity(self):
        h=self.capture([dict(name='antimage_blink',ent=300)])
        self.assertTrue(h['_ks'].busy);self.assertTrue(h['_ks_busy_verified'])
        self.assertEqual(h['_ks_active_casts'],(('antimage_blink',300),))
        self.assertFalse(h['_ks'].actions)
    def test_non_profile_active_item_has_reader_verified_activity(self):
        h=self.capture([dict(name='antimage_blink',ent=300)],Inventory(True,((0,1,400,'item_test_active'),)))
        self.assertTrue(h['_ks_busy_verified'])
        self.assertIn(('item_test_active',400),h['_ks_active_casts'])
    def test_unknown_ability_is_not_masked_by_later_known_busy(self):
        h=self.capture([dict(name='unknown',ent=301),dict(name='antimage_blink',ent=300)],broken=(301,))
        self.assertTrue(h['_ks'].busy);self.assertFalse(h['_ks_busy_verified'])
    def test_unknown_item_is_not_masked_by_known_hero_cast(self):
        h=self.capture([dict(name='antimage_blink',ent=300)],Inventory(True,((0,1,400,'item_test_active'),)),broken=(400,))
        self.assertTrue(h['_ks'].busy);self.assertFalse(h['_ks_busy_verified'])
    def test_missing_abilities_do_not_verify_item_only_activity(self):
        h=self.capture([],Inventory(True,((0,1,400,'item_test_active'),)))
        self.assertFalse(h['_ks_busy_verified'])


class BusyServiceTests(unittest.TestCase):
    def setUp(self):
        self.f=base.DodgeServiceTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.d.enabled=True;self.f.me=replace(self.f.me,busy=True)
        self.f.tick()
    def tick(self):
        with patch('ks_service.time.perf_counter',return_value=10.1):self.f.ks.tick(self.f.h,0.)
    def test_verified_non_profile_manual_cast_can_be_interrupted(self):
        h=self.f.h[0];h['_ks_busy_reason']='item_test_active';h['_ks_busy_verified']=True
        self.tick();self.assertEqual(len(self.f.b.calls),1)
    def test_explicit_unknown_cannot_be_overridden_by_legacy_name(self):
        h=self.f.h[0];h['_ks_busy_reason']='lion_finger_of_death';h['_ks_busy_verified']=False
        self.tick();self.assertFalse(self.f.b.calls)
    def test_truthy_string_is_not_verification(self):
        h=self.f.h[0];h['_ks_busy_reason']='lion_finger_of_death';h['_ks_busy_verified']='yes'
        self.tick();self.assertFalse(self.f.b.calls)
