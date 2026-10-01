"""Damage/Quell fixtures. Synthetic memory, not validation in a running game."""
from dataclasses import replace
import math
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_last_hit as old
import test_last_hit_alpha4 as alpha4
import test_target_probe as memory
from last_hit_core import Model,quelling_bonus,armor_multiplier
from last_hit_quelling import active_items,resolve,read_quelling
from last_hit_reader import Reader

Q='CDOTA_Modifier_Item_QuellingBlade'
B='CDOTA_Modifier_Item_Battlefury'
ITEM=((0,0x10003,0x60000,'item_quelling_blade'),)


class QuellModifierTests(unittest.TestCase):
    def setUp(self):
        self.f=alpha4.LiveStatsTests();self.f.setUp()
    def vector(self,values):
        entries=[(cls,'damage_bonus' if cls==Q else 'quelling_bonus','<f',melee) for cls,melee,ranged in values]
        ptrs=self.f.vector(entries)
        for obj,(cls,melee,ranged) in zip(ptrs,values):
            field='damage_bonus_ranged' if cls==Q else 'quelling_bonus_ranged'
            self.f.p.put(obj+self.f.p.classes[cls]['fields'][field],struct.pack('<f',ranged))
        return ptrs
    def test_live_screen_values_8_and_4(self):
        self.vector([(Q,8,4)]);self.assertEqual(self.f.read()[0],0)
        self.assertEqual(self.f.r.quelling,(self.f.hero,{'item_quelling_blade':(8,4)}))
    def test_values_not_hardcoded_to_screenshot(self):
        self.vector([(Q,13,6)]);self.f.read()
        self.assertEqual(self.f.r.quelling[1]['item_quelling_blade'],(13,6))
    def test_copies_do_not_add(self):
        self.vector([(Q,8,4),(Q,8,4)]);self.f.read()
        self.assertEqual(self.f.r.quelling[1]['item_quelling_blade'],(8,4))
    def test_battlefury_reads_only_quell_component(self):
        self.vector([(B,10,5)]);self.f.read()
        self.assertEqual(self.f.r.quelling[1]['item_bfury'],(10,5))
    def test_foreign_modifier_owner_rejected(self):
        obj=self.vector([(Q,8,4)])[0]
        self.f.p.put(obj+self.f.c['modifier_manager'],struct.pack('<Q',self.f.manager+8))
        with self.assertRaises(ValueError):self.f.read()
        self.assertIsNone(self.f.r.quelling)
    def test_bad_value_not_a_bonus_and_does_not_become_ias(self):
        self.vector([(Q,-8,4)]);self.assertEqual(self.f.read()[0],0)
        self.assertIsNone(self.f.r.quelling)
    def test_unreadable_value_does_not_retain_previous_bonus(self):
        obj=self.vector([(Q,8,4)])[0];self.f.read()
        del self.f.p.memory[obj+self.f.p.classes[Q]['fields']['damage_bonus_ranged']]
        self.f.read();self.assertIsNone(self.f.r.quelling)
    def test_unstable_value_rejected(self):
        obj=self.vector([(Q,8,4)])[0];read=self.f.p.read;address=obj+self.f.p.classes[Q]['fields']['damage_bonus'];seen=[]
        def changed(a,n):
            if a==address:
                seen.append(1)
                if len(seen)>1:return struct.pack('<f',9)
            return read(a,n)
        self.f.p.read=changed
        with self.assertRaises(ValueError):self.f.read()
        self.assertIsNone(self.f.r.quelling)
    def test_no_modifiers_is_verified_empty_map(self):
        self.vector([]);self.f.read();self.assertEqual(self.f.r.quelling,(self.f.hero,{}))
    def test_reader_shares_existing_ias_scan(self):
        self.vector([(Q,8,4)])
        r=Reader.__new__(Reader);r.dot=None;r.stats=self.f.r;r.stat_cache={}
        with patch('last_hit_stats.Probe',return_value=self.f.p),patch('last_hit_reader.time.perf_counter',return_value=10):
            r.modifier_stats((self.f.hero,123));calls=self.f.p.reads
            r.modifier_stats((self.f.hero,123));self.assertEqual(self.f.p.reads,calls)
        self.assertEqual(r.quell_cache[(self.f.hero,123)][1]['item_quelling_blade'],(8,4))


