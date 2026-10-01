"""Alpha4 regressions: actual user edge values + SYNTHETIC polling/positions/input.
These tests demonstrate command selection, NOT a Windows/Dota reflection.
"""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
import test_dodger as base
import test_live_stacks as stack_base
from helpers import ROOT,settings
from dodge_assassinate import MARK,EVIDENCE,Mark,MarkedAssassinate,release_clock_matches
from dodge_reader import LaunchTracker
from dodger import choose_defense,Threat
from ks_core import Action,Pending

CAPTURES=json.loads((ROOT/'tests/fixtures/assassinate_alpha3.json').read_text())['casts']


class MemoryHarness:
    def __init__(self):
        self.f=base.ReaderTests();self.f.setUp();f=self.f
        f.me=replace(f.me,name='npc_dota_hero_antimage',actions=(Action('antimage_counterspell',0x70000,4,0,50),))
        f.enemy=replace(f.enemy,name='npc_dota_hero_sniper',idx=7,pos=(1500.,0.))
        self.caster_handle=0x18007;self.token=0
        f.put(f.enemy.entity+16,'<Q',0x80000)
        for i in range(20):f.memory[0x80000+i]=0
        f.put(0x80000,'<Q',f.enemy.entity);f.put(0x80010,'<I',self.caster_handle)
        self.entities={1:(f.me.entity,f.me.name),7:(f.enemy.entity,f.enemy.name),2:(f.ep,f.name)}
        f.dot.get_entity=lambda idx:self.entities.get(idx)
        f.field(f.cls,'m_hTarget','<I',0xffffffff)
        self.extra=[];self.heroes=[];self.error=None;self.scan_count=0

    def scan(self,*args):
        self.scan_count+=1
        if self.error:raise ValueError(self.error)
        return ((MARK,self.token),)

    def frame(self,now,phase,cd=0,start=100.,token=0):
        f=self.f;self.token=token
        f.field('C_DOTABaseAbility','m_bInAbilityPhase','<B',phase)
        f.field('C_DOTABaseAbility','m_fCooldown','<f',cd)
        f.field('C_DOTABaseAbility','m_flCastStartTime','<f',start)
        self.heroes=[dict(is_local=True,_ks=replace(f.me,at=now),_ks_viewport=(1920,1080)),
                     dict(_ks=replace(f.enemy,at=now),abilities=[dict(name=f.name,ent=f.ep)])]+self.extra
        with patch('dodge_reader.time.perf_counter',return_value=now),patch.object(f.combat.modifiers,'instances',side_effect=self.scan):
            result=f.reader.capture(f.dot,self.heroes)
        self.heroes[0]['_dodge']=result
        return result

    def cast(self,begin=10.,duration=2.,start=100.,release_start=None,cd=10.,mark=True):
        self.frame(begin-.04,0,start=50.,token=0)
        end=begin+duration;t=begin
        while t<end-1e-6:
            self.frame(t,1,start=start,token=0x90000 if mark else 0);t+=.04
        return self.frame(end,0,cd,start+duration if release_start is None else release_start,0x90000 if mark else 0)


