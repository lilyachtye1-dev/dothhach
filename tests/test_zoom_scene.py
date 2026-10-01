"""Scene named-field controls: synthetic fixtures, NOT a live compatibility test."""
import hashlib
import struct
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import test_zoom_verified as verified_fixture
from zoom import scene
from zoom.scene import Context,Group,RestoreError


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.fixture=verified_fixture.VerifiedTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.m=self.fixture.m;self.m.heap.extend(b'\0'*(0x40000-len(self.m.heap)))
        self.esys=self.m.obj+0x1000;self.chunk=self.m.obj+0x4000
        self.m.put(self.m.base+0x50100,struct.pack('<Q',self.esys))
        self.m.put(self.esys+0x10,struct.pack('<Q',self.chunk))
        code=b'\x90'*(scene.FARZ_BYTES-1)+b'\xc3';self.m.put(self.m.base+0x3000,code)
        self.m.put(self.m.vtable+21*8,struct.pack('<Q',self.m.base+0x3000))
        for key,value in [('ENTITY_SYSTEM_RVA',0x50100),('FARZ_RVA',0x3000),('FARZ_SHA256',hashlib.sha256(code).hexdigest())]:
            p=patch.object(scene,key,value);p.start();self.addCleanup(p.stop)
        self.eps={};self.original={}
        for i,(name,(cls,off,group)) in enumerate(scene.KINDS.items(),1):
            ep=self.m.obj+0x20000+(i-1)*0x1000;ident=self.chunk+i*0x70
            vt=self.m.base+0x46000+(i-1)*0x400;col=vt+0x100;typ=vt+0x180
            self.m.put(vt-8,struct.pack('<Q',col));self.m.put(vt,struct.pack('<Q',self.m.base+0x1400))
            self.m.put(col,struct.pack('<IIIIII',1,0,0,typ-self.m.base,0,col-self.m.base))
            self.m.put(typ+16,('.?AV'+cls+'@@\0').encode())
            self.m.put(ep,struct.pack('<Q',vt));self.m.put(ep+16,struct.pack('<Q',ident))
            self.m.put(ident,struct.pack('<Q',ep));self.m.put(ident+16,struct.pack('<I',0x8000+i))
            self.m.put(ident+32,struct.pack('<Q',ep+0x800));self.m.put(ep+0x800,name.encode()+b'\0')
            original=struct.pack('<f',-1 if group=='farz' else .8)
            self.m.put(ep+off,original);self.eps[name]=ep;self.original[ep+off]=original
        self.camera=self.fixture.find();self.c=Context(self.m,self.camera)
    def group(self,kind):return Group(self.c,kind)
    def value(self,name):return struct.unpack('<f',self.m.read(self.eps[name]+scene.KINDS[name][1],4))[0]
    def test_scan_is_readonly_and_typed(self):
        self.assertEqual(len(self.c.scan()),4);self.assertFalse(self.m.writes)
    def test_farz_roundtrip(self):
        g=self.group('farz');self.assertEqual(g.start(),1);self.assertEqual(self.value('dota_gamemode'),18000)
        g.poll();g.stop();self.assertEqual(self.value('dota_gamemode'),-1)
    def test_haze_three_named_fields_roundtrip(self):
        g=self.group('haze');self.assertEqual(g.start(),3)
        for name in self.eps:
            if name!='dota_gamemode':self.assertEqual(self.value(name),0)
        g.stop()
        for addr,old in self.original.items():self.assertEqual(self.m.read(addr,4),old)
    def test_farz_not_lowered_if_original_is_higher(self):
        ep=self.eps['dota_gamemode'];self.m.put(ep+0x760,struct.pack('<f',24000));g=self.group('farz')
        self.assertEqual(g.start(),0);g.poll();g.stop();self.assertEqual(self.value('dota_gamemode'),24000);self.assertFalse(self.m.writes)
    def test_groups_independent(self):
        a=self.group('farz');b=self.group('haze');a.start();b.start();a.stop()
        self.assertEqual(self.value('dota_gamemode'),-1);self.assertEqual(self.value('env_fog_controller'),0);b.stop()
    def test_only_explicit_float_fields_written(self):
        for kind in ('farz','haze'):
            g=self.group(kind);g.start();g.stop()
        self.assertTrue(all(addr in self.original and len(data)==4 for addr,data in self.m.writes))
        self.assertEqual(self.m.value(),-1) # camera override untouched
    def test_polls_do_not_enforce(self):
        g=self.group('haze');g.start()
        for _ in range(20):g.poll()
        self.assertEqual(len(self.m.writes),3)
    def test_wrong_rtti_refuses_before_writes(self):
        ep=self.eps['env_fog_controller'];vt=struct.unpack('<Q',self.m.read(ep,8))[0]
        self.m.put(vt+0x180+16,b'.?AVOther@@\0')
        with self.assertRaisesRegex(ValueError,'RTTI'):self.group('haze').start()
        self.assertFalse(self.m.writes)
    def test_bad_haze_identity_does_not_block_independent_farz(self):
        ep=self.eps['env_fog_controller'];self.m.put(ep+16,b'\0'*8)
        g=self.group('farz');g.start();g.stop()
    def test_handle_serial_change_prevents_restore(self):
        g=self.group('farz');g.start();ident=g.records[0][0].identity
        self.m.put(ident+16,struct.pack('<I',0x10001))
        with self.assertRaises(RestoreError):g.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_backlink_required(self):
        self.m.put(self.eps['dota_gamemode']+16,b'\0'*8)
        with self.assertRaises(ValueError):self.group('farz').start()
        self.assertFalse(self.m.writes)
    def test_far_method_changed_refused(self):
        self.m.put(self.m.base+0x3000,b'\xcc')
        with self.assertRaisesRegex(ValueError,'FarZ'):self.group('farz').start()
        self.assertFalse(self.m.writes)
    def test_game_scene_change_prevents_restoring_old_object(self):
        g=self.group('farz');g.start();self.m.put(self.m.base+0x50100,struct.pack('<Q',self.esys+8))
        with self.assertRaises(RestoreError):g.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_foreign_writer_not_overwritten(self):
        g=self.group('farz');g.start();self.m.put(self.eps['dota_gamemode']+0x760,struct.pack('<f',20000))
        with self.assertRaises(ValueError):g.poll()
        with self.assertRaises(RestoreError):g.stop()
        self.assertEqual(self.value('dota_gamemode'),20000);self.assertEqual(len(self.m.writes),1)
    def test_invalid_density_refuses_whole_plan(self):
        self.m.put(self.eps['env_cubemap_fog']+0x618,struct.pack('<f',5))
        with self.assertRaises(ValueError):self.group('haze').start()
        self.assertFalse(self.m.writes)
    def test_invalid_farz_and_nonfinite_refused(self):
        for n in (-2,1,float('nan'),float('inf'),70000):
            self.m.put(self.eps['dota_gamemode']+0x760,struct.pack('<f',n))
            with self.subTest(n=n),self.assertRaises(ValueError):self.group('farz').start()
        self.assertFalse(self.m.writes)
    def test_zero_farz_accepted_as_original(self):
        self.m.put(self.eps['dota_gamemode']+0x760,struct.pack('<f',0));g=self.group('farz');g.start();g.stop()
        self.assertEqual(self.value('dota_gamemode'),0)
    def test_failed_second_write_rolls_first_back(self):
        real=self.m.write;calls=[]
        def write(a,b):
            calls.append(a)
            if len(calls)==2:raise OSError('second write failed')
            return real(a,b)
        self.m.write=write;g=self.group('haze')
        with self.assertRaises(OSError):g.start()
        self.assertFalse(g.active);self.assertEqual(g.records,[])
        for addr,old in self.original.items():self.assertEqual(self.m.read(addr,4),old)
    def test_failed_partial_write_reports_rollback_failure(self):
        real=self.m.write;calls=[]
        def write(a,b):
            calls.append(a)
            if len(calls)==2:self.m.put(a,b'\xff'*4);raise OSError('partial')
            return real(a,b)
        self.m.write=write;g=self.group('haze')
        with self.assertRaises(RestoreError):g.start()
        self.assertEqual(g.records,[])
    def test_cancel_before_scan_no_writes(self):
        with self.assertRaises(InterruptedError):Group(self.c,'farz',lambda:True).start()
        self.assertFalse(self.m.writes)
    def test_scan_deadline(self):
        with patch('zoom.scene.time.monotonic',side_effect=[0,6]):
            with self.assertRaises(TimeoutError):self.c.scan()
    def test_missing_mode_refused(self):
        self.m.put(self.chunk+0x70,b'\0'*8)
        with self.assertRaisesRegex(ValueError,'не найдены'):self.group('farz').start()
    def test_unaligned_name_pointer_is_supported(self):
        ep=self.eps['dota_gamemode'];self.m.put(ep+0x901,b'dota_gamemode\0');self.m.put(self.chunk+0x70+32,struct.pack('<Q',ep+0x901))
        g=self.group('farz');g.start();g.stop()
    def test_noop_haze_does_not_claim_foreign_changes_on_stop(self):
        for name,ep in self.eps.items():
            if name!='dota_gamemode':self.m.put(ep+scene.KINDS[name][1],struct.pack('<f',0))
        g=self.group('haze');self.assertEqual(g.start(),0)
        self.m.put(self.eps['env_fog_controller']+0x620,struct.pack('<f',.5))
        with self.assertRaises(ValueError):g.poll()
        g.stop();self.assertFalse(self.m.writes)

    def worker(self):
        from zoom.scene_app import Worker
        self.end=False;self.m.close=lambda:None
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.m.base,size=self.m.size,path='synthetic'),end_pressed=lambda:self.end)
        for name,value in [('API',api),('Memory',self.m)]:
            p=patch('zoom.scene_app.'+name,return_value=value);p.start();self.addCleanup(p.stop)
        legacy=patch('zoom.scene_app.DirectFarZ',side_effect=lambda c,cancel:Group(c,'farz',cancel));legacy.start();self.addCleanup(legacy.stop)
        self.w=Worker();self.w.start();self.addCleanup(self.stop_worker);self.wait(lambda e:e['ready'] and e['kind']=='state')
    def stop_worker(self):self.w.quit.set();self.w.join(3)
    def wait(self,pred):
        import time
        end=time.monotonic()+3
        while time.monotonic()<end:
            e=self.w.events.get(timeout=max(.01,end-time.monotonic()))
            if e['kind'] in ('error','restore_error'):self.fail(e['text'])
            if pred(e):return e
        self.fail('worker timeout')
    def test_worker_both_options_restore_on_close(self):
        self.worker();self.assertFalse(self.m.writes)
        self.w.commands.put('farz');self.wait(lambda e:e['farz'])
        self.w.commands.put('haze');self.wait(lambda e:e['haze'])
        self.w.quit.set();self.assertTrue(self.w.done.wait(3))
        for addr,old in self.original.items():self.assertEqual(self.m.read(addr,4),old)
    def test_worker_end_restores(self):
        self.worker();self.w.commands.put('farz');self.wait(lambda e:e['farz']);self.end=True
        self.assertTrue(self.w.done.wait(3));self.assertEqual(self.value('dota_gamemode'),-1)


if __name__=='__main__':unittest.main()
