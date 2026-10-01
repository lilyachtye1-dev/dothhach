"""Offline regressions: click targeting, owned Stop, damage bounds, target loss."""
import ctypes as C
from dataclasses import replace
import json
import struct
import unittest
from unittest.mock import patch

import test_last_hit as old
import test_last_hit_alpha2 as alpha2
from last_hit_core import Model,Shot,armor_multiplier
from last_hit_orders import OwnedAttack
from ks_key_state import PhysicalModifiers
from last_hit_service import validate


class OwnedOrderTests(unittest.TestCase):
    def plan(self):
        f=old.frame();return OwnedAttack(f.hero.uid,f.creeps[0].uid,4,1,(1,0,0,1920,1080),10.4,12.5,0,10,.2)
    def test_no_stop_just_because_packet_was_sent(self):
        self.assertEqual(self.plan().observe(old.frame(now=10.5),10.5),'wait')
    def test_changed_timestamp_does_not_stop_windup(self):
        p=self.plan();f=old.frame(h=old.hero(last_attack=10.3),now=10.4)
        self.assertEqual(p.observe(f,10.4),'wait');self.assertGreaterEqual(p.due,10.56)
    def test_stop_after_confirmed_activity_and_windup(self):
        p=self.plan();f=old.frame(h=old.hero(last_attack=10.1),now=10.5)
        self.assertEqual(p.observe(f,10.5),'stop')
    def test_one_missing_sample_is_not_dead_target_proof(self):
        p=self.plan();f=old.frame([],now=10.5)
        self.assertEqual(p.observe(f,10.5),'wait');self.assertEqual(p.observe(f,10.51),'wait')
        self.assertEqual(p.observe(old.frame([],now=10.53),10.53),'stop')
    def test_stale_or_incomplete_frame_never_allows_stop(self):
        p=self.plan();f=old.frame(h=old.hero(last_attack=10.1),now=10.5)
        self.assertEqual(p.observe(replace(f,incomplete=True),10.5),'wait')
        self.assertEqual(p.observe(f,10.7),'wait')
    def test_changed_hero_expiry_or_clock_reset_revokes_order(self):
        for f,now in [(old.frame(h=replace(old.hero(),uid=(111,9)),now=10.5),10.5),(old.frame(now=13),13),(old.frame(now=9),10.5)]:
            self.assertEqual(self.plan().observe(f,now),'expired')


class ManualObserverTests(unittest.TestCase):
    def setUp(self):
        self.m=PhysicalModifiers();self.m.alive=self.m.orders_ready=True;self.m.at=10
    def token(self):
        with patch('ks_key_state.time.perf_counter',return_value=10.1):return self.m.activity_token()
    def test_non_modifier_press_changes_generation_not_text_history(self):
        n=self.token();self.m.event(81,True);self.assertEqual(self.token(),n+1)
        self.assertNotIn(81,self.m.keys)
    def test_repeat_and_release_not_new_orders(self):
        self.m.event(81,True);n=self.token();self.m.event(81,True);self.m.event(81,False)
        self.assertEqual(self.token(),n)
    def test_injected_events_and_mouse_motion_not_manual(self):
        n=self.token();self.m.event(65,True,True);self.m.mouse_event(0x204,True);self.m.mouse_event(0x200)
        self.assertEqual(self.token(),n)
    def test_real_mouse_click_and_wheel_cancel_ownership(self):
        n=self.token();self.m.mouse_event(0x204);self.m.mouse_event(0x20a);self.assertEqual(self.token(),n+2)
    def test_missing_or_stale_hook_disables_token(self):
        self.m.orders_ready=False;self.assertIsNone(self.token());self.m.orders_ready=True;self.m.at=0
        self.assertIsNone(self.token())