class MarkReaderTests(unittest.TestCase):
    def setUp(self):self.h=MemoryHarness()

    def test_invalid_handle_real_positive_end_clock_creates_mark_threat(self):
        frame=self.h.cast()
        self.assertTrue(frame.build_ok);self.assertEqual(len(frame.threats),1)
        self.assertEqual(frame.threats[0].evidence,EVIDENCE)
        self.assertEqual(frame.threats[0].target,self.h.f.me.entity)
        self.assertIn('метка',frame.compact_reason)
        self.assertIn('запусков 1',frame.status)
        self.assertIn('цель — вы 1',frame.status)

    def test_all_four_user_cast_edges_reach_counterspell_selection(self):
        for capture in CAPTURES:
            with self.subTest(start=capture['begin']['start']):
                h=MemoryHarness();b=capture['begin'];r=capture['release']
                self.assertEqual(b['target_raw'],'0xffffffff')
                self.assertTrue(capture['marks'][0]['present'][MARK])
                frame=h.cast(b['at'],r['at']-b['at'],b['start'],r['start'],r['cd'])
                self.assertEqual(len(frame.threats),1)
                me=h.heroes[0]['_ks'];enemy=h.heroes[1]['_ks']
                decision=choose_defense(me,[me,enemy],frame.threats,r['at'],(1920,1080),settings())
                self.assertIsNotNone(decision);self.assertEqual(decision.key,ord('E'))
                self.assertEqual(decision.profile.name,'antimage_counterspell')

    def test_phase_cd_without_local_mark_still_never_authorize(self):
        self.assertFalse(self.h.cast(mark=False).threats)

    def test_stale_mark_present_before_cast_is_not_rebound_to_caster(self):
        self.h.frame(9.96,0,start=50.,token=0x90000)
        self.h.frame(10,1,token=0x90000);self.h.frame(10.04,1,token=0x90000)
        self.assertFalse(self.h.frame(10.08,0,10,100.,0x90000).threats)

    def test_mark_seen_mid_phase_without_baseline_is_not_a_launch(self):
        self.h.frame(10,1,token=0x90000);self.h.frame(10.04,1,token=0x90000)
        self.assertFalse(self.h.frame(10.08,0,10,100.,0x90000).threats)

    def test_cancellation_or_no_cooldown_has_no_threat(self):
        frame=self.h.cast(cd=0)
        self.assertFalse(frame.threats)
        self.assertFalse(self.h.frame(12.13,0,10,102.,0x90000).threats)

    def test_delayed_cooldown_within_grace(self):
        self.assertFalse(self.h.cast(cd=0).threats)
        self.assertEqual(len(self.h.frame(12.06,0,10,102.,0x90000).threats),1)

    def test_unrelated_forward_timestamp_is_rejected(self):
        self.assertFalse(self.h.cast(release_start=150.).threats)

    def test_phase_start_change_cancels_episode(self):
        self.h.frame(9.96,0,start=50.)
        self.h.frame(10,1,token=0x90000)
        self.h.frame(10.04,1,start=101.,token=0x90000)
        self.assertFalse(self.h.frame(10.08,0,10,101.,0x90000).threats)

    def test_marker_replacement_cancels_episode(self):
        self.h.frame(9.96,0,start=50.)
        self.h.frame(10,1,token=0x90000)
        self.h.frame(10.04,1,token=0x91000)
        self.assertFalse(self.h.frame(10.08,0,10,100.,0x91000).threats)

    def test_marker_disappearance_clears_active_flight(self):
        self.assertTrue(self.h.cast().threats)
        self.assertFalse(self.h.frame(12.05,0,9.9,102.,0).threats)

    def test_unknown_modifier_scan_clears_active_flight(self):
        self.h.cast();self.h.error='owner mismatch'
        f=self.h.frame(12.05,0,9.9,102.,0x90000)
        self.assertFalse(f.threats)
        self.assertTrue(any('owner mismatch' in text for _,text in f.events))

    def test_gap_clears_flight_no_reconstruction(self):
        self.h.cast();self.assertFalse(self.h.frame(12.3,0,9.7,102.,0x90000).threats)

    def test_changed_game_clock_after_launch_clears_flight(self):
        self.h.cast();self.assertFalse(self.h.frame(12.05,0,9.9,200.,0x90000).threats)

    def test_second_possible_sniper_blocks_correlation(self):
        self.h.extra=[dict(_ks=replace(self.h.f.enemy,entity=0xa0000,idx=8,visible=False))]
        frame=self.h.cast()
        self.assertFalse(frame.threats);self.assertIn('несколько',frame.compact_reason)

    def test_unreadable_second_caster_blocks_correlation(self):
        self.h.extra=[dict(designer='npc_dota_hero_sniper')]
        self.assertFalse(self.h.cast().threats)

    def test_known_other_target_not_overridden_by_local_mark(self):
        self.h.f.field(self.h.f.cls,'m_hTarget','<I',0x18008)
        self.assertFalse(self.h.cast().threats)

    def test_same_index_wrong_serial_not_overridden(self):
        self.h.f.field(self.h.f.cls,'m_hTarget','<I',self.h.f.handle+0x8000)
        self.assertFalse(self.h.cast().threats)

    def test_local_identity_backlink_required(self):
        self.h.f.put(0x60000,'<Q',0xdead000)
        self.assertFalse(self.h.cast().threats)

    def test_caster_serial_change_drops_episode(self):
        self.h.frame(9.96,0,start=50.)
        self.h.frame(10,1,token=0x90000);self.h.frame(10.04,1,token=0x90000)
        self.h.f.put(0x80010,'<I',self.h.caster_handle+0x8000)
        self.assertFalse(self.h.frame(10.08,0,10,100.,0x90000).threats)

    def test_ability_serial_change_drops_episode(self):
        self.h.frame(9.96,0,start=50.)
        self.h.frame(10,1,token=0x90000);self.h.frame(10.04,1,token=0x90000)
        self.h.f.put(0x50010,'<I',self.h.f.ah+0x8000)
        self.assertFalse(self.h.frame(10.08,0,10,100.,0x90000).threats)

    def test_positive_cached_mark_keeps_original_timestamp(self):
        self.h.frame(9.96,0,start=50.)
        self.h.frame(10,1,token=0x90000)
        stamp=self.h.f.reader.mark_cache.at;count=self.h.scan_count
        self.h.frame(10.01,1,token=0x90000)
        self.assertEqual(self.h.f.reader.mark_cache.at,stamp)
        self.assertEqual(self.h.scan_count,count)

    def test_hidden_source_clears_mark_episode(self):
        self.h.cast();self.h.f.enemy=replace(self.h.f.enemy,visible=False)
        self.assertFalse(self.h.frame(12.04,0,9.9,102.,0x90000).threats)

    def test_local_teleport_clears_mark_episode(self):
        self.h.cast();self.h.f.me=replace(self.h.f.me,pos=(3000.,3000.))
        self.assertFalse(self.h.frame(12.04,0,9.9,102.,0x90000).threats)

    def test_invalid_instance_pointer_rejected(self):
        self.h.frame(9.96,0,start=50.)
        self.h.frame(10,1,token=1);self.h.frame(10.04,1,token=1)
        self.assertFalse(self.h.frame(10.08,0,10,100.,1).threats)

    def test_cached_mark_age_not_rejuvenated_in_threat(self):
        self.h.cast();f=self.h.frame(12.01,0,9.9,102.,0x90000)
        self.assertEqual(f.threats[0].observed,12.)

    def test_torn_phase_during_mark_scan_skips_frame_then_recovers(self):
        self.h.frame(9.96,0,start=50.)
        for i in range(49):self.h.frame(10+i*.04,1,token=0x90000)
        original=self.h.scan
        def torn(*args):
            self.h.f.field('C_DOTABaseAbility','m_bInAbilityPhase','<B',0)
            self.h.f.field('C_DOTABaseAbility','m_flCastStartTime','<f',102.)
            return original(*args)
        self.h.scan=torn
        self.assertFalse(self.h.frame(11.98,1,token=0x90000).threats)
        self.h.scan=original
        self.assertEqual(len(self.h.frame(12.02,0,10,102.,0x90000).threats),1)

    def test_mark_can_arrive_one_poll_before_phase(self):
        self.h.frame(9.92,0,start=50.)
        self.h.frame(9.96,0,start=50.,token=0x90000)
        self.h.frame(10,1,token=0x90000);self.h.frame(10.04,1,token=0x90000)
        self.assertEqual(len(self.h.frame(10.08,0,10,100.,0x90000).threats),1)

    def test_mark_too_far_before_phase_is_not_reassigned(self):
        self.h.frame(9.72,0,start=50.)
        for i in range(6):self.h.frame(9.76+i*.04,0,start=50.,token=0x90000)
        self.h.frame(10,1,token=0x90000);self.h.frame(10.04,1,token=0x90000)
        self.assertFalse(self.h.frame(10.08,0,10,100.,0x90000).threats)

    def test_mark_can_arrive_after_phase_within_bound(self):
        self.h.frame(9.96,0,start=50.)
        self.h.frame(10,1,token=0);self.h.frame(10.06,1,token=0x90000)
        self.h.frame(10.1,1,token=0x90000)
        self.assertEqual(len(self.h.frame(10.14,0,10,100.,0x90000).threats),1)

    def test_invalid_build_does_not_read_mark_or_send(self):
        self.h.f.reader.check_build=lambda dot:False
        self.assertFalse(self.h.cast().build_ok);self.assertEqual(self.h.scan_count,0)


