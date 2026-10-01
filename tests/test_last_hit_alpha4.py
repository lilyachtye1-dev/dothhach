"""alpha4: full pipeline fixtures, not claims of a live Dota verification."""
import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from helpers import ROOT
import test_last_hit as old
import test_target_probe as memory
from last_hit_core import Model
from last_hit_units import candidate_name,entity_kind,lane_type
from last_hit_stats import LiveStats,numeric,IAS,SIEGE
from last_hit_service import validate


class LaneVocabularyTests(unittest.TestCase):
    def test_all_four_both_teams_and_upgrades(self):
        for team in ('goodguys','badguys'):
            for kind in ('melee','ranged','flagbearer','siege'):
                for suffix in ('','_upgraded','_upgraded_mega'):
                    name='npc_dota_'+('' if kind=='siege' else 'creep_')+team+'_'+kind+suffix
                    self.assertTrue(candidate_name(name));self.assertEqual(lane_type(name),kind)
                    self.assertEqual(entity_kind('C_DOTA_BaseNPC_Creep',name),'creep')
    def test_neutrals_summons_and_heroes_not_promoted_to_lane(self):
        for name in ('npc_dota_neutral_centaur_khan','npc_dota_hero_antimage','npc_dota_lycan_wolf','npc_dota_badguys_siege_fake'):
            self.assertIsNone(entity_kind('C_DOTA_BaseNPC_Creep',name))
    def test_known_derived_classes_and_generic_designer(self):
        self.assertEqual(entity_kind('C_DOTA_BaseNPC_Creep_Siege','npc_dota_creep_siege'),'creep')
        self.assertEqual(lane_type('npc_dota_creep_lane','C_DOTA_BaseNPC_Creep_Lane',2),'ranged')
        self.assertIsNone(entity_kind('C_DOTA_BaseNPC_Creep_Neutral','npc_dota_badguys_siege'))
    def test_full_entity_list_not_highest_index_only(self):
        source=(ROOT/'last_hit_reader.py').read_text()
        self.assertIn('scan.entities(full_scan=True)',source)

    def test_actual_dot_entity_scan_recovers_creep_above_reported_highest_index(self):
        tree=ast.parse((ROOT/'dota_esp.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Dot')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='entities')
        scope={'struct':struct,'ptr_ok':lambda n:bool(n and n>=0x10000),'DW_GAME_ENTITY_SYSTEM':32,'DW_HIGHEST_INDEX':64}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),'actual-entities','exec'),scope)
        block=bytearray(512*0x70);idx=2049;ep=0x30000
        struct.pack_into('<Q',block,0x70,ep);struct.pack_into('<I',block,0x70+16,0x10000+idx)
        d=SimpleNamespace(client=0x10000000,u64=lambda p:0x50000 if p==0x10000020 else 0x80000 if p==0x50000+0x10+4*8 else 0,
            i32=lambda p:10,rb=lambda p,n:bytes(block) if p==0x80000 else None,
            _identity_name=lambda *args:'npc_dota_badguys_siege')
        self.assertEqual(scope['entities'](d,full_scan=False),{})
        self.assertEqual(scope['entities'](d,full_scan=True)[idx],(ep,'npc_dota_badguys_siege'))


