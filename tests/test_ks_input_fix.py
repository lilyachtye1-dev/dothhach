"""KS priority route and quiet disabled Dodger; synthetic user32, not live Dota."""
import io
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import unittest
import test_alpha6 as alpha6
import test_service as service
import test_dodge_diagnostics as diagnostics
from helpers import ROOT


class KSPriorityTests(unittest.TestCase):
    def setUp(self):
        self.h=alpha6.InputHarness()
        self.h.s.defensive_input=False
        self.h.d=self.h.f.d  # Real KS Decision, not a defensive command.

    def test_mouse_and_other_letters_allow_one_real_ks_key_pair(self):
        self.h.f.keys={1,2,69,81,87,68,70,90,88,67}
        self.assertEqual(self.h.cast()[0],'sent')
        self.assertEqual(self.h.events(),[(82,False),(82,True)])
        self.assertEqual(self.h.f.keys,{1,2,69,81,87,68,70,90,88,67})

    def test_each_modifier_and_all_modifiers_are_restored(self):
        for mods in ({160},{161},{162},{163},{164},{165},{160,161,162,163,164,165}):
            with self.subTest(modifiers=mods):
                self.setUp();self.h.mods(mods);self.h.f.keys.update((1,2,69))
                self.assertEqual(self.h.cast()[0],'sent')
                self.assertEqual(self.h.packets[0],[(k,True) for k in sorted(mods)]+[(82,False),(82,True)])
                self.assertEqual(self.h.f.keys,set(mods)|{1,2,69})
                self.assertEqual(self.h.physical.keys,mods)

    def test_modifier_release_during_cast_is_not_repressed(self):
        self.h.mods({164})
        def release(events):
            if (82,False) in events:self.h.physical.event(164,False)
        self.h.hook=release
        self.assertEqual(self.h.cast()[0],'sent')
        self.assertNotIn((164,False),self.h.events())
        self.assertNotIn(164,self.h.f.keys)

    def test_no_physical_modifier_tracking_skips_without_mutating_keys(self):
        self.h.mods({164});self.h.physical.alive=False
        self.assertEqual(self.h.cast()[0],'skipped')
        self.assertFalse(self.h.events());self.assertEqual(self.h.f.keys,{164})

    def test_own_key_and_guard_keys_never_emit(self):
        for key in (82,13,9,35,45,91,92):
            self.h.f.keys={key}
            self.assertEqual(self.h.cast()[0],'skipped')
            self.assertFalse(self.h.events())

    def test_enter_after_aim_skips_and_can_resume_after_release(self):
        self.h.f.move_hook=lambda:self.h.f.keys.add(13)
        self.assertEqual(self.h.cast()[0],'skipped');self.assertFalse(self.h.events())
        self.h.f.move_hook=None;self.h.f.keys.clear()
        self.assertEqual(self.h.cast()[0],'sent')

    def test_partial_pair_releases_cast_key_and_restores_alt(self):
        self.h.mods({164});self.h.partial=2  # Alt-up + R-down, no R-up.
        self.assertEqual(self.h.cast()[0],'failed')
        self.assertIn((82,True),self.h.events());self.assertNotIn(82,self.h.f.keys)
        self.assertEqual(self.h.f.keys,{164})

    def test_cursor_move_before_final_send_still_refuses(self):
        count=[]
        def armed():
            count.append(1)
            if len(count)==2:self.h.f.cursor=(7,8)
            return True
        with patch('ks_input.time.perf_counter',return_value=10.1):
            result,_=self.h.b.cast(self.h.d,self.h.s,self.h.f.ctx,armed)
        self.assertEqual(result,'skipped');self.assertFalse(self.h.events())

    def test_service_to_actual_sender_with_mouse_alt_and_other_spell(self):
        f=service.ServiceTests();f.setUp();self.addCleanup(f.tearDown)
        self.h.mods({164});self.h.f.keys.update((1,2,69))
        f.ks.backend=self.h.b;f.ks.toggle.enabled=True
        f.tick(10.14)
        self.assertIn('SENT',f.ks.status)
        self.assertEqual(self.h.events().count((82,False)),1)
        self.assertEqual(self.h.f.keys,{164,1,2,69})
        f.tick(10.16)
        self.assertEqual(self.h.events().count((82,False)),1)  # Ledger still blocks repeats.

    def test_busy_hero_still_cannot_be_interrupted_by_ks(self):
        f=service.ServiceTests();f.setUp();self.addCleanup(f.tearDown)
        f.ks.backend=self.h.b;f.ks.toggle.enabled=True
        f.heroes[0]['_ks']=replace(f.heroes[0]['_ks'],busy=True)
        self.h.f.keys.update((1,2,69));f.tick(10.14)
        self.assertFalse(self.h.events());self.assertTrue(f.ks.toggle.enabled)


class QuietDodgerTests(unittest.TestCase):
    def setUp(self):
        self.f=diagnostics.ServiceDiagnosticsTests();self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d=self.f.d;self.ks=self.f.ks;self.heroes=self.f.f.h

    def test_off_prints_once_despite_changing_status_and_heartbeats(self):
        self.d.log_key='';out=io.StringIO()
        with redirect_stdout(out),patch.object(self.ks.log,'info') as log:
            for now in (10.,14.,18.,30.):
                self.d.status='OFF '+str(now)
                with patch('dodge_service.time.perf_counter',return_value=now):self.d.trace(self.heroes,0)
        self.assertEqual(out.getvalue().count('[DODGER'),1)
        self.assertEqual(log.call_count,1)
        self.assertNotIn('reader=',out.getvalue())

    def test_disabled_events_are_not_printed_or_replayed_on_enable(self):
        self.heroes[0]['_dodge']=replace(self.heroes[0]['_dodge'],events=((9,'old'),(10,'old')))
        out=io.StringIO()
        with redirect_stdout(out),patch.object(self.ks.log,'info') as log:
            self.d.trace(self.heroes,0);self.d.enabled=True;self.d.trace(self.heroes,0)
        self.assertNotIn('[DODGER OBS',out.getvalue())
        self.assertFalse(any('[DODGER OBS' in str(c) for c in log.call_args_list))
        self.assertEqual(self.d.event_sequence,10)

    def test_pending_feedback_is_not_muted_when_disabled(self):
        out=io.StringIO()
        with redirect_stdout(out):self.d.feedback('Нет CD',('test',1),10.1,'не подтверждено')
        self.assertIn('Нет CD',out.getvalue())


class NoCameraTests(unittest.TestCase):
    def test_native_build_has_no_camera_dependency(self):
        self.assertNotIn('camera_menu.cs',(ROOT/'build_overlay.bat').read_text())
        self.assertNotIn('CameraPage',(ROOT/'ks_menu.cs').read_text())
        self.assertFalse((ROOT/'camera_worker.py').exists())


if __name__=='__main__':unittest.main()
