import copy
from dataclasses import replace
import hashlib
import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from helpers import ROOT, fixture, settings
from ks_core import choose
from ks_modifiers import ModifierReader, talent_levels, validate_layout

COUNTER='CDOTA_Modifier_Lion_FingerOfDeath_Kill_Counter'
TALENT='special_bonus_unique_lion_8'


class Memory:
    def __init__(self):self.blocks={};self.types={};self.entities={}
    def rb(self,a,n):
        for base,raw in self.blocks.items():
            if base<=a and a+n<=base+len(raw):return bytes(raw[a-base:a-base+n])
        return None
    def get_entity(self,idx):return self.entities.get(idx)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.s=settings()
        self.classes=json.loads((ROOT/'client_dll.json').read_text())['client.dll']['classes']
        self.digest=hashlib.sha256((ROOT/'client_dll.json').read_bytes()).hexdigest()
        self.reader=ModifierReader(self.classes,self.s.live_modifier_layout,self.digest)
        self.samples=json.loads((ROOT/'tests/fixtures/lion_stacks.json').read_text())['snapshots']
        self.load(0)

    def load(self,index):
        self.sample=copy.deepcopy(self.samples[index]);self.mem=Memory()
        self.hero=int(self.sample['hero']['entity'],16);self.manager=int(self.sample['manager'],16)
        self.mem.blocks[self.manager]=bytearray.fromhex(self.sample['manager_hex'])
        for a in self.sample['arrays']:self.mem.blocks[int(a['address'],16)]=bytearray.fromhex(a['raw_hex'])
        for o in self.sample['objects']:
            addr=int(o['address'],16);self.mem.blocks[addr]=bytearray.fromhex(o['raw_hex']);self.mem.types[addr]=o['cls']
        self.counter=next(a for a,cls in self.mem.types.items() if cls==COUNTER)

    def read(self):
        probe=SimpleNamespace(image=self.sample['client'],read=self.mem.rb,runtime_type=lambda p:self.mem.types.get(p))
        with patch('ks_modifiers.Probe',return_value=probe):
            return dict(self.reader.stacks(self.mem,self.hero,{COUNTER}))

    def test_all_three_real_capture_vectors(self):
        for i in range(3):
            self.load(i)
            self.assertEqual(self.read()[COUNTER],i+1)

    def test_reads_current_value_not_cached_value(self):
        self.assertEqual(self.read()[COUNTER],1)
        struct.pack_into('<i',self.mem.blocks[self.counter],0x84,9)
        self.assertEqual(self.read()[COUNTER],9)

    def test_missing_counter_only_zero_after_complete_list_validation(self):
        raw=self.mem.blocks[self.manager]
        n=struct.unpack_from('<i',raw,16)[0];p=struct.unpack_from('<Q',raw,24)[0]
        ptrs=list(struct.unpack_from('<'+'Q'*n,self.mem.blocks[p]));ptrs.remove(self.counter)
        struct.pack_into('<i',raw,16,len(ptrs));struct.pack_into('<'+'Q'*len(ptrs),self.mem.blocks[p],0,*ptrs)
        self.assertEqual(self.read()[COUNTER],0)

    def test_bad_owner_blocks(self):
        struct.pack_into('<Q',self.mem.blocks[self.counter],0x30,self.manager+8)
        with self.assertRaisesRegex(ValueError,'owner'):self.read()

    def test_bad_manager_owner_blocks(self):
        struct.pack_into('<Q',self.mem.blocks[self.manager],8,self.hero+8)
        with self.assertRaisesRegex(ValueError,'owner'):self.read()

    def test_unknown_type_not_zero(self):
        del self.mem.types[self.counter]
        with self.assertRaisesRegex(ValueError,'unknown modifier'):self.read()

    def test_negative_or_huge_count_blocks(self):
        for n in (-1,10001):
            struct.pack_into('<i',self.mem.blocks[self.counter],0x84,n)
            with self.assertRaisesRegex(ValueError,'stack count'):self.read()

    def test_wrong_build_blocks(self):
        self.sample['client']['timestamp']+=1
        with self.assertRaisesRegex(ValueError,'build'):self.read()

    def test_wrong_schema_blocks(self):
        self.reader.digest='0'*64
        with self.assertRaisesRegex(ValueError,'schema'):self.read()

    def test_oversized_vector_blocks(self):
        struct.pack_into('<i',self.mem.blocks[self.manager],16,1000)
        with self.assertRaisesRegex(ValueError,'vector'):self.read()

    def test_duplicate_vector_blocks(self):
        p=struct.unpack_from('<Q',self.mem.blocks[self.manager],24)[0]
        self.mem.blocks[p][8:16]=self.mem.blocks[p][:8]
        with self.assertRaisesRegex(ValueError,'duplicate'):self.read()

    def test_torn_read_blocks(self):
        original=self.mem.rb;reads=0
        def rb(a,n):
            nonlocal reads
            if a==self.counter+0x84:
                reads+=1
                if reads>1:return struct.pack('<i',10)
            return original(a,n)
        self.mem.rb=rb
        with self.assertRaisesRegex(ValueError,'changed'):self.read()

    def test_layout_validation(self):
        for key,value in [('stack_count',True),('stack_count',-1),('stack_count',0x10000),('max_count',10000),('schema_sha256','xyz')]:
            c=dict(self.s.live_modifier_layout);c[key]=value
            with self.assertRaises(ValueError):validate_layout(c)