class ActualCaptureTests(unittest.TestCase):
    def setUp(self):
        self.f=old.ReaderTests();self.f.setUp();self.r=self.f.r;self.p=self.f.p;self.d=self.f.d
        self.names={};self.classes={};self.r.entries={}
        self.r.modifier_stats=lambda uid,siege=False:(.5 if siege else 0.,'synthetic owned modifier')
        self.d.cs=lambda ptr,n:self.names.get(ptr)
        self.d.pos=lambda ep:(100+(ep-0x20000)/0x10000*30,0,0)
        self.d.w2s=lambda m,p:(500+p[0],400,1)
        self.p.runtime_type=lambda ep:self.classes.get(ep)
        self.r.unit=lambda ep,name,kind,clock,matrix:old.hero() if kind=='hero' else None
        for i,role in enumerate(('melee','ranged','flagbearer','siege')):
            ep=0x20000+i*0x10000;idx=i+2;ident=0x100000+i*0x10000;ptr=0x200000+i*0x10000
            name='npc_dota_'+('' if role=='siege' else 'creep_')+'badguys_'+role
            self.f.entities[idx]=(ep,name);self.r.entries[idx]=(ep,name);self.f.put_identity(ep,ident,0x10000+idx)
            self.classes[ep]='C_DOTA_BaseNPC_Creep_Siege' if role=='siege' else 'C_DOTA_BaseNPC_Creep_Lane'
            self.names[ptr]=name
            for cls,key,fmt,val in [('C_BaseEntity','m_iHealth','<i',30),('C_BaseEntity','m_iMaxHealth','<i',500),
                    ('C_BaseEntity','m_iTeamNum','<B',3),('C_BaseEntity','m_lifeState','<B',0),
                    ('C_DOTA_BaseNPC','m_flPhysicalArmorValue','<f',2),('C_DOTA_BaseNPC','m_flHealthRegen','<f',0),
                    ('C_DOTA_BaseNPC','m_flHullRadius','<f',24),('C_DOTA_BaseNPC','m_iszUnitName','<Q',ptr)]:
                self.p.put(ep+self.p.classes[cls]['fields'][key],struct.pack(fmt,val))
        # No timing asset may be consulted for target-only reading.
        self.r.assets=SimpleNamespace(timing=lambda *a:(_ for _ in ()).throw(AssertionError('target timing dependency')))
    def capture(self):
        with patch('last_hit_reader.time.perf_counter',return_value=10):return self.r.capture((100,'npc_dota_hero_lion'))
    def test_real_capture_reads_all_four_without_target_attack_timing(self):
        f=self.capture();self.assertFalse(f.error);self.assertEqual(len(f.creeps),4)
        self.assertEqual({c.lane_type for c in f.creeps},{'melee','ranged','flagbearer','siege'})
        self.assertEqual(f.creeps[-1].incoming_multiplier,.5)
        for role in ('melee','ranged','flagbearer','siege'):self.assertEqual(dict(f.diagnostics)[role],1)
    def test_full_capture_to_planner_all_four_can_be_selected(self):
        f=self.capture()
        for c in f.creeps:
            chosen,_=Model().choose(replace(f,creeps=(c,)),allow_current_hp=True)
            self.assertIsNotNone(chosen,c.name);self.assertEqual(chosen.target.uid,c.uid)
    def test_base_rtti_uses_live_unit_name_not_generic_designer(self):
        self.classes[0x20000]='C_DOTA_BaseNPC_Creep'
        self.f.entities[2]=self.r.entries[2]=(0x20000,'npc_dota_creep')
        f=self.capture();self.assertEqual(len(f.creeps),4);self.assertEqual(dict(f.diagnostics)['unsupported'],0)
    def test_bad_target_is_counted_without_hiding_other_targets(self):
        del self.p.memory[0x30000+self.p.classes['C_DOTA_BaseNPC']['fields']['m_flPhysicalArmorValue']]
        f=self.capture();self.assertEqual(len(f.creeps),3);self.assertEqual(dict(f.diagnostics)['invalid'],1)
        self.assertIn('m_flPhysicalArmorValue',' '.join(f.details))
    def test_unrecognized_type_has_its_own_counter(self):
        self.classes[0x30000]='C_DOTA_BaseNPC_Creep_Neutral'
        f=self.capture();self.assertEqual(len(f.creeps),3);self.assertEqual(dict(f.diagnostics)['unsupported'],1)
    def test_siege_with_unknown_modifier_is_visible_but_not_auto_targeted(self):
        self.r.modifier_stats=lambda *args:(None,'unknown vector')
        f=self.capture();c=next(c for c in f.creeps if c.lane_type=='siege')
        m=Model();chosen,_=m.choose(replace(f,creeps=(c,)));self.assertIsNone(chosen)
        self.assertEqual(m.rows[0]['state'],'damage_unknown')