class ClickAndStopInputTests(unittest.TestCase):
    def setUp(self):
        self.f=alpha2.QuickInputTests();self.f.setUp();self.h=self.f.h
    def click(self,deny=False,allowed=lambda:True):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            return self.h.b.last_hit_target_click((800,400),65,self.h.f.ctx,allowed,deny)
    def test_enemy_is_move_right_click_not_attack_move_key(self):
        self.assertEqual(self.click()[0],'sent')
        self.assertEqual(self.f.f.packets,[[('mouse',0xe001),('mouse',8),('mouse',16)]])
    def test_deny_is_normal_attack_key_plus_left_click(self):
        self.assertEqual(self.click(True)[0],'sent')
        self.assertEqual(self.f.f.packets,[[('mouse',0xe001),('key',65,False),('key',65,True),('mouse',2),('mouse',4)]])
    def test_revocation_before_click_emits_nothing(self):
        self.assertEqual(self.click(allowed=lambda:False)[0],'skipped');self.assertFalse(self.f.f.packets)
    def test_partial_right_button_down_is_released(self):
        self.f.f.count=2;self.assertEqual(self.click()[0],'failed')
        self.assertEqual(self.f.f.packets[-1],[('mouse',16)])
    def test_partial_deny_key_is_released_without_new_click(self):
        self.f.f.count=2;self.assertEqual(self.click(True)[0],'failed')
        self.assertEqual(self.f.f.packets[-1],[('key',65,True)])
    def test_held_manual_right_mouse_not_synthesized_again(self):
        self.h.f.keys.add(2);self.assertEqual(self.click()[0],'skipped');self.assertFalse(self.f.f.packets)
    def test_stop_does_not_move_cursor_or_click(self):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            self.assertEqual(self.h.b.last_hit_stop(83,self.h.f.ctx,lambda:True)[0],'sent')
        self.assertEqual(self.f.f.packets,[[('key',83,False),('key',83,True)]])
        self.assertFalse(self.h.f.moves)
    def test_stop_revocation_and_end_guard(self):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            self.assertEqual(self.h.b.last_hit_stop(83,self.h.f.ctx,lambda:False)[0],'skipped')
            self.h.f.keys.add(35);self.assertEqual(self.h.b.last_hit_stop(83,self.h.f.ctx,lambda:True)[0],'skipped')
        self.assertFalse(self.f.f.packets)


class DamageAndAimTests(unittest.TestCase):
    def test_armor_and_live_hero_damage_change_threshold(self):
        m=Model();h=old.hero(damage_min=86,damage_max=90)
        a=m.choose(old.frame([old.unit(hp=40,armor=0)],h=h))[0]
        b=m.choose(old.frame([old.unit(hp=40,armor=2)],h=h))[0]
        self.assertAlmostEqual(a.damage,83);self.assertAlmostEqual(b.damage,73.785714,places=5)
        c=m.choose(old.frame([old.unit(hp=40,armor=2)],h=replace(h,damage_min=106)))[0]
        self.assertGreater(c.damage,b.damage)
    def test_reserve_is_explicit_not_fabricated_damage(self):
        m=Model();h=old.hero(damage_min=86,damage_max=90);f=old.frame([old.unit(hp=80,armor=0)],h=h)
        self.assertIsNotNone(m.choose(f,damage_reserve=0)[0]);self.assertIsNone(m.choose(f,damage_reserve=8)[0])
        self.assertAlmostEqual(m.watch.damage,76.12)
    def test_already_killable_not_vetoed_only_by_speculative_dps(self):
        m=Model();c=old.unit(hp=20);m.history[c.uid]=[(9.5,500),(9.8,100),(10,20)]
        self.assertIsNone(m.choose(old.frame([c]))[0])
        choice=m.choose(old.frame([c]),allow_current_hp=True)[0]
        self.assertEqual(choice.kind,'last_hit');self.assertIn('риск',choice.reason)
    def test_known_tower_event_still_blocks_expected_dead_target(self):
        m=Model();c=old.unit(hp=20);m.shots=[Shot((3,3),c.uid,10,10.01,10.05,50,70)]
        self.assertIsNone(m.choose(old.frame([c]),allow_current_hp=True)[0])
    def test_negative_armor_keeps_increased_physical_damage(self):
        self.assertGreater(armor_multiplier(-2),1)
    def test_reader_damage_bonus_is_added_once(self):
        f=old.ReaderTests();f.setUp();u=f.read();self.assertEqual(u.damage_bonus,5)
        self.assertEqual((u.damage_min,u.damage_max),(25,30))
    def test_collision_body_aim_preserves_ground_world_position(self):
        f=old.ReaderTests();f.setUp();collision=0xd0000
        f.field('C_BaseEntity','m_pCollision','<Q',collision)
        f.p.put(collision+f.p.classes['CCollisionProperty']['fields']['m_vecMins'],struct.pack('<ffffff',-20,-20,0,20,20,80))
        points=[];f.d.w2s=lambda m,p:points.append(p) or (800,400,1)
        u=f.read();self.assertEqual(u.pos,(100,0,0));self.assertEqual(points[-1],(100,0,40))
        self.assertTrue(f.verify());self.assertEqual(points[-1],(100,0,40))
    def test_missing_collision_uses_geometric_not_damage_fallback(self):
        f=old.ReaderTests();f.setUp();points=[];f.d.w2s=lambda m,p:points.append(p) or (800,400,1)
        f.read();self.assertEqual(points[-1],(100,0,32))


