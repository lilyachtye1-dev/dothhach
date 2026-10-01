"""Quick Attack, planning age/commitment, and actual native marker DTO regressions."""
import ctypes as C
from dataclasses import replace
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from helpers import ROOT
import test_last_hit as old
from last_hit_core import Model
from last_hit_service import validate


class QuickInputTests(unittest.TestCase):
    def setUp(self):
        self.f=old.InputTests();self.f.setUp();self.h=self.f.h
        class Mouse(C.Structure):_fields_=[('dx',C.c_int32),('dy',C.c_int32),('dwFlags',C.c_uint32)]
        class Key(C.Structure):_fields_=[('wVk',C.c_uint16),('dwFlags',C.c_uint32)]
        class Input(C.Structure):_fields_=[('type',C.c_uint32),('ki',Key),('mi',Mouse)]
        self.h.b.INPUT=Input;self.metrics={76:0,77:0,78:1920,79:1080}
        self.h.b.u.GetSystemMetrics=lambda key:self.metrics[key]
        self.points=[];send=self.h.b.u.SendInput
        def capture(n,packet,size):
            if not hasattr(packet,'_obj'):
                self.points.extend((v.mi.dx,v.mi.dy,v.mi.dwFlags) for v in packet if v.type==0)
            return send(n,packet,size)
        self.h.b.u.SendInput=capture
    def cast(self,allowed=None):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            return self.h.b.last_hit_attack((800,400),65,self.h.f.ctx,allowed or (lambda:5 in self.h.f.keys),quick=True)
    def test_one_atomic_move_and_key_packet_without_lmb(self):
        self.assertEqual(self.cast()[0],'sent')
        self.assertEqual(self.f.packets,[[('mouse',0xe001),('key',65,False),('key',65,True)]])
        self.assertFalse(self.h.f.moves);self.assertEqual(self.h.f.keys,{5})
    def test_moving_mouse_during_permission_check_does_not_cancel_quick_attack(self):
        def allowed():self.h.f.cursor=(7,8);return True
        self.assertEqual(self.cast(allowed)[0],'sent');self.assertEqual(len(self.f.packets),1)
    def test_expensive_permission_check_only_once(self):
        calls=[]
        self.assertEqual(self.cast(lambda:calls.append(1) or True)[0],'sent');self.assertEqual(len(calls),1)
    def test_release_or_revocation_before_packet_never_moves_cursor(self):
        self.assertEqual(self.cast(lambda:False)[0],'skipped')
        self.assertFalse(self.f.packets);self.assertFalse(self.h.f.moves)
    def test_negative_virtual_desktop_origin_is_supported(self):
        self.metrics={76:-1920,77:-200,78:3840,79:1280}
        self.assertEqual(self.cast()[0],'sent')
        expected=int((self.h.f.ctx[1]+800+1920+.5)*65536/3840)
        self.assertEqual(self.points[0][0],expected)
    def test_bad_virtual_geometry_fails_closed(self):
        self.metrics[78]=0;self.assertEqual(self.cast()[0],'skipped');self.assertFalse(self.f.packets)
    def test_partial_key_down_released_no_mouse_button_events(self):
        self.f.count=2;self.assertEqual(self.cast()[0],'failed')
        self.assertEqual(self.h.f.keys,{5});self.assertEqual(self.f.packets[-1],[('key',65,True)])
    def test_partial_move_only_does_not_invent_key_or_click(self):
        self.f.count=1;self.assertEqual(self.cast()[0],'failed');self.assertEqual(len(self.f.packets),1)
        self.assertEqual(self.h.f.keys,{5})
    def test_alt_neutralized_before_packet_then_restored(self):
        self.h.mods({164});self.assertEqual(self.cast()[0],'sent')
        self.assertEqual(self.f.packets[0][0],('key',164,True));self.assertEqual(self.h.f.keys,{5,164})
    def test_physical_alt_release_during_send_is_not_repressed(self):
        self.h.mods({164});self.f.hook=lambda e:self.h.physical.event(164,False)
        self.assertEqual(self.cast()[0],'sent');self.assertEqual(self.h.f.keys,{5})
    def test_end_or_own_key_held_blocks_quick(self):
        for key in (1,9,13,35,45,65,91,92):
            self.h.f.keys={5,key};self.assertEqual(self.cast()[0],'skipped')
        self.assertFalse(self.f.packets)
    def test_exception_after_key_attempt_releases_key_not_lmb(self):
        send=self.h.b.u.SendInput;calls=[]
        def fail(n,packet,size):
            calls.append(1)
            if len(calls)==1:self.h.f.keys.add(65);raise OSError('synthetic')
            return send(n,packet,size)
        self.h.b.u.SendInput=fail;self.assertEqual(self.cast()[0],'failed')
        self.assertEqual(self.h.f.keys,{5});self.assertEqual(self.f.packets,[[('key',65,True)]])


