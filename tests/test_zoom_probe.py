"""exp2: read-only diagnostics for the failed +0x2E4 profile, no offset approval."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_zoom_standalone import Memory
from zoom.locator import discover,locate,UnsupportedDistance,DISTANCE_OFFSET
from zoom.probe import capture,save,FORMAT,method_capture,main
from zoom.win32 import Memory as WinMemory


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.m=Memory();self.m.put(self.m.obj+DISTANCE_OFFSET,struct.pack('<f',0))
        self.c=discover(self.m,self.m.base,self.m.size)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def collect(self):
        with patch('zoom.probe.time.sleep'):return capture(self.m,self.c)
    def test_discover_succeeds_but_writer_locator_still_refuses_invalid_distance(self):
        self.assertEqual(self.c.obj,self.m.obj)
        with self.assertRaises(UnsupportedDistance) as error:locate(self.m,self.m.base,self.m.size)
        self.assertEqual(error.exception.camera,self.c);self.assertFalse(self.m.writes)
    def test_old_270_1200_is_reported_not_promoted_to_write_offset(self):
        self.m.put(self.m.obj+0x270,struct.pack('<f',1200));r=self.collect()
        self.assertEqual(r['samples'][0]['fields']['0x270']['f32'],1200)
        self.assertEqual(r['samples'][0]['fields']['0x2e4']['f32'],0)
        self.assertFalse(r['approved_for_write']);self.assertFalse(self.m.writes)
        with self.assertRaises(UnsupportedDistance):locate(self.m,self.m.base,self.m.size)
    def test_three_samples_and_bounded_code(self):
        r=self.collect();self.assertTrue(r['complete']);self.assertEqual(len(r['samples']),3)
        self.assertEqual(len(bytes.fromhex(r['samples'][0]['raw_0x000_0x400'])),0x400)
        self.assertLessEqual(len(r['candidate_vtable_slots']),32)
        self.assertEqual(len(r['functions']),3)
        self.assertTrue(all(len(bytes.fromhex(b['hex']))<=160 for rows in r['functions'].values() for b in rows))
    def test_write_function_never_called(self):
        self.m.write=lambda *a: self.fail('diagnostics attempted write')
        self.assertEqual(self.collect()['memory_writes_by_probe'],0)
    def test_nonfinite_values_are_json_null_with_raw_bits(self):
        self.m.put(self.m.obj+0x270,struct.pack('<f',float('nan')));r=self.collect()
        f=r['samples'][0]['fields']['0x270'];self.assertIsNone(f['f32']);self.assertFalse(f['finite'])
        json.dumps(r,allow_nan=False)
    def test_camera_change_discards_unstable_sample(self):
        read=self.m.read
        def changed(a,n):
            raw=read(a,n)
            if a==self.m.obj and n==0x400:self.m.put(self.m.slot,struct.pack('<Q',self.m.obj+0x100))
            return raw
        self.m.read=changed;r=self.collect()
        self.assertFalse(r['complete']);self.assertEqual(r['samples'],[]);self.assertTrue(r['errors']);self.assertFalse(self.m.writes)
    def test_cancellation_does_not_save_or_write(self):
        with self.assertRaises(InterruptedError):capture(self.m,self.c,lambda:True)
        self.assertFalse(self.m.writes)
    def test_deadline_returns_explicit_partial(self):
        with patch('zoom.probe.time.monotonic',side_effect=[0,4,4]):r=capture(self.m,self.c)
        self.assertFalse(r['complete']);self.assertTrue(r['errors']);self.assertFalse(self.m.writes)
    def test_unreadable_object_explicit_partial(self):
        read=self.m.read
        def unavailable(a,n):
            if a==self.m.obj and n==0x400:raise OSError('synthetic unavailable')
            return read(a,n)
        self.m.read=unavailable;r=self.collect();self.assertFalse(r['complete']);self.assertEqual(r['samples'],[])
    def test_internal_jump_thunks_bounded_to_three_blocks(self):
        points=[self.m.base+0x1300,self.m.base+0x1500,self.m.base+0x1700,self.m.base+0x1900]
        for a,b in zip(points,points[1:]):self.m.put(a,b'\xe9'+struct.pack('<i',b-a-5))
        result=method_capture(self.m,self.c.image,points[0]);self.assertEqual(len(result),3)
    def test_external_jump_target_not_read(self):
        address=self.m.base+0x1300;self.m.put(address,b'\xe9'+struct.pack('<i',self.m.obj-address-5))
        self.assertEqual(len(method_capture(self.m,self.c.image,address)),1)
    def test_function_data_pointer_not_followed(self):
        with self.assertRaises(ValueError):method_capture(self.m,self.c.image,self.m.obj)
    def test_mutable_indirect_jump_slot_not_followed(self):
        address=self.m.base+0x1300;self.m.put(address,b'\xff\x25'+struct.pack('<i',self.m.slot-address-6))
        self.assertEqual(len(method_capture(self.m,self.c.image,address)),1)
    def test_roundtrip_file_size_and_no_temp_left(self):
        r=self.collect();path=save(r,self.root/'zoom_probe.json')
        self.assertEqual(json.loads(path.read_text()),r);self.assertLess(path.stat().st_size,128*1024)
        self.assertFalse(path.with_suffix('.json.tmp').exists())
    def test_diagnostic_cannot_approve_writes(self):
        r=self.collect();r['approved_for_write']=True
        with self.assertRaises(ValueError):save(r,self.root/'zoom_probe.json')
        self.assertFalse(list(self.root.iterdir()))
    def test_oversize_refused(self):
        r={'format':FORMAT,'approved_for_write':False,'bad':'x'*150000}
        with self.assertRaises(ValueError):save(r,self.root/'zoom_probe.json')
        self.assertFalse(list(self.root.iterdir()))
    def test_cli_opens_readonly_and_closes(self):
        self.closed=[];self.m.close=lambda:self.closed.append(True)
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.m.base,size=self.m.size),end_pressed=lambda:False)
        with patch('zoom.win32.API',return_value=api),patch('zoom.win32.Memory',return_value=self.m) as constructor,patch('zoom.probe.__file__',str(self.root/'probe.py')),patch('zoom.probe.time.sleep'):
            self.assertEqual(main(),0)
        constructor.assert_called_once_with(api,123,readonly=True)
        self.assertEqual(self.closed,[True]);self.assertFalse(self.m.writes)
        self.assertTrue((self.root/'zoom_probe.json').exists())
    def test_app_unknown_distance_saves_report_without_creating_writer(self):
        from zoom.app import Worker
        self.m.close=lambda:None
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.m.base,size=self.m.size,path='synthetic'),end_pressed=lambda:False)
        w=Worker();w.api=api
        with patch('zoom.app.locate',side_effect=locate),patch('zoom.app.Memory',return_value=self.m),patch('zoom.app.ROOT',self.root),patch('zoom.probe.time.sleep'):
            with self.assertRaisesRegex(ValueError,'zoom_probe.json'):w.connect()
        self.assertIsNone(w.control);self.assertTrue((self.root/'zoom_probe.json').exists());self.assertFalse(self.m.writes)
        w.restore()


class ReadonlyHandleTests(unittest.TestCase):
    def test_readonly_flags_and_write_refusal(self):
        calls=[]
        api=SimpleNamespace(k=SimpleNamespace(OpenProcess=lambda flags,b,pid:calls.append(flags) or 999,GetProcessId=lambda h:123,CloseHandle=lambda h:True))
        mem=WinMemory(api,123,readonly=True)
        self.assertEqual(calls[0]&(0x20|0x8),0)
        self.assertTrue(calls[0]&0x10)
        with self.assertRaisesRegex(ValueError,'Диагностическое'):mem.write(0x10000,b'1234')
        mem.close()
    def test_normal_handle_retains_required_write_operation_right(self):
        calls=[]
        api=SimpleNamespace(k=SimpleNamespace(OpenProcess=lambda flags,b,pid:calls.append(flags) or 999,GetProcessId=lambda h:123,CloseHandle=lambda h:True))
        mem=WinMemory(api,123)
        self.assertEqual(calls[0]&(0x20|0x8),0x28);mem.close()


if __name__=='__main__':unittest.main()
