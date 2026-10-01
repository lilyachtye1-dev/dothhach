"""Synthetic PE/RTTI/process memory tests; not Windows or live Dota proof."""
import math
import struct
import unittest
from dataclasses import replace
from zoom.locator import PATTERN,locate,read_image,camera_type,matches,DISTANCE_OFFSET
from zoom.control import Control,target_bytes


class Memory:
    def __init__(self):
        self.base=0x10000000;self.size=0x90000;self.data=bytearray(self.size)
        self.obj=0x40000000;self.heap=bytearray(0x1000);self.writes=[];self.living=True;self.writeable=True
        self.put(self.base,b'MZ');self.put(self.base+60,struct.pack('<I',0x80))
        header=bytearray(24);header[:4]=b'PE\0\0';struct.pack_into('<HHI',header,4,0x8664,3,123456);struct.pack_into('<H',header,20,240)
        self.put(self.base+0x80,header);optional=bytearray(240);struct.pack_into('<H',optional,0,0x20b);struct.pack_into('<I',optional,56,self.size)
        self.put(self.base+0x98,optional);self.table=self.base+0x188
        for i,(name,rva,size,flags) in enumerate(((b'.text',0x1000,0x42000,0x60000020),(b'.rdata',0x45000,0x4000,0x40000040),(b'.data',0x50000,0x3000,0xc0000040))):
            row=bytearray(40);row[:len(name)]=name;struct.pack_into('<II',row,8,size,rva);struct.pack_into('<I',row,36,flags);self.put(self.table+i*40,row)
        self.sig=self.base+0x1100;self.slot=self.base+0x50040;self.vtable=self.base+0x45100
        self.add_signature(self.sig)
        self.put(self.slot,struct.pack('<Q',self.obj));self.put(self.obj,struct.pack('<Q',self.vtable))
        self.col=self.base+0x45000;self.put(self.vtable-8,struct.pack('<Q',self.col))
        self.put(self.col,struct.pack('<IIIIII',1,0,0,0x45200,0x45300,0x45000))
        self.name_at=self.base+0x45210;self.put(self.name_at,b'.?AVCDOTA_Camera@@\0')
        for i in range(3):self.put(self.vtable+i*8,struct.pack('<Q',self.base+0x1300+i*0x10))
        self.put(self.obj+DISTANCE_OFFSET,struct.pack('<f',1200))
    def add_signature(self,where):
        raw=bytearray(v or 0 for v in PATTERN);raw[12]=0x0d
        struct.pack_into('<i',raw,13,self.slot-0x20-(where+17));self.put(where,raw)
    def read(self,address,size):
        if not self.living:raise OSError('dead')
        if self.base<=address and address+size<=self.base+self.size:return bytes(self.data[address-self.base:address-self.base+size])
        if self.obj<=address and address+size<=self.obj+len(self.heap):return bytes(self.heap[address-self.obj:address-self.obj+size])
        raise OSError('unmapped synthetic memory')
    def put(self,address,data):
        if self.base<=address<self.base+self.size:self.data[address-self.base:address-self.base+len(data)]=data
        elif self.obj<=address<self.obj+len(self.heap):self.heap[address-self.obj:address-self.obj+len(data)]=data
        else:raise OSError('bad test address')
    def writable(self,address,size):return self.living and self.writeable and self.obj<=address and address+size<=self.obj+len(self.heap)
    def alive(self):return self.living
    def write(self,address,data):
        if not self.writable(address,len(data)):raise OSError('not writable')
        self.writes.append((address,bytes(data)));self.put(address,data)
    def value(self):return struct.unpack('<f',self.read(self.obj+DISTANCE_OFFSET,4))[0]