class MarkServiceTests(unittest.TestCase):
    def setUp(self):
        self.service=base.DodgeServiceTests();self.service.setUp();self.addCleanup(self.service.tearDown)
        self.h=MemoryHarness();self.ks=self.service.ks
        self.ks.dodger.enabled=True

    def run_cast(self,preview=False,manual=False):
        self.ks.settings.auto_cast=not preview
        if manual:self.service.b.keys.add(2)
        self.h.frame(9.96,0,start=50.)
        for i in range(51):
            now=10+i*.04;phase=i<50
            f=self.h.frame(now,int(phase),0 if phase else 10,100. if phase else 102.,0x90000)
            with patch('dodge_service.time.perf_counter',return_value=now):self.ks.tick(self.h.heroes,0)
        return f

    def test_reader_to_sender_counterspell_sent_exactly_once(self):
        self.run_cast()
        self.assertEqual(len(self.service.b.calls),1)
        self.assertEqual(self.service.b.calls[0].key,ord('E'))
        self.assertEqual(self.service.b.calls[0].action.name,'antimage_counterspell')
        self.assertEqual(self.ks.dodger.last_action,'SENT · Counterspell')
        for i in range(1,8):
            now=12+i*.04;self.h.frame(now,0,10-i*.04,102.,0x90000)
            with patch('dodge_service.time.perf_counter',return_value=now):self.ks.tick(self.h.heroes,0)
        self.assertEqual(len(self.service.b.calls),1)

    def test_preview_selects_counterspell_without_input(self):
        self.run_cast(preview=True)
        self.assertFalse(self.service.b.calls)
        self.assertIn('PREVIEW · Counterspell',self.ks.dodger.last_action)

    def test_mouse_input_no_longer_blocks_counterspell(self):
        self.run_cast(manual=True)
        self.assertEqual(len(self.service.b.calls),1)
        self.assertEqual(self.service.b.calls[0].key,ord('E'))

    def test_other_ability_key_no_longer_blocks_counterspell(self):
        self.service.b.keys.add(ord('R'));self.run_cast()
        self.assertEqual(len(self.service.b.calls),1);self.assertEqual(self.service.b.calls[0].key,ord('E'))

    def test_disabled_counterspell_still_blocks(self):
        self.ks.dodger.disabled.add('antimage_counterspell');self.run_cast()
        self.assertFalse(self.service.b.calls)

    def test_pending_ks_command_blocks_second_sender(self):
        self.ks.ledger.hero=self.h.f.me.entity
        self.ks.ledger.pending=Pending(Action('item_dagon',0xa0000,1,0,100,0),9.9,15.,15.,False)
        self.ks.ledger.next_send=15.;self.run_cast()
        self.assertFalse(self.service.b.calls)

    def test_focus_loss_still_disarms(self):
        self.service.b.focus=False;self.run_cast()
        self.assertFalse(self.service.b.calls);self.assertFalse(self.ks.dodger.enabled)

    def test_proof_expiring_between_plan_and_input_is_rechecked(self):
        frame=self.h.cast()
        frame=replace(frame,threats=(replace(frame.threats[0],observed=11.94),))
        self.h.heroes[0]['_dodge']=frame
        original=self.service.b.cast
        def delayed(command,settings,context,armed):
            with patch('dodge_service.time.perf_counter',return_value=12.02):
                return original(command,settings,context,armed)
        self.service.b.cast=delayed
        with patch('dodge_service.time.perf_counter',return_value=12.):self.ks.tick(self.h.heroes,0)
        self.assertFalse(self.service.b.calls)
        self.assertIn('ввод пропущен',self.ks.dodger.status)

    def test_missing_mana_not_overridden(self):
        self.h.f.me=replace(self.h.f.me,mana=0);self.run_cast()
        self.assertFalse(self.service.b.calls)


