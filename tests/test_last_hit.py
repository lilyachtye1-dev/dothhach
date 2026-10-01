"""Offline Last Hit contracts: model, local KV/VPK, actual input transaction, IPC."""
import ctypes as C
from dataclasses import replace
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from helpers import ROOT
from last_hit_assets import kv1, vpk_read, AttackData
from last_hit_core import Unit, Frame, Shot, Model, armor_multiplier
from last_hit_service import LastHit, validate, valid_bind
import test_alpha6 as alpha6
import test_service as service
import test_target_probe as probe_tests
from last_hit_reader import Reader


def unit(kind='creep',uid=(200,0x10002),**kw):
    d=dict(uid=uid,name='npc_dota_creep_badguys_melee',kind=kind,team=3,hp=50,maxhp=500,pos=(100,0,0),screen=(800,400),
        yaw=0,damage_min=50,damage_max=50,armor=0,regen=0,attack_range=600,hull=24,bat=1,attack_point=.1,turn_rate=.6,
        projectile_speed=0,interval=1,last_attack=0,target=(),visible=True)
    d.update(kw);return Unit(**d)

def hero(**kw):
    d=dict(team=2,name='npc_dota_hero_lion',pos=(0,0,0),damage_min=100,damage_max=100,hp=800,maxhp=1000)
    d.update(kw);return unit('hero',(100,0x10001),**d)

def frame(creeps=None,towers=(),h=None,now=10):
    return Frame(now,now,(1920,1080),h or hero(),tuple(creeps if creeps is not None else (unit(),)),tuple(towers))


