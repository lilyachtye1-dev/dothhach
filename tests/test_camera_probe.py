import ast
from dataclasses import replace
import json
import struct
import time
import tempfile
import zipfile
from unittest.mock import patch
from types import SimpleNamespace
import unittest

from helpers import ROOT, fixture, settings, fake_module
from ks_core import Action, choose
from ks_reader import combat_visible
from collect_live import Probe, ProbeLimit, COUNTER, stack_candidates


class CameraTests(unittest.TestCase):
    def setUp(self):
        me, self.enemy = fixture()
        self.me = replace(me, name='npc_dota_hero_zuus',
                          actions=(Action('zuus_thundergods_wrath', 300, 3, 0., 100),))
        self.s = settings()

    def choose(self, enemy=None, me=None):
        return choose((me or self.me, enemy or self.enemy), self.s, 10.1, (1920,1080))

    def test_global_no_projection_or_hud_restriction(self):
        for screen in ((), (-9999, 0), (1000, 1060), (float('nan'), 10)):
            with self.subTest(screen=screen):
                self.assertIsNotNone(self.choose(replace(self.enemy, screen=screen)))

    def test_global_without_world_coordinates(self):
        self.assertIsNotNone(self.choose(replace(self.enemy, pos=(), screen=()), replace(self.me, pos=())))

    def test_global_still_checks_fog_freshness_hp_and_inventory(self):
        for changed in ({'visible':False}, {'alive':False}, {'at':9.}, {'hp':float('nan')},
                        {'inventory_known':False}, {'illusion':True}):
            with self.subTest(changed=changed):
                self.assertIsNone(self.choose(replace(self.enemy, screen=(), **changed)))

    def test_unit_and_point_still_require_screen(self):
        me, _ = fixture()
        for name, level in (('lion_finger_of_death',1), ('lion_impale',4)):
            me = replace(me, actions=(Action(name,300,level,0,100),))
            for screen in ((), (-200,0), (1000,1060)):
                self.assertIsNone(self.choose(replace(self.enemy, screen=screen), me))

    def test_no_target_requires_world_radius_not_projection(self):
        me = replace(self.me, name='npc_dota_hero_queenofpain',
                     actions=(Action('queenofpain_scream_of_pain',300,4,0,100),))
        self.assertIsNotNone(self.choose(replace(self.enemy, pos=(400,0), screen=()),me))
        self.assertIsNone(self.choose(replace(self.enemy, pos=(10000,0), screen=()),me))
        self.assertIsNone(self.choose(replace(self.enemy, pos=(), screen=()),me))

    def test_reader_offscreen_annotation(self):
        self.assertTrue(combat_visible(dict(hide=True, screen=None, ks_visibility_known=True, ks_combat_visible=True)))
        self.assertFalse(combat_visible(dict(hide=True)))
        self.assertFalse(combat_visible(dict(ks_visibility_known=False, ks_combat_visible=True)))
        self.assertFalse(combat_visible(dict(ks_visibility_known=True, ks_combat_visible=False)))


class DotVisibilityTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT/'dota_esp.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Dot')
        methods = {'dormant_state', 'is_dormant', 'overlay_fast_update'}
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
        ns = dict(vars(fake_module()), time=time)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), 'dot_methods', 'exec'), ns)
        self.d = ns['Dot']()
        self.d.scene_node = lambda ep: 0x100000
        self.d.rb = lambda a,n: b'\0'
        self.d.raw_matrix = lambda: (1,)
        self.d.curtime = lambda: 123.
        self.d.pos = lambda ep: (500,0,0)
        self.d.i32 = lambda addr: 500
        self.d.f32 = lambda addr: 100.
        self.d.w2s = lambda matrix, pos: None
        self.d.hero_liveness = lambda *args: dict(alive=True,fog=False,respawn=0,hp=500,maxhp=1000,life=0,why='hp>0')
        self.hero = dict(ent=0x200000, idx=2, team=3, alive=True, pos=(500,0,0),
                         _last_pos=(500,0,0), _last_pos_t=0., _last_sc=(1000,500))

    def test_strict_dormancy_unknown_fails_closed(self):
        for raw in (None,b'',b'\xFF'):
            self.d.rb = lambda a,n,raw=raw: raw
            self.assertIsNone(self.d.dormant_state(1))
            self.assertTrue(self.d.is_dormant(1))
        self.d.rb=lambda a,n:b'\1'
        self.assertTrue(self.d.dormant_state(1))
        self.d.rb=lambda a,n:b'\0'
        self.assertFalse(self.d.dormant_state(1))

    def test_offscreen_alive_is_combat_visible_not_rendered(self):
        h=self.d.overlay_fast_update([self.hero],my_pos=(0,0,0),my_team=2)[0][0]
        self.assertTrue(h['hide'])
        self.assertTrue(h['ks_combat_visible'])
        self.assertTrue(h['ks_visibility_known'])
        self.assertFalse(h['fog'])

    def test_camera_motion_does_not_turn_stationary_enemy_into_fog(self):
        self.d.w2s=lambda m,p:(-4000,500)
        h=self.d.overlay_fast_update([self.hero],my_pos=(0,0,0),my_team=2)[0][0]
        self.assertTrue(h['ks_combat_visible'])
        self.assertFalse(h['fog'])

    def test_real_dormancy_or_unreadable_bit_not_combat_visible(self):
        for raw in (None,b'\1'):
            self.d.rb=lambda a,n,raw=raw:raw
            h=self.d.overlay_fast_update([self.hero])[0][0]
            self.assertFalse(h['ks_combat_visible'])
            self.assertTrue(h['hide'])

    def test_read_exception_clears_previous_visibility(self):
        def fail(*args): raise ValueError('read failure')
        self.d.pos=fail
        self.hero.update(ks_combat_visible=True,ks_visibility_known=True)
        h=self.d.overlay_fast_update([self.hero])[0][0]
        self.assertFalse(h['ks_combat_visible'])
        self.assertFalse(h['ks_visibility_known'])