class PlanningTests(unittest.TestCase):
    def test_old_frame_age_shifts_impact_window(self):
        m=Model();f=old.frame();a=m.choose(f)[0];b=m.choose(f,frame_age=.1)[0]
        self.assertAlmostEqual(b.impact_early-a.impact_early,.1)
        self.assertAlmostEqual(b.impact_late-a.impact_late,.1)
    def test_age_can_expire_attack_cooldown_without_double_counting(self):
        f=old.frame(h=old.hero(last_attack=9.09));m=Model()
        self.assertIsNone(m.choose(f)[0]);self.assertIsNotNone(m.choose(f,frame_age=.1)[0])
    def test_stale_age_refused(self):
        self.assertIsNone(Model().choose(old.frame(),frame_age=.151)[0])
    def test_single_damage_burst_not_extrapolated_as_sustained_dps(self):
        m=Model();c=old.unit(hp=160);m.history[c.uid]=[(9.7,300),(9.8,160),(10,160)]
        self.assertEqual(m.dps(c.uid),0);self.assertIsNone(m.choose(old.frame([c]))[0])
    def test_multiple_recent_hits_still_reject_dead_before_impact(self):
        m=Model();c=old.unit(hp=20);m.history[c.uid]=[(9.5,500),(9.8,100),(10,20)]
        self.assertGreater(m.dps(c.uid),0);self.assertIsNone(m.choose(old.frame([c]))[0])
    def test_old_damage_does_not_persist_forever(self):
        m=Model();c=old.unit();m.history[c.uid]=[(9.4,300),(9.5,200),(9.6,50),(10,50)]
        self.assertEqual(m.dps(c.uid),0)
    def test_wait_candidate_is_not_permission_to_send(self):
        m=Model();c=old.unit(hp=300)
        self.assertIsNone(m.choose(old.frame([c]))[0]);self.assertEqual(m.watch.target.uid,c.uid)
        self.assertEqual(m.watch.kind,'wait')
    def test_committed_uid_excluded_but_other_creep_can_be_selected(self):
        m=Model();a=old.unit();b=old.unit(uid=(201,65539))
        self.assertIsNone(m.choose(old.frame([a]),excluded={a.uid})[0])
        self.assertEqual(m.choose(old.frame([a,b]),excluded={a.uid})[0].target.uid,b.uid)


