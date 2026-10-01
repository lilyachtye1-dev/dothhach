"""exp4 verified-profile/sentinel regression tests; synthetic memory, not live Dota."""
import hashlib
import math
import struct
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from test_zoom_standalone import Memory
from zoom import verified
from zoom.control import Control,decode,target_bytes
from zoom.locator import UnsupportedDistance,locate


class VerifiedTests(unittest.TestCase):
    def setUp(self):
        self.m=Memory();self.m.put(self.m.obj+0x2e4,struct.pack('<f',-1))
        self.m.put(self.m.obj+0xdc,struct.pack('<f',-1))
        self.m.put(self.m.obj+0x15c,struct.pack('<f',1200))
        self.update=self.m.base+0x2000
        self.code=b'\x90'*(verified.UPDATE_BYTES-1)+b'\xc3'
        self.m.put(self.update,self.code);self.m.put(self.m.vtable+24,struct.pack('<Q',self.update))
        pin=(123456,self.m.size,self.m.vtable-self.m.base,self.m.slot-self.m.base,self.m.sig-self.m.base,0x2000)
        for name,value in [('PIN',pin),('UPDATE_SHA256',hashlib.sha256(self.code).hexdigest())]:
            p=patch.object(verified,name,value);p.start();self.addCleanup(p.stop)
    def find(self):return verified.locate_verified(self.m,self.m.base,self.m.size)
    def control(self):return Control(self.m,self.find())
    def test_verified_auto_connect_does_not_write(self):
        c=self.control();self.assertFalse(c.active);self.assertEqual(c.poll(),-1);self.assertFalse(self.m.writes)
    def test_enable_slider_off_restores_minus_one_exactly(self):
        c=self.control();self.assertEqual(c.arm(1500),1500);self.assertEqual(c.apply(1750),1750)
        self.assertEqual(c.poll(),1750);self.assertIn('Автоматическая',c.stop())
        self.assertEqual(c.poll(),-1);self.assertEqual([d for _,d in self.m.writes],[struct.pack('<f',x) for x in (1500,1750,-1)])
    def test_positive_original_restored_not_default(self):
        self.m.put(self.m.obj+0x2e4,struct.pack('<f',1375));c=self.control();c.arm(1600);c.stop();self.assertEqual(self.m.value(),1375)
    def test_legacy_profile_still_refuses_sentinel(self):
        with self.assertRaises(UnsupportedDistance):locate(self.m,self.m.base,self.m.size)
        with self.assertRaises(ValueError):decode(struct.pack('<f',-1))
    def test_user_target_cannot_be_minus_one(self):
        with self.assertRaises(ValueError):target_bytes(-1)
    def test_invalid_original_values_rejected(self):
        for n in (-2,0,1,599,2601,float('nan'),float('inf')):
            self.m.put(self.m.obj+0x2e4,struct.pack('<f',n))
            with self.subTest(n=n),self.assertRaises(UnsupportedDistance):self.find()
        self.assertFalse(self.m.writes)
    def test_unknown_timestamp_rejected_even_with_plausible_distance(self):
        self.m.put(self.m.obj+0x2e4,struct.pack('<f',1200));self.m.put(self.m.base+0x88,struct.pack('<I',123457))
        with self.assertRaisesRegex(ValueError,'Сборка'):self.find()
        self.assertFalse(self.m.writes)
    def test_unknown_code_rejected(self):
        self.m.put(self.update+500,b'\xcc')
        with self.assertRaisesRegex(ValueError,'Код'):self.find()
    def test_wrong_update_pointer_rejected(self):
        self.m.put(self.m.vtable+24,struct.pack('<Q',self.update+16))
        with self.assertRaisesRegex(ValueError,'метод'):self.find()
    def test_code_change_after_connect_prevents_write(self):
        c=self.control();self.m.put(self.update+10,b'\xcc')
        with self.assertRaises(ValueError):c.arm(1500)
        self.assertFalse(self.m.writes)
    def test_camera_change_after_enable_prevents_restore(self):
        c=self.control();c.arm(1500);self.m.put(self.m.slot,struct.pack('<Q',self.m.obj+8))
        with self.assertRaises(ValueError):c.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_foreign_value_never_replayed(self):
        c=self.control();c.arm(1500);self.m.put(self.m.obj+0x2e4,struct.pack('<f',1600))
        with self.assertRaises(ValueError):c.poll()
        with self.assertRaises(ValueError):c.stop()
        self.assertEqual(self.m.value(),1600);self.assertEqual(len(self.m.writes),1)
    def test_game_returns_sentinel_no_unnecessary_restore(self):
        c=self.control();c.arm(1500);self.m.put(self.m.obj+0x2e4,struct.pack('<f',-1))
        with self.assertRaises(ValueError):c.poll()
        c.stop();self.assertEqual(len(self.m.writes),1)
    def test_animation_timer_rejected(self):
        for n in (0.5,float('nan'),float('inf')):
            self.m.put(self.m.obj+0xdc,struct.pack('<f',n))
            with self.subTest(n=n),self.assertRaisesRegex(ValueError,'переход'):self.find()
        self.assertFalse(self.m.writes)
    def test_zero_timer_allowed(self):
        self.m.put(self.m.obj+0xdc,struct.pack('<f',0));self.assertEqual(self.control().poll(),-1)
    def test_animation_started_after_connect_prevents_write(self):
        c=self.control();self.m.put(self.m.obj+0xdc,struct.pack('<f',.5))
        with self.assertRaises(ValueError):c.arm(1500)
        self.assertFalse(self.m.writes)
    def test_animation_during_ownership_stops_without_fighting_game(self):
        c=self.control();c.arm(1500);self.m.put(self.m.obj+0xdc,struct.pack('<f',1))
        with self.assertRaises(ValueError):c.poll()
        with self.assertRaises(ValueError):c.stop()
        self.assertEqual(len(self.m.writes),1)
    def test_repeat_toggle_roundtrips_auto(self):
        c=self.control()
        for target in (1000,1500,2200):c.arm(target);c.stop();self.assertEqual(c.poll(),-1)
        self.assertEqual(len(self.m.writes),6)
    def test_poll_is_not_continuous_enforcement(self):
        c=self.control();c.arm(1500)
        for _ in range(50):self.assertEqual(c.poll(),1500)
        self.assertEqual(len(self.m.writes),1)
    def test_ui_auto_not_negative_distance(self):
        from zoom.app import current_text
        self.assertEqual(current_text(-1,False),'авто игры   ·   OFF')
        self.assertEqual(current_text(1500,True),'задано 1500   ·   ON')
    def test_cancel_before_write(self):
        c=Control(self.m,self.find(),lambda:True)
        with self.assertRaises(InterruptedError):c.arm(1500)
        c.stop();self.assertEqual(self.m.value(),-1);self.assertFalse(self.m.writes)
    def test_all_writes_are_same_four_byte_override_field(self):
        c=self.control();c.arm(1500);c.apply(1800);c.stop()
        self.assertTrue(all(a==self.m.obj+0x2e4 and len(d)==4 for a,d in self.m.writes))
        self.assertEqual(struct.unpack('<f',self.m.read(self.m.obj+0x15c,4))[0],1200)

    def start_worker(self):
        from zoom.app import Worker
        self.end=False;self.m.close=lambda:None
        api=SimpleNamespace(dota_pid=lambda:123,module=lambda *a:SimpleNamespace(base=self.m.base,size=self.m.size,path='synthetic'),end_pressed=lambda:self.end)
        for name,value in [('API',api),('Memory',self.m)]:
            p=patch('zoom.app.'+name,return_value=value);p.start();self.addCleanup(p.stop)
        # Actual app locator is NOT substituted: verified path must succeed.
        self.w=Worker();self.w.start();self.addCleanup(self.stop_worker)
        self.wait('ready')
    def stop_worker(self):self.w.quit.set();self.w.join(3)
    def wait(self,kind):
        import time
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            e=self.w.events.get(timeout=max(.01,deadline-time.monotonic()))
            if e['kind']==kind:return e
            if e['kind']=='error':self.fail(e['text'])
        self.fail('no event '+kind)
    def test_worker_off_returns_auto(self):
        self.start_worker();self.assertFalse(self.m.writes)
        self.w.commands.put(('on',1500));self.assertTrue(self.wait('ready')['active'])
        self.w.commands.put(('off',None));e=self.wait('ready');self.assertEqual(e['current'],-1);self.assertFalse(e['active'])
    def test_worker_normal_close_restores_auto(self):
        self.start_worker();self.w.commands.put(('on',1500));self.wait('ready');self.w.quit.set()
        self.assertTrue(self.w.done.wait(3));self.assertEqual(self.m.value(),-1)
    def test_worker_end_restores_auto(self):
        self.start_worker();self.w.commands.put(('on',1500));self.wait('ready');self.end=True
        self.assertTrue(self.w.done.wait(3));self.assertEqual(self.m.value(),-1)
    def test_worker_slider_then_close_restores_auto(self):
        self.start_worker();self.w.commands.put(('on',1500));self.wait('ready')
        self.w.commands.put(('target',1750));self.assertEqual(self.wait('ready')['current'],1750)
        self.w.quit.set();self.assertTrue(self.w.done.wait(3));self.assertEqual(self.m.value(),-1)


if __name__=='__main__':unittest.main()