class LiveStatsTests(unittest.TestCase):
    def setUp(self):
        self.p=memory.Probe();self.c=json.loads((ROOT/'ks_profiles.json').read_text())['settings']['live_modifier_layout']
        self.p.image=dict(timestamp=self.c['client_timestamp'],size=self.c['client_size'])
        self.r=LiveStats(self.p.classes,self.c,hashlib.sha256((ROOT/'client_dll.json').read_bytes()).hexdigest())
        self.hero=0x40000;self.manager=self.hero+self.p.classes['C_DOTA_BaseNPC']['fields']['m_ModifierManager']
        self.data=0x100000;self.types={};self.p.runtime_type=lambda obj:self.types.get(obj)
    def vector(self,entries):
        c=self.c;header=bytearray(40);struct.pack_into('<Q',header,c['manager_owner'],self.hero)
        struct.pack_into('<i',header,c['count'],len(entries));struct.pack_into('<Q',header,c['data'],self.data)
        struct.pack_into('<i',header,c['capacity'],len(entries));self.p.put(self.manager,header)
        ptrs=[]
        for i,(cls,field,fmt,value) in enumerate(entries):
            obj=0x200000+i*0x10000;ptrs.append(obj);self.types[obj]=cls
            self.p.put(obj,struct.pack('<Q',0x10005000+i*16));self.p.put(obj+c['modifier_manager'],struct.pack('<Q',self.manager))
            self.p.put(obj+self.p.classes[cls]['fields'][field],struct.pack(fmt,value))
        self.p.put(self.data,struct.pack('<'+'Q'*len(ptrs),*ptrs));return ptrs
    def read(self,siege=False):
        with patch('last_hit_stats.Probe',return_value=self.p):return self.r.read(None,self.hero,siege)
    def test_phase_treads_and_two_wraith_bands_add_live_values(self):
        names=['CDOTA_Modifier_Item_PhaseBoots','CDOTA_Modifier_Item_PowerTreads','CDOTA_Modifier_Item_WraithBand','CDOTA_Modifier_Item_WraithBand']
        self.vector([(n,'bonus_attack_speed','<f',v) for n,v in zip(names,(0,25,7,7))])
        self.assertEqual(self.read()[0],39)
    def test_integer_fields_are_not_misread_as_denormal_zero(self):
        self.vector([('CDOTA_Modifier_Item_PowerTreads','bonus_attack_speed','<i',30)])
        self.assertEqual(self.read()[0],30)
    def test_siege_incoming_penalty_is_live_not_hardcoded(self):
        self.vector([(SIEGE,'incoming_hero_damage_penalty','<f',40)])
        self.assertAlmostEqual(self.read(True)[0],.6)
    def test_siege_absence_requires_complete_verified_vector(self):
        self.vector([]);self.assertEqual(self.read(True)[0],1)
    def test_foreign_owner_refused(self):
        objs=self.vector([('CDOTA_Modifier_Item_PowerTreads','bonus_attack_speed','<f',30)])
        self.p.put(objs[0]+self.c['modifier_manager'],struct.pack('<Q',self.manager+8))
        self.assertRaises(ValueError,self.read)
    def test_unreadable_stat_not_silently_zero(self):
        objs=self.vector([('CDOTA_Modifier_Item_PowerTreads','bonus_attack_speed','<f',30)])
        del self.p.memory[objs[0]+self.p.classes['CDOTA_Modifier_Item_PowerTreads']['fields']['bonus_attack_speed']]
        self.assertRaises(ValueError,self.read)
    def test_numeric_unknown_nan_and_huge_values_refused(self):
        self.assertEqual(numeric(struct.pack('<f',-0.)),0)
        for value in (float('nan'),float('inf'),3000.):
            self.assertRaises(ValueError,numeric,struct.pack('<f',value))
    def test_changed_agility_or_item_speed_invalidates_old_calibration(self):
        m=Model();a=old.hero(interval=1,speed_signature=(1,100,30,25));m.update(old.frame(h=a))
        m.intervals[a.uid]=[(10,.5),(10.1,.5)]
        b=replace(a,interval=.9,speed_signature=(1,100,40,25));m.update(old.frame(h=b,now=10.2))
        self.assertEqual(m.measured_interval(b,10.2),.9)
    def test_similar_idle_gaps_cannot_slow_live_ias_estimate(self):
        m=Model();h=old.hero(interval=.7,speed_signature=(1,100,30,25));m.intervals[h.uid]=[(10,2),(10.1,2)]
        self.assertEqual(m.measured_interval(h,10.2),.7)


