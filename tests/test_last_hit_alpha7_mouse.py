"""Synthetic Win32 buttons and sender integration; not a live Dota validation."""
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helpers import ROOT
import test_last_hit_alpha6_controls as old
from ks_key_state import PhysicalModifiers
from last_hit_service import validate


class MouseBackendTests(unittest.TestCase):
    def setUp(self):
        self.f=old.RMBTests();self.f.setUp();self.h=self.f.h;self.b=self.h.b;self.m=self.h.physical
        self.m.mouse_ready=True;self.now=10.1
        original=self.b.u.SendInput
        def send(n,packet,size):
            rows=[packet._obj] if hasattr(packet,'_obj') else list(packet)
            limit=n if self.f.f.f.count is None else self.f.f.f.count
            for v in rows[:limit]:
                if v.type!=0:continue
                flags=v.mi.dwFlags
                for mask,key,up in ((2,1,False),(4,1,True),(8,2,False),(16,2,True)):
                    if flags==mask:
                        if up:self.h.f.keys.discard(key)
                        else:self.h.f.keys.add(key)
                        self.m.mouse_event({(1,False):0x201,(1,True):0x202,(2,False):0x204,(2,True):0x205}[key,up],True)
            return original(n,packet,size)
        self.b.u.SendInput=send
    def physical(self,key,down):
        self.m.mouse_event({(1,True):0x201,(1,False):0x202,(2,True):0x204,(2,False):0x205}[key,down])
        if down:self.h.f.keys.add(key)
        else:self.h.f.keys.discard(key)
    def clock(self):
        self.m.at=self.now
        return patch('ks_input.time.perf_counter',return_value=self.now)
    def start(self,seconds=.4,allowed=lambda:True):
        with self.clock():return self.b.last_hit_priority_rmb((800,400),self.h.f.ctx,allowed,seconds)
    def finish(self,reason='done',resume=True):
        with self.clock():return self.b.finish_last_hit_mouse_priority(reason,resume)
    def watch(self):
        with self.clock():return self.b.watch_last_hit_mouse_priority(self.now)
    def test_rmb_is_logically_released_attack_sent_physical_hold_retained(self):
        self.physical(2,True);self.assertEqual(self.start()[0],'sent')
        self.assertIn(2,self.m.buttons);self.assertNotIn(2,self.h.f.keys)
        self.assertEqual(self.f.f.f.packets[:2],[[('mouse',16)],[('mouse',0xe001),('mouse',8),('mouse',16)]])
        self.assertIsNotNone(self.b._lh_mouse_priority)
    def test_held_lmb_and_rmb_are_borrowed_and_restored(self):
        self.physical(1,True);self.physical(2,True);self.assertEqual(self.start()[0],'sent')
        self.assertFalse({1,2}&self.h.f.keys)
        self.now=10.6;w=self.finish()
        self.assertEqual(w.resumed,(1,2));self.assertTrue({1,2}<=self.h.f.keys)
        self.assertEqual(self.h.f.cursor,self.f.before)
    def test_released_button_is_never_repressed(self):
        self.physical(2,True);self.start();self.physical(2,False)
        self.now=10.6;w=self.finish();self.assertEqual(w.resumed,());self.assertNotIn(2,self.h.f.keys)
    def test_one_of_two_buttons_released_only_other_restored(self):
        self.physical(1,True);self.physical(2,True);self.start();self.physical(1,False)
        self.now=10.6;w=self.finish();self.assertEqual(w.resumed,(2,))
    def test_release_repress_is_not_replayed_as_original_gesture(self):
        self.physical(2,True);self.start();self.physical(2,False);self.physical(2,True)
        before=len(self.f.f.f.packets);self.finish();self.assertEqual(len(self.f.f.f.packets),before)
    def test_release_during_resume_is_cleaned_up(self):
        self.physical(2,True);self.start()
        self.f.after_send=lambda:self.physical(2,False)
        self.now=10.6;self.finish();self.assertNotIn(2,self.h.f.keys)
        self.assertEqual(self.f.f.f.packets[-1],[('mouse',16)])
    def test_cursor_returns_after_60ms_while_button_remains_up(self):
        self.physical(2,True);self.start();self.assertNotEqual(self.h.f.cursor,self.f.before)
        self.now=10.14;self.watch();self.assertNotEqual(self.h.f.cursor,self.f.before)
        self.now=10.18;self.watch();self.assertEqual(self.h.f.cursor,self.f.before)
        self.assertNotIn(2,self.h.f.keys)
    def test_cursor_delay_starts_after_slow_permission_read(self):
        self.physical(2,True);calls=[]
        def allow():
            calls.append(1)
            if len(calls)==2:self.now=10.20
            return True
        self.m.at=self.now
        with patch('ks_input.time.perf_counter',side_effect=lambda:self.now):
            self.assertEqual(self.b.last_hit_priority_rmb((800,400),self.h.f.ctx,allow,.4)[0],'sent')
        self.assertAlmostEqual(self.b._lh_mouse_priority.due,10.60)
        self.now=10.22;self.watch();self.assertNotEqual(self.h.f.cursor,self.f.before)
        self.now=10.27;self.watch();self.assertEqual(self.h.f.cursor,self.f.before)
    def test_permission_delay_cannot_overrun_loan_budget(self):
        self.physical(2,True);calls=[]
        def allow():
            calls.append(1)
            if len(calls)==2:self.now=10.20
            return True
        self.m.at=self.now
        with patch('ks_input.time.perf_counter',side_effect=lambda:self.now):
            result=self.b.last_hit_priority_rmb((800,400),self.h.f.ctx,allow,.85)
        self.assertEqual(result[0],'skipped')
        self.assertEqual(self.f.f.f.packets,[[('mouse',16)],[('mouse',8)]])
        self.assertIsNone(self.b._lh_mouse_priority)
    def test_moving_mouse_wins_return_and_hold_resumes_at_current_point(self):
        self.physical(2,True);self.start();self.m.mouse_event(0x200);self.h.f.cursor=(500,450)
        self.now=10.6;self.finish();self.assertEqual(self.h.f.cursor,(500,450));self.assertIn(2,self.h.f.keys)
    def test_current_hud_position_never_gets_synthetic_button_down(self):
        self.physical(2,True);self.start();self.m.mouse_event(0x200);self.h.f.cursor=(100,100)
        self.now=10.6;w=self.finish();self.assertFalse(w.resumed);self.assertNotIn(2,self.h.f.keys)
    def test_window_change_never_replays_over_desktop(self):
        self.physical(2,True);self.start();self.h.f.focus=False;self.now=10.3
        self.watch();self.assertIsNone(self.b._lh_mouse_priority);self.assertNotIn(2,self.h.f.keys)
    def test_end_cancels_without_replaying_mouse(self):
        self.physical(2,True);self.start();self.h.f.keys.add(35);self.now=10.3
        self.watch();self.assertIsNone(self.b._lh_mouse_priority);self.assertNotIn(2,self.h.f.keys)
    def test_keyboard_command_revokes_window_and_restores_physical_hold(self):
        self.physical(2,True);self.start();self.m.event(81,True);self.now=10.3
        self.watch();self.assertIsNone(self.b._lh_mouse_priority);self.assertIn(2,self.h.f.keys)
    def test_deadline_releases_without_waiting_for_a_hit_callback(self):
        self.physical(2,True);self.start();self.now=11.01;self.watch()
        self.assertIsNone(self.b._lh_mouse_priority);self.assertIn(2,self.h.f.keys)
    def test_unknown_physical_mouse_state_never_sends(self):
        self.physical(2,True);self.m.mouse_ready=False
        self.assertEqual(self.start()[0],'skipped');self.assertFalse(self.f.f.f.packets)
    def test_logical_physical_mismatch_never_sends(self):
        self.h.f.keys.add(2);self.assertEqual(self.start()[0],'skipped');self.assertFalse(self.f.f.f.packets)
    def test_too_long_or_nonfinite_window_never_borrows_buttons(self):
        self.physical(2,True)
        for duration in (1.2,float('nan'),float('inf'),-.1):self.assertEqual(self.start(duration)[0],'skipped')
        self.assertFalse(self.f.f.f.packets);self.assertIn(2,self.h.f.keys)
    def test_permission_revoked_before_borrow_sends_nothing(self):
        self.physical(2,True);self.assertEqual(self.start(allowed=lambda:False)[0],'skipped')
        self.assertFalse(self.f.f.f.packets)
    def test_permission_revoked_after_borrow_restores_hold_without_attack(self):
        self.physical(2,True);calls=[]
        def allow():calls.append(1);return len(calls)==1
        self.assertEqual(self.start(allowed=allow)[0],'skipped')
        self.assertEqual(self.f.f.f.packets,[[('mouse',16)],[('mouse',8)]])
        self.assertIn(2,self.h.f.keys);self.assertIsNone(self.b._lh_mouse_priority)
    def test_partial_borrow_fails_closed_and_restores_only_live_state(self):
        self.physical(1,True);self.physical(2,True);self.f.f.f.count=1
        self.assertEqual(self.start()[0],'failed');self.assertTrue({1,2}<=self.h.f.keys)
        self.assertIsNone(self.b._lh_mouse_priority)
    def test_no_hold_uses_normal_rmb_without_priority_loan(self):
        self.assertEqual(self.start()[0],'sent');self.assertIsNone(getattr(self.b,'_lh_mouse_priority',None))
    def test_cannot_open_second_window(self):
        self.physical(2,True);self.start();before=len(self.f.f.f.packets)
        self.assertEqual(self.start()[0],'skipped');self.assertEqual(len(self.f.f.f.packets),before)
    def test_new_killsteal_cancels_loan_before_cast_path(self):
        self.physical(2,True);self.start()
        calls=[]
        with self.clock(),patch.object(self.b,'_cast_priority',side_effect=lambda *a,**kw:calls.append(self.b._lh_mouse_priority) or ('skipped','synthetic')):
            # Real Decision routes to the same priority path as production KS.
            self.h.s.defensive_input=False
            self.b.cast(self.h.f.d,self.h.s,self.h.f.ctx,lambda:True)
        self.assertEqual(calls,[None]);self.assertIn(2,self.h.f.keys)


