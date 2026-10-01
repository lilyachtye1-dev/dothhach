"""Direct FarZ branch/ownership tests with constructed code and synthetic memory."""
import hashlib
import struct
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import test_zoom_scene as scene_fixture
from zoom import scene
from zoom.farz import DirectFarZ,layout_from_code
from zoom.control import Control


class FarZTests(unittest.TestCase):
    def setUp(self):
        self.f=scene_fixture.SceneTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.m=self.f.m;self.c=self.f.c;self.base=self.m.base
        heap_writable=self.m.writable
        self.m.writable=lambda a,n:heap_writable(a,n) or (self.m.living and self.m.writeable and self.base+0x50000<=a and a+n<=self.base+0x53000)
        self.selector_ref=self.base+0x50200;self.gate_ref=self.base+0x50208
        self.cache=self.base+0x50300;self.guard=self.cache+4;self.split=self.base+0x50400
        self.selector=self.m.obj+0x30000;self.gate=self.m.obj+0x30100
        self.m.put(self.selector_ref,struct.pack('<Q',self.selector));self.m.put(self.gate_ref,struct.pack('<Q',self.gate))
        self.m.put(self.selector+0x58,struct.pack('<i',0));self.m.put(self.gate+0x58,b'\0')
        self.m.put(self.guard,struct.pack('<i',-2147483646));self.m.put(self.cache,struct.pack('<f',-1))
        self.m.put(self.split+24,struct.pack('<f',-1))
        address=self.base+scene.FARZ_RVA;code=bytearray(b'\x90'*scene.FARZ_BYTES)
        for off,prefix,n,target in [(6,b'\x48\x8b\x05',7,self.selector_ref),(0x23,b'\x48\x8b\x05',7,self.gate_ref),(0x3e,b'\x48\x8d\x0d',7,self.split),(0x68,b'\x39\x05',6,self.guard),(0x74,b'\x48\x8d\x05',7,self.cache)]:
            code[off:off+len(prefix)]=prefix;struct.pack_into('<i',code,off+len(prefix),target-(address+off+n))
        self.m.put(address,code)
        p=patch.object(scene,'FARZ_SHA256',hashlib.sha256(code).hexdigest());p.start();self.addCleanup(p.stop)
        # Remove GameMode entirely; the new path must not need it.
        self.m.put(self.f.chunk+0x70,b'\0'*8)
        self.g=DirectFarZ(self.c)
    def value(self,a=None):return struct.unpack('<f',self.m.read(self.cache if a is None else a,4))[0]
    def test_direct_path_without_entity_scan_or_gamemode(self):
        self.c.scan=lambda *a:self.fail('FarZ must not scan entities')
        self.assertEqual(self.g.start(),1);self.assertEqual(self.value(),18000);self.g.stop();self.assertEqual(self.value(),-1)
    def test_parser_resolves_signed_rip_displacements(self):
        code=self.m.read(self.base+scene.FARZ_RVA,scene.FARZ_BYTES);r=layout_from_code(code,self.base+scene.FARZ_RVA)
        self.assertEqual(r.cache,self.cache);self.assertEqual(r.guard,self.guard);self.assertEqual(r.split_base,self.split)
    def test_bad_opcode_refused(self):
        code=bytearray(self.m.read(self.base+scene.FARZ_RVA,scene.FARZ_BYTES));code[6]=0x90
        with self.assertRaises(ValueError):layout_from_code(code,self.base+scene.FARZ_RVA)
    def test_preserves_positive_original(self):
        self.m.put(self.cache,struct.pack('<f',7000));self.g.start();self.g.stop();self.assertEqual(self.value(),7000)
    def test_higher_original_no_write(self):
        self.m.put(self.cache,struct.pack('<f',25000));self.assertEqual(self.g.start(),0);self.g.stop()
        self.assertEqual(self.value(),25000);self.assertFalse(self.m.writes)
    def test_uninitialized_guard_refused(self):
        for guard in (0,-1,1):
            self.m.put(self.guard,struct.pack('<i',guard))
            with self.subTest(guard=guard),self.assertRaisesRegex(ValueError,'Инициализация'):self.g.start()
        self.assertFalse(self.m.writes)
    def test_bad_selector_refused(self):
        self.m.put(self.selector+0x58,struct.pack('<i',99))
        with self.assertRaises(ValueError):self.g.start()
        self.assertFalse(self.m.writes)
    def test_bad_gate_refused(self):
        self.m.put(self.gate+0x58,b'\x02')
        with self.assertRaises(ValueError):self.g.start()
        self.assertFalse(self.m.writes)
    def test_invalid_values_refused(self):
        for n in (0,1,-2,float('nan'),float('inf'),70000):
            self.m.put(self.cache,struct.pack('<f',n))
            with self.subTest(n=n),self.assertRaises(ValueError):self.g.start()
        self.assertFalse(self.m.writes)
    def test_null_selector_pointer_refused(self):
        self.m.put(self.selector_ref,b'\0'*8)
        with self.assertRaises(ValueError):self.g.start()
        self.assertFalse(self.m.writes)
    def test_split_branch_selected_exactly(self):
        self.m.put(self.selector+0x58,struct.pack('<i',1));self.m.put(self.gate+0x58,b'\1')
        self.g.start();self.assertEqual(self.value(self.split+24),18000);self.assertEqual(self.value(),-1)
        self.g.stop();self.assertEqual(self.value(self.split+24),-1)
    def test_gate_off_keeps_default_branch_even_for_selector_one(self):
        self.m.put(self.selector+0x58,struct.pack('<i',1));self.g.start()
        self.assertEqual(self.value(),18000);self.assertEqual(self.value(self.split+24),-1);self.g.stop()
    def test_readonly_storage_never_unprotected(self):
        writable=self.m.writable;self.m.writable=lambda a,n:False if a==self.cache else writable(a,n)
        with self.assertRaisesRegex(ValueError,'RW'):self.g.start()
        self.assertFalse(self.m.writes)
    def test_cancel_before_write(self):
        self.g.cancel=lambda:True
        with self.assertRaises(InterruptedError):self.g.start()
        self.assertEqual(self.value(),-1);self.assertFalse(self.m.writes)
    def test_no_constant_enforcement(self):
        self.g.start()
        for _ in range(30):self.g.poll()
        self.assertEqual(len(self.m.writes),1)
    def test_foreign_change_refuses_restore(self):
        self.g.start();self.m.put(self.cache,struct.pack('<f',16000))
        with self.assertRaises(ValueError):self.g.poll()
        with self.assertRaises(scene.RestoreError):self.g.stop()
        self.assertEqual(self.value(),16000);self.assertEqual(len(self.m.writes),1)
    def test_already_restored_does_not_rewrite(self):
        self.g.start();self.m.put(self.cache,struct.pack('<f',-1))
        self.g.stop();self.assertEqual(len(self.m.writes),1)
    def test_branch_change_stops_but_can_restore_unchanged_static_storage(self):
        self.g.start();self.m.put(self.selector+0x58,struct.pack('<i',1));self.m.put(self.gate+0x58,b'\1')
        with self.assertRaises(ValueError):self.g.poll()
        self.g.stop();self.assertEqual(self.value(),-1);self.assertEqual(self.value(self.split+24),-1)
    def test_camera_swap_does_not_leave_global_override_stuck(self):
        self.g.start();self.m.put(self.m.slot,struct.pack('<Q',self.m.obj+8))
        with self.assertRaises(ValueError):self.g.poll()
        self.g.stop();self.assertEqual(self.value(),-1)
    def test_scene_swap_can_restore_same_module_storage(self):
        self.g.start();self.m.put(self.base+scene.ENTITY_SYSTEM_RVA,struct.pack('<Q',self.f.esys+8))
        with self.assertRaises(ValueError):self.g.poll()
        self.g.stop();self.assertEqual(self.value(),-1)
    def test_guard_epoch_change_refuses_restore(self):
        self.g.start();self.m.put(self.guard,struct.pack('<i',-2147483645))
        with self.assertRaises(scene.RestoreError):self.g.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_module_change_refuses_restore(self):
        self.g.start();self.m.put(self.base+0x88,struct.pack('<I',123457))
        with self.assertRaises(scene.RestoreError):self.g.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_code_change_prevents_new_write(self):
        self.m.put(self.base+scene.FARZ_RVA,b'\xcc')
        with self.assertRaises(ValueError):self.g.start()
        self.assertFalse(self.m.writes)
    def test_partial_write_not_blindly_repaired(self):
        def partial(a,b):self.m.put(a,b[:2]+self.m.read(a+2,2));raise OSError('partial write')
        self.m.write=partial
        with self.assertRaises(scene.RestoreError):self.g.start()
        self.assertFalse(self.g.active);self.assertIsNone(self.g.original)
    def test_write_then_error_can_conditionally_restore(self):
        real=self.m.write;calls=[]
        def write(a,b):
            real(a,b);calls.append(a)
            if len(calls)==1:raise OSError('failure after full write')
        self.m.write=write
        with self.assertRaises(OSError):self.g.start()
        self.assertEqual(self.value(),-1)
    def test_zoom_and_haze_still_independent(self):
        zoom=Control(self.m,self.f.camera);zoom.arm(1500);haze=scene.Group(self.c,'haze');haze.start()
        self.g.start();self.g.stop();self.assertEqual(zoom.poll(),1500)
        self.assertEqual(self.f.value('env_fog_controller'),0);haze.stop();zoom.stop()
    def test_only_selected_four_bytes_changed(self):
        self.g.start();self.g.stop()
        self.assertTrue(all(a==self.cache and len(b)==4 for a,b in self.m.writes));self.assertEqual(self.m.value(),-1)

    def worker(self):
        from zoom.scene_app import Worker
        self.end=False;self.m.close=lambda:None
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.base,size=self.m.size,path='synthetic'),end_pressed=lambda:self.end)
        for name,value in [('API',api),('Memory',self.m)]:
            p=patch('zoom.scene_app.'+name,return_value=value);p.start();self.addCleanup(p.stop)
        self.w=Worker();self.w.start();self.addCleanup(self.stop_worker);self.wait(lambda e:e['ready'] and e['kind']=='state')
    def stop_worker(self):self.w.quit.set();self.w.join(3)
    def wait(self,pred):
        import time
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            e=self.w.events.get(timeout=max(.01,deadline-time.monotonic()))
            if e['kind'] in ('error','restore_error'):self.fail(e['text'])
            if pred(e):return e
        self.fail('worker timeout')
    def test_worker_direct_farz_without_gamemode(self):
        self.worker();self.w.commands.put('farz');self.wait(lambda e:e['farz'])
        self.assertEqual(self.value(),18000);self.w.commands.put('farz');self.wait(lambda e:not e['farz'])
        self.assertEqual(self.value(),-1)
    def test_worker_close_restores(self):
        self.worker();self.w.commands.put('farz');self.wait(lambda e:e['farz']);self.w.quit.set()
        self.assertTrue(self.w.done.wait(3));self.assertEqual(self.value(),-1)
    def test_worker_end_restores(self):
        self.worker();self.w.commands.put('farz');self.wait(lambda e:e['farz']);self.end=True
        self.assertTrue(self.w.done.wait(3));self.assertEqual(self.value(),-1)


if __name__=='__main__':unittest.main()