class DirectClockTests(unittest.TestCase):
    def test_positive_clock_change_requires_observed_phase_begin(self):
        self.assertTrue(release_clock_matches(5345.8335,5347.8335,10,12.005))
        self.assertFalse(release_clock_matches(5345.8335,5347.8335,None,12.005))
        self.assertFalse(release_clock_matches(5345.8335,5349.8335,10,12.005))

    def test_direct_handle_path_also_accepts_matching_release_clock(self):
        # Exercise production tracker with an explicitly observed idle phase.
        from helpers import fixture
        me,enemy=fixture();tracker=LaunchTracker()
        s=dict(caster=enemy.entity,entity=300,name='sniper_assassinate',target=me.entity,
               handle=0x18001,phase=False,cd=0.,start=50.,pos=(1500.,0.),ability_handle=0x28002)
        tracker.update([s],me,9.96)
        for i in range(50):tracker.update([dict(s,phase=True,start=100.)],me,10+i*.04)
        self.assertEqual(len(tracker.update([dict(s,phase=False,start=102.,cd=10.)],me,12)),1)

    def test_mark_evidence_has_tighter_freshness_than_regular_frame(self):
        h=MemoryHarness();frame=h.cast()
        me=replace(h.f.me,at=12.08)
        self.assertIsNone(choose_defense(me,[me,h.f.enemy],frame.threats,12.08,(1920,1080),settings()))

    def test_direct_flight_drops_when_ability_serial_changes(self):
        from helpers import fixture
        me,enemy=fixture();tracker=LaunchTracker()
        s=dict(caster=enemy.entity,entity=300,name='sniper_assassinate',target=me.entity,
               handle=0x18001,phase=True,cd=0.,start=100.,pos=(1500.,0.),ability_handle=0x28002)
        tracker.update([s],me,10);tracker.update([s],me,10.04)
        self.assertTrue(tracker.update([dict(s,phase=False,cd=10.)],me,10.08))
        self.assertFalse(tracker.update([dict(s,phase=False,cd=9.9,ability_handle=0x30002)],me,10.12))

    def test_shackleshot_cannot_use_mark_evidence(self):
        from helpers import fixture
        me,enemy=fixture();me=replace(me,actions=(Action('item_manta',300,1,0,125,2),))
        t=Threat((1,),enemy.entity,me.entity,'windrunner_shackleshot',10.5,11,10,evidence=EVIDENCE)
        self.assertIsNone(choose_defense(me,[me,enemy],[t],10,(1920,1080),settings()))


class ModifierTokenTests(unittest.TestCase):
    def test_instances_reuses_validated_owner_vector_not_stack_count(self):
        from types import SimpleNamespace
        f=stack_base.ReplayTests();f.setUp()
        probe=SimpleNamespace(image=f.sample['client'],read=f.mem.rb,runtime_type=lambda p:f.mem.types.get(p))
        with patch('ks_modifiers.Probe',return_value=probe):
            result=dict(f.reader.instances(f.mem,f.hero,{stack_base.COUNTER,MARK}))
        self.assertEqual(result[stack_base.COUNTER],f.counter);self.assertEqual(result[MARK],0)


if __name__=='__main__':unittest.main()
