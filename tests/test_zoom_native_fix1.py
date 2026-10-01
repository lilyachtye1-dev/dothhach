"""Regressions for native camera command coalescing and transient Windows IPC IO.
WPF state model is executed with Mono; templates are structurally checked only.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import test_zoom_integrated as previous

ROOT=Path(__file__).resolve().parents[1]


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.f=previous.ProtocolTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.s=self.f.s;self.engine=self.f.engine
    def arm(self):self.f.apply(revision=1,distance=1500)
    def test_sharing_violation_does_not_cancel_fresh_command(self):
        self.arm()
        with patch.object(Path,'read_text',side_effect=PermissionError('synthetic Windows sharing overlap')):
            self.assertFalse(self.s.cancelled(1));self.s.step()
        self.assertEqual(self.s.snapshot()['distance'],1500);self.assertEqual(len(self.engine.calls),1)
    def test_transient_error_without_previous_valid_command_refused(self):
        self.f.write(self.f.doc())
        with patch.object(Path,'read_text',side_effect=PermissionError()):self.assertIsNone(self.s.read_command())
    def test_grace_does_not_refresh_itself(self):
        self.arm();at=self.s.cached_at
        with patch.object(Path,'read_text',side_effect=PermissionError()):
            self.assertIsNotNone(self.s.read_command());self.assertEqual(self.s.cached_at,at)
            self.s.cached_at-=.3;self.assertIsNone(self.s.read_command())
    def test_expired_utc_gets_no_grace(self):
        self.arm();self.s.cached_command['utc_ms']-=3000
        with patch.object(Path,'read_text',side_effect=PermissionError()):self.assertTrue(self.s.cancelled(1))
    def test_missing_file_not_masked_by_cache(self):
        self.arm();self.s.path.unlink();self.assertTrue(self.s.cancelled(1));self.assertIsNone(self.s.cached_command)
    def test_invalid_json_purges_cache(self):
        self.arm();self.s.path.write_text('{');self.assertIsNone(self.s.read_command());self.assertIsNone(self.s.cached_command)
        with patch.object(Path,'read_text',side_effect=PermissionError()):self.assertIsNone(self.s.read_command())
    def test_wrong_session_purges_cache(self):
        self.arm();d=self.f.doc();d['session']='b'*32;self.f.write(d)
        self.assertIsNone(self.s.read_command());self.assertIsNone(self.s.cached_command)
    def test_oversized_file_purges_cache(self):
        self.arm();self.s.path.write_text('x'*3000);self.assertIsNone(self.s.read_command());self.assertIsNone(self.s.cached_command)
    def test_other_io_errors_do_not_reuse_cache(self):
        self.arm()
        with patch.object(Path,'read_text',side_effect=OSError('disk failure')):self.assertTrue(self.s.cancelled(1))
    def test_cancellation_reason_visible_in_state_and_console(self):
        def lost(*args,**kw):self.s.path.unlink();self.s.cancelled(1);raise InterruptedError()
        self.engine.apply=lost
        with contextlib.redirect_stdout(io.StringIO()) as output:self.arm()
        self.assertIn('Нет свежей команды',self.s.snapshot()['error']);self.assertIn('[Камера]',output.getvalue())
    def test_build_failure_visible_in_main_console(self):
        self.engine.error='Сборка камеры не поддерживается'
        with contextlib.redirect_stdout(io.StringIO()) as output:self.arm()
        self.assertIn('Сборка камеры',output.getvalue());self.assertEqual(self.s.snapshot()['distance'],0)
    def test_busy_snapshot_shows_stage(self):
        self.engine.stage='Проверяю версию';self.s.publish(busy=True)
        self.assertEqual(self.s.snapshot()['status'],'Проверяю версию')
        self.s.publish(busy=False,status='Авто');self.assertEqual(self.s.snapshot()['status'],'Авто')
    def test_long_io_denial_does_restore_without_rearm(self):
        self.arm();self.s.cached_at-=1;self.s.lease=0
        with patch.object(Path,'read_text',side_effect=PermissionError()):self.s.step()
        self.assertEqual(self.s.snapshot()['distance'],0)
        self.s.step();self.assertEqual(len(self.engine.calls),1)
    def test_retry_higher_revision_after_file_recovers(self):
        self.arm();self.s.cached_at-=1;self.s.lease=0
        with patch.object(Path,'read_text',side_effect=PermissionError()):self.s.step()
        self.f.apply(revision=2,distance=1750);self.assertEqual(self.s.snapshot()['distance'],1750)


class NativeModelTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('mcs') and shutil.which('mono'),'optional Mono not installed')
    def test_actual_csharp_draft_commit_ack_model(self):
        source=(ROOT/'zoom_menu.cs').read_text();model=source[source.index('    internal sealed class ZoomIntent'):source.index('    partial class MenuWindow')]
        code='using System; namespace EspOverlay {'+model+'''
        class Smoke {
            static int n;
            static void Check(bool ok,string label){n++;if(!ok)throw new Exception(label);}
            static void Main(){
                var x=new ZoomIntent();x.ResetAfter(-1);
                Check(x.Revision==0&&x.Distance==0&&!x.Haze,"startup off");
                x.Acknowledge(0,false,0,false);
                x.StageDistance(1500);x.Acknowledge(0,false,0,false);
                Check(x.Pending&&x.Distance==1500&&x.SentDistance==0,"old ack does not move draft");
                for(int i=1000;i<=2200;i+=25)x.StageDistance(i);
                Check(x.Revision==0&&x.SentDistance==0,"drag never changes heartbeat");
                Check(x.Commit()&&x.Revision==1&&x.SentDistance==2200&&x.Awaiting,"commit final only");
                Check(!x.Commit()&&x.Revision==1,"release and debounce not duplicate");
                x.Acknowledge(0,false,0,false);Check(x.Distance==2200&&x.Awaiting,"stale ack ignored");
                x.Acknowledge(1,true,0,false);Check(x.Awaiting,"busy not final");
                x.Acknowledge(1,false,2200,false);Check(!x.Awaiting,"active ack");
                x.StageDistance(1800);x.Acknowledge(1,false,2200,false);
                Check(x.Distance==1800&&x.SentDistance==2200,"heartbeat during second drag");
                x.Commit();x.Acknowledge(2,false,0,false);
                Check(x.Distance==0&&x.SentDistance==1800&&x.Revision==2,"error ack never mutates old command");
                x.StageDistance(1750);x.ToggleHaze();x.Commit();
                Check(x.Revision==3&&x.SentDistance==1750&&x.SentHaze,"haze plus distance share atomic commit");
                x.Acknowledge(3,false,1750,true);Check(x.Haze&&!x.Awaiting,"haze ack");
                x.Acknowledge(99,false,0,false);Check(x.Haze&&x.Distance==1750,"unrelated ack ignored");
                x.StageDistance(2000);x.Stop();
                Check(x.Revision==4&&x.SentDistance==0&&!x.SentHaze&&!x.Pending,"End cancels pending drag");
                x.ResetAfter(94);Check(x.Revision==95&&x.SentDistance==0&&!x.Haze,"reopened menu sends newer OFF");
                x.StageDistance(975);x.Commit();Check(x.SentDistance==0,"Auto sentinel");
                x.StageDistance(1000);x.Commit();Check(x.SentDistance==1000,"minimum");
                x.StageDistance(2200);x.Commit();Check(x.SentDistance==2200,"maximum");
                x.ResetAfter(-1);Check(x.Revision==0&&x.Distance==0&&!x.SentHaze,"fresh session resets");
                Console.WriteLine("PASS "+n+" native camera state assertions");
            }
        }}'''
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);cs=tmp/'state.cs';exe=tmp/'state.exe';cs.write_text(code)
            built=subprocess.run(['mcs','-warnaserror','-langversion:5','-out:'+str(exe),str(cs)],capture_output=True,text=True,timeout=30)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            run=subprocess.run(['mono',str(exe)],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr);self.assertIn('PASS 19 native camera state assertions',run.stdout)
    def test_timer_sends_only_committed_values(self):
        text=(ROOT/'zoom_menu.cs').read_text();write=text[text.index('void WriteZoom()'):text.index('void FinishZoom()')]
        self.assertIn('distance=zoomIntent.SentDistance,haze=zoomIntent.SentHaze',write)
        self.assertIn('zoomTimer.Tick+=delegate{WriteZoom();}',text)
        self.assertIn('if(!zoomDragging)CommitZoom()',text)
        self.assertIn('Thumb.DragCompletedEvent',text)
    def test_inline_templates_valid_xml_with_real_track_and_thumb(self):
        source=(ROOT/'zoom_menu.cs').read_text();found=re.findall(r'const string (Zoom\w+Xaml)=@"(.*?)";',source,re.S)
        self.assertEqual(len(found),2)
        ns={'p':'http://schemas.microsoft.com/winfx/2006/xaml/presentation'}
        slider=ET.fromstring(dict(found)['ZoomSliderXaml']);ET.fromstring(dict(found)['ZoomButtonXaml'])
        track=slider.find('.//p:Track',ns);self.assertIsNotNone(track)
        self.assertEqual(track.attrib['{http://schemas.microsoft.com/winfx/2006/xaml}Name'],'PART_Track')
        self.assertIsNotNone(slider.find('.//p:Thumb',ns));self.assertIn('TryZoomTemplate(slider',source)
    def test_camera_errors_mirrored_into_user_supplied_main_log(self):
        source=(ROOT/'zoom_menu.cs').read_text();self.assertIn('OverlayLog.Write("CAMERA revision=',source)
        self.assertIn('Не удалось передать настройку',source);self.assertIn('В игре:',source)


if __name__=='__main__':unittest.main()