class PhysicalButtonTests(unittest.TestCase):
    def test_injected_up_does_not_erase_physical_hold(self):
        m=PhysicalModifiers();m.mouse_event(0x204);before=m.button_versions.copy()
        m.mouse_event(0x205,True);self.assertEqual(m.buttons,{2});self.assertEqual(m.button_versions,before)
        m.mouse_event(0x205);self.assertEqual(m.buttons,set());self.assertGreater(m.button_versions[2],before[2])
    def test_freshness_required_for_mouse_snapshot(self):
        m=PhysicalModifiers();m.alive=m.orders_ready=m.mouse_ready=True;m.at=10
        with patch('ks_key_state.time.perf_counter',return_value=10.1):self.assertIsNotNone(m.mouse_snapshot())
        with patch('ks_key_state.time.perf_counter',return_value=10.4):self.assertIsNone(m.mouse_snapshot())
    def test_read_returns_detached_versions(self):
        m=PhysicalModifiers();m.alive=m.orders_ready=m.mouse_ready=True;m.at=10
        with patch('ks_key_state.time.perf_counter',return_value=10.1):_,versions=m.mouse_snapshot()
        versions[2]=999;self.assertEqual(m.button_versions[2],0)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.f=old.ControlTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.s=self.f.s;self.b=self.f.f.f.b;self.ends=[]
        self.f.f.doc.update(mouse_priority=True,single_hit=False);self.f.save()
        self.b.last_hit_priority_rmb=self.start
        self.b.finish_last_hit_mouse_priority=self.finish
        self.b.watch_last_hit_mouse_priority=lambda now:None
        self.s.reader.verify_hero=lambda uid:True
    def start(self,point,context,allowed,seconds,**kw):
        if not allowed():return 'skipped','denied'
        self.b._lh_mouse_priority=SimpleNamespace(held={2},resumed=(),failed=False,reason='',token=self.f.seq,
                    due=10.14+seconds,deadline=11.04)
        return 'sent','synthetic priority'
    def finish(self,reason,resume=True):
        w=self.b._lh_mouse_priority;self.b._lh_mouse_priority=None;w.reason=reason
        w.resumed=(2,) if resume else ();self.ends.append(reason);return w
    def test_priority_option_routes_to_new_backend(self):
        self.f.f.tick();self.assertIsNotNone(self.s.mouse_window);self.assertFalse(self.f.calls)
    def test_no_repeat_attack_while_loan_active(self):
        self.f.f.tick();window=self.s.mouse_window;self.f.f.tick(10.16)
        self.assertIs(self.s.mouse_window,window);self.assertIn('Приоритет',self.s.status)
    def test_estimated_completion_restores_and_revokes_delayed_order(self):
        self.f.f.tick();self.f.f.tick(10.7)
        self.assertIsNone(self.s.mouse_window);self.assertIsNone(self.s.pending);self.assertIsNone(self.s.owned)
        self.assertGreaterEqual(self.s.block_until,10.82)
    def test_deactivation_returns_owned_mouse(self):
        self.f.f.tick();self.f.f.doc.update(enabled=False,revision=2);self.f.save();self.f.f.tick(10.3)
        self.assertIsNone(self.s.mouse_window);self.assertTrue(self.ends)
    def test_higher_priority_returns_mouse_and_revokes_stop(self):
        self.f.f.tick();self.f.f.tick(10.3,blocked=True)
        self.assertIsNone(self.s.mouse_window);self.assertIsNone(self.s.owned)
    def test_menu_never_replays_held_button(self):
        self.f.f.tick();window=self.s.mouse_window
        self.f.f.f.ks.ui_menu_open=True;self.f.f.tick(10.3)
        self.assertEqual(window.resumed,());self.assertIsNone(self.s.mouse_window)
    def test_hero_change_ends_ownership(self):
        self.f.f.tick();self.s.reader.latest=replace(self.s.reader.latest,hero=replace(self.s.reader.latest.hero,uid=(777,88)))
        self.f.f.tick(10.3);self.assertIsNone(self.s.mouse_window)
    def test_option_off_preserves_previous_route(self):
        self.f.f.doc['mouse_priority']=False;self.f.save();self.f.f.tick()
        self.assertEqual(len(self.f.calls),1);self.assertIsNone(self.s.mouse_window)
    def test_preview_never_borrows_mouse(self):
        self.f.f.f.ks.settings.auto_cast=False;self.f.f.tick();self.assertIsNone(self.s.mouse_window)
    def test_option_type_strict(self):
        doc=dict(self.f.f.doc,mouse_priority=1)
        self.assertFalse(validate(doc,self.f.f.f.ks.ui_session,100000))
    def test_native_migration_preserves_deny_confirmation_and_bind(self):
        source=(ROOT/'last_hit_menu.cs').read_text()
        self.assertIn('if(p!=null&&p.input_version<7){p.mouse_priority=true;p.input_version=7;}',source)
        self.assertIn('mouse_priority=lhPrefs.mouse_priority',source)


