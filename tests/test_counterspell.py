"""Alpha5: real WinInput guard with fake user32; service/recovery synthetic frames.
No Windows/Dota execution. Recorded alpha4 SENT/CD timestamps are log facts only.
"""
import json
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from helpers import ROOT
from ks_core import Action
from dodge_input import COUNTERSPELL as CS
from dodge_recovery import CounterspellRecovery
from dodger import Threat
import test_dodger as base
import test_reader_input as inputs


class BackendPolicyTests(unittest.TestCase):
    def setUp(self):
        self.f=inputs.InputTests();self.f.setUp()
        self.a=Action(CS,400,4,0,50)
        self.me=replace(self.f.me,name='npc_dota_hero_antimage',actions=(self.a,))
        self.d=SimpleNamespace(target=self.me,action=self.a,key=69,profile=SimpleNamespace(needs_cursor=False,cast='point'))
        self.f.s.counterspell_mouse_passthrough=True
        self.f.s.counterspell_block_keys=tuple(map(ord,'QWERDFZXCVBN'))+(115,)
    def test_lmb_rmb_are_not_pressed_released_or_moved_by_sender(self):
        for keys in ({1},{2},{1,2}):
            with self.subTest(keys=keys):
                self.f.keys=set(keys);self.f.sent.clear()
                self.assertEqual(self.f.cast(self.d)[0],'sent')
                self.assertEqual(self.f.sent,[[(1,69,0),(1,69,2)]])
                self.assertEqual(self.f.keys,keys);self.assertFalse(self.f.moves)
    def test_no_opt_in_keeps_old_mouse_guard(self):
        self.f.s.counterspell_mouse_passthrough=False;self.f.keys={2}
        self.assertEqual(self.f.cast(self.d)[0],'skipped');self.assertFalse(self.f.sent)
    def test_exception_is_not_a_generic_no_target_bypass(self):
        self.f.keys={2}
        for name in ('item_manta','zuus_thundergods_wrath'):
            self.d.action=replace(self.a,name=name)
            self.assertEqual(self.f.cast(self.d)[0],'skipped')
        self.assertFalse(self.f.sent)
    def test_wrong_hero_target_key_or_cursor_cannot_opt_in(self):
        self.f.keys={2}
        for me in (replace(self.me,local=False),replace(self.me,name='npc_dota_hero_lion'),
                   replace(self.me,alive=False),replace(self.me,actions=())):
            self.d.target=me;self.assertEqual(self.f.cast(self.d)[0],'skipped')
        self.d.target=self.me;self.d.key=82
        self.assertEqual(self.f.cast(self.d)[0],'skipped')
        self.d.key=69;self.d.profile.needs_cursor=True
        self.assertEqual(self.f.cast(self.d)[0],'skipped');self.assertFalse(self.f.moves)
    def test_modifiers_and_hard_stops_remain_blockers(self):
        for key in (9,13,16,17,18,35,45,91,92):
            with self.subTest(key=key):
                self.f.keys={2,key};self.assertEqual(self.f.cast(self.d)[0],'skipped')
        self.assertFalse(self.f.sent)
    def test_held_e_is_never_released_by_automation(self):
        self.f.keys={2,69};self.assertEqual(self.f.cast(self.d)[0],'skipped')
        self.assertEqual(self.f.keys,{2,69});self.assertFalse(self.f.sent)
    def test_other_spell_and_custom_item_keys_block(self):
        for key in (81,82,90,115):
            self.f.keys={2,key};self.assertEqual(self.f.cast(self.d)[0],'skipped')
        self.assertFalse(self.f.sent)
    def test_focus_armed_and_stale_guards_remain(self):
        self.f.keys={2};self.f.focus=False
        self.assertEqual(self.f.cast(self.d)[0],'skipped')
        self.f.focus=True;self.f.armed=False
        self.assertEqual(self.f.cast(self.d)[0],'skipped')
        self.f.armed=True;self.d.target=replace(self.me,at=9.)
        self.assertEqual(self.f.cast(self.d)[0],'skipped');self.assertFalse(self.f.sent)
    def test_mouse_arriving_between_guards_is_allowed(self):
        def context():self.f.keys.add(2);return self.f.ctx
        self.f.b.context=context
        self.assertEqual(self.f.cast(self.d)[0],'sent');self.assertFalse(self.f.moves)
    def test_modifier_arriving_between_guards_is_blocked(self):
        def context():self.f.keys.add(16);return self.f.ctx
        self.f.b.context=context
        self.assertEqual(self.f.cast(self.d)[0],'skipped');self.assertFalse(self.f.sent)
    def test_partial_send_is_failure_with_compensating_keyup_only(self):
        self.f.keys={2};self.f.partial=True
        self.assertEqual(self.f.cast(self.d)[0],'failed')
        self.assertEqual(self.f.sent[-1],[(1,69,2)]);self.assertEqual(self.f.keys,{2})