class SingleHitServiceTests(unittest.TestCase):
    def setUp(self):
        self.f=old.ServiceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.s=self.f.s;self.seq=1;self.stops=[]
        self.f.f.b.activity_token=lambda:self.seq
        self.f.f.b.last_hit_target_click=lambda *args,**kwargs:self.f.f.b.last_hit_attack(*args)
        def stop(key,context,allowed):
            if not allowed():return 'skipped','revoked'
            self.stops.append(key);return 'sent','fake stop'
        self.f.f.b.last_hit_stop=stop;self.s.reader.verify_hero=lambda uid:True
        self.f.doc.update(click_mode=True,single_hit=True,stop_key=83,damage_reserve=8)
        self.s.path.write_text(json.dumps(self.f.doc))
    def confirmed(self):
        self.s.reader.latest=replace(self.s.reader.latest,hero=replace(self.s.reader.latest.hero,last_attack=10.2))
    def test_stop_once_after_own_attack_not_on_input_submission(self):
        self.f.tick();self.assertFalse(self.stops);self.confirmed();self.f.tick(10.3)
        self.assertFalse(self.stops);self.f.tick(10.6);self.f.tick(10.7)
        self.assertEqual(self.stops,[83])
    def test_manual_command_revokes_delayed_stop(self):
        self.f.tick();self.confirmed();self.seq+=1;self.f.tick(10.6);self.assertFalse(self.stops)
        self.assertIsNone(self.s.owned)
    def test_manual_cancellation_does_not_later_latch_feedback_fault(self):
        self.f.tick();self.seq+=1;self.f.tick(10.3)
        self.assertIsNone(self.s.pending);self.f.tick(12.4)
        self.assertFalse(self.s.fault);self.assertTrue(self.s.enabled)
    def test_higher_priority_action_revokes_delayed_stop(self):
        self.f.tick();self.confirmed();self.f.tick(10.6,blocked=True);self.f.tick(10.7)
        self.assertFalse(self.stops)
    def test_release_never_sends_late_stop(self):
        self.f.tick();self.confirmed();self.f.f.b.keys.clear();self.f.tick(10.6);self.assertFalse(self.stops)
    def test_busy_manual_cast_revokes_stop(self):
        self.f.tick();self.confirmed()
        for h in self.f.f.heroes:
            if h.get('is_local'):h['_ks']=replace(h['_ks'],busy='manual cast')
        self.f.tick(10.6);self.assertFalse(self.stops);self.assertIsNone(self.s.owned)
    def test_target_loss_does_not_latch_whole_module_off(self):
        self.f.tick();self.s.reader.latest=replace(self.s.reader.latest,creeps=())
        self.f.tick(10.3);self.f.tick(10.4);self.f.tick(10.6);self.f.tick(12.4)
        self.assertTrue(self.s.enabled);self.assertFalse(self.s.fault);self.assertIsNone(self.s.pending)
        self.assertEqual(self.stops,[83])
    def test_target_present_no_feedback_still_faults_not_blind_retry(self):
        self.f.tick();self.f.tick(12.4);self.assertTrue(self.s.fault);self.assertFalse(self.stops)
    def test_missing_observer_cannot_arm_single_hit_transaction(self):
        self.seq=None;self.f.tick();self.assertFalse(self.f.calls)
    def test_hero_identity_change_refuses_stop(self):
        self.f.tick();self.confirmed();self.s.reader.verify_hero=lambda uid:False;self.f.tick(10.6)
        self.assertFalse(self.stops)
    def test_old_revision_cannot_stop_after_bind_change(self):
        self.f.tick();self.confirmed();self.f.doc['revision']=2;self.f.doc['stop_key']=72
        self.s.path.write_text(json.dumps(self.f.doc));self.f.tick(10.6);self.assertFalse(self.stops)
    def test_damage_armor_visible_on_marker(self):
        self.f.tick()
        self.assertEqual(self.s.marker['hero_min'],100);self.assertEqual(self.s.marker['armor'],0)
        self.assertEqual(self.s.marker['reserve'],8)
    def test_strict_optional_control_fields(self):
        for values in ({'single_hit':1},{'click_mode':'yes'},{'damage_reserve':26},{'damage_reserve':True},{'stop_key':13},{'stop_key':65,'single_hit':True}):
            self.assertFalse(validate(old.command(**values),'session',100000))


if __name__=='__main__':unittest.main()