class ActiveItemTests(unittest.TestCase):
    def setUp(self):
        self.p=memory.Probe();self.hero=(0x40000,0x10002);self.item=0x60000;self.handle=0x10003
        self.entities={2:(self.hero[0],'npc_dota_hero_antimage'),3:(self.item,'item_quelling_blade')}
        self.d=SimpleNamespace(get_entity=lambda index:self.entities.get(index),curtime=lambda:10.)
        self.d.esys=0
        self.identity(self.hero[0],self.hero[1],0x80000);self.identity(self.item,self.handle,0x81000)
        self.base=self.hero[0]+self.off('C_DOTA_BaseNPC','m_Inventory')+self.off('C_DOTA_UnitInventory','m_hItems')
        self.slots=[0xffffffff]*19;self.slots[0]=self.handle;self.write_slots()
        self.field('C_DOTA_Item','m_bItemEnabled','<B',1);self.field('C_DOTA_Item','m_flEnableTime','<f',0)
        self.p.put(self.hero[0]+self.off('C_DOTA_BaseNPC','m_iAttackCapabilities'),struct.pack('<i',1))
    def off(self,c,f):return self.p.classes[c]['fields'][f]
    def identity(self,ep,handle,ident):
        self.p.put(ep+self.off('CEntityInstance','m_pEntity'),struct.pack('<Q',ident))
        data=bytearray(20);struct.pack_into('<Q',data,0,ep);struct.pack_into('<I',data,16,handle);self.p.put(ident,data)
    def field(self,c,f,fmt,n):self.p.put(self.item+self.off(c,f),struct.pack(fmt,n))
    def write_slots(self):self.p.put(self.base,struct.pack('<I'+'I'*19,19,*self.slots))
    def read(self):return active_items(self.d,self.p,self.hero,10)
    def test_active_item_identity_and_slot(self):self.assertEqual(self.read(),ITEM)
    def test_empty_inventory(self):
        self.slots[0]=0xffffffff;self.write_slots();self.assertEqual(self.read(),())
    def test_backpack_stash_and_neutral_slot_do_not_apply(self):
        for slot in (6,7,8,9,16):
            self.slots=[0xffffffff]*19;self.slots[slot]=self.handle;self.write_slots()
            self.assertEqual(self.read(),())
    def test_disabled_item(self):
        self.field('C_DOTA_Item','m_bItemEnabled','<B',0);self.assertEqual(self.read(),())
    def test_backpack_activation_delay(self):
        self.field('C_DOTA_Item','m_flEnableTime','<f',12);self.assertEqual(self.read(),())
    def test_activation_nan_unknown(self):
        self.field('C_DOTA_Item','m_flEnableTime','<f',float('nan'))
        with self.assertRaises(ValueError):self.read()
    def test_unreadable_inventory_is_not_empty(self):
        del self.p.memory[self.base]
        with self.assertRaises(ValueError):self.read()
    def test_recycled_serial_rejected(self):
        self.identity(self.item,0x20003,0x81000)
        with self.assertRaises(ValueError):self.read()
    def test_chop_tree_cooldown_does_not_disable_passive(self):
        self.field('C_DOTABaseAbility','m_fCooldown','<f',100.)
        self.assertEqual(self.read(),ITEM)
    def test_item_moved_during_activation_read(self):
        read=self.p.read;address=self.item+self.off('C_DOTA_Item','m_flEnableTime')
        def moved(a,n):
            raw=read(a,n)
            if a==address:self.slots[0]=0xffffffff;self.slots[6]=self.handle;self.write_slots()
            return raw
        self.p.read=moved
        with self.assertRaises(ValueError):self.read()
    def reader(self):
        r=Reader.__new__(Reader);r.dot=r.verify_dot=self.d;r.probe=r.verify_probe=self.p
        r.quell_cache={self.hero:(10.,{'item_quelling_blade':(8,4)})};return r
    def test_reader_resolves_selected_attack_type(self):
        with patch('time.perf_counter',return_value=10.1):
            self.assertEqual(read_quelling(self.reader(),self.hero,10,False).bonus,8)
            self.assertEqual(read_quelling(self.reader(),self.hero,10,True).bonus,4)
    def test_stale_modifier_cache_never_applied(self):
        with patch('time.perf_counter',return_value=10.3):
            q=read_quelling(self.reader(),self.hero,10,False)
        self.assertFalse(q.known);self.assertEqual(q.bonus,0)
    def test_missing_inventory_never_uses_cached_bonus(self):
        del self.p.memory[self.base]
        with patch('time.perf_counter',return_value=10.1):
            q=read_quelling(self.reader(),self.hero,10,False)
        self.assertFalse(q.known);self.assertEqual(q.bonus,0)
    def hero_unit(self):return replace(old.hero(),uid=self.hero,quelling_bonus=8,quelling_known=True,quelling_items=ITEM)
    def test_final_verifier_accepts_same_active_item(self):self.assertTrue(self.reader().verify_quelling(self.hero_unit()))
    def test_final_verifier_revokes_sale_or_move(self):
        r=self.reader();self.slots[0]=0xffffffff;self.write_slots()
        self.assertFalse(r.verify_quelling(self.hero_unit()))
    def test_final_verifier_revokes_attack_type_change(self):
        self.p.put(self.hero[0]+self.off('C_DOTA_BaseNPC','m_iAttackCapabilities'),struct.pack('<i',2))
        self.assertFalse(self.reader().verify_quelling(self.hero_unit()))


