import copy
import ctypes as C
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import struct
import unittest
from unittest.mock import patch

from helpers import ROOT, fixture, settings, fake_module
from ks_core import Settings, Action, choose
from ks_inventory import Inventory
from ks_reader import CombatReader
from ks_input import WinInput


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.s=settings()
        self.r=CombatReader(fake_module(),ROOT/'client_dll.json',self.s)
        self.fields={'m_iHealth':1000,'m_lifeState':0,'m_iTeamNum':2,'m_flMana':675.,
            'm_bIsIllusion':0,'m_flHealthRegen':0.,'m_nUnitState64':0,'m_flMagicalResistanceValue':25.,
            'm_flIntellectTotal':100.,'m_iLevel':1,'m_fCooldown':0.,'m_iManaCost':100,
            'm_bActivated':1,'m_bHidden':0,'m_bInAbilityPhase':0,'m_bInIndefiniteCooldown':0,
            'm_flChannelStartTime':0.,'m_nAbilityCurrentCharges':2}

    def values(self,d,e,c,f,fmt): return self.fields[f]

    def test_schema_mismatch(self):
        m=fake_module(); m.AB_F_COOLDOWN+=8
        with self.assertRaisesRegex(ValueError,'mismatch'):
            CombatReader(m,ROOT/'client_dll.json',self.s)

    def test_strict_read_does_not_turn_failure_into_ready(self):
        for raw in (None,b'\x00',struct.pack('<f',float('nan'))):
            dot=SimpleNamespace(rb=lambda a,n:raw)
            with self.assertRaises(ValueError):
                self.r.value(dot,100,'C_DOTABaseAbility','m_fCooldown','<f')

    def test_negative_cooldown_rejected(self):
        self.fields['m_fCooldown']=-1
        with patch.object(self.r,'value',side_effect=self.values),self.assertRaises(ValueError):
            self.r.ability(None,'lion_finger_of_death',300)

    def test_dagon_level_is_profile_data_not_reader_special_case(self):
        with patch.object(self.r,'value',side_effect=self.values):
            a,busy=self.r.ability(None,'item_dagon_3',300,slot=2)
        self.assertEqual(a.level,1)  # raw level unchanged
        self.assertEqual(self.s.match(a)[1],3)  # JSON supplies variant level
        self.assertEqual(a.charges,0)  # Dagon is not wrongly treated as a charged item

    def test_new_profile_discovered_without_reader_edits(self):
        doc=json.loads((ROOT/'ks_profiles.json').read_text())
        entry=copy.deepcopy(doc['abilities'][1]); entry.update(id='test_nuke',variants={'test_nuke':0},hero='lion',key='T')
        doc['abilities'].append(entry)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'p.json'; path.write_text(json.dumps(doc))
            r=CombatReader(fake_module(),ROOT/'client_dll.json',Settings(path))
        dot=SimpleNamespace(get_entity=lambda idx:(100,'npc_dota_hero_lion'),curtime=lambda:100.,
             abilities_of=lambda ents,ep,nm:[{'name':'test_nuke','ent':300}])
        hero=dict(idx=1,ent=100,is_local=True,alive=True,pos=(0,0),screen=(800,500))
        with patch.object(r,'value',side_effect=self.values),patch('ks_reader.read_inventory',return_value=Inventory(False,reason='unknown')):
            h=r.capture(dot,[hero])[0]
        self.assertEqual(h['_ks_error'],'')
        self.assertEqual(h['_ks'].actions[0].name,'test_nuke')
        self.assertFalse(h['_ks'].inventory_known)
        self.assertEqual(h['_ks'].intellect,100)
        self.assertNotIn('_ks',hero)

    def test_motion_needs_stable_fresh_samples(self):
        self.assertFalse(self.r.observe_motion(1,(0,0),10.,True)[0])
        self.r.observe_motion(1,(0,0),10.1,True)
        self.r.observe_motion(1,(0,0),10.2,True)
        known,speed,still=self.r.observe_motion(1,(0,0),10.4,True)
        self.assertTrue(known); self.assertEqual(speed,0); self.assertGreaterEqual(still,.3)
        known,speed,still=self.r.observe_motion(1,(100,0),10.5,True)
        self.assertEqual(still,0)
        self.assertGreater(speed,40)

    def test_motion_resets_on_stale_fog_or_entity_change(self):
        self.r.observe_motion(1,(0,0),10.,True)
        self.assertFalse(self.r.observe_motion(1,(0,0),11.,True)[0])
        self.assertFalse(self.r.observe_motion(1,(0,0),11.1,False)[0])
        self.assertFalse(self.r.observe_motion(1,(0,0),11.2,True)[0])
        self.assertFalse(self.r.observe_motion(2,(0,0),11.3,True)[0])

    def test_old_channel_start_not_permanent(self):
        self.assertFalse(self.r.channel_busy(100.,120.))
        self.assertTrue(self.r.channel_busy(100.,101.))
        self.assertTrue(self.r.channel_busy(100.,None))

    def test_discovery_not_limited_to_seven(self):
        s=(ROOT/'dota_esp.py').read_text()
        method=s[s.index('    def abilities_of('):s.index('    def talent_names_of(')]
        self.assertNotIn('len(out) >= 7',method)
        self.assertIn('min(size, 32)',method)


    def test_live_stack_data_reaches_damage_model(self):
        dot=SimpleNamespace(get_entity=lambda idx:(100,'npc_dota_hero_lion'),curtime=lambda:100.,
             abilities_of=lambda *args:[{'name':'lion_finger_of_death','ent':300}])
        hero=dict(idx=1,ent=100,is_local=True,designer='npc_dota_hero_lion',alive=True,pos=(0,0),screen=(800,500))
        stacks=(('CDOTA_Modifier_Lion_FingerOfDeath_Kill_Counter',3),)
        talents=(('special_bonus_unique_lion_8',1),)
        with patch.object(self.r,'value',side_effect=self.values),patch('ks_reader.read_inventory',return_value=Inventory(True)), \
             patch.object(self.r.modifiers,'stacks',return_value=stacks),patch('ks_reader.talent_levels',return_value=talents):
            h=self.r.capture(dot,[hero])[0]
        self.assertEqual(h['_ks'].stack_values,stacks)
        self.assertEqual(h['_ks'].talent_levels,talents)
        _,enemy=fixture(at=h['_ks'].at)
        decision=choose((h['_ks'],enemy),self.s,h['_ks'].at+.01,(1920,1080))
        self.assertAlmostEqual(decision.damage,735*.73)

    def test_failed_stack_read_does_not_fabricate_zero(self):
        dot=SimpleNamespace(get_entity=lambda idx:(100,'npc_dota_hero_lion'),curtime=lambda:100.,
             abilities_of=lambda *args:[{'name':'lion_finger_of_death','ent':300}])
        hero=dict(idx=1,ent=100,is_local=True,designer='npc_dota_hero_lion',alive=True,pos=(0,0),screen=(800,500))
        with patch.object(self.r,'value',side_effect=self.values),patch('ks_reader.read_inventory',return_value=Inventory(True)), \
             patch.object(self.r.modifiers,'stacks',side_effect=ValueError('unreadable')), \
             patch('ks_reader.talent_levels',return_value=(('special_bonus_unique_lion_8',1),)):
            h=self.r.capture(dot,[hero])[0]
        self.assertEqual(h['_ks'].stack_values,())
        self.assertTrue(any('unreadable' in x for x in h['_ks_warnings']))
        _,enemy=fixture(at=h['_ks'].at)
        self.assertIsNone(choose((h['_ks'],enemy),self.s,h['_ks'].at+.01,(1920,1080)))