class CounterspellServiceTests(unittest.TestCase):
    def setUp(self):
        self.f=base.DodgeServiceTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.me=replace(self.f.me,name='npc_dota_hero_antimage',actions=(Action(CS,401,4,0,50),))
        self.f.t=replace(self.f.t,latest=11.2)
        self.d=self.f.d;self.b=self.f.b;self.d.enabled=True
    def new_threat(self,t,identity):
        self.f.t=replace(self.f.t,id=(identity,2,3),earliest=t+.7,latest=t+1.1)
    def samples(self,start,end):
        for i in range(round((end-start)*10)+1):self.f.tick(round(start+i*.1,3))
    def miss_once(self):
        self.f.tick();self.samples(11.7,12.3)
        self.assertIn((CS,401),self.d.quarantined)
    def miss_twice(self):
        self.miss_once();self.new_threat(12.4,2);self.f.tick(12.4)
        self.samples(14.,14.6)
        self.assertEqual(len(self.b.calls),2);self.assertEqual(self.d.recovery.failures,2)
    def test_mouse_held_allows_e(self):
        self.b.keys={1,2};self.f.tick()
        self.assertEqual([x.key for x in self.b.calls],[69])
    def test_end_to_end_service_real_backend_guard(self):
        real=inputs.InputTests();real.setUp();real.keys={1,2}
        self.f.ks.backend=real.b;self.f.ks.toggle.enabled=True;self.f.tick()
        self.assertEqual(real.sent,[[(1,69,0),(1,69,2)]]);self.assertFalse(real.moves)
        self.f.tick(10.12);self.assertEqual(len(real.sent),1)
    def test_residual_mouse_pause_does_not_delay_e(self):
        self.b.keys={2};self.f.tick(9.8);self.assertFalse(self.b.calls)
        self.b.keys.clear();self.f.tick(10.)
        self.assertEqual(self.d.pause_until,0.);self.assertEqual(len(self.b.calls),1)
    def test_other_keyboard_key_has_no_residual_pause(self):
        self.b.keys={82};self.f.tick(10.)
        self.b.keys.clear();self.f.tick(10.1);self.assertEqual(len(self.b.calls),1)
        self.assertEqual(self.b.calls[0].key,69)
    def test_mouse_allows_other_defenses_when_e_on_cd(self):
        self.b.keys={2};self.f.me=replace(self.f.me,actions=(replace(self.f.me.actions[0],cooldown=2),Action('item_manta',400,1,0,125,2)))
        self.f.tick();self.assertEqual(len(self.b.calls),1);self.assertEqual(self.b.calls[0].action.name,'item_manta')
    def test_disabled_e_does_not_enable_mouse_exception(self):
        self.d.disabled.add(CS);self.b.keys={2};self.f.tick();self.assertFalse(self.b.calls)
    def test_busy_hero_still_blocks(self):
        self.f.me=replace(self.f.me,busy=True);self.b.keys={2};self.f.tick()
        self.assertFalse(self.b.calls);self.assertIn('кастует',self.d.status)
    def test_arm_request_can_complete_during_mouse_play(self):
        with patch('dodge_service.time.perf_counter',return_value=10.):self.d.control(True,None,False)
        self.b.keys={2};self.f.tick();self.assertTrue(self.d.enabled);self.assertEqual(len(self.b.calls),1)
    def test_pending_ks_command_still_blocks(self):
        self.b.keys={2};self.f.ks.ledger.next_send=12.;self.f.tick();self.assertFalse(self.b.calls)
    def test_off_is_not_auto_rearmed_by_mouse_exception(self):
        self.d.enabled=False;self.b.keys={2};self.f.tick();self.assertFalse(self.b.calls)
    def test_no_cd_is_visible_and_does_not_repeat_old_threat(self):
        self.miss_once();self.assertEqual(self.d.last_action,'Нет CD · Counterspell')
        row=next(r for r in self.d.dashboard(self.f.h)['sources'] if r['name']==CS)
        self.assertEqual(row['status'],'quarantined');self.assertFalse(row['ready'])
        self.d.used.clear();self.new_threat(12.4,1);self.f.tick(12.4)
        self.assertEqual(len(self.b.calls),1);self.assertIn((CS,401),self.d.quarantined)
    def test_first_miss_recovers_on_new_threat_and_sends_once(self):
        self.miss_once();self.new_threat(12.4,2);self.b.keys={2};self.f.tick(12.4)
        self.f.tick(12.42);self.f.tick(12.44)
        self.assertEqual(len(self.b.calls),2);self.assertNotIn((CS,401),self.d.quarantined)
        self.assertEqual(self.d.recovery.failures,1)  # Recovery is NOT confirmation.
    def test_two_misses_latch_instead_of_repeated_attempts(self):
        self.miss_twice();self.new_threat(14.7,3);self.f.tick(14.7)
        self.assertEqual(len(self.b.calls),2);self.assertIn('Две попытки без CD',self.d.status)
    def test_explicit_menu_rearm_allows_next_new_threat_after_two_misses(self):
        self.miss_twice()
        with patch('dodge_service.time.perf_counter',return_value=14.65):self.d.control(True,None,False)
        self.new_threat(14.7,3);self.f.tick(14.7)
        self.assertEqual(len(self.b.calls),3);self.assertEqual(self.d.recovery.failures,0)
    def test_partial_failure_does_not_use_automatic_or_menu_reset_recovery(self):
        self.b.result='failed';self.miss_once();self.assertFalse(self.d.enabled)
        with patch('dodge_service.time.perf_counter',return_value=12.35):self.d.control(True,None,False)
        self.b.result='sent';self.new_threat(12.4,2);self.f.tick(12.4)
        self.assertEqual(len(self.b.calls),1);self.assertIn((CS,401),self.d.quarantined)
    def test_switching_heroes_does_not_erase_partial_input_quarantine(self):
        self.b.result='failed';self.f.tick()
        old=self.f.me
        self.f.me=replace(old,entity=101,actions=(replace(old.actions[0],entity=402),))
        self.f.tick(10.2);self.assertIsNotNone(self.d.pending)
        self.samples(11.7,12.3)
        self.assertIn((CS,401),self.d.quarantined)
        self.assertEqual(self.d.last_event_at,0.)  # Old hero feedback stays in the log.
        self.f.me=old;self.f.tick(12.5)
        with patch('dodge_service.time.perf_counter',return_value=12.6):self.d.control(True,None,False)
        self.samples(12.6,13.2);self.new_threat(13.3,3);self.f.tick(13.3)
        self.assertEqual(len(self.b.calls),1);self.assertIn((CS,401),self.d.quarantined)
    def test_fresh_cooldown_resets_consecutive_failure_budget(self):
        self.miss_once()
        self.d.sync(replace(self.f.me,at=12.31,actions=(replace(self.f.me.actions[0],cooldown=1),)),12.31)
        self.assertNotIn((CS,401),self.d.quarantined);self.assertEqual(self.d.recovery.failures,0)
        self.new_threat(12.4,2);self.f.tick(12.4);self.samples(14.,14.6)
        self.assertEqual(self.d.recovery.failures,1)
    def test_stale_cooldown_cannot_lift_quarantine(self):
        self.miss_once()
        self.d.sync(replace(self.f.me,at=10.,actions=(replace(self.f.me.actions[0],cooldown=1),)),12.4)
        self.assertIn((CS,401),self.d.quarantined);self.assertEqual(self.d.recovery.failures,1)
    def test_disabled_e_cannot_recover_implicitly(self):
        self.miss_once();self.d.disabled.add(CS);self.new_threat(12.4,2);self.f.tick(12.4)
        self.assertIn((CS,401),self.d.quarantined);self.assertEqual(len(self.b.calls),1)
    def test_preview_does_not_spend_next_attempt(self):
        self.miss_once();self.f.ks.settings.auto_cast=False;self.new_threat(12.4,2);self.f.tick(12.4)
        self.assertEqual(len(self.b.calls),1);self.assertIsNone(self.d.pending)
        self.assertEqual(self.d.recovery.failures,1);self.assertIn('PREVIEW',self.d.status)
    def test_confirmed_attempt_then_recorded_missing_cd_sequence(self):
        data=json.loads((ROOT/'tests/fixtures/counterspell_alpha4.json').read_text())
        self.f.tick()
        first=data['events'][0]['seconds'];cd=data['events'][1]['seconds']
        t=10.1+cd-first
        self.f.me=replace(self.f.me,actions=(replace(self.f.me.actions[0],cooldown=3),))
        self.f.tick(t);self.assertIsNone(self.d.pending)
        self.assertEqual(self.d.last_action,'CD подтверждён · Counterspell')
        t=10.1+data['events'][2]['seconds']-first
        self.f.me=replace(self.f.me,actions=(replace(self.f.me.actions[0],cooldown=0),))
        self.new_threat(t,2);self.f.tick(t)
        timeout=10.1+data['events'][3]['seconds']-first
        self.f.tick(timeout);self.assertIn((CS,401),self.d.quarantined)
        self.samples(round(timeout+.1,3),round(timeout+.8,3))
        self.new_threat(timeout+.9,3);self.f.tick(timeout+.9)
        self.assertEqual(len(self.b.calls),3)