class ReaderWiringTests(unittest.TestCase):
    def setUp(self):
        self.f=old.ReaderTests();self.f.setUp()
        self.f.field('C_DOTA_BaseNPC_Hero','m_flAgilityTotal','<f',31.)
    def test_quell_is_separate_from_generic_damage_in_actual_unit_reader(self):
        from last_hit_quelling import Quelling
        with patch('last_hit_reader.read_quelling',return_value=Quelling(8,True,'synthetic',ITEM)) as read:
            u=self.f.read('hero')
        self.assertEqual((u.damage_min,u.damage_max,u.damage_bonus),(25,30,5))
        self.assertEqual(u.quelling_bonus,8);self.assertEqual(u.quelling_items,ITEM)
        self.assertFalse(read.call_args.args[-1])
    def test_actual_attack_capability_selects_ranged_not_projectile_fallback(self):
        from last_hit_quelling import Quelling
        self.f.field('C_DOTA_BaseNPC','m_iAttackCapabilities','<i',2)
        with patch('last_hit_reader.read_quelling',return_value=Quelling(4,True,'synthetic',ITEM)) as read:
            u=self.f.read('hero')
        self.assertTrue(u.quelling_ranged);self.assertTrue(read.call_args.args[-1]);self.assertEqual(u.quelling_bonus,4)


class DamageTests(unittest.TestCase):
    def setUp(self):self.h=replace(old.hero(damage_min=50,damage_max=60),quelling_bonus=8,quelling_known=True)
    def pick(self,h=None,c=None,**kw):
        m=Model();f=old.frame(h=h or self.h,creeps=(c or old.unit(),));m.choose(f,**kw);return m.rows[0]['choice']
    def test_melee_quell_added_before_armor(self):
        self.assertAlmostEqual(self.pick(c=old.unit(armor=10),margin=0).damage,58*armor_multiplier(10))
    def test_ranged_uses_4_from_live_values(self):
        q=resolve(ITEM,{'item_quelling_blade':(8,4)},True)
        self.assertEqual(self.pick(h=replace(self.h,quelling_bonus=q.bonus),margin=0).damage,54)
    def test_deny_has_no_quell(self):self.assertEqual(self.pick(c=old.unit(team=2),margin=0).damage,50)
    def test_no_bonus_to_hero_or_tower(self):
        for kind in ('hero','tower'):self.assertEqual(quelling_bonus(self.h,replace(old.unit(),kind=kind)),0)
    def test_unknown_bonus_is_not_assumed_eight(self):
        self.assertEqual(self.pick(h=replace(self.h,quelling_known=False),margin=0).damage,50)
    def test_generic_bonus_not_added_twice(self):
        self.assertEqual(self.pick(h=replace(self.h,damage_bonus=20),margin=0).damage,58)
    def test_reserve_margin_and_siege_multiplier(self):
        self.assertAlmostEqual(self.pick(c=old.unit(incoming_multiplier=.6),damage_reserve=10,margin=3).damage,58*.6*.9-3)
    def test_quell_can_change_last_hit_threshold(self):
        # A blade can move a 55HP enemy from wait into the conservative threshold.
        m=Model();f=old.frame(h=self.h,creeps=(old.unit(hp=55),))
        self.assertIsNotNone(m.choose(f,margin=0)[0])
        self.assertIsNone(m.choose(replace(f,hero=replace(self.h,quelling_bonus=0)),margin=0)[0])
    def test_duplicate_and_battlefury_use_max_not_sum(self):
        items=ITEM+((1,0x10004,0x70000,'item_quelling_blade'),(2,0x10005,0x90000,'item_bfury'))
        self.assertEqual(resolve(items,{'item_quelling_blade':(8,4),'item_bfury':(10,5)},False).bonus,10)
    def test_active_item_without_modifier_is_unknown(self):
        q=resolve(ITEM,{},False);self.assertFalse(q.known);self.assertEqual(q.bonus,0)
    def test_absent_active_items_are_known_zero(self):self.assertTrue(resolve((),None,False).known)
    def test_nonfinite_bonus_rejected(self):
        for value in (float('nan'),float('inf'),-8,200):
            self.assertEqual(quelling_bonus(replace(self.h,quelling_bonus=value),old.unit()),0)


