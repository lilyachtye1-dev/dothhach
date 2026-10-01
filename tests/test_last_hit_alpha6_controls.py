"""RMB-only and cursor ownership on synthetic Win32; no claim of live input proof."""
import ctypes as C
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from helpers import ROOT
import test_last_hit as old
import test_last_hit_alpha2 as a2
from ks_key_state import PhysicalModifiers
from last_hit_service import validate
from last_hit_units import candidate_name


class RMBTests(unittest.TestCase):
    def setUp(self):
        self.f=a2.QuickInputTests();self.f.setUp();self.h=self.f.h
        self.h.physical.orders_ready=True
        self.before=(321,222);self.h.f.cursor=self.before;self.after_send=None
        send=self.h.b.u.SendInput
        def simulate(n,packet,size):
            rows=[packet._obj] if hasattr(packet,'_obj') else list(packet)
            limit=n if self.f.f.count is None else self.f.f.count
            for v in rows[:limit]:
                if v.type==0 and v.mi.dwFlags&0x8000:
                    left,top,w,h=(self.f.metrics[k] for k in (76,77,78,79))
                    self.h.f.cursor=(left+int(v.mi.dx*w/65536),top+int(v.mi.dy*h/65536))
            result=send(n,packet,size)
            if self.after_send:self.after_send()
            return result
        self.h.b.u.SendInput=simulate
    def click(self,deny=False,allowed=lambda:True,restore=True):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            result=self.h.b.last_hit_rmb((800,400),self.h.f.ctx,allowed,deny,restore)
            # FIX1 restoration is deferred to a later sender tick, not SendInput.
            self.h.b.flush_last_hit_cursor(10.17,lambda:True)
            return result
    def test_enemy_rmb_and_return_cursor(self):
        self.assertEqual(self.click()[0],'sent')
        self.assertEqual(self.f.f.packets,[[('mouse',0xe001),('mouse',8),('mouse',16)]])
        self.assertEqual(self.h.f.cursor,self.before)
    def test_deny_has_identical_rmb_packet_no_A_or_LMB(self):
        result,note=self.click(True);self.assertEqual(result,'sent');self.assertIn('союзнику',note)
        self.assertEqual(self.f.f.packets,[[('mouse',0xe001),('mouse',8),('mouse',16)]])
        self.assertEqual(self.h.f.cursor,self.before)
    def test_held_attack_and_other_letter_keys_do_not_block_rmb(self):
        self.h.f.keys.update((65,81,87,69,68,70,90,88,67))
        self.assertEqual(self.click()[0],'sent')
        self.assertTrue({65,81,87,69,68,70,90,88,67}<=self.h.f.keys)
    def test_physical_movement_wins_even_if_cursor_returns_to_aim(self):
        self.after_send=lambda:self.h.physical.mouse_event(0x200)
        self.assertEqual(self.click()[0],'sent');self.assertNotEqual(self.h.f.cursor,self.before)
        self.assertFalse(self.h.f.moves)
    def test_physical_movement_to_other_point_is_not_overwritten(self):
        def move():
            self.h.physical.mouse_event(0x200);self.h.f.cursor=(17,29)
        self.after_send=move;self.click();self.assertEqual(self.h.f.cursor,(17,29))
    def test_new_keyboard_order_prevents_cursor_restore(self):
        self.after_send=lambda:self.h.physical.event(81,True)
        self.click();self.assertFalse(self.h.f.moves)
    def test_focus_change_prevents_restore(self):
        self.after_send=lambda:setattr(self.h.f,'focus',False)
        self.click();self.assertFalse(self.h.f.moves)
    def test_latest_user_position_after_permission_is_saved(self):
        def permitted():self.h.f.cursor=(31,41);return True
        self.click(allowed=permitted);self.assertEqual(self.h.f.cursor,(31,41))
    def test_new_command_after_cursor_capture_revokes_before_send(self):
        get=self.h.b.u.GetCursorPos
        def race(point):
            result=get(point);self.h.physical.event(81,True);return result
        self.h.b.u.GetCursorPos=race
        self.assertEqual(self.click()[0],'skipped');self.assertFalse(self.f.f.packets)
    def test_new_motion_after_cursor_capture_revokes_return_not_attack(self):
        get=self.h.b.u.GetCursorPos
        def race(point):
            result=get(point);self.h.physical.mouse_event(0x200);return result
        self.h.b.u.GetCursorPos=race
        self.assertEqual(self.click()[0],'sent');self.assertTrue(self.f.f.packets)
        self.assertFalse(self.h.f.moves)
    def test_modifier_change_after_cursor_capture_revokes_before_send(self):
        get=self.h.b.u.GetCursorPos
        def race(point):
            result=get(point);self.h.physical.event(164,True);return result
        self.h.b.u.GetCursorPos=race
        self.assertEqual(self.click()[0],'skipped');self.assertFalse(self.f.f.packets)
    def test_option_off_keeps_cursor_at_aim(self):
        self.click(restore=False);self.assertNotEqual(self.h.f.cursor,self.before);self.assertFalse(self.h.f.moves)
    def test_missing_motion_observer_fails_before_input(self):
        self.h.physical.orders_ready=False
        self.assertEqual(self.click()[0],'skipped');self.assertFalse(self.f.f.packets)
    def test_partial_rmb_down_gets_release_and_cursor_cleanup(self):
        self.f.f.count=2;self.assertEqual(self.click()[0],'failed')
        self.assertEqual(self.f.f.packets[-1],[('mouse',16)]);self.assertEqual(self.h.f.cursor,self.before)
    def test_revoke_produces_no_move_click_or_restore(self):
        self.assertEqual(self.click(allowed=lambda:False)[0],'skipped')
        self.assertFalse(self.f.f.packets);self.assertFalse(self.h.f.moves)
    def test_held_physical_mouse_not_released_or_duplicated(self):
        for key in (1,2):
            self.h.f.keys.add(key);self.assertEqual(self.click()[0],'skipped')
            self.assertIn(key,self.h.f.keys);self.h.f.keys.discard(key)
        self.assertFalse(self.f.f.packets)
    def test_alt_restored_to_current_physical_state(self):
        self.h.mods({164});self.assertEqual(self.click()[0],'sent')
        self.assertIn(164,self.h.f.keys);self.assertEqual(self.h.f.cursor,self.before)
    def test_modifiers_released_by_user_are_not_repressed(self):
        self.h.mods({164});self.after_send=lambda:self.h.physical.event(164,False)
        self.click();self.assertNotIn(164,self.h.f.keys)
    def test_negative_desktop_origin_restores_correct_coordinates(self):
        self.f.metrics={76:-1920,77:-200,78:3840,79:1280}
        self.assertEqual(self.click()[0],'sent');self.assertEqual(self.h.f.cursor,self.before)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.m=PhysicalModifiers();self.m.alive=self.m.orders_ready=True;self.m.at=10.
    def tokens(self):
        with patch('ks_key_state.time.perf_counter',return_value=10.1):return self.m.activity_token(),self.m.motion_token()
    def test_physical_motion_not_a_manual_command(self):
        old=self.tokens();self.m.mouse_event(0x200)
        self.assertEqual(self.tokens(),(old[0],old[1]+1))
    def test_injected_motion_and_click_change_neither_token(self):
        old=self.tokens();self.m.mouse_event(0x200,True);self.m.mouse_event(0x204,True)
        self.assertEqual(self.tokens(),old)
    def test_stale_mouse_observer_has_no_token(self):
        self.m.at=0;self.assertEqual(self.tokens(),(None,None))


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.f=old.ServiceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.s=self.f.s;self.seq=1;self.calls=[]
        self.f.f.b.activity_token=lambda:self.seq
        self.f.f.b.last_hit_rmb=self.rmb
        self.f.doc.update(rmb_only=True,return_cursor=True,rmb_deny_confirmed=True,click_mode=True)
        self.save()
    def save(self):self.s.path.write_text(json.dumps(self.f.doc))
    def rmb(self,point,context,allowed,deny=False,return_cursor=True):
        if not allowed():return 'skipped','revoked'
        self.calls.append((deny,return_cursor));return 'sent','synthetic RMB'
    def test_service_routes_enemy_to_rmb_not_legacy_sender(self):
        self.f.tick();self.assertEqual(self.calls,[(False,True)]);self.assertFalse(self.f.calls)
    def test_service_routes_deny_to_rmb_not_A_LMB(self):
        self.s.reader.latest=replace(self.s.reader.latest,creeps=(old.unit(team=2),))
        self.f.tick();self.assertEqual(self.calls,[(True,True)]);self.assertFalse(self.f.calls)
    def test_unconfirmed_dota_deny_setting_disables_only_deny(self):
        self.f.doc['rmb_deny_confirmed']=False;self.save()
        self.s.reader.latest=replace(self.s.reader.latest,creeps=(old.unit(team=2),))
        self.f.tick();self.assertFalse(self.calls);self.assertTrue(self.s.helpers)
    def test_enemy_still_allowed_without_deny_confirmation(self):
        self.f.doc['rmb_deny_confirmed']=False;self.save();self.f.tick()
        self.assertEqual(self.calls,[(False,True)])
    def test_cancelled_unlaunched_order_has_no_phantom_missile_or_long_block(self):
        self.f.tick();uid=self.s.pending[0];self.assertIn(uid,self.s.in_flight)
        self.seq+=1;self.f.tick(10.20)
        self.assertIsNone(self.s.pending);self.assertNotIn(uid,self.s.in_flight)
        self.assertLessEqual(self.s.block_until,10.25);self.assertFalse(self.s.fault)
        self.f.tick(10.27);self.assertEqual(len(self.calls),2)
    def test_already_observed_attack_is_not_erased_as_unlaunched(self):
        self.f.tick();uid=self.s.pending[0];old_until=self.s.block_until
        self.s.reader.latest=replace(self.s.reader.latest,hero=replace(self.s.reader.latest.hero,last_attack=10.16))
        self.seq+=1;self.f.tick(10.2)
        self.assertEqual(self.s.block_until,old_until);self.assertIn(uid,self.s.in_flight)
    def test_manual_command_during_verification_revokes_click(self):
        def verify(*args):self.seq+=1;return True
        self.s.reader.verify_target=verify;self.f.tick();self.assertFalse(self.calls)
    def test_release_menu_and_priority_remain_guards(self):
        self.f.tick(blocked=True);self.assertFalse(self.calls)
        self.f.f.b.keys.clear();self.f.tick();self.assertFalse(self.calls)
        self.f.f.b.keys={5};self.f.f.ks.ui_menu_open=True;self.f.tick();self.assertFalse(self.calls)
    def test_helper_only_and_preview_never_send(self):
        self.f.doc['helper_only']=True;self.save();self.f.tick();self.assertFalse(self.calls)
        self.f.doc.update(helper_only=False,revision=2);self.save();self.f.f.ks.settings.auto_cast=False
        self.f.tick(10.3);self.assertFalse(self.calls)
    def test_boolean_options_are_strict(self):
        for key in ('rmb_only','return_cursor','rmb_deny_confirmed'):
            doc=dict(self.f.doc);doc[key]=1
            self.assertFalse(validate(doc,self.f.f.ks.ui_session,100000))
    def test_no_autoattack_setting_is_required_in_controls(self):
        self.assertTrue(validate(self.f.doc,self.f.f.ks.ui_session,100000))
        self.assertNotIn('auto_attack',self.f.doc)


class VocabularyAndMenuTests(unittest.TestCase):
    def test_ability_entities_are_not_lane_discovery_candidates(self):
        for name in ('creep_siege','creep_irresolute','creep_piercing'):self.assertFalse(candidate_name(name))
        for name in ('npc_dota_creep_lane','npc_dota_badguys_siege','npc_dota_creep_goodguys_flagbearer'):
            self.assertTrue(candidate_name(name))
    def test_native_controls_force_rmb_and_migrate_without_resetting_activation(self):
        s=(ROOT/'last_hit_menu.cs').read_text()
        self.assertIn('rmb_only=true',s);self.assertIn('input_version=6',s)
        self.assertIn('p.return_cursor=true;p.rmb_deny_confirmed=false;',s)
        self.assertNotIn('autoattack_never',s);self.assertNotIn('автоатака «Никогда»',s)
        self.assertNotIn('extra.Children.Add(quick)',s)
    def test_startup_banner_no_longer_demands_never_or_A(self):
        s=(ROOT/'start.bat').read_text();self.assertIn('[Last Hit alpha7]',s)
        self.assertNotIn('Auto Attack NEVER',s)


if __name__=='__main__':unittest.main()