class ModelTests(unittest.TestCase):
    def setUp(self):self.m=Model()
    def choose(self,f=None,**kw):
        f=f or frame();self.m.update(f);return self.m.choose(f,**kw)[0]
    def test_basic_enemy_last_hit(self):self.assertEqual(self.choose().kind,'last_hit')
    def test_deny_enabled_and_threshold(self):
        self.assertEqual(self.choose(frame([unit(team=2)])).kind,'deny')
        self.assertIsNone(self.choose(frame([unit(team=2)]),deny=False))
        self.assertIsNone(self.choose(frame([unit(team=2,hp=260)])))
    def test_enemy_has_priority_over_deny(self):
        self.assertEqual(self.choose(frame([unit(team=2,hp=1,uid=(201,0x10003)),unit()])).kind,'last_hit')
    def test_never_chases_or_clicks_hud_fog_dead(self):
        for changed in ({'pos':(3000,0,0)},{'screen':(100,1000)},{'screen':()},{'visible':False},{'hp':0}):
            self.assertIsNone(self.choose(frame([unit(**changed)])))
    def test_min_damage_not_random_maximum(self):
        self.assertIsNone(self.choose(frame([unit(hp=75)],h=hero(damage_min=50,damage_max=120))))
    def test_armor_reduces_damage(self):
        self.assertLess(armor_multiplier(10),1)
        self.assertGreater(armor_multiplier(-10),1)
        self.assertIsNone(self.choose(frame([unit(hp=90,armor=10)])))
    def test_attack_cooldown(self):self.assertIsNone(self.choose(frame(h=hero(last_attack=9.5))))
    def test_turn_and_projectile_increase_impact(self):
        c=unit(pos=(500,0,0));a=self.m.impact(hero(),c,10,.08)
        b=self.m.impact(hero(yaw=180,projectile_speed=500),c,10,.08)
        self.assertGreater(b[0],a[0]+1.)
    def test_repeated_attack_stamps_calibrate_interval(self):
        for now,stamp in ((10,9.8),(10.5,10.3),(11,10.8)):
            f=frame(h=hero(last_attack=stamp),now=now)
            # Sampling must be continuous, not gaps >350ms.
            self.m.previous[f.hero.uid]=(now-.025,stamp-.5,())
            self.m.update(f)
        self.assertAlmostEqual(self.m.measured_interval(f.hero,11),.5)
    def test_incomplete_or_error_frame_blocks(self):
        self.assertIsNone(self.choose(replace(frame(),incomplete=True)))
        self.assertIsNone(self.choose(replace(frame(),error='unreadable')))
    def test_forecast_rejects_dead_before_impact(self):
        c=unit(hp=20)
        self.m.history[c.uid]=[(9.5,500),(9.8,100),(10,20)]
        self.assertIsNone(self.m.choose(frame([c]))[0])
    def test_tower_event_requires_stable_exact_target(self):
        c=unit();t=unit('tower',(300,0x10003),target=c.uid,pos=(400,0,0),projectile_speed=500,last_attack=9.0)
        self.m.update(frame([c],[t],now=9.98));self.m.update(frame([c],[replace(t,last_attack=10.0)],now=10.01))
        self.assertEqual(len(self.m.shots),1);self.assertEqual(self.m.shots[0].target,c.uid)
        self.m.update(frame([c],[replace(t,last_attack=10.0)],now=10.03));self.assertEqual(len(self.m.shots),1)
    def test_first_seen_tower_does_not_invent_a_shot(self):
        c=unit();t=unit('tower',target=c.uid,projectile_speed=750,last_attack=10)
        self.m.update(frame([c],[t]));self.assertFalse(self.m.shots)
    def test_ambiguous_target_change_does_not_attribute_new_shot(self):
        a=unit();b=unit(uid=(201,0x10003));t=unit('tower',(300,0x10004),target=a.uid,projectile_speed=500,last_attack=9.8)
        self.m.update(frame([a,b],[t],now=9.9));self.m.update(frame([a,b],[replace(t,target=b.uid,last_attack=10)],now=10.01))
        self.assertFalse(self.m.shots)
    def test_flying_shot_survives_later_tower_target_switch(self):
        a=unit();b=unit(uid=(201,0x10003));t=unit('tower',(300,0x10004),target=a.uid,pos=(500,0,0),projectile_speed=500,last_attack=9.8)
        self.m.update(frame([a,b],[t],now=9.9));self.m.update(frame([a,b],[replace(t,last_attack=10)],now=10.01))
        self.m.update(frame([a,b],[replace(t,target=b.uid,last_attack=10)],now=10.02))
        self.assertEqual(self.m.shots[0].target,a.uid)
    def test_recycled_creep_does_not_inherit_shot(self):
        c=unit();self.m.shots=[Shot((3,3),c.uid,10,10.5,10.8,50,60)]
        self.m.update(frame([replace(c,uid=(200,0x20002))]));self.assertFalse(self.m.shots)
    def test_tower_prepare_then_last_hit(self):
        c=unit(hp=180);h=hero(damage_min=50,damage_max=50)
        self.m.shots=[Shot((300,0x10003),c.uid,9.99,10.6,10.8,80,120)]
        pick=self.m.choose(frame([c],h=h),margin=0)[0]
        self.assertEqual(pick.kind,'prepare')
        self.assertIsNone(self.m.choose(frame([c],h=h),tower=False,margin=0)[0])
        # After the actual tower damage has been observed, no synthetic HP subtraction.
        self.m.shots=[]
        self.assertEqual(self.m.choose(frame([replace(c,hp=30)],h=h,now=11),margin=0)[0].kind,'last_hit')
    def test_no_preparation_if_own_attack_arrives_after_tower(self):
        c=unit(hp=180);self.m.shots=[Shot((3,3),c.uid,10,10.05,10.1,80,120)]
        pick=self.m.choose(frame([c],h=hero(damage_min=50,damage_max=50)),margin=0)[0]
        self.assertIsNone(pick)
    def test_shot_already_in_hp_not_subtracted_as_certain(self):
        c=unit(hp=100);s=Shot((3,3),c.uid,9,9.9,10.3,80,100)
        low,high=self.m.hp_bounds(c,10,10.4,10.5,[s]);self.assertEqual(high,100)
    def test_hero_change_and_time_reset_drop_history(self):
        self.m.update(frame());self.m.shots=[Shot((3,3),unit().uid,10,20,21,10,20)]
        self.m.update(frame(h=replace(hero(),uid=(101,5)),now=10.1));self.assertFalse(self.m.shots)