class RecoveryProofTests(unittest.TestCase):
    def setUp(self):
        self.f=inputs.InputTests();self.f.setUp()
        self.a=Action(CS,401,4,0,50);self.me=replace(self.f.me,name='npc_dota_hero_antimage',actions=(self.a,))
        self.r=CounterspellRecovery();self.r.failure(self.a.identity,100,('old',),11.2,11.7,True)
        self.t=Threat(('new',),200,100,'sniper_assassinate',13.,13.4,12.3,evidence='local_mark_launch')
        for at in (11.7,11.8,11.9,12.,12.1,12.2,12.3):self.r.observe(replace(self.me,at=at),at,.25)
        self.me=replace(self.me,at=12.3)
    def allowed(self,t=None,now=12.3):return self.r.allows(self.me,(t or self.t,),{},now,.25)
    def test_valid_ready_new_proof_allows(self):self.assertTrue(self.allowed())
    def test_same_id_wrong_target_unknown_evidence_or_stale_proof_rejects(self):
        for change in (dict(id=('old',)),dict(target=999),dict(evidence='guess'),
                       dict(observed=12.2),dict(earliest=12.2),dict(latest=float('nan')),
                       dict(name='windrunner_shackleshot')):
            with self.subTest(change=change):self.assertFalse(self.allowed(replace(self.t,**change)))
    def test_used_new_threat_is_not_eligible(self):
        self.assertFalse(self.r.allows(self.me,(self.t,),{self.t.id:99},12.3,.25))
    def test_gap_stale_and_cached_samples_do_not_establish_half_second(self):
        for me in (replace(self.me,at=10),replace(self.me,at=12.8)):
            self.r.observe(me,12.8,.25);self.assertFalse(self.allowed(now=12.8))
        self.r.failure(self.a.identity,100,('old',),11.2,11.7,True)
        for now in (11.7,11.9,12.3):self.r.observe(replace(self.me,at=11.7),now,.25)
        self.assertFalse(self.allowed())
    def test_readiness_loss_resets_stability(self):
        for change in (dict(cooldown=.2),dict(mana=9999),dict(casting=True),dict(hidden=True),dict(activated=False),dict(level=0)):
            with self.subTest(change=change):
                self.r.observe(replace(self.me,actions=(replace(self.a,**change),)),12.3,.25)
                self.assertFalse(self.allowed())
    def test_death_busy_or_other_hero_reset_stability(self):
        for change in (dict(alive=False),dict(busy=True),dict(entity=999)):
            self.r.observe(replace(self.me,**change),12.3,.25);self.assertFalse(self.allowed())
    def test_recovery_does_not_precede_old_flight_end(self):
        self.r.after=13.1;self.assertFalse(self.allowed())
    def test_partial_input_never_allows_even_with_reset(self):
        self.r.safe_sent=False;self.r.request_reset();self.assertFalse(self.allowed())
    def test_initial_arm_does_not_pre_authorize_future_failure_reset(self):
        r=CounterspellRecovery();r.request_reset();self.assertFalse(r.requested)
    def test_different_action_identity_starts_fresh_budget(self):
        self.r.failure((CS,402),100,('next',),14.,14.5,True)
        self.assertEqual(self.r.failures,1);self.assertEqual(self.r.identity,(CS,402))


if __name__=='__main__':unittest.main()