class MarkerServiceTests(unittest.TestCase):
    def setUp(self):
        self.f=old.ServiceTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.s=self.f.s
    def wire(self,now=10.14):
        with patch('last_hit_service.time.perf_counter',return_value=now):return self.s.dashboard()
    def test_marker_is_actual_sent_uid_not_a_nearby_guess(self):
        self.f.tick();m=self.wire()['marker']
        self.assertEqual(m['uid'],str(old.unit().uid));self.assertEqual(m['state'],'sent')
        self.assertEqual((m['x'],m['y']),(800,400));self.assertEqual(m['width'],1920)
    def test_wait_marker_has_no_input(self):
        self.s.reader.latest=replace(self.s.reader.latest,creeps=(old.unit(hp=300),))
        self.f.tick();self.assertFalse(self.f.calls);self.assertEqual(self.wire()['marker']['state'],'wait')
    def test_release_hides_marker(self):
        self.f.tick();self.f.f.b.keys.clear();self.f.tick(10.16);self.assertIsNone(self.wire(10.16)['marker'])
    def test_menu_and_priority_hide_marker(self):
        self.f.tick();self.f.tick(10.16,blocked=True);self.assertIsNone(self.wire(10.16)['marker'])
        self.f.f.ks.ui_menu_open=True;self.f.tick(10.2);self.assertIsNone(self.wire(10.2)['marker'])
    def test_stale_marker_hidden_even_without_another_tick(self):
        self.f.tick();self.assertIsNone(self.wire(10.4)['marker'])
    def test_pending_target_tracks_fresh_screen_not_old_point(self):
        self.f.tick();c=replace(old.unit(),screen=(810,405))
        self.s.reader.latest=replace(self.s.reader.latest,creeps=(c,));self.f.tick(10.16)
        m=self.wire(10.16)['marker'];self.assertEqual((m['x'],m['y']),(810,405))
    def test_missing_target_clears_ring_not_last_seen(self):
        self.f.tick();self.s.reader.latest=replace(self.s.reader.latest,creeps=());self.f.tick(10.16)
        self.assertIsNone(self.wire(10.16)['marker'])
    def test_marker_preference_does_not_disable_automation(self):
        self.f.doc['show_marker']=False;self.s.path.write_text(json.dumps(self.f.doc));self.f.tick()
        self.assertEqual(len(self.f.calls),1);self.assertIsNone(self.wire()['marker'])
    def test_quick_mode_passed_to_same_sender(self):
        self.f.doc['quick_attack']=True;self.s.path.write_text(json.dumps(self.f.doc));calls=[]
        def send(*args,**kwargs):calls.append(kwargs);return 'sent','fake'
        self.f.f.b.last_hit_attack=send;self.f.tick();self.assertEqual(calls,[{'quick':True}])
    def test_legacy_controls_keep_normal_mode_and_optional_types_strict(self):
        self.assertTrue(validate(old.command(),'session',100000))
        self.assertFalse(validate(old.command(quick_attack=1),'session',100000))
        self.assertFalse(validate(old.command(show_marker='yes'),'session',100000))
        self.f.tick();self.assertFalse(self.s.quick_attack)
    def test_feedback_does_not_allow_repeat_on_projectile_target(self):
        h=replace(self.s.reader.latest.hero,projectile_speed=100,attack_point=.1,interval=.2)
        self.s.reader.latest=replace(self.s.reader.latest,hero=h)
        self.f.tick();self.s.reader.latest=replace(self.s.reader.latest,hero=replace(h,last_attack=10.14))
        self.f.tick(10.5);self.assertEqual(len(self.f.calls),1)


class NativeMarkerTests(unittest.TestCase):
    def test_draw_hook_and_physical_to_dip_coordinates_present(self):
        native=(ROOT/'last_hit_menu.cs').read_text()
        self.assertIn('DrawLastHitTarget(dc,ppd)',(ROOT/'esp_overlay.cs').read_text())
        self.assertIn('m.x*pixelToDipX+offX',native);self.assertIn('m.y*pixelToDipY+offY',native)
        self.assertIn('m.IsFresh(elapsed)',native);self.assertIn('show_marker=lhPrefs.show_marker',native)
    @unittest.skipUnless(shutil.which('mcs') and shutil.which('mono'),'optional Mono toolchain not installed')
    def test_actual_native_dto_expiry_and_default_quick_preferences(self):
        source=(ROOT/'ks_menu.cs').read_text();dtos=source[source.index('    public class SourceData'):source.index('    static class MenuTheme')]
        menu=(ROOT/'last_hit_menu.cs').read_text();prefs=menu[menu.index('    public class LastHitPreferences'):menu.index('    partial class MenuWindow')]
        main='''class Smoke {
            static void Check(bool b){if(!b)throw new Exception("marker contract");}
            static void Main(){
                var serializer=new System.Web.Script.Serialization.JavaScriptSerializer();
                var p=serializer.Deserialize<LastHitPreferences>("{\\"key\\":5}");
                Check(!p.quick_attack&&p.show_marker&&p.single_hit);
                var m=new LastHitMarker{visible=true,x=800,y=400,hp=50,damage=70,width=1920,height=1080,age=.05};
                Check(m.IsFresh(.03));Check(!m.IsFresh(.16));Check(!m.IsFresh(-1));
                m.x=Double.NaN;Check(!m.IsFresh(0));m.x=800;m.y=1000;Check(!m.IsFresh(0));
                m.y=400;m.visible=false;Check(!m.IsFresh(0));System.Console.WriteLine("PASS marker");
            }
        }'''
        code='using System;using System.Collections.Generic;namespace EspOverlay {'+dtos+prefs+main+'}'
        with tempfile.TemporaryDirectory() as t:
            t=Path(t);cs=t/'marker.cs';exe=t/'marker.exe';cs.write_text(code)
            built=subprocess.run(['mcs','-warnaserror','-langversion:5','-r:System.Web.Extensions','-out:'+str(exe),str(cs)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            run=subprocess.run(['mono',str(exe)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)


if __name__=='__main__':unittest.main()