class TalentTests(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((ROOT/'client_dll.json').read_text())['client.dll']['classes']
        self.mem=Memory();self.hero=0x200000;self.data=0x300000;self.talent=0x400000
        self.base=self.hero+self.c['C_DOTA_BaseNPC']['fields']['m_vecAbilities']
        self.address=self.talent+self.c['C_DOTABaseAbility']['fields']['m_iLevel']
        h=bytearray(24);struct.pack_into('<Q',h,0,self.data);struct.pack_into('<i',h,8,2)
        self.mem.blocks[self.base]=h
        self.mem.blocks[self.data]=bytearray(struct.pack('<II',101,102))
        self.mem.blocks[self.address]=bytearray(struct.pack('<i',1))
        self.mem.entities={101:(self.talent,TALENT),102:(0x500000,'lion_impale')}

    def read(self):return dict(talent_levels(self.mem,self.hero,{TALENT},self.c))

    def test_learned_and_unlearned(self):
        self.assertEqual(self.read()[TALENT],1)
        self.mem.blocks[self.address]=struct.pack('<i',0)
        self.assertEqual(self.read()[TALENT],0)

    def test_missing_talent_not_assumed_unlearned(self):
        self.mem.entities[101]=(self.talent,'special_bonus_other')
        with self.assertRaises(ValueError):self.read()

    def test_level_not_hero_level(self):
        self.mem.blocks[self.address]=struct.pack('<i',30)
        with self.assertRaises(ValueError):self.read()

    def test_torn_talent_rejected(self):
        original=self.mem.rb;calls=0
        def rb(a,n):
            nonlocal calls
            if a==self.address:
                calls+=1
                return struct.pack('<i',1 if calls==1 else 0)
            return original(a,n)
        self.mem.rb=rb
        with self.assertRaises(ValueError):self.read()


class StackFormulaTests(unittest.TestCase):
    def decide(self,n=3,talent=0):
        me,e=fixture()
        me=replace(me,stack_values=() if n is None else ((COUNTER,n),),
                   talent_levels=() if talent is None else ((TALENT,talent),))
        return choose((me,e),settings(),10.1,(1920,1080))

    def test_twenty_five_per_stack(self):
        self.assertAlmostEqual(self.decide().damage,(600+3*25)*.73)

    def test_forty_five_only_if_talent_learned(self):
        self.assertAlmostEqual(self.decide(talent=1).damage,(600+3*45)*.73)

    def test_unknown_count_blocks_finger(self):
        self.assertIsNone(self.decide(n=None))

    def test_unknown_talent_uses_only_confirmed_per_stack_damage(self):
        decision=self.decide(talent=None)
        self.assertIsNotNone(decision)
        self.assertAlmostEqual(decision.damage,(600+3*25)*.73)

    def test_zero_count_does_not_need_talent_for_zero_bonus(self):
        self.assertAlmostEqual(self.decide(n=0,talent=None).damage,600*.73)

    def test_invalid_count_does_not_create_bonus(self):
        for n in (-1,10001,True,2.5):self.assertIsNone(self.decide(n=n))

    def test_base_plus_stack_then_resistance(self):
        self.assertAlmostEqual(self.decide(n=10,talent=1).damage,1050*.73)