class InputTests(unittest.TestCase):
    def setUp(self):
        class K(C.Structure):
            _fields_=[('wVk',C.c_uint16),('dwFlags',C.c_uint32)]
        class I(C.Structure):
            _fields_=[('type',C.c_uint32),('ki',K)]
        self.b=WinInput.__new__(WinInput); self.b.INPUT=I
        self.ctx=(123,100,100,1920,1080)
        self.focus=True; self.keys=set(); self.armed=True
        self.cursor=(0,0); self.moves=[]; self.sent=[]
        self.partial=False; self.move_hook=None
        self.b.context=lambda:self.ctx if self.focus else None
        self.b.down=lambda k:k in self.keys
        def move(x,y):
            self.cursor=(x,y); self.moves.append((x,y))
            if self.move_hook:self.move_hook()
            return True
        def getcursor(ptr):
            ptr._obj.x,ptr._obj.y=self.cursor
            return True
        def send(n,arr,size):
            if hasattr(arr,'_obj'): arr=[arr._obj]
            self.sent.append([(arr[i].type,arr[i].ki.wVk,arr[i].ki.dwFlags) for i in range(n)])
            return 1 if self.partial else n
        self.b.u=SimpleNamespace(SetCursorPos=move,GetCursorPos=getcursor,SendInput=send)
        self.s=settings(); self.me,self.enemy=fixture()
        self.d=choose((self.me,self.enemy),self.s,10.1,(1920,1080))

    def cast(self,d=None):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            return self.b.cast(d or self.d,self.s,self.ctx,lambda:self.armed)

    def test_quickcast_keyboard_only_without_hold(self):
        result,_=self.cast()
        self.assertEqual(result,'sent')
        self.assertEqual(self.sent,[[(1,82,0),(1,82,2)]])
        self.assertEqual(self.cursor,(1100,570))
        self.assertFalse(self.keys)  # no keyboard toggle is required

    def test_point_aims_at_ground_not_body_offset(self):
        me=replace(self.me,actions=(Action('lion_impale',300,4,0,100),))
        d=choose((me,self.enemy),self.s,10.1,(1920,1080))
        self.assertEqual(self.cast(d)[0],'sent')
        self.assertEqual(self.cursor,(1100,600))

    def test_global_does_not_move_cursor(self):
        me=replace(self.me,name='npc_dota_hero_zuus',actions=(Action('zuus_thundergods_wrath',300,3,0,100),))
        d=choose((me,replace(self.enemy,screen=())),self.s,10.1,(1920,1080))
        self.assertEqual(self.cast(d)[0],'sent')
        self.assertEqual(self.moves,[])

    def test_no_target_does_not_move_cursor(self):
        me=replace(self.me,name='npc_dota_hero_queenofpain',actions=(Action('queenofpain_scream_of_pain',300,4,0,100),))
        d=choose((me,replace(self.enemy,pos=(400,0))),self.s,10.1,(1920,1080))
        self.assertEqual(self.cast(d)[0],'sent')
        self.assertEqual(self.moves,[])

    def test_disabled_blocks_input_but_legacy_f6_is_not_a_killsteal_toggle(self):
        self.armed=False
        self.assertEqual(self.cast()[0],'skipped')
        self.armed=True; self.keys.add(117)
        self.assertEqual(self.cast()[0],'sent')
        self.assertEqual(len(self.sent),1)

    def test_own_key_chat_keys_and_untracked_modifiers_block(self):
        for key in (82,13,9,16,35,91,92):
            self.keys={key}
            self.assertEqual(self.cast()[0],'skipped')
        self.assertEqual(self.sent,[])

    def test_escape_does_not_interrupt_killsteal_input(self):
        self.keys={27}
        self.assertEqual(self.cast()[0],'sent')
        self.assertEqual(len(self.sent),1)

    def test_refocus_guard_after_move(self):
        self.move_hook=lambda:setattr(self,'focus',False)
        self.assertEqual(self.cast()[0],'skipped')
        self.assertEqual(self.sent,[])

    def test_stop_after_move(self):
        self.move_hook=lambda:setattr(self,'armed',False)
        self.assertEqual(self.cast()[0],'skipped')
        self.assertEqual(self.sent,[])

    def test_partial_send_keyup_cleanup_no_mouse(self):
        self.partial=True
        self.assertEqual(self.cast()[0],'failed')
        self.assertEqual(self.sent[-1],[(1,82,2)])
        self.assertTrue(all(event[0]==1 for batch in self.sent for event in batch))

    def test_stale_before_send(self):
        with patch('ks_input.time.perf_counter',side_effect=[10.1,10.5]):
            self.assertEqual(self.b.cast(self.d,self.s,self.ctx,lambda:True)[0],'skipped')
        self.assertEqual(self.sent,[])

    def test_insert_menu_key_blocks_native_cast(self):
        self.keys.add(0x2D)
        self.assertNotIn('Insert',self.b.hard_stop_reason())  # Temporary menu guard, not OFF.
        self.assertEqual(self.cast()[0],'skipped')
        self.assertEqual(self.sent,[])


if __name__=='__main__': unittest.main()
