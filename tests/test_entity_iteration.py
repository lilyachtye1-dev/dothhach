import ast
import struct
import time
import unittest

from helpers import ROOT, fake_module


class EntityIterationTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'dota_esp.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Dot')
        names = {'find_player_resource', 'ensure_index', 'resolve_hero_for_ctrl', 'entities'}
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        ns = dict(vars(fake_module()), time=time, struct=struct, ptr_ok=lambda p: bool(p))
        self.DW_GAME_ENTITY_SYSTEM = ns['DW_GAME_ENTITY_SYSTEM']
        exec(compile(ast.Module(body=[cls], type_ignores=[]), 'dot_entity_snapshot', 'exec'), ns)
        self.Dot = ns['Dot']

    def test_player_resource_scan_can_force_fetch_without_mutating_iterator(self):
        d = self.Dot.__new__(self.Dot)
        d._pr_idx = d._pr_ent = None
        d._utl_vec = lambda _base: [(0x500000, 1, 'fixture')]
        d.i32 = lambda _address: 50
        d.u32 = lambda _address: 0x8005
        d.get_entity = lambda idx: (0x600000, 'npc_dota_hero_lion') if idx == 5 else None
        d.is_real_hero = lambda ents, idx, *_args: (
            idx in ents and (ents[idx][1] or '').startswith('npc_dota_hero_'))
        ents = {1: (0x200000, 'game_rules')}

        result = d.find_player_resource(ents)

        self.assertEqual(result, (1, 0x200000))
        self.assertIn(5, ents)

    def test_assigned_hero_above_highest_index_is_still_force_fetched(self):
        d = self.Dot.__new__(self.Dot)
        d.get_entity = lambda idx: (0x600000, 'npc_dota_hero_lion') if idx == 2060 else None
        d.is_real_hero = lambda ents, idx, *_args: (
            idx in ents and (ents[idx][1] or '').startswith('npc_dota_hero_'))
        d.pr_id_to_pawn = lambda *_args: 0
        c = {'pid': 3, 'hero_assigned': 2060, 'last_idx': 0, 'pawn_idx': 0}
        ents = {}

        result = d.resolve_hero_for_ctrl(ents, c, {}, [])

        self.assertEqual(result, (2060, 'assigned'))
        self.assertIn(2060, ents)

    def _entity_scan_fixture(self):
        d = self.Dot.__new__(self.Dot)
        d.client = 0x100000
        esys = 0x200000
        d.rb = lambda _address, size: bytes(size)
        d.i32 = lambda _address: 600
        d.u64 = lambda address: esys if address == d.client + self.DW_GAME_ENTITY_SYSTEM else (
            0x300000 + ((address - (esys + 0x10)) // 8) * 0x10000
            if esys + 0x10 <= address < esys + 0x10 + 64 * 8 else 0)
        chunk_reads = []
        original_rb = d.rb
        d.rb = lambda address, size: (chunk_reads.append(address) or original_rb(address, size))
        return d, chunk_reads

    def test_feed_scan_reads_only_chunks_through_highest_index(self):
        d, chunk_reads = self._entity_scan_fixture()

        result = d.entities(full_scan=False)

        self.assertEqual(result, {})
        self.assertEqual(len(chunk_reads), 2)

    def test_full_scan_mode_retains_all_64_chunks(self):
        d, chunk_reads = self._entity_scan_fixture()

        result = d.entities(full_scan=True)

        self.assertEqual(result, {})
        self.assertEqual(len(chunk_reads), 64)


if __name__ == '__main__':
    unittest.main()