class Memory:
    client=0x10000000
    def __init__(self):
        self.blocks={}
        dos=bytearray(64);dos[:2]=b'MZ';struct.pack_into('<I',dos,0x3C,0x80)
        pe=bytearray(256);pe[:4]=b'PE\0\0';struct.pack_into('<H',pe,24,0x20B)
        struct.pack_into('<I',pe,80,0x200000)
        self.blocks[self.client]=dos;self.blocks[self.client+0x80]=pe
    def rb(self,addr,count):
        for base,raw in self.blocks.items():
            if base<=addr and addr+count<=base+len(raw):
                return bytes(raw[addr-base:addr-base+count])
        return None
    def add_modifier(self,address,cls=COUNTER):
        base=self.client
        obj=bytearray(0x1B00);struct.pack_into('<Q',obj,0,base+0x1608)
        self.blocks[address]=obj
        self.blocks[base+0x1600]=struct.pack('<QQ',base+0x1700,base+0x1100)
        self.blocks[base+0x1700]=struct.pack('<6I',1,0,0,0x1800,0x1900,0x1700)
        name=('.?AV'+cls+'@@').encode()+b'\0'
        self.blocks[base+0x1810]=name.ljust(192,b'\0')


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.classes=json.loads((ROOT/'client_dll.json').read_text())['client.dll']['classes']
        self.mem=Memory();self.obj=0x20000000
        self.mem.add_modifier(self.obj)

    def probe(self): return Probe(self.mem,self.classes)

    def test_live_rtti_not_foreign_rvas(self):
        self.assertEqual(self.probe().runtime_type(self.obj),COUNTER)

    def test_rejects_wrong_locator_image(self):
        raw=bytearray(self.mem.blocks[self.mem.client+0x1700]);struct.pack_into('<I',raw,20,0x2700)
        self.mem.blocks[self.mem.client+0x1700]=raw
        self.assertIsNone(self.probe().runtime_type(self.obj))

    def test_rejects_secondary_vptr_and_unreadable(self):
        raw=bytearray(self.mem.blocks[self.mem.client+0x1700]);struct.pack_into('<I',raw,4,8)
        self.mem.blocks[self.mem.client+0x1700]=raw
        p=self.probe()
        self.assertIsNone(p.runtime_type(self.obj))
        self.assertIsNone(p.runtime_type(0xBAD00000))

    def test_manager_neighbourhood_only_candidates(self):
        hero=0x30000000;container=0x40000000
        fields=self.classes['C_DOTA_BaseNPC']['fields']
        manager=hero+fields['m_ModifierManager'];span=fields['m_Inventory']-fields['m_ModifierManager']
        raw=bytearray(span);struct.pack_into('<Q',raw,0x30,container);self.mem.blocks[manager]=raw
        raw=bytearray(0x400);struct.pack_into('<Q',raw,0x18,self.obj);self.mem.blocks[container]=raw
        result=self.probe().modifiers(hero)
        self.assertEqual(len(result['candidates']),1)
        self.assertEqual(result['candidates'][0]['class'],COUNTER)
        self.assertIn('NOT a validated',result['warning'])
        self.assertTrue(result['candidates'][0]['raw_hex'])

    def test_invalid_pe_rejected(self):
        self.mem.blocks[self.mem.client][0]=0
        with self.assertRaises(ValueError):self.probe()

    def test_budget_enforced(self):
        p=self.probe();p.max_reads=p.reads
        with self.assertRaises(ProbeLimit):p.runtime_type(self.obj)

    @staticmethod
    def snapshots():
        out=[]
        for n in (1,2,3):
            raw=bytearray(0x100);struct.pack_into('<i',raw,0x70,n)
            out.append({'expected_stacks':n,'hero':{'entity':'0x100'},'client':{'timestamp':1,'size':2},
                        'modifiers':{'candidates':[{'class':COUNTER,'raw_hex':raw.hex()}]}})
        return out

    def test_stack_correlation_not_automatic_binding(self):
        r=stack_candidates(self.snapshots())
        self.assertEqual(r['offsets_i32'],['0x70'])
        self.assertEqual(r['status'],'candidates_only')

    def test_stack_needs_three_distinct_counts(self):
        snapshots=self.snapshots()
        self.assertEqual(stack_candidates(snapshots[:2])['status'],'insufficient')
        for s in snapshots:s['expected_stacks']=1
        self.assertEqual(stack_candidates(snapshots)['status'],'insufficient')

    def test_stack_rejects_ambiguous_instances(self):
        snapshots=self.snapshots()
        snapshots[0]['modifiers']['candidates']*=2
        self.assertEqual(stack_candidates(snapshots)['status'],'insufficient')

    def test_stack_rejects_changed_hero_or_build(self):
        for section,key,val in [('hero','entity','0x200'),('client','timestamp',4)]:
            snapshots=self.snapshots();snapshots[1][section][key]=val
            self.assertEqual(stack_candidates(snapshots)['status'],'insufficient')

    def test_capture_with_fake_dot_and_no_native_input(self):
        from collect_live import capture
        hero=0x30000000
        self.mem.blocks[hero]=bytearray(0x6000)
        self.mem.snapshot=lambda:{'heroes':[dict(is_local=True,ent=hero,idx=1,
            designer='npc_dota_hero_lion',level=10)]}
        self.mem.abilities_of=lambda *args:[]
        with patch.dict('sys.modules',{'dota_esp':fake_module()}):
            result=capture(self.mem,self.classes,'baseline',3)
        self.assertEqual(result['expected_stacks'],3)
        self.assertTrue(result['inventory']['known'])
        self.assertEqual(result['hero']['entity'],hex(hero))
        self.assertEqual(result['modifiers']['candidates'],[])

    def test_save_single_valid_archive(self):
        from pathlib import Path
        from collect_live import save
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'client_dll.json').write_bytes((ROOT/'client_dll.json').read_bytes())
            with patch('collect_live.ROOT',root):
                path=save(self.snapshots())
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.namelist(),['live_probe.json'])
                data=json.loads(archive.read('live_probe.json'))
            self.assertEqual(data['stack_analysis']['offsets_i32'],['0x70'])
            self.assertTrue(data['read_only'])
            self.assertFalse(path.with_suffix('.tmp').exists())