class LocatorTests(unittest.TestCase):
    def setUp(self):self.m=Memory()
    def find(self):return locate(self.m,self.m.base,self.m.size)
    def test_locates_source_profile_without_writing(self):
        c=self.find();self.assertEqual(c.obj,self.m.obj);self.assertEqual(c.address,self.m.obj+0x2e4);self.assertEqual(c.slot,self.m.slot);self.assertFalse(self.m.writes)
    def test_pattern_crossing_chunk_boundary_is_found(self):
        self.m.put(self.m.sig,b'\0'*len(PATTERN));where=self.m.base+0x1000+256*1024-12;self.m.add_signature(where)
        self.assertEqual(self.find().signature_address,where)
    def test_duplicate_is_not_first_match_wins(self):
        self.m.add_signature(self.m.sig+100)
        with self.assertRaisesRegex(ValueError,'несколько'):self.find()
    def test_missing_signature_never_guesses(self):
        self.m.put(self.m.sig,b'\0'*len(PATTERN))
        with self.assertRaisesRegex(ValueError,'не найдена'):self.find()
    def test_match_in_data_is_ignored(self):
        self.m.put(self.m.sig,b'\0'*len(PATTERN));self.m.add_signature(self.m.base+0x51000)
        with self.assertRaisesRegex(ValueError,'не найдена'):self.find()
    def test_unreadable_code_chunk_fails_closed(self):
        read=self.m.read
        def broken(a,n):
            if n>100000:raise OSError('unreadable code')
            return read(a,n)
        self.m.read=broken
        with self.assertRaises(OSError):self.find()
    def test_cancellation_before_scan(self):
        with self.assertRaises(InterruptedError):locate(self.m,self.m.base,self.m.size,lambda:True)
        self.assertFalse(self.m.writes)
    def test_wrong_instruction_modrm(self):
        self.m.put(self.m.sig+12,b'\x0c')
        with self.assertRaisesRegex(ValueError,'RIP'):self.find()
    def test_manager_must_be_data_not_code(self):
        self.m.slot=self.m.base+0x2000;self.m.add_signature(self.m.sig)
        with self.assertRaisesRegex(ValueError,'manager'):self.find()
    def test_null_camera_not_ready(self):
        self.m.put(self.m.slot,b'\0'*8)
        with self.assertRaisesRegex(ValueError,'не готов'):self.find()
    def test_wrong_rtti_name_rejected(self):
        self.m.put(self.m.name_at,b'.?AVSomeUnrelatedCamera@@\0')
        with self.assertRaisesRegex(ValueError,'RTTI'):self.find()
    def test_rtti_self_identity_rejected(self):
        self.m.put(self.m.col+20,struct.pack('<I',0x45008))
        with self.assertRaisesRegex(ValueError,'RTTI'):self.find()
    def test_vtable_function_must_be_executable(self):
        self.m.put(self.m.vtable,struct.pack('<Q',self.m.base+0x45180))
        with self.assertRaisesRegex(ValueError,'vtable'):self.find()
    def test_no_execute_page_writes(self):
        self.m.writeable=False
        with self.assertRaisesRegex(ValueError,'записи'):self.find()
    def test_unplausible_and_nan_distance_rejected(self):
        for x in (0.,90.,99999.,float('nan'),float('inf')):
            self.m.put(self.m.obj+DISTANCE_OFFSET,struct.pack('<f',x))
            with self.subTest(x=x),self.assertRaises(ValueError):self.find()
    def test_stale_getdistance_field_not_used_as_fallback(self):
        self.m.put(self.m.obj+0x270,struct.pack('<f',1200));self.m.put(self.m.obj+DISTANCE_OFFSET,struct.pack('<f',0))
        with self.assertRaises(ValueError):self.find()
    def test_distance_changes_during_locator_check(self):
        read=self.m.read;seen=[]
        def changing(a,n):
            raw=read(a,n)
            if a==self.m.obj+DISTANCE_OFFSET:
                seen.append(1)
                if len(seen)>1:return struct.pack('<f',1300)
            return raw
        self.m.read=changing
        with self.assertRaisesRegex(ValueError,'меняется'):self.find()
    def test_x86_is_refused(self):
        self.m.put(self.m.base+0x84,struct.pack('<H',0x14c))
        with self.assertRaises(ValueError):self.find()
    def test_mismatched_module_size(self):
        with self.assertRaises(ValueError):locate(self.m,self.m.base,self.m.size-100)
    def test_overlapping_sections(self):
        self.m.put(self.m.table+40+12,struct.pack('<I',0x2000))
        with self.assertRaisesRegex(ValueError,'Пересекающиеся'):self.find()
    def test_pe_signature_checked(self):
        self.m.put(self.m.base+0x80,b'BAD!')
        with self.assertRaises(ValueError):self.find()
    def test_camera_swap_invalidates_snapshot(self):
        c=self.find();self.m.put(self.m.slot,struct.pack('<Q',self.m.obj+0x100))
        with self.assertRaisesRegex(ValueError,'сменился'):c.validate(self.m)
    def test_signature_change_invalidates_snapshot(self):
        c=self.find();self.m.put(self.m.sig,b'\x90')
        with self.assertRaisesRegex(ValueError,'Код'):c.validate(self.m)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.m=Memory();self.c=Control(self.m,locate(self.m,self.m.base,self.m.size))
    def test_connect_and_poll_never_write(self):
        for _ in range(10):self.assertEqual(self.c.poll(),1200)
        self.assertFalse(self.m.writes)
    def test_enable_sets_distance_once(self):
        self.assertEqual(self.c.arm(1500),1500)
        for _ in range(10):self.c.poll()
        self.assertEqual(len(self.m.writes),1)
    def test_slider_change_while_on(self):
        self.c.arm(1500);self.c.apply(1800);self.assertEqual(self.m.value(),1800)
    def test_repeated_value_not_rewritten(self):
        self.c.arm(1500);self.c.apply(1500);self.assertEqual(len(self.m.writes),1)
    def test_off_restores_exact_original_bytes(self):
        original=struct.pack('<f',1234.125);self.m.put(self.m.obj+DISTANCE_OFFSET,original)
        self.c.arm(1500);self.c.stop();self.assertEqual(self.m.read(self.m.obj+DISTANCE_OFFSET,4),original)
    def test_original_captured_at_enable_not_at_scan(self):
        self.m.put(self.m.obj+DISTANCE_OFFSET,struct.pack('<f',1350));self.c.arm(1500);self.c.stop();self.assertEqual(self.m.value(),1350)
    def test_second_disable_does_not_write(self):
        self.c.arm(1500);self.c.stop();count=len(self.m.writes);self.c.stop();self.assertEqual(count,len(self.m.writes))
    def test_never_enabled_stop_does_not_write(self):self.c.stop();self.assertFalse(self.m.writes)
    def test_no_writes_when_target_is_original(self):self.c.arm(1200);self.c.stop();self.assertFalse(self.m.writes)
    def test_user_range_and_nonfinite_values(self):
        for x in (999,2201,float('nan'),float('inf'),True,'1500'):
            with self.subTest(x=x),self.assertRaises(ValueError):self.c.arm(x)
        self.assertFalse(self.m.writes)
    def test_apply_while_off_rejected(self):
        with self.assertRaises(ValueError):self.c.apply(1500)
        self.assertFalse(self.m.writes)
    def test_no_double_arm(self):
        self.c.arm(1500)
        with self.assertRaises(ValueError):self.c.arm(1800)
        self.assertEqual(len(self.m.writes),1)
    def test_foreign_change_is_not_overwritten_or_restored(self):
        self.c.arm(1500);self.m.put(self.m.obj+DISTANCE_OFFSET,struct.pack('<f',1600))
        with self.assertRaises(ValueError):self.c.poll()
        with self.assertRaises(ValueError):self.c.stop()
        self.assertEqual(self.m.value(),1600);self.assertEqual(len(self.m.writes),1)
    def test_game_reset_to_original_does_not_start_rewrite_loop(self):
        self.c.arm(1500);self.m.put(self.m.obj+DISTANCE_OFFSET,struct.pack('<f',1200))
        with self.assertRaises(ValueError):self.c.poll()
        self.c.stop();self.assertEqual(len(self.m.writes),1);self.assertFalse(self.c.active)
    def test_dead_process_never_restored(self):
        self.c.arm(1500);self.m.living=False
        with self.assertRaises(ValueError):self.c.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_new_camera_never_receives_old_original(self):
        self.c.arm(1500);self.m.put(self.m.slot,struct.pack('<Q',self.m.obj+0x100))
        with self.assertRaises(ValueError):self.c.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_readback_failure_does_not_retry(self):
        def fail(a,raw):self.m.writes.append((a,raw))
        self.m.write=fail
        with self.assertRaises(ValueError):self.c.arm(1500)
        self.assertFalse(self.c.active);self.assertTrue(self.c.fault);self.c.stop();self.assertEqual(len(self.m.writes),1)
    def test_failed_api_after_complete_write_can_restore(self):
        write=self.m.write
        def fail(a,raw):write(a,raw);raise OSError('synthetic API failure')
        self.m.write=fail
        with self.assertRaises(OSError):self.c.arm(1500)
        self.m.write=write;self.c.stop();self.assertEqual(self.m.value(),1200)
    def test_partial_unknown_value_never_blindly_repaired(self):
        def partial(a,raw):self.m.writes.append((a,raw));self.m.put(a,b'\xaa\xbb\xcc\xdd');raise OSError('partial')
        self.m.write=partial
        with self.assertRaises(OSError):self.c.arm(1500)
        with self.assertRaises(ValueError):self.c.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_cancel_before_write_still_allows_restoration(self):
        self.c.arm(1500);self.c.cancel=lambda:True
        with self.assertRaises(InterruptedError):self.c.apply(1600)
        self.c.stop();self.assertEqual(self.m.value(),1200)
    def test_identity_rechecked_immediately_before_write(self):
        camera=self.c.camera;calls=[]
        class Swapping:
            address=camera.address
            def validate(inner,mem):
                calls.append(1)
                if len(calls)==3:mem.put(mem.slot,struct.pack('<Q',mem.obj+0x100))
                camera.validate(mem)
        self.c.camera=Swapping()
        with self.assertRaises(ValueError):self.c.arm(1500)
        self.assertFalse(self.m.writes)


