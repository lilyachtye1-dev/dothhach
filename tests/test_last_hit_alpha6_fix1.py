"""Deferred cursor cleanup, not a claim that Dota acknowledged a command."""
import unittest
from unittest.mock import patch
import test_last_hit_alpha6_controls as controls


class DeferredCursorTests(unittest.TestCase):
    def setUp(self):
        self.f=controls.RMBTests();self.f.setUp();self.b=self.f.h.b
    def send(self):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            return self.b.last_hit_rmb((800,400),self.f.h.f.ctx,lambda:True)
    def flush(self,at,allowed=lambda:True):
        with patch('ks_input.time.perf_counter',return_value=at):
            return self.b.flush_last_hit_cursor(at,allowed)
    def test_send_does_not_immediately_restore(self):
        self.assertEqual(self.send()[0],'sent')
        self.assertNotEqual(self.f.h.f.cursor,self.f.before)
        self.assertFalse(self.f.h.f.moves)
    def test_before_due_retains_aim_without_locking_mouse(self):
        self.send();aim=self.f.h.f.cursor
        self.assertEqual(self.flush(10.13),'waiting');self.assertEqual(self.f.h.f.cursor,aim)
        self.assertFalse(self.f.h.f.moves)
    def test_later_sender_tick_restores_once(self):
        self.send();self.assertEqual(self.flush(10.17),'restored')
        self.assertEqual(self.f.h.f.cursor,self.f.before)
        moves=len(self.f.h.f.moves);self.assertIsNone(self.flush(10.19))
        self.assertEqual(len(self.f.h.f.moves),moves)
    def test_queued_game_can_sample_target_before_restore(self):
        # Alpha6's immediate finally restore fails this model. It is deliberately
        # asynchronous relative to SendInput, like a consumer on a later frame.
        self.send();target=(self.f.h.f.ctx[1]+800,self.f.h.f.ctx[2]+400)
        self.flush(10.12)
        sampled=self.f.h.f.cursor
        self.assertLessEqual(abs(sampled[0]-target[0]),1)
        self.assertLessEqual(abs(sampled[1]-target[1]),1)
        self.flush(10.17);self.assertEqual(self.f.h.f.cursor,self.f.before)
    def test_user_motion_during_delay_cancels_restore(self):
        self.send();self.f.h.physical.mouse_event(0x200);self.f.h.f.cursor=(7,8)
        self.assertEqual(self.flush(10.17),'discarded');self.assertEqual(self.f.h.f.cursor,(7,8))
        self.assertIsNone(self.flush(10.18))
    def test_motion_away_then_back_to_aim_still_revokes(self):
        self.send();self.f.h.physical.mouse_event(0x200)
        self.assertEqual(self.flush(10.17),'discarded');self.assertFalse(self.f.h.f.moves)
    def test_new_keyboard_order_revokes(self):
        self.send();self.f.h.physical.event(81,True)
        self.assertEqual(self.flush(10.17),'discarded');self.assertFalse(self.f.h.f.moves)
    def test_physical_mouse_button_held_revokes(self):
        self.send();self.f.h.f.keys.add(2)
        self.assertEqual(self.flush(10.17),'discarded');self.assertIn(2,self.f.h.f.keys)
    def test_expired_cursor_return_never_runs_late(self):
        self.send();self.assertEqual(self.flush(10.29),'discarded');self.assertFalse(self.f.h.f.moves)
    def test_priority_or_disable_revokes_even_before_due(self):
        self.send();self.assertEqual(self.flush(10.12,lambda:False),'discarded')
        self.assertIsNone(self.flush(10.17));self.assertFalse(self.f.h.f.moves)
    def test_focus_change_revokes(self):
        self.send();self.f.h.f.focus=False
        self.assertEqual(self.flush(10.17),'discarded');self.assertFalse(self.f.h.f.moves)
    def test_getcursor_failure_discards_without_retry(self):
        self.send();self.b.u.GetCursorPos=lambda p:False
        self.assertEqual(self.flush(10.17),'unreadable');self.assertIsNone(self.flush(10.18))
    def test_changed_position_without_motion_token_is_not_overwritten(self):
        self.send();self.f.h.f.cursor=(7,8)
        self.assertEqual(self.flush(10.17),'moved');self.assertEqual(self.f.h.f.cursor,(7,8))
    def test_revocation_during_last_permission_check(self):
        self.send();calls=[]
        def allowed():calls.append(1);return len(calls)==1
        self.assertEqual(self.flush(10.17,allowed),'discarded');self.assertFalse(self.f.h.f.moves)
    def test_expiry_during_final_check_cannot_restore_late(self):
        self.send();clock=[10.17];calls=[]
        def allowed():
            calls.append(1)
            if len(calls)>1:clock[0]=10.30
            return True
        with patch('ks_input.time.perf_counter',side_effect=lambda:clock[0]):
            self.assertEqual(self.b.flush_last_hit_cursor(10.17,allowed),'discarded')
        self.assertFalse(self.f.h.f.moves)
    def test_no_sleep_or_worker_is_created(self):
        with patch('ks_input.time.sleep',side_effect=AssertionError('sender must not sleep')):
            self.send();self.flush(10.12);self.flush(10.17)


class ControllerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.f=controls.ControlTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.s=self.f.s;self.results=[]
        self.f.f.f.b.flush_last_hit_cursor=lambda now,allowed:self.results.append(allowed())
    def test_active_session_can_drain_cleanup(self):
        self.f.f.tick();self.assertEqual(self.results,[True])
    def test_higher_priority_drops_cleanup(self):
        self.f.f.tick(blocked=True);self.assertEqual(self.results,[False])
    def test_release_drops_cleanup(self):
        self.f.f.f.b.keys.clear();self.f.f.tick();self.assertEqual(self.results,[False])
    def test_menu_drops_cleanup(self):
        self.f.f.f.ks.ui_menu_open=True;self.f.f.tick();self.assertEqual(self.results,[False])
    def test_helper_only_drops_cleanup(self):
        self.f.f.doc['helper_only']=True;self.f.save();self.f.f.tick();self.assertEqual(self.results,[False])
    def test_deny_disabled_is_visible_in_pipeline(self):
        self.f.f.doc['rmb_deny_confirmed']=False;self.f.save();self.f.f.tick()
        self.assertIn('ДЕНАЙ OFF',self.s.pipeline)
    def test_rejected_sends_are_aggregated_not_silently_lost(self):
        self.f.f.f.b.last_hit_rmb=lambda *a,**kw:('skipped','synthetic race')
        self.f.f.tick();self.assertEqual(self.s.input_rejects,{'synthetic race':1})
        self.f.f.tick(10.17);self.assertEqual(self.s.input_rejects,{'synthetic race':2})


if __name__=='__main__':unittest.main()