class ServiceRebuildTests(unittest.TestCase):
    def setUp(self):
        self.f=old.ServiceTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.s=self.f.s
        self.seq=1;self.f.f.b.activity_token=lambda:self.seq
        self.s.reader.latest=replace(self.s.reader.latest,creeps=(replace(old.unit(),lane_type='melee'),))
    def controls(self,**kwargs):
        self.f.doc.update(kwargs);self.s.path.write_text(json.dumps(self.f.doc))
    def wire(self,t):
        with patch('last_hit_service.time.perf_counter',return_value=t):return self.s.dashboard()
    def test_helper_without_hold_shows_target_but_sends_nothing(self):
        self.f.f.b.keys.clear();self.f.tick();d=self.wire(10.14)
        self.assertFalse(self.f.calls);self.assertEqual(len(d['markers']),1);self.assertIsNone(d['marker'])
    def test_helper_only_mode_cannot_send_even_while_held(self):
        self.controls(helper_only=True);self.f.tick();self.assertFalse(self.f.calls)
        self.assertEqual(len(self.wire(10.14)['markers']),1)
    def test_manual_actions_do_not_block_by_default(self):
        self.f.f.b.keys.clear();self.f.tick();self.f.f.b.keys={5};self.seq+=1;self.f.tick(10.16)
        self.assertEqual(len(self.f.calls),1);self.assertFalse(self.s.pause_manual)
    def test_manual_pause_opt_in_does_not_hide_detection(self):
        self.controls(pause_manual=True);self.f.f.b.keys.clear();self.f.tick()
        self.f.f.b.keys={5};self.seq+=1;self.f.tick(10.16)
        self.assertFalse(self.f.calls);self.assertEqual(len(self.wire(10.16)['markers']),1)
    def test_out_of_range_is_visible_with_separate_reason(self):
        c=replace(old.unit(),pos=(900,0,0),lane_type='ranged');self.s.reader.latest=replace(self.s.reader.latest,creeps=(c,))
        self.f.tick();self.assertFalse(self.f.calls)
        self.assertEqual(self.wire(10.14)['markers'][0]['state'],'range');self.assertIn('в дальности 0',self.s.pipeline)
    def test_toggle_first_press_on_release_stays_second_press_off(self):
        self.controls(activation='toggle');self.f.f.b.keys.clear();self.f.tick()
        self.f.f.b.keys={5};self.f.tick(10.16);self.assertTrue(self.s.active)
        self.f.f.b.keys.clear();self.f.tick(10.18);self.assertTrue(self.s.active)
        self.f.f.b.keys={5};self.f.tick(10.2);self.assertFalse(self.s.active)
        self.assertEqual(len(self.f.calls),1)
    def test_toggle_is_not_key_autorepeat(self):
        self.controls(activation='toggle');self.f.tick();self.f.tick(10.16);self.assertTrue(self.s.active)
    def test_toggle_cannot_arm_through_open_menu(self):
        self.controls(activation='toggle',menu_open=True);self.f.tick();self.assertFalse(self.s.active);self.assertFalse(self.f.calls)
    def test_stale_helper_never_shows_old_creeps(self):
        self.f.tick();self.assertEqual(self.wire(10.5)['markers'],[])
    def test_invalid_options_fail_closed(self):
        for kwargs in ({'activation':'auto'},{'pause_manual':1},{'helper_only':'yes'}):
            self.assertFalse(validate(old.command(**kwargs),'session',100000))


if __name__=='__main__':unittest.main()
