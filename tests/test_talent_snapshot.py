import ast
import json
import struct
import unittest
from types import SimpleNamespace

from helpers import ROOT, fake_module
from ks_reader import talent_levels_from_snapshot

TALENT = 'special_bonus_unique_lion_8'


class Memory:
    def __init__(self):
        self.blocks = {}
        self.entities = {}

    def rb(self, address, size):
        for base, data in self.blocks.items():
            if base <= address and address + size <= base + len(data):
                return bytes(data[address - base:address - base + size])
        return None

    def get_entity(self, idx):
        return self.entities.get(idx)


class TalentSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.classes = json.loads((ROOT / 'client_dll.json').read_text(encoding='utf-8'))['client.dll']['classes']
        self.dot = Memory()
        self.idx, self.entity, self.offset = 101, 0x400000, self.classes['C_DOTABaseAbility']['fields']['m_iLevel']
        self.dot.entities[self.idx] = (self.entity, TALENT)
        self.dot.blocks[self.entity + self.offset] = bytearray(struct.pack('<i', 1))
        self.records = [{'name': TALENT, 'ent': self.entity, 'idx': self.idx,
                         'handle': 0x8000 | self.idx, 'level': 1}]

    def read(self, records=None):
        return talent_levels_from_snapshot(self.dot, self.records if records is None else records,
                                           {TALENT}, self.classes)

    def test_accepts_learned_and_unlearned_levels_only_from_live_entity(self):
        self.assertEqual(self.read(), ((TALENT, 1),))
        self.dot.blocks[self.entity + self.offset] = bytearray(struct.pack('<i', 0))
        self.records[0]['level'] = 0
        self.assertEqual(self.read(), ((TALENT, 0),))

    def test_rejects_snapshot_and_live_level_disagreement(self):
        self.records[0]['level'] = 0
        with self.assertRaisesRegex(ValueError, 'mismatched'):
            self.read()

    def test_rejects_torn_or_invalid_level(self):
        self.dot.blocks[self.entity + self.offset] = bytearray(struct.pack('<i', 2))
        with self.assertRaisesRegex(ValueError, 'invalid/mismatched'):
            self.read()

    def test_rejects_changed_entity_identity(self):
        self.dot.entities[self.idx] = (self.entity + 0x1000, TALENT)
        with self.assertRaisesRegex(ValueError, 'entity changed'):
            self.read()

    def test_missing_talent_reports_detected_names(self):
        with self.assertRaisesRegex(ValueError, 'requested talent missing') as cm:
            talent_levels_from_snapshot(self.dot, [], {TALENT}, self.classes)
        self.assertIn(TALENT, str(cm.exception))

    def test_duplicate_talent_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, 'duplicate talent'):
            self.read(self.records * 2)


class DotTalentVectorTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'dota_esp.py').read_text(encoding='utf-8'))
        original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Dot')
        names = {'ensure_index', 'talent_names_of'}
        methods = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in names]
        dot_class = ast.ClassDef(name='Dot', bases=[], keywords=[], body=methods, decorator_list=[])
        namespace = vars(fake_module())
        namespace.update(struct=struct, ptr_ok=lambda value: 0x10000 <= value < 0x7FFFFFFFFFFF,
                         M_VEC_ABILITIES=0xC30, AB_I_LEVEL=self.offset_for_level())
        module = ast.Module(body=[dot_class], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), 'dot_talent_vector_test', 'exec'), namespace)
        self.Dot = namespace['Dot']
        self.memory = Memory()
        self.hero, self.vector, self.array = 0x200000, 0x300000, 0x310000
        base = self.hero + 0xC30
        header = bytearray(24)
        struct.pack_into('<Q', header, 0, self.array)
        struct.pack_into('<i', header, 8, 2)
        self.memory.blocks[base] = header
        self.memory.blocks[self.array] = bytearray(struct.pack('<II', 0x8000 | 5, 0x8000 | 6))
        self.memory.entities[5] = (0x400000, TALENT)
        self.memory.entities[6] = (0x500000, 'lion_impale')
        self.memory.blocks[0x400000 + self.offset_for_level()] = bytearray(struct.pack('<i', 1))
        self.dot = self.Dot.__new__(self.Dot)
        self.dot.rb = self.memory.rb
        self.dot.ensure_index = self.Dot.ensure_index.__get__(self.dot, self.Dot)

    @staticmethod
    def offset_for_level():
        return 0x628

    def test_dot_exports_records_not_just_names(self):
        result = self.dot.talent_names_of(self.memory.entities, self.hero)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], TALENT)
        self.assertEqual(result[0]['level'], 1)
        self.assertEqual(result[0]['idx'], 5)
        self.assertEqual(result[0]['ent'], 0x400000)

    def test_unreadable_level_is_unknown_not_zero(self):
        self.memory.blocks[0x400000 + self.offset_for_level()] = bytearray(struct.pack('<i', 2))
        result = self.dot.talent_names_of(self.memory.entities, self.hero)
        self.assertIsNone(result[0]['level'])


if __name__ == '__main__':
    unittest.main()