class WorkerTests(unittest.TestCase):
    def start_worker(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from zoom.app import Worker
        self.m=Memory();self.end=False;self.m.close=lambda:None
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.m.base,size=self.m.size,path='synthetic/client.dll'),end_pressed=lambda:self.end)
        self.p1=patch('zoom.app.API',return_value=api);self.p1.start();self.addCleanup(self.p1.stop)
        self.p2=patch('zoom.app.Memory',return_value=self.m);self.p2.start();self.addCleanup(self.p2.stop)
        legacy=patch('zoom.app.locate',side_effect=locate);legacy.start();self.addCleanup(legacy.stop)
        self.w=Worker();self.w.start();self.addCleanup(self.cleanup_worker)
        return self.wait('ready')
    def cleanup_worker(self):self.w.quit.set();self.w.join(3)
    def wait(self,kind):
        import time
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            e=self.w.events.get(timeout=max(.01,deadline-time.monotonic()))
            if e['kind']==kind:return e
        self.fail('no worker event '+kind)
    def test_connection_is_read_only(self):
        e=self.start_worker();self.assertFalse(e['active']);self.assertFalse(self.m.writes)
    def test_close_restores_while_enabled(self):
        self.start_worker();self.w.commands.put(('on',1500));self.wait('ready')
        self.w.quit.set();self.assertTrue(self.w.done.wait(3));self.assertEqual(self.m.value(),1200)
    def test_end_restores_and_ends_worker(self):
        self.start_worker();self.w.commands.put(('on',1500));self.wait('ready')
        self.end=True;self.assertTrue(self.w.done.wait(3));self.assertEqual(self.m.value(),1200)
    def test_foreign_change_emits_error_without_replay(self):
        self.start_worker();self.w.commands.put(('on',1500));self.wait('ready')
        self.m.put(self.m.obj+DISTANCE_OFFSET,struct.pack('<f',1650));self.wait('error')
        self.assertEqual(self.m.value(),1650);self.assertIsNone(self.w.control)


if __name__=='__main__':unittest.main()