class HealthEvidenceTests(unittest.TestCase):
    def setUp(self):self.m=Model();self.uid=old.unit().uid
    def sample(self,t,hp,**kw):self.m.update(old.frame(now=t,creeps=(old.unit(hp=hp,**kw),)))
    def losses(self):
        for t,hp in ((10,100),(10.1,90),(10.2,80)):self.sample(t,hp)
    def test_observed_loss_has_no_source_attribution(self):
        self.losses();loss,span=self.m.observed_loss(self.uid)
        self.assertEqual(loss,20);self.assertAlmostEqual(span,.2)
    def test_heal_breaks_dps_history(self):
        self.losses();self.assertGreater(self.m.dps(self.uid),0)
        self.sample(10.3,140);self.assertEqual(self.m.dps(self.uid),0);self.assertEqual(self.m.observed_loss(self.uid),(0,0))
    def test_sampling_gap_breaks_history(self):
        self.losses();self.sample(10.7,60);self.assertEqual(self.m.dps(self.uid),0)
    def test_maxhp_or_team_change_breaks_history(self):
        self.losses();self.sample(10.3,70,maxhp=600);self.assertEqual(self.m.dps(self.uid),0)
        self.losses();self.sample(10.3,70,team=2);self.assertEqual(self.m.dps(self.uid),0)
    def test_single_drop_is_not_sustained_dps(self):
        self.sample(10,100);self.sample(10.1,50);self.assertEqual(self.m.dps(self.uid),0)
        self.assertEqual(self.m.observed_loss(self.uid)[0],50)
    def test_own_attack_window_not_extrapolated_as_foreign_dps(self):
        self.m.record_own_attack(self.uid,10.2);self.losses()
        self.assertEqual(self.m.dps(self.uid),0);self.assertEqual(self.m.observed_loss(self.uid)[0],20)
    def test_future_clean_observations_can_resume_estimate(self):
        self.m.record_own_attack(self.uid,10.2)
        for t,hp in ((10.4,100),(10.5,90),(10.6,80)):self.sample(t,hp)
        self.assertGreater(self.m.dps(self.uid),0)
    def test_exact_target_tower_forecast_not_counted_again_as_hp_trend(self):
        self.losses();c=old.unit(hp=80)
        shot=old.Shot((300,1),c.uid,10,10.3,10.4,10,10)
        low,high=self.m.hp_bounds(c,10.2,10.5,10.6,[shot]);self.assertEqual((low,high),(70,70))


class ServiceDamageTests(unittest.TestCase):
    def setUp(self):
        self.f=old.ServiceTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.s=self.f.s
        self.s.reader.latest=replace(self.s.reader.latest,hero=replace(self.s.reader.latest.hero,quelling_bonus=8,quelling_known=True,quelling_items=ITEM))
    def test_bonus_dependent_order_is_revoked_by_final_item_guard(self):
        self.s.reader.verify_quelling=lambda hero:False
        self.f.tick();self.assertFalse(self.f.calls)
    def test_bonus_reaches_helper_and_dashboard(self):
        self.s.reader.verify_quelling=lambda hero:True
        self.f.tick()
        self.assertEqual(self.s.helpers[0]['quell'],8)
        self.assertIn('hp_loss',self.s.helpers[0]);self.assertIn('Quell +8',self.s.pipeline)
    def test_successful_order_marks_ambiguous_own_impact_window(self):
        self.s.reader.verify_quelling=lambda hero:True
        self.f.tick();self.assertIn(old.unit().uid,self.s.model.own_impacts)


if __name__=='__main__':unittest.main()
