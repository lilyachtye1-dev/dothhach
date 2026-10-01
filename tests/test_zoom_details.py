"""Read-only exp3 continuation, artificial memory only."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_zoom_standalone import Memory
from zoom.locator import discover,locate,UnsupportedDistance
from zoom import details
from zoom.probe import save


class DetailsTests(unittest.TestCase):
    def setUp(self):
        self.m=Memory();self.m.vtable=self.m.base+0x46000
        self.m.put(self.m.obj,struct.pack('<Q',self.m.vtable))
        self.m.put(self.m.vtable-8,struct.pack('<Q',self.m.col))
        for i in range(64):self.m.put(self.m.vtable+8*i,struct.pack('<Q',self.m.base+0x1300+i*0x10))
        self.targets=[]
        for k,(i,rva,n,prefix) in enumerate(details.TARGETS):
            rva=0x1800+k*0x3000
            self.m.put(self.m.vtable+8*i,struct.pack('<Q',self.m.base+rva))
            self.m.put(self.m.base+rva,prefix)
            self.targets.append((i,rva,n,prefix))
        self.m.put(self.m.obj+0x2e4,struct.pack('<f',-1))
        self.m.put(self.m.obj+0x15c,struct.pack('<f',1200))
        self.c=discover(self.m,self.m.base,self.m.size)
        self.expected=(self.c.image.timestamp,self.m.size,0x46000,self.c.typename)
        p=patch('zoom.details.EXPECTED',self.expected);p.start();self.addCleanup(p.stop)
        p=patch('zoom.details.TARGETS',tuple(self.targets));p.start();self.addCleanup(p.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def get(self):return details.capture_details(self.m,self.c)
    def test_known_profile_collects_longer_code_without_writes(self):
        r=self.get();self.assertTrue(r['complete']);self.assertFalse(self.m.writes)
        self.assertEqual(len(r['targeted_code']),8)
        self.assertEqual(len(r['candidate_vtable_slots']),64)
        self.assertEqual(max(x['bytes'] for x in r['targeted_code']),8192)
    def test_old_sentinel_does_not_allow_writer(self):
        self.assertFalse(self.get()['approved_for_write'])
        with self.assertRaises(UnsupportedDistance):locate(self.m,self.m.base,self.m.size)
        self.assertFalse(self.m.writes)
    def test_15c_value_reported_not_approved(self):
        r=self.get();self.assertEqual(r['samples'][0]['fields']['0x15c']['f32'],1200)
        self.assertEqual(r['samples'][0]['fields']['0x2e4']['f32'],-1)
        self.assertFalse(r['approved_for_write'])
    def test_no_heap_walk_or_large_heap_read(self):
        read=self.m.read
        def bounded(a,n):
            if self.m.obj<=a<self.m.obj+len(self.m.heap):self.assertLessEqual(a+n,self.m.obj+0x328)
            return read(a,n)
        self.m.read=bounded;self.assertTrue(self.get()['complete'])
    def test_changed_build_rejected(self):
        with patch('zoom.details.EXPECTED',(0,0,0,'')):
            with self.assertRaisesRegex(ValueError,'Сборка'):self.get()
    def test_changed_prefix_prevents_long_reads(self):
        _,rva,_,_=self.targets[-1];self.m.put(self.m.base+rva,b'\x90')
        r=self.get();self.assertFalse(r['complete']);self.assertEqual(r['targeted_code'],[])
        self.assertEqual(r['samples'],[])
    def test_target_pointer_mismatch(self):
        self.m.put(self.m.vtable+3*8,struct.pack('<Q',self.m.base+0x2500))
        r=self.get();self.assertFalse(r['complete']);self.assertEqual(r['targeted_code'],[])
    def test_short_slots_stop_at_nonexecutable(self):
        self.m.put(self.m.vtable+40*8,struct.pack('<Q',self.m.obj))
        r=self.get();self.assertTrue(r['complete']);self.assertEqual(len(r['candidate_vtable_slots']),41)
        self.assertIn('stopped',r['candidate_vtable_slots'][-1]['status'])
    def test_cancel_raises(self):
        with self.assertRaises(InterruptedError):details.capture_details(self.m,self.c,lambda:True)
        self.assertFalse(self.m.writes)
    def test_deadline_explicit_partial(self):
        with patch('zoom.details.time.monotonic',side_effect=[0,4,4]):r=self.get()
        self.assertFalse(r['complete']);self.assertTrue(r['errors']);self.assertEqual(r['targeted_code'],[])
    def test_camera_swap_discards_sample(self):
        read=self.m.read
        def changed(a,n):
            raw=read(a,n)
            if a==self.m.obj and n==details.OBJECT_BYTES:self.m.put(self.m.slot,struct.pack('<Q',self.m.obj+8))
            return raw
        self.m.read=changed;r=self.get();self.assertFalse(r['complete']);self.assertEqual(r['samples'],[])
    def test_unstable_code_not_reported_as_good(self):
        read=self.m.read;seen=[]
        def changed(a,n):
            raw=read(a,n)
            if n==8192:
                seen.append(1)
                if len(seen)==2:return b'\x90'+raw[1:]
            return raw
        self.m.read=changed;r=self.get();self.assertFalse(r['complete'])
        self.assertFalse(any(x['slot_index']==3 for x in r['targeted_code']))
    def test_no_write_call_even_if_api_available(self):
        self.m.write=lambda *a:self.fail('write called')
        self.assertTrue(self.get()['complete'])
    def test_json_roundtrip_and_bound(self):
        r=self.get();p=save(r,self.root/'zoom_details.json')
        self.assertEqual(json.loads(p.read_text()),r);self.assertLess(p.stat().st_size,128*1024)
    def test_nan_serialized_without_nonstandard_json(self):
        self.m.put(self.m.obj+0x15c,struct.pack('<f',float('nan')))
        r=self.get();self.assertIsNone(r['samples'][0]['fields']['0x15c']['f32']);json.dumps(r,allow_nan=False)
    def test_cli_readonly_and_closes(self):
        closed=[];self.m.close=lambda:closed.append(True)
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.m.base,size=self.m.size),end_pressed=lambda:False)
        with patch('zoom.win32.API',return_value=api),patch('zoom.win32.Memory',return_value=self.m) as constructor,patch('zoom.details.__file__',str(self.root/'details.py')):
            self.assertEqual(details.main(),0)
        constructor.assert_called_once_with(api,123,readonly=True)
        self.assertEqual(closed,[True]);self.assertTrue((self.root/'zoom_details.json').exists())
    def test_cli_error_closes_and_does_not_claim_new_report(self):
        closed=[];self.m.close=lambda:closed.append(True)
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.m.base,size=self.m.size),end_pressed=lambda:False)
        with patch('zoom.win32.API',return_value=api),patch('zoom.win32.Memory',return_value=self.m),patch('zoom.details.EXPECTED',(0,0,0,'')),patch('zoom.details.__file__',str(self.root/'details.py')):
            self.assertEqual(details.main(),1)
        self.assertEqual(closed,[True]);self.assertFalse((self.root/'zoom_details.json').exists())


if __name__=='__main__':unittest.main()