class CleanupAndNativeTests(unittest.TestCase):
    def test_outer_sender_error_revokes_mouse_without_replay(self):
        from ks_service import KillSteal
        calls=[]
        def fail():raise RuntimeError('synthetic poll failure')
        fake=SimpleNamespace(error='',backend=SimpleNamespace(finish_last_hit_mouse_priority=lambda *a,**k:calls.append((a,k))),
                             poll_ui_controls=fail,toggle=SimpleNamespace(enabled=True),
                             log=SimpleNamespace(exception=lambda *a:None),_log_status=lambda:None)
        KillSteal.tick(fake,[],0)
        self.assertFalse(fake.toggle.enabled)
        self.assertEqual(calls,[(('sender error',),{'resume':False})])

    def test_actual_native_preferences_migration_and_roundtrip(self):
        import shutil,subprocess,tempfile
        from pathlib import Path
        if not shutil.which('mcs') or not shutil.which('mono'):self.skipTest('optional Mono not installed')
        source=(ROOT/'last_hit_menu.cs').read_text()
        prefs=source[source.index('    public class LastHitPreferences'):source.index('    partial class MenuWindow')]
        migration='\n'.join(line.strip() for line in source.splitlines() if 'if(p!=null&&p.input_version<' in line)
        code='using System;using System.Web.Script.Serialization;'+prefs+"""
class Smoke {
 static LastHitPreferences M(string text) {
 var p=new JavaScriptSerializer().Deserialize<LastHitPreferences>(text);
 """+migration+"""
 return p; }
 static void Main() {
 var j=new JavaScriptSerializer();
 var old=new LastHitPreferences{input_version=6,key=6,activation="toggle",rmb_deny_confirmed=true,return_cursor=false,mouse_priority=false,latency_ms=120,damage_reserve=7};
 var p=M(j.Serialize(old));
 if(p.input_version!=7||!p.mouse_priority||p.key!=6||p.activation!="toggle"||!p.rmb_deny_confirmed||p.return_cursor||p.latency_ms!=120||p.damage_reserve!=7)throw new Exception("v6 migration reset existing preferences");
 p.mouse_priority=false;
 p=M(j.Serialize(p));
 if(p.mouse_priority||!p.rmb_deny_confirmed||p.key!=6)throw new Exception("v7 reload reset user choice");
 Console.WriteLine("PASS native preferences migration");
 }}"""
        with tempfile.TemporaryDirectory() as tmp:
            cs=Path(tmp)/'prefs.cs';exe=Path(tmp)/'prefs.exe';cs.write_text(code)
            build=subprocess.run(['mcs','-warnaserror','-langversion:5','-r:System.Web.Extensions','-out:'+str(exe),str(cs)],capture_output=True,text=True,timeout=30)
            self.assertEqual(build.returncode,0,build.stdout+build.stderr)
            run=subprocess.run(['mono',str(exe)],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertIn('PASS native preferences migration',run.stdout)


if __name__=='__main__':unittest.main()