class AssetsTests(unittest.TestCase):
    def test_nested_kv(self):
        self.assertEqual(kv1('"x" { "a" ".3" // hi\n "nested" {"b" "4"} }')['x']['nested']['b'],'4')
    def test_bad_kv_refused(self):
        for text in ('"x" { "a" "b"','}'):self.assertRaises(ValueError,kv1,text)
    def test_local_hero_defaults_and_missing_data(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'scripts/npc';p.mkdir(parents=True)
            (p/'npc_heroes.txt').write_text('"DOTAHeroes" {"npc_dota_hero_base" {"AttackAnimationPoint" ".3" "MovementTurnRate" ".6" "ProjectileSpeed" "900"} "npc_dota_hero_lion" {"AttackAnimationPoint" ".43"}}')
            (p/'npc_units.txt').write_text('"DOTAUnits" {}')
            a=AttackData(t);self.assertEqual(a.timing('npc_dota_hero_lion',True),(.43,.6,900))
            self.assertRaises(ValueError,a.timing,'missing',True)
    def vpk(self,folder,version=2,external=False,bad=False):
        data=b'hello npc';pre=data[:3];body=data[3:]
        entry=struct.pack('<IHHIIH',0 if bad else zlib.crc32(data),len(pre),0 if external else 0x7fff,0,len(body),0xffff)
        tree=b'txt\0scripts/npc\0npc_units\0'+entry+pre+b'\0\0\0'
        header=struct.pack('<III',0x55aa1234,version,len(tree))+(bytes(16) if version==2 else b'')
        p=Path(folder)/'pak01_dir.vpk';p.write_bytes(header+tree+(b'' if external else body))
        if external:(Path(folder)/'pak01_000.vpk').write_bytes(body)
        return p
    def test_vpk_embedded_v1_v2_and_external_preload(self):
        with tempfile.TemporaryDirectory() as t:
            for version,external in ((1,False),(2,False),(2,True)):
                self.assertEqual(vpk_read(self.vpk(t,version,external),'scripts/npc/npc_units.txt'),'hello npc')
    def test_vpk_corruption_missing_and_oversize_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertRaises(ValueError,vpk_read,self.vpk(t,bad=True),'scripts/npc/npc_units.txt')
            self.assertRaises(FileNotFoundError,vpk_read,self.vpk(t),'scripts/npc/not_there.txt')


def command(**kw):
    d=dict(session='session',revision=1,utc_ms=100000,enabled=True,key=5,attack_key=65,deny=True,tower=True,menu_open=False,latency_ms=80)
    d.update(kw);return d


class ProtocolTests(unittest.TestCase):
    def test_default_mouse4_and_alternative_mouse_keys(self):
        self.assertTrue(validate(command(),'session',100000))
        for k in (4,5,6,81,112,123):self.assertTrue(valid_bind(k))
    def test_reserved_and_bool_keys_refused(self):
        for k in (True,1,2,9,13,27,35,45,91,92,0,256):self.assertFalse(valid_bind(k))
    def test_strict_shape_types_and_expiry(self):
        for kw in ({'enabled':1},{'key':True},{'key':65},{'attack_key':5},{'latency_ms':301},{'revision':-1},{'utc_ms':97000},{'utc_ms':101000},{'session':'old'},{'junk':1}):
            self.assertFalse(validate(command(**kw),'session',100000),kw)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.h=alpha6.InputHarness();self.h.f.keys={5};self.h.s.defensive_input=False
        class Mouse(C.Structure):_fields_=[('dwFlags',C.c_uint32)]
        class Key(C.Structure):_fields_=[('wVk',C.c_uint16),('dwFlags',C.c_uint32)]
        class Input(C.Structure):_fields_=[('type',C.c_uint32),('ki',Key),('mi',Mouse)]
        self.h.b.INPUT=Input;self.packets=[];self.count=None;self.hook=None
        def send(n,arr,size):
            if hasattr(arr,'_obj'):arr=[arr._obj]
            events=[('key',arr[i].ki.wVk,bool(arr[i].ki.dwFlags&2)) if arr[i].type==1 else ('mouse',arr[i].mi.dwFlags) for i in range(n)]
            self.packets.append(events);count=n if self.count is None else self.count;self.count=None
            for e in events[:count]:
                if e[0]=='key':
                    if e[2]:self.h.f.keys.discard(e[1])
                    else:self.h.f.keys.add(e[1])
                elif e[1]==2:self.h.f.keys.add(1)
                elif e[1]==4:self.h.f.keys.discard(1)
            if self.hook:self.hook(events)
            return count
        self.h.b.u.SendInput=send
    def cast(self):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            return self.h.b.last_hit_attack((800,400),65,self.h.f.ctx,lambda:5 in self.h.f.keys and self.h.f.armed)
    def test_mouse4_is_held_and_one_attack_click_emitted(self):
        self.assertEqual(self.cast()[0],'sent')
        self.assertEqual(self.packets,[[('key',65,False),('key',65,True),('mouse',2),('mouse',4)]])
        self.assertEqual(self.h.f.keys,{5})
    def test_release_before_call_sends_nothing(self):
        self.h.f.keys.clear();self.assertEqual(self.cast()[0],'skipped');self.assertFalse(self.packets)
    def test_release_during_aim_sends_nothing(self):
        self.h.f.move_hook=lambda:self.h.f.keys.discard(5)
        self.assertEqual(self.cast()[0],'skipped');self.assertFalse(self.packets)
    def test_menu_end_chat_own_attack_key_and_held_lmb_block(self):
        for k in (1,9,13,35,45,91,92,65):
            self.h.f.keys={5,k};self.assertEqual(self.cast()[0],'skipped');self.assertFalse(self.packets)
    def test_alt_is_temporarily_neutralized_and_restored(self):
        self.h.mods({164});self.assertEqual(self.cast()[0],'sent')
        self.assertEqual(self.packets[0][0],('key',164,True));self.assertEqual(self.h.f.keys,{5,164})
    def test_physical_alt_release_is_not_repressed(self):
        self.h.mods({164})
        self.hook=lambda e:self.h.physical.event(164,False) if ('key',65,False) in e else None
        self.assertEqual(self.cast()[0],'sent');self.assertEqual(self.h.f.keys,{5})
    def test_partial_mouse_down_is_released(self):
        self.count=3
        self.assertEqual(self.cast()[0],'failed');self.assertNotIn(1,self.h.f.keys);self.assertNotIn(65,self.h.f.keys)
        self.assertEqual(self.packets[-1],[('mouse',4)])
    def test_partial_key_down_is_released_without_click(self):
        self.count=1;self.assertEqual(self.cast()[0],'failed')
        self.assertEqual(self.packets[-1],[('key',65,True)]);self.assertNotIn(65,self.h.f.keys)
    def test_cursor_change_during_last_guard_never_clicks(self):
        calls=[]
        def allowed():
            calls.append(1)
            if len(calls)==3:self.h.f.cursor=(7,8)
            return True
        with patch('ks_input.time.perf_counter',return_value=10.1):
            result=self.h.b.last_hit_attack((800,400),65,self.h.f.ctx,allowed)
        self.assertEqual(result[0],'skipped');self.assertFalse(self.packets);self.assertEqual(self.h.f.cursor,(7,8))
    def test_exception_during_packet_releases_key_and_lmb(self):
        send=self.h.b.u.SendInput;calls=[]
        def fail(n,arr,size):
            calls.append(1)
            if len(calls)==1:
                self.h.f.keys.update({65,1});raise OSError('synthetic insertion exception')
            return send(n,arr,size)
        self.h.b.u.SendInput=fail
        self.assertEqual(self.cast()[0],'failed');self.assertEqual(self.h.f.keys,{5})
        self.assertEqual(self.packets,[[('key',65,True)],[('mouse',4)]])
    def test_focus_change_after_aim_blocks(self):
        self.h.f.move_hook=lambda:setattr(self.h.f,'focus',False)
        self.assertEqual(self.cast()[0],'skipped');self.assertFalse(self.packets)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.f=service.ServiceTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.s=self.f.ks.last_hit;self.s.reader=SimpleNamespace(latest=frame(now=10.14),verify_target=lambda *args:True);self.f.b.keys={5}
        self.calls=[]
        def send(point,key,ctx,allowed):
            if not allowed():return 'skipped','revoked'
            self.calls.append((point,key));return 'sent','fake'
        self.f.b.last_hit_attack=send
        self.doc=command(session=self.f.ks.ui_session)
        self.s.path.write_text(json.dumps(self.doc))
    def tick(self,now=10.14,blocked=False):
        self.s.reader.latest=replace(self.s.reader.latest,at=now,game_time=now)
        for h in self.f.heroes:
            if h.get('_ks'):h['_ks']=replace(h['_ks'],at=now)
        with patch('last_hit_service.time.time',return_value=100),patch('last_hit_service.time.perf_counter',return_value=now):
            self.s.tick(self.f.heroes,0,blocked)
    def test_mouse4_sends_then_pending_prevents_repeat(self):
        self.tick();self.assertEqual(len(self.calls),1)
        self.tick(10.16);self.assertEqual(len(self.calls),1)
    def test_released_hold_sends_no_attack(self):
        self.f.b.keys.clear();self.tick();self.assertFalse(self.calls);self.assertTrue(self.s.enabled)
    def test_ks_defense_reservation_blocks_last_hit(self):
        self.tick(blocked=True);self.assertFalse(self.calls)
    def test_menu_and_unready_data_block(self):
        self.f.ks.ui_menu_open=True;self.tick();self.assertFalse(self.calls)
        self.f.ks.ui_menu_open=False;self.s.reader.latest=replace(frame(),incomplete=True);self.tick();self.assertFalse(self.calls)
    def test_expired_or_old_session_cannot_arm(self):
        self.doc['session']='previous';self.s.path.write_text(json.dumps(self.doc));self.tick();self.assertFalse(self.calls)
    def test_missing_feedback_latches_off_not_repeat(self):
        self.tick();self.tick(12.2);self.assertFalse(self.s.enabled);self.assertTrue(self.s.fault);self.assertEqual(len(self.calls),1)
        self.tick(12.3);self.assertEqual(len(self.calls),1)
    def test_fresh_command_required_after_failure(self):
        self.tick();self.tick(12.2);self.doc['revision']=2;self.s.path.write_text(json.dumps(self.doc));self.tick(12.4)
        self.assertFalse(self.s.fault);self.assertEqual(len(self.calls),2)
    def test_preview_never_sends(self):
        self.f.ks.settings.auto_cast=False;self.tick();self.assertFalse(self.calls);self.assertIn('PREVIEW',self.s.status)
    def test_release_cancels_feedback_wait_without_fault(self):
        self.tick();self.f.b.keys.clear();self.tick(10.2);self.tick(12.3)
        self.assertFalse(self.s.fault);self.assertIsNone(self.s.pending);self.assertEqual(len(self.calls),1)
    def test_menu_interrupt_preserves_enabled_state(self):
        self.tick();self.f.ks.ui_menu_open=True;self.tick(10.2);self.tick(12.3)
        self.assertTrue(self.s.enabled);self.assertFalse(self.s.fault);self.assertIsNone(self.s.pending)
    def test_higher_priority_interrupt_is_not_misreported_as_input_failure(self):
        self.tick();self.tick(10.2,blocked=True);self.tick(12.3,blocked=True)
        self.assertFalse(self.s.fault);self.assertEqual(len(self.calls),1)
    def test_pre_send_entity_verifier_failure_cannot_emit(self):
        self.s.reader.verify_target=lambda *args:False;self.tick();self.assertFalse(self.calls)
    def test_expired_heartbeat_revokes_permission_between_polls(self):
        self.tick();self.s.pending=None;self.s.block_until=0
        self.doc['utc_ms']=97000;self.s.path.write_text(json.dumps(self.doc));self.tick(10.16)
        self.assertEqual(len(self.calls),1)
    def test_disabled_after_fresh_rev_and_hold_does_not_retry(self):
        self.doc['enabled']=False;self.s.path.write_text(json.dumps(self.doc));self.tick();self.assertFalse(self.calls)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.r=Reader.__new__(Reader);self.p=probe_tests.Probe();self.r.probe=self.p;self.r.verify_probe=self.p
        self.ep=0x20000;self.handle=0x10002;self.name='npc_dota_creep_lane';self.ident=0x90000;self.node=0xb0000
        self.entities={2:(self.ep,self.name),1:(0x40000,'npc_dota_hero_lion')}
        self.d=SimpleNamespace(get_entity=lambda idx:self.entities.get(idx),dormant_state=lambda ep:False,
            pos=lambda ep:(100,0,0),scene_node=lambda ep:self.node,cs=lambda p,n:'npc_dota_creep_badguys_melee',
            w2s=lambda m,p:(800,400,1),screen=(1920,1080),_screen_size=lambda:(1920,1080),raw_matrix=lambda:[1]*16,
            curtime=lambda:10)
        self.r.dot=self.r.verify_dot=self.d
        self.r.assets=SimpleNamespace(timing=lambda *a:(.3,.6,900))
        self.put_identity(self.ep,self.ident,self.handle)
        self.put_identity(0x40000,0xa0000,0x10001)
        self.field('C_BaseEntity','m_iHealth','<i',400);self.field('C_BaseEntity','m_iMaxHealth','<i',500)
        self.field('C_BaseEntity','m_iTeamNum','<B',3);self.field('C_BaseEntity','m_lifeState','<B',0)
        fields={'m_iszUnitName':('<Q',0xc0000),'m_iAttackCapabilities':('<i',1),'m_flBaseAttackTime':('<f',1.),
            'm_iBaseAttackSpeed':('<i',100),'m_iDamageMin':('<i',20),'m_iDamageMax':('<i',25),'m_iDamageBonus':('<i',5),
            'm_flPhysicalArmorValue':('<f',2.),'m_flHealthRegen':('<f',0.),'m_iAttackRange':('<i',150),
            'm_flHullRadius':('<f',24.),'m_flLastAttackTime':('<f',9.)}
        for key,(fmt,value) in fields.items():self.field('C_DOTA_BaseNPC',key,fmt,value)
        self.p.put(self.node+self.p.classes['CGameSceneNode']['fields']['m_angAbsRotation'],struct.pack('<fff',0,45,0))
    def field(self,cls,name,fmt,value):
        self.p.put(self.ep+self.p.classes[cls]['fields'][name],struct.pack(fmt,value))
    def put_identity(self,ep,ident,handle):
        self.p.put(ep+self.p.classes['CEntityInstance']['fields']['m_pEntity'],struct.pack('<Q',ident))
        self.p.put(ident,struct.pack('<QQI',ep,0,handle))
    def read(self,kind='creep'):return self.r.unit(self.ep,self.name,kind,10,[1]*16)
    def test_real_schema_fields_and_full_serial_identity(self):
        u=self.read();self.assertEqual(u.uid,(self.ep,self.handle));self.assertEqual((u.damage_min,u.damage_max),(25,30))
        self.assertAlmostEqual(u.yaw,45);self.assertEqual(u.name,'npc_dota_creep_badguys_melee')
    def test_unknown_or_dormant_unit_not_visible(self):
        for value in (None,True):
            self.d.dormant_state=lambda ep:value;self.assertIsNone(self.read())
    def test_nonfinite_stat_rejected_not_zero(self):
        self.field('C_DOTA_BaseNPC','m_flPhysicalArmorValue','<f',float('nan'))
        self.assertRaises(ValueError,self.read)
    def test_missing_armor_rejected_not_default(self):
        del self.p.memory[self.ep+self.p.classes['C_DOTA_BaseNPC']['fields']['m_flPhysicalArmorValue']]
        self.assertRaises(ValueError,self.read)
    def test_tower_handle_must_match_serial_not_just_index(self):
        self.field('C_DOTA_BaseNPC_Tower','m_hTowerAttackTarget','<I',0x20001)
        self.assertEqual(self.read('tower').target,())
        self.field('C_DOTA_BaseNPC_Tower','m_hTowerAttackTarget','<I',0x10001)
        self.assertEqual(self.read('tower').target,(0x40000,0x10001))
    def verify(self):return self.r.verify_target((self.ep,self.handle),(0x40000,0x10001),(800,400),400)
    def test_pre_send_verifier_accepts_current_identity_position_hp(self):self.assertTrue(self.verify())
    def test_pre_send_verifier_refuses_recycled_object(self):
        self.put_identity(self.ep,self.ident,0x20002);self.assertFalse(self.verify())
    def test_pre_send_verifier_refuses_healed_or_damaged_target(self):
        self.field('C_BaseEntity','m_iHealth','<i',450);self.assertFalse(self.verify())
    def test_pre_send_verifier_refuses_camera_or_target_motion(self):
        self.d.w2s=lambda m,p:(840,400,1);self.assertFalse(self.verify())
    def test_pre_send_verifier_refuses_dead_and_hidden(self):
        self.field('C_BaseEntity','m_lifeState','<B',1);self.assertFalse(self.verify())



class NativeTests(unittest.TestCase):
    def test_native_sidebar_build_and_mouse4_capture_present(self):
        self.assertIn('last_hit_menu.cs',(ROOT/'build_overlay.bat').read_text())
        source=(ROOT/'last_hit_menu.cs').read_text()
        self.assertIn('MouseButton.XButton1?5',source)
        self.assertIn('CaptureLastHitKey',(ROOT/'ks_menu.cs').read_text())
        self.assertIn('SelectScript("Last Hit")',(ROOT/'ks_menu.cs').read_text())
    def test_reader_has_no_sender_or_memory_writer(self):
        source=(ROOT/'last_hit_reader.py').read_text()
        for token in ('SendInput','write_bytes','WriteProcessMemory'):self.assertNotIn(token,source)


if __name__=='__main__':unittest.main()
