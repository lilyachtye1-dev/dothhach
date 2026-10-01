"""Native menu IPC/session and real accepted-control integration on fake memory."""
import json
import tempfile
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import test_zoom_farz as fixture
from zoom.integrated import CameraService,NativeEngine,validate,attach_state

SESSION='a'*32


class FakeEngine:
    def __init__(self):self.calls=[];self.stops=0;self.error=None;self.poll_error=None;self.end=False;self.restore=[]
    def apply(self,distance,haze,cancel):
        if cancel():raise InterruptedError()
        if self.error:raise ValueError(self.error)
        self.calls.append((distance,haze))
    def stop_all(self):self.stops+=1;return self.restore
    def poll(self):
        if self.poll_error:raise ValueError(self.poll_error)
    def end_pressed(self):return self.end


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'zoom').mkdir();self.engine=FakeEngine();self.s=CameraService(self.root,SESSION,engine=self.engine)
        self.addCleanup(self.s.close)
    def doc(self,revision=1,distance=1500,haze=False):return dict(version=1,session=SESSION,revision=revision,utc_ms=int(time.time()*1000),distance=distance,haze=haze)
    def write(self,doc):self.s.path.write_text(json.dumps(doc));return doc
    def apply(self,**kw):self.s.step() if not kw else None;doc=self.write(self.doc(**kw));self.s.step();return doc
    def test_startup_off_without_connect_or_write(self):
        self.s.step();self.assertEqual(self.engine.calls,[]);self.assertEqual(self.s.snapshot()['distance'],0)
    def test_valid_command(self):self.assertTrue(validate(self.doc(),SESSION,int(time.time()*1000)))
    def test_reject_wrong_types_ranges_extra_keys(self):
        for field,value in [('distance',999),('distance',1501),('distance',True),('distance',2201),('haze',1),('revision',True),('revision',-1),('version',2),('session','b'*32),('utc_ms',0),('extra',1)]:
            d=self.doc();d[field]=value
            with self.subTest(field=field,value=value):self.assertFalse(validate(d,SESSION,int(time.time()*1000)))
    def test_auto_and_endpoints_valid(self):
        for n in (0,1000,2200):self.assertTrue(validate(self.doc(distance=n),SESSION,int(time.time()*1000)))
    def test_old_session_cannot_enable(self):
        d=self.doc();d['session']='b'*32;self.write(d);self.s.step();self.assertFalse(self.engine.calls)
    def test_same_revision_heartbeat_no_repeat(self):
        self.apply(revision=1);self.write(self.doc(revision=1));self.s.step();self.assertEqual(self.engine.calls,[(1500,False)])
    def test_new_revision_slider_applies(self):
        self.apply(revision=1);self.apply(revision=2,distance=1750);self.assertEqual(self.engine.calls[-1],(1750,False))
    def test_auto_is_explicit_off_request(self):
        self.apply(revision=1);self.apply(revision=2,distance=0);self.assertEqual(self.engine.calls[-1],(0,False))
    def test_fault_not_rearmed_by_same_revision(self):
        self.engine.error='no camera';self.apply(revision=1);self.engine.error=None
        self.write(self.doc(revision=1));self.s.step();self.assertEqual(self.engine.calls,[])
        self.apply(revision=2);self.assertEqual(len(self.engine.calls),1)
    def test_lease_expiry_restores_no_auto_resume(self):
        self.apply(revision=1);self.s.path.unlink();self.s.lease=0;self.s.step()
        self.assertEqual(self.s.snapshot()['distance'],0);self.assertGreater(self.engine.stops,0)
        self.write(self.doc(revision=1));self.s.step();self.assertEqual(len(self.engine.calls),1)
    def test_host_stall_restores(self):
        self.apply(revision=1);self.s.host_at=time.monotonic()-3;self.s.step();self.assertEqual(self.s.snapshot()['distance'],0)
        self.s.pulse();self.s.step();self.assertEqual(len(self.engine.calls),1)
    def test_same_revision_mutation_cancels_inflight(self):
        self.apply(revision=1);self.write(self.doc(revision=1,distance=0));self.assertTrue(self.s.cancelled(1))
    def test_newer_revision_cancels_inflight(self):
        self.apply(revision=1);self.write(self.doc(revision=2));self.assertTrue(self.s.cancelled(1))
    def test_stale_revision_ignored(self):
        self.apply(revision=2);self.write(self.doc(revision=1,distance=2000));self.s.step();self.assertEqual(len(self.engine.calls),1)
    def test_missing_or_oversize_file_rejected(self):
        self.s.path.write_text('x'*3000);self.assertIsNone(self.s.read_command())
        self.s.path.write_text('{');self.assertIsNone(self.s.read_command())
    def test_preview_never_calls_apply(self):
        self.s.preview=True;self.apply(revision=1,distance=1800,haze=True)
        self.assertEqual(self.engine.calls,[]);self.assertEqual(self.s.snapshot()['distance'],0)
    def test_monitor_failure_stops_and_latches(self):
        self.apply(revision=1);self.engine.poll_error='foreign writer';self.s.step();self.assertEqual(self.s.snapshot()['distance'],0)
        self.engine.poll_error=None;self.s.step();self.assertEqual(len(self.engine.calls),1)
    def test_restore_failure_visible(self):
        self.apply(revision=1);self.engine.restore=['synthetic restore failure'];self.s.off('OFF')
        self.assertIn('synthetic',self.s.snapshot()['restore_error'])
    def test_end_hard_stop(self):
        self.engine.end=True;self.s.step();self.assertTrue(self.s.quit.is_set())
    def test_thread_close_restores(self):
        self.write(self.doc());self.s.start();deadline=time.monotonic()+2
        while self.s.snapshot()['distance']==0 and time.monotonic()<deadline:time.sleep(.01)
        self.s.close();self.assertTrue(self.s.done.is_set());self.assertEqual(self.s.snapshot()['distance'],0)
    def test_telemetry_preserves_existing_fields(self):
        old={'version':1,'last_hit':{'enabled':True},'disabled':['x'],'session':SESSION}
        line=attach_state('UI1\t'+json.dumps(old),self.s.snapshot());got=json.loads(line[4:])
        zoom=got.pop('zoom');self.assertEqual(got,old);self.assertEqual(zoom['distance'],0)
    def test_long_discovery_uses_latest_heartbeat_not_initial_deadline(self):
        clock=[time.monotonic()];wall=[time.time()]
        self.write(self.doc())
        def slow(distance,haze,cancel):
            clock[0]+=3;wall[0]+=3;self.s.pulse();self.write(self.doc())
            self.assertFalse(cancel());self.engine.calls.append((distance,haze))
        self.engine.apply=slow
        with patch('zoom.integrated.time.monotonic',side_effect=lambda:clock[0]),patch('zoom.integrated.time.time',side_effect=lambda:wall[0]):
            self.s.step()
        self.assertEqual(self.s.snapshot()['distance'],1500);self.assertGreater(self.s.lease,clock[0]+1)
    def test_exact_timestamp_bounds(self):
        for delta,allowed in [(-2001,False),(-2000,True),(500,True),(501,False)]:
            d=self.doc();d['utc_ms']=500000+delta
            self.assertEqual(validate(d,SESSION,500000),allowed)
    def test_ui_disappears_during_apply_cancels(self):
        def lost(distance,haze,cancel):
            self.s.path.unlink()
            if cancel():raise InterruptedError()
            self.fail('must cancel')
        self.engine.apply=lost;self.apply(revision=1)
        self.assertEqual(self.s.snapshot()['distance'],0);self.assertGreater(self.engine.stops,0)
    def test_snapshot_is_copy(self):
        s=self.s.snapshot();s['distance']=2000;self.assertEqual(self.s.snapshot()['distance'],0)


class NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.FarZTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.m=self.f.m
        self.engine=NativeEngine();self.released=[];self.closed=[];self.end=False
        self.m.close=lambda:self.closed.append(True)
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.m.base,size=self.m.size),end_pressed=lambda:self.end)
        for name,val in [('API',api),('Memory',self.m),('instance_lock',lambda:self.released.append('zoom')),('scene_lock',lambda:self.released.append('scene'))]:
            p=patch('zoom.integrated.'+name,return_value=val);p.start();self.addCleanup(p.stop)
        self.addCleanup(self.engine.stop_all)
    def apply(self,distance,haze=False):self.engine.apply(distance,haze,lambda:False)
    def test_slider_auto_enables_farz(self):
        self.apply(1500);self.assertEqual(self.m.value(),1500);self.assertEqual(self.f.value(),18000)
        self.assertTrue(self.engine.farz.active)
    def test_slider_change_not_repeated_farz_write(self):
        self.apply(1500);n=len(self.m.writes);self.apply(1750)
        self.assertEqual(len(self.m.writes),n+1);self.assertEqual(self.m.value(),1750)
    def test_auto_restores_zoom_farz_keeps_haze(self):
        self.apply(1500,True);self.apply(0,True)
        self.assertEqual(self.m.value(),-1);self.assertEqual(self.f.value(),-1)
        self.assertEqual(self.f.f.value('env_fog_controller'),0)
    def test_haze_button_does_not_rewrite_active_zoom(self):
        self.apply(1500);self.apply(1500,True);self.apply(1500,False)
        self.assertEqual(self.m.value(),1500);self.assertEqual(self.f.value(),18000)
        self.assertEqual(len([x for x in self.m.writes if x[0]==self.m.obj+0x2e4]),1)
    def test_haze_only_no_zoom_or_farz_write(self):
        self.apply(0,True);self.assertEqual(self.m.value(),-1);self.assertEqual(self.f.value(),-1)
    def test_off_releases_both_mutexes_and_handle(self):
        self.apply(1500,True);self.apply(0,False)
        self.assertEqual(self.m.value(),-1);self.assertEqual(self.f.value(),-1)
        self.assertEqual(self.released,['scene','zoom']);self.assertEqual(self.closed,[True])
    def test_noop_startup_off_does_not_open_process(self):
        self.apply(0,False);self.assertIsNone(self.engine.api);self.assertFalse(self.closed)
    def test_foreign_zoom_does_not_prevent_independent_farz_restore(self):
        self.apply(1500);import struct
        self.m.put(self.m.obj+0x2e4,struct.pack('<f',1600));errors=self.engine.stop_all()
        self.assertTrue(errors);self.assertEqual(self.m.value(),1600);self.assertEqual(self.f.value(),-1)
    def test_standalone_mutex_conflict_stops_before_process_open(self):
        with patch('zoom.integrated.scene_lock',side_effect=OSError('close Scene')):
            with self.assertRaises(OSError):self.apply(1500)
        self.assertEqual(self.released,['zoom']);self.assertIsNone(self.engine.mem);self.assertFalse(self.m.writes)
    def service(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);root=Path(t.name);(root/'zoom').mkdir()
        service=CameraService(root,SESSION,engine=self.engine);self.addCleanup(service.close)
        service.path.write_text(json.dumps(dict(version=1,session=SESSION,revision=1,utc_ms=int(time.time()*1000),distance=1500,haze=True)))
        return service
    def test_actual_worker_end_restores_all_three_features(self):
        service=self.service();service.start();deadline=time.monotonic()+2
        while service.snapshot()['busy'] or service.snapshot()['distance']!=1500:
            if time.monotonic()>deadline:self.fail('worker did not arm')
            time.sleep(.01)
        self.end=True;self.assertTrue(service.done.wait(2))
        self.assertEqual(self.m.value(),-1);self.assertEqual(self.f.value(),-1)
        self.assertNotEqual(self.f.f.value('env_fog_controller'),0)
        self.assertEqual(self.released,['scene','zoom'])
    def test_actual_worker_close_restores_all_three_features(self):
        service=self.service();service.step();self.assertEqual(self.m.value(),1500)
        service.close();self.assertEqual(self.m.value(),-1);self.assertEqual(self.f.value(),-1)
        self.assertNotEqual(self.f.f.value('env_fog_controller'),0)
    def test_zoom_failure_rolls_back_already_enabled_farz(self):
        service=self.service()
        with patch('zoom.integrated.Control.arm',side_effect=ValueError('synthetic zoom failure')):service.step()
        self.assertEqual(self.m.value(),-1);self.assertEqual(self.f.value(),-1)
        self.assertIsNone(self.engine.mem);self.assertEqual(service.snapshot()['distance'],0)
    def test_haze_failure_rolls_back_zoom_and_farz(self):
        service=self.service()
        with patch('zoom.integrated.Group.start',side_effect=ValueError('synthetic haze failure')):service.step()
        self.assertEqual(self.m.value(),-1);self.assertEqual(self.f.value(),-1)
        self.assertIsNone(self.engine.mem);self.assertFalse(service.snapshot()['haze'])
    def test_farz_failure_prevents_zoom_write(self):
        import struct
        self.m.put(self.f.guard,struct.pack('<i',0))
        with self.assertRaises(ValueError):self.apply(1500)
        self.engine.stop_all();self.assertFalse(self.m.writes)


class NativeIntegrationSourceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('mcs') and shutil.which('mono'),'optional Mono not installed')
    def test_actual_csharp_camera_dto_reads_python_telemetry(self):
        root=Path(__file__).resolve().parents[1];menu=(root/'ks_menu.cs').read_text()
        dtos=menu[menu.index('    public class SourceData'):menu.index('    static class MenuTheme')]
        code='using System;using System.IO;using System.Collections.Generic;using System.Globalization;using System.Web.Script.Serialization;namespace EspOverlay {'+dtos+'''
        class CameraSmoke { static int Main(string[] args) {
            if(new DashboardData().zoom.available)return 1;
            var d=new JavaScriptSerializer().Deserialize<DashboardData>(File.ReadAllText(args[0]));
            if(d.version!=1||d.zoom.distance!=1750||d.zoom.revision!=2||!d.zoom.haze||!d.zoom.farz||!d.zoom.available||d.zoom.busy||d.zoom.preview||!d.last_hit.enabled)return 2;
            Console.WriteLine("PASS actual camera DTO");return 0;
        }} }'''
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);cs=tmp/'smoke.cs';exe=tmp/'smoke.exe';payload=tmp/'state.json';cs.write_text(code)
            line=attach_state('UI1\t{"version":1,"last_hit":{"enabled":true}}',dict(distance=1750,revision=2,haze=True,farz=True,available=True,busy=False,preview=False))
            payload.write_text(line[4:])
            built=subprocess.run(['mcs','-warnaserror','-langversion:5','-r:System.Web.Extensions','-out:'+str(exe),str(cs)],capture_output=True,text=True,timeout=30)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            run=subprocess.run(['mono',str(exe),str(payload)],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr);self.assertIn('PASS actual camera DTO',run.stdout)
    def test_page_after_lasthit_two_controls_no_extra_farz_button(self):
        root=Path(__file__).resolve().parents[1];menu=(root/'ks_menu.cs').read_text();ui=(root/'zoom_menu.cs').read_text()
        self.assertLess(menu.index('ModuleButton("◉","Last Hit"'),menu.index('ModuleButton("↔","Камера"'))
        self.assertIn('zoom_menu.cs',(root/'build_overlay.bat').read_text());self.assertIn('ZoomPage()',menu)
        self.assertEqual(ui.count('new Slider'),1);self.assertEqual(ui.count('new Button'),1)
        self.assertIn('FinishZoom();FinishLastHit()',menu)
    def test_insert_hide_does_not_finish_camera(self):
        root=Path(__file__).resolve().parents[1];s=(root/'ks_menu.cs').read_text()
        body=s[s.index('public void ToggleMenu()'):s.index('void SetPage(')]
        self.assertNotIn('FinishZoom',body);self.assertNotIn('zoomDistance',body)
    def test_feed_no_forced_exit_before_restore(self):
        s=(Path(__file__).resolve().parents[1]/'esp_feed.py').read_text()
        self.assertNotIn('os._exit',s);self.assertIn('self.camera_service.close()',s)
        self.assertIn('finally:\n            self._running = False',s)
    def test_service_no_input_sender(self):
        s=(Path(__file__).resolve().parents[1]/'zoom/integrated.py').read_text()
        for name in ('SendInput','mouse_event','keybd_event','ks_input','WinInput'):self.assertNotIn(name,s)


if __name__=='__main__':unittest.main()
